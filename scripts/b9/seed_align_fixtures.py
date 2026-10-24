from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select
from starlette.exceptions import HTTPException as StarletteHTTPException

from align_api import integrations
from align_api.auth import SESSION_COOKIE
from align_api.config import get_settings
from align_api.db import SessionFactory
from align_api.idempotency import canonical_hash
from align_api.integrations import GroupMappingCreate, MappingCreate
from align_api.main import create_app
from align_api.models import (
    Assignment,
    AssignmentGroup,
    AssignmentVersion,
    AuditEvent,
    ContextVersion,
    Course,
    Enrollment,
    FileAsset,
    GroupMembership,
    IdempotencyRecord,
    Job,
    LmsCourseAssignmentMapping,
    LmsImportEvent,
    LtiCoursePermission,
    LtiIdentityBinding,
    LtiPlatform,
    LtiSessionBinding,
    OutboxEvent,
    ParseArtifact,
    Session,
    Submission,
    SubmissionVersion,
    SubmissionVersionMember,
    User,
)
from align_api.moodle_adapter import (
    MoodleAdapterError,
    MoodleWebService,
    configured_moodle_token,
)
from align_api.security import token_digest

QA_DIR = Path("/run/b9-qa")
MOODLE_FIXTURE_PATH = QA_DIR / "moodle-fixtures.json"
ALIGN_FIXTURE_PATH = QA_DIR / "align-fixtures.json"


def _load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"invalid B9 fixture metadata: {path.name}")
    return value


def _context_for(db, course: Course, lecturer: User) -> ContextVersion:
    context = (
        db.get(ContextVersion, course.approved_context_version_id)
        if course.approved_context_version_id is not None
        else None
    )
    if context is not None and context.state == "approved":
        return context
    context = db.scalar(
        select(ContextVersion)
        .where(ContextVersion.course_id == course.id, ContextVersion.state == "approved")
        .order_by(ContextVersion.version_no.desc())
    )
    if context is None:
        fields = {
            "context_description": {"value": f"Fixture sintetis B9 untuk {course.code}."},
            "outcomes_status": "not_provided",
            "learning_outcomes": [],
        }
        context = ContextVersion(
            course_id=course.id,
            version_no=1,
            state="approved",
            fields_json=fields,
            instruction_sources_json=[],
            content_hash=canonical_hash(fields),
            approved_by=lecturer.id,
            approved_at=datetime.now(UTC),
            revision=1,
        )
        db.add(context)
        db.flush()
    course.approved_context_version_id = context.id
    return context


def _ensure_assignment(
    db,
    *,
    course: Course,
    lecturer: User,
    context: ContextVersion,
    title: str,
    mode: str,
    students: list[User],
) -> tuple[Assignment, AssignmentVersion, AssignmentGroup | None]:
    assignment = db.scalar(
        select(Assignment).where(Assignment.course_id == course.id, Assignment.title == title)
    )
    if assignment is None:
        assignment = Assignment(course_id=course.id, title=title, administrative_state="open")
        db.add(assignment)
        db.flush()
    version = db.scalar(
        select(AssignmentVersion)
        .where(AssignmentVersion.assignment_id == assignment.id)
        .order_by(AssignmentVersion.version_no.desc())
    )
    if version is None:
        now = datetime.now(UTC)
        instruction = f"Submission final sintetis B9: {title}."
        version = AssignmentVersion(
            assignment_id=assignment.id,
            version_no=1,
            state="published",
            context_version_id=context.id,
            instruction_original=instruction,
            instruction_hash=hashlib.sha256(instruction.encode("utf-8")).hexdigest(),
            mode=mode,
            opens_at=now - timedelta(days=1),
            due_at=now + timedelta(days=1),
            late_policy="accept_with_label",
            limits_json={"max_files": 5, "max_file_bytes": 20_971_520},
            task_facts_json={},
            package_approved_by=lecturer.id,
            package_approved_at=now,
            published_by=lecturer.id,
            published_at=now,
            revision=1,
        )
        db.add(version)
        db.flush()
    elif version.mode != mode or version.state != "published":
        raise SystemExit(f"existing B9 assignment fixture is incompatible: {title}")
    assignment.active_version_id = version.id

    group = None
    if mode == "group":
        group = db.scalar(
            select(AssignmentGroup).where(
                AssignmentGroup.assignment_id == assignment.id,
                AssignmentGroup.name == "B9 Kelompok A",
            )
        )
        if group is None:
            group = AssignmentGroup(assignment_id=assignment.id, name="B9 Kelompok A", active=True)
            db.add(group)
            db.flush()
        for student in students:
            if db.scalar(
                select(GroupMembership.id).where(
                    GroupMembership.assignment_id == assignment.id,
                    GroupMembership.group_id == group.id,
                    GroupMembership.user_id == student.id,
                )
            ) is None:
                db.add(
                    GroupMembership(
                        assignment_id=assignment.id,
                        group_id=group.id,
                        user_id=student.id,
                        active=True,
                    )
                )
    return assignment, version, group


def _login_client(email: str) -> tuple[TestClient, str]:
    settings = get_settings()
    if settings.align_seed_password is None:
        raise SystemExit("B9 ALIGN seed password is not configured")
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings

    def qa_http_error(_request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"qa_detail": exc.detail})

    app.add_exception_handler(StarletteHTTPException, qa_http_error)
    client = TestClient(app, base_url=settings.app_origin)
    response = client.post(
        "/api/v1/auth/login",
        headers={"Origin": settings.app_origin},
        json={
            "email": email,
            "password": settings.align_seed_password.get_secret_value(),
        },
    )
    if response.status_code != 200:
        raise SystemExit(f"synthetic ALIGN fixture login failed for {email}: {response.status_code}")
    return client, response.json()["csrf_token"]


def _post_import(
    *,
    actor_email: str,
    mapping: dict[str, object],
    body: dict[str, object],
    key: str,
) -> dict[str, object]:
    settings = get_settings()
    client, csrf = _login_client(actor_email)
    try:
        response = client.post(
            "/api/v1/integrations/moodle/imports",
            headers={
                "Origin": settings.app_origin,
                "X-CSRF-Token": csrf,
                "Idempotency-Key": key,
            },
            json=body,
        )
        if response.status_code != 201:
            try:
                diagnostic = response.json().get("qa_detail", response.text)
            except ValueError:
                diagnostic = response.text
            raise SystemExit(
                f"B9 import acceptance failed for {mapping['id']}: "
                f"{response.status_code} {diagnostic}"
            )
        return response.json()
    finally:
        client.close()


def _import_body(
    *,
    mapping: dict[str, object],
    actor_email: str,
    owner_id: str | None,
    roster_ids: list[str],
    moodle_user_id: int | None = None,
    moodle_group_id: int | None = None,
) -> dict[str, object]:
    client, _csrf = _login_client(actor_email)
    try:
        response = client.get(
            "/api/v1/integrations/moodle/imports",
            params={"mapping_id": mapping["id"]},
        )
        if response.status_code != 200:
            raise SystemExit(
                f"B9 Moodle attempt discovery failed for {mapping['id']}: "
                f"{response.status_code} {response.text}"
            )
        candidates = response.json()["candidates"]
        candidate = next(
            (
                item
                for item in candidates
                if (
                    moodle_user_id is not None
                    and item["moodle_user_id"] == moodle_user_id
                )
                or (
                    moodle_group_id is not None
                    and item["moodle_group_id"] == moodle_group_id
                )
            ),
            None,
        )
        if candidate is None or candidate["blocked_reason"] is not None:
            raise SystemExit(
                f"B9 final Moodle attempt is unavailable for mapping {mapping['id']}"
            )
        if owner_id is not None and candidate.get("owner", {}).get("user_id") != owner_id:
            raise SystemExit("B9 Moodle owner does not match the explicit local binding")
        if owner_id is None:
            actual_roster = sorted(member["user_id"] for member in candidate["group"]["members"])
            if actual_roster != sorted(roster_ids):
                raise SystemExit("B9 group roster does not match the local snapshot")
        return {
            "mapping_id": mapping["id"],
            "expected_mapping_revision": mapping["revision"],
            "attempt_id": candidate["attempt_id"],
            "confirmed_owner_user_id": owner_id,
            "confirmed_roster_user_ids": roster_ids,
        }
    finally:
        client.close()


def _check_receipt_and_audit(receipt: dict[str, object]) -> bool:
    event_id = UUID(str(receipt["id"]))
    version_id = UUID(str(receipt["submission_version_id"]))
    with SessionFactory() as db:
        event = db.get(LmsImportEvent, event_id)
        version = db.get(SubmissionVersion, version_id)
        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "submission.moodle_imported",
                AuditEvent.object_id == version_id,
            )
        )
        return bool(
            event is not None
            and version is not None
            and event.id == event_id
            and event.submission_version_id == version_id
            and event.job_id == UUID(str(receipt["job_id"]))
            and audit is not None
            and audit.new_version_refs_json["import_event_id"] == str(event_id)
        )


def _run_import_checks(
    *,
    mappings: dict[str, dict[str, object]],
    users: dict[str, str],
    moodle: dict[str, object],
) -> dict[str, object]:
    cases = [
        {
            "key": "IF101-individual",
            "actor": "dosen@align.local",
            "owner": users["mahasiswa@align.local"],
            "roster": [],
            "moodle_user_id": int(moodle["users"]["b9student1"]["id"]),
        },
        {
            "key": "IF101-group",
            "actor": "dosen@align.local",
            "owner": None,
            "roster": [users["mahasiswa@align.local"], users["mahasiswa2@align.local"]],
            "moodle_group_id": int(moodle["groups"]["IF101 Kelompok A"]["id"]),
        },
        {
            "key": "IF202-individual",
            "actor": "dosen2@align.local",
            "owner": users["mahasiswa3@align.local"],
            "roster": [],
            "moodle_user_id": int(moodle["users"]["b9student3"]["id"]),
        },
    ]
    results: dict[str, object] = {}
    for case in cases:
        mapping = mappings[case["key"]]
        body = _import_body(
            mapping=mapping,
            actor_email=case["actor"],
            owner_id=case["owner"],
            roster_ids=case["roster"],
            moodle_user_id=case.get("moodle_user_id"),
            moodle_group_id=case.get("moodle_group_id"),
        )
        idempotency_key = str(uuid5(NAMESPACE_URL, f"align-b9:import:{case['key']}:initial"))
        receipt = _post_import(
            actor_email=case["actor"],
            mapping=mapping,
            body=body,
            key=idempotency_key,
        )
        replay = _post_import(
            actor_email=case["actor"],
            mapping=mapping,
            body=body,
            key=idempotency_key,
        )
        if receipt != replay:
            raise SystemExit("B9 exact-key import retry did not return the stored receipt")
        if receipt["status"] != "imported" or not _check_receipt_and_audit(receipt):
            raise SystemExit("B9 import receipt, immutable version or audit reference is inconsistent")
        results[case["key"]] = {
            "receipt_id": receipt["id"],
            "submission_version_id": receipt["submission_version_id"],
            "job_id": receipt["job_id"],
            "exact_key_replay": True,
            "audit_reference_matches": True,
        }

        if case["key"] == "IF101-individual":
            barrier = Barrier(4)

            def concurrent_retry(
                index: int,
                *,
                barrier_arg: Barrier = barrier,
                actor_email: str = str(case["actor"]),
                target_mapping: dict[str, object] = mapping,
                import_body: dict[str, object] = body,
            ) -> dict[str, object]:
                barrier_arg.wait()
                key = str(uuid5(NAMESPACE_URL, f"align-b9:import:concurrent:{index}"))
                return _post_import(
                    actor_email=actor_email,
                    mapping=target_mapping,
                    body=import_body,
                    key=key,
                )

            with ThreadPoolExecutor(max_workers=4) as pool:
                concurrent = list(pool.map(concurrent_retry, range(4)))
            if any(
                item["id"] != receipt["id"]
                or item["submission_version_id"] != receipt["submission_version_id"]
                or item["job_id"] != receipt["job_id"]
                for item in concurrent
            ):
                raise SystemExit("four concurrent identical Moodle imports were not deduplicated")
            results[case["key"]]["four_concurrent_same_source"] = True
    return results


def _run_source_change_check(
    *,
    mapping: dict[str, object],
    users: dict[str, str],
    moodle: dict[str, object],
) -> dict[str, object]:
    actor_email = "dosen@align.local"
    body = _import_body(
        mapping=mapping,
        actor_email=actor_email,
        owner_id=users["mahasiswa@align.local"],
        roster_ids=[],
        moodle_user_id=int(moodle["users"]["b9student1"]["id"]),
    )
    with SessionFactory() as db:
        baseline = db.scalar(
            select(LmsImportEvent)
            .where(
                LmsImportEvent.mapping_id == UUID(str(mapping["id"])),
                LmsImportEvent.moodle_attempt_id == body["attempt_id"],
                LmsImportEvent.status == "imported",
            )
            .order_by(LmsImportEvent.created_at.asc())
        )
        if baseline is None:
            raise SystemExit("B9 source-change test requires its earlier source receipt")
        baseline_version = db.get(SubmissionVersion, baseline.submission_version_id)
        if baseline_version is None:
            raise SystemExit("B9 baseline Moodle submission version is unavailable")

    key = str(uuid5(NAMESPACE_URL, "align-b9:source-change:IF101-individual"))
    receipt = _post_import(actor_email=actor_email, mapping=mapping, body=body, key=key)
    replay = _post_import(actor_email=actor_email, mapping=mapping, body=body, key=key)
    if receipt != replay or not _check_receipt_and_audit(receipt):
        raise SystemExit("B9 changed-source receipt, retry or audit reference is inconsistent")
    with SessionFactory() as db:
        imported = db.get(LmsImportEvent, UUID(str(receipt["id"])))
        imported_version = db.get(SubmissionVersion, UUID(str(receipt["submission_version_id"])))
        if (
            imported is None
            or imported_version is None
            or imported.source_fingerprint == baseline.source_fingerprint
            or imported.id == baseline.id
            or imported_version.version_no != baseline_version.version_no + 1
        ):
            raise SystemExit("changed Moodle file did not create the next immutable source version")
    return {
        "new_key_imported_changed_source": True,
        "immutable_version_incremented": True,
        "exact_key_replay": True,
        "audit_reference_matches": True,
    }


def _individual_import_request(
    *,
    mapping: dict[str, object],
    users: dict[str, str],
    moodle: dict[str, object],
) -> tuple[dict[str, object], str, str]:
    actor_email = "dosen@align.local"
    body = _import_body(
        mapping=mapping,
        actor_email=actor_email,
        owner_id=users["mahasiswa@align.local"],
        roster_ids=[],
        moodle_user_id=int(moodle["users"]["b9student1"]["id"]),
    )
    return body, actor_email, str(uuid4())


def _post_import_status(
    *, actor_email: str, body: dict[str, object], key: str
) -> tuple[int, dict[str, object]]:
    settings = get_settings()
    client, csrf = _login_client(actor_email)
    try:
        response = client.post(
            "/api/v1/integrations/moodle/imports",
            headers={
                "Origin": settings.app_origin,
                "X-CSRF-Token": csrf,
                "Idempotency-Key": key,
            },
            json=body,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {"detail": "non-json response"}
        return response.status_code, payload if isinstance(payload, dict) else {}
    finally:
        client.close()


def _import_state(
    *, mapping_id: str, attempt_id: str, owner_id: str
) -> dict[str, int]:
    with SessionFactory() as db:
        mapping = db.get(LmsCourseAssignmentMapping, UUID(mapping_id))
        if mapping is None:
            raise SystemExit("B9 import failure fixture mapping disappeared")
        version = db.get(AssignmentVersion, mapping.assignment_version_id)
        if version is None:
            raise SystemExit("B9 import failure fixture assignment version disappeared")
        assignment = db.get(Assignment, version.assignment_id)
        if assignment is None:
            raise SystemExit("B9 import failure fixture assignment disappeared")
        submission = db.scalar(
            select(Submission).where(
                Submission.assignment_id == assignment.id,
                Submission.individual_user_id == UUID(owner_id),
            )
        )
        version_ids = (
            db.scalars(
                select(SubmissionVersion.id).where(
                    SubmissionVersion.submission_id == submission.id
                )
            ).all()
            if submission is not None
            else []
        )
        return {
            "events": int(
                db.scalar(
                    select(func.count()).select_from(LmsImportEvent).where(
                        LmsImportEvent.mapping_id == mapping.id,
                        LmsImportEvent.moodle_attempt_id == attempt_id,
                    )
                )
                or 0
            ),
            "versions": len(version_ids),
            "assets": int(
                db.scalar(
                    select(func.count()).select_from(FileAsset).where(
                        FileAsset.submission_id == submission.id
                        if submission is not None
                        else FileAsset.id == UUID(int=0),
                        FileAsset.purpose == "submission",
                    )
                )
                or 0
            ),
            "jobs": int(
                db.scalar(
                    select(func.count()).select_from(Job).where(
                        Job.subject_id.in_(version_ids)
                    )
                )
                or 0
            )
            if version_ids
            else 0,
        }


def _expect_storage_outage(
    *, mapping: dict[str, object], users: dict[str, str], moodle: dict[str, object]
) -> dict[str, object]:
    body, actor_email, key = _individual_import_request(
        mapping=mapping, users=users, moodle=moodle
    )
    before = _import_state(
        mapping_id=str(mapping["id"]),
        attempt_id=str(body["attempt_id"]),
        owner_id=str(body["confirmed_owner_user_id"]),
    )
    status, payload = _post_import_status(actor_email=actor_email, body=body, key=key)
    after = _import_state(
        mapping_id=str(mapping["id"]),
        attempt_id=str(body["attempt_id"]),
        owner_id=str(body["confirmed_owner_user_id"]),
    )
    if status != 503 or before != after:
        raise SystemExit(
            "B9 storage outage did not fail without partial DB writes "
            f"(status={status}, unchanged={before == after})"
        )
    with SessionFactory() as db:
        actor = db.scalar(select(User).where(User.normalized_email == actor_email))
        record = db.scalar(
            select(IdempotencyRecord.id).where(
                IdempotencyRecord.actor_id == actor.id if actor is not None else False,
                IdempotencyRecord.route == "/api/v1/integrations/moodle/imports",
                IdempotencyRecord.key == key,
            )
        )
        if record is not None:
            raise SystemExit("B9 failed storage import unexpectedly retained an idempotency receipt")
    fixture = {
        "actor_email": actor_email,
        "body": body,
        "key": key,
        "mapping_id": str(mapping["id"]),
        "before": before,
        "attempt_id": str(body["attempt_id"]),
        "owner_id": str(body["confirmed_owner_user_id"]),
    }
    (QA_DIR / "storage-outage-recovery.json").write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"storage_outage": True, "http_status": status, "database_unchanged": True}


def _retry_storage_outage() -> dict[str, object]:
    fixture = _load_json(QA_DIR / "storage-outage-recovery.json")
    status, receipt = _post_import_status(
        actor_email=str(fixture["actor_email"]),
        body=fixture["body"],
        key=str(fixture["key"]),
    )
    before = fixture["before"]
    after = _import_state(
        mapping_id=str(fixture["mapping_id"]),
        attempt_id=str(fixture["attempt_id"]),
        owner_id=str(fixture["owner_id"]),
    )
    if (
        status != 201
        or not _check_receipt_and_audit(receipt)
        or after["events"] != int(before["events"]) + 1
        or after["versions"] != int(before["versions"]) + 1
        or after["assets"] != int(before["assets"]) + 1
        or after["jobs"] != int(before["jobs"]) + 1
    ):
        raise SystemExit("B9 storage retry did not create exactly one audited import")
    fixture["receipt"] = receipt
    fixture["after"] = after
    (QA_DIR / "storage-outage-recovery.json").write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"storage_retry": True, "receipt_audit_matches": True, "single_import_created": True}


def _import_while_broker_unavailable(
    *, mapping: dict[str, object], users: dict[str, str], moodle: dict[str, object]
) -> dict[str, object]:
    body, actor_email, key = _individual_import_request(
        mapping=mapping, users=users, moodle=moodle
    )
    settings = get_settings()
    client, csrf = _login_client(actor_email)
    probe = Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
    before = _import_state(
        mapping_id=str(mapping["id"]),
        attempt_id=str(body["attempt_id"]),
        owner_id=str(body["confirmed_owner_user_id"]),
    )
    try:
        print("B9_TEST_WAITING_FOR_REDIS_OUTAGE", flush=True)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                probe.ping()
            except RedisError:
                break
            time.sleep(0.2)
        else:
            raise SystemExit("B9 broker outage was not started within 120 seconds")

        response = client.post(
            "/api/v1/integrations/moodle/imports",
            headers={
                "Origin": settings.app_origin,
                "X-CSRF-Token": csrf,
                "Idempotency-Key": key,
            },
            json=body,
        )
        status = response.status_code
        try:
            receipt = response.json()
        except ValueError:
            receipt = {}
        if (
            status != 201
            or not isinstance(receipt, dict)
            or not _check_receipt_and_audit(receipt)
            or str(receipt.get("status")) != "imported"
        ):
            raise SystemExit(
                f"B9 import did not commit while Redis was unavailable (HTTP {status})"
            )
        after = _import_state(
            mapping_id=str(mapping["id"]),
            attempt_id=str(body["attempt_id"]),
            owner_id=str(body["confirmed_owner_user_id"]),
        )
        if (
            after["events"] != int(before["events"]) + 1
            or after["versions"] != int(before["versions"]) + 1
            or after["assets"] != int(before["assets"]) + 1
            or after["jobs"] != int(before["jobs"]) + 1
        ):
            raise SystemExit("B9 broker outage import did not create exactly one version/job")
        job_id = UUID(str(receipt["job_id"]))
        with SessionFactory() as db:
            event = db.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "job.parse_submission",
                    OutboxEvent.subject_id == job_id,
                )
            )
            job = db.get(Job, job_id)
            if (
                event is None
                or event.delivered_at is not None
                or job is None
                or job.status != "queued"
            ):
                raise SystemExit("B9 broker outage did not leave a queued parser outbox event")
        fixture = {"job_id": str(job_id), "outbox_id": str(event.id), "receipt": receipt}
        (QA_DIR / "broker-outbox-recovery.json").write_text(
            json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return {"broker_unavailable": True, "outbox_pending": True, "job_queued": True}
    finally:
        probe.close()
        client.close()


def _verify_broker_outbox_recovery() -> dict[str, object]:
    fixture = _load_json(QA_DIR / "broker-outbox-recovery.json")
    job_id = UUID(str(fixture["job_id"]))
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        with SessionFactory() as db:
            event = db.get(OutboxEvent, UUID(str(fixture["outbox_id"])))
            job = db.get(Job, job_id)
            if event is not None and job is not None:
                if job.status in {"failed", "blocked"}:
                    raise SystemExit(f"B9 parser job did not recover: {job.status}/{job.stage}")
                if event.delivered_at is not None and job.status == "ready_for_review":
                    if not _check_receipt_and_audit(fixture["receipt"]):
                        raise SystemExit("B9 recovered parser job lost its import audit linkage")
                    return {
                        "outbox_delivered_after_restore": True,
                        "parser_job_status": job.status,
                        "receipt_audit_matches": True,
                    }
        time.sleep(2)
    raise SystemExit("B9 Redis/outbox/parser recovery did not finish within 180 seconds")


def _check_source_change_during_download(
    *, mapping: dict[str, object], users: dict[str, str], moodle: dict[str, object]
) -> dict[str, object]:
    body, actor_email, key = _individual_import_request(
        mapping=mapping, users=users, moodle=moodle
    )
    settings = get_settings()
    with SessionFactory() as db:
        mapping_row = db.get(LmsCourseAssignmentMapping, UUID(str(mapping["id"])))
        platform = db.get(LtiPlatform, mapping_row.platform_id) if mapping_row else None
        if mapping_row is None or platform is None:
            raise SystemExit("B9 source-change test mapping or platform disappeared")
        real_client, attempts = asyncio.run(
            integrations._load_current_attempts(settings, platform, mapping_row)
        )
        attempt = next(
            (item for item in attempts if item["attemptid"] == body["attempt_id"]), None
        )
        if attempt is None:
            raise SystemExit("B9 source-change test could not load the Moodle attempt")
        student_id = int(moodle["users"]["b9student1"]["id"])
        changed_attempt = copy.deepcopy(attempt)
        changed_attempt["timemodified"] = int(attempt["timemodified"]) + 1
        remote_submission_id, attempt_number = map(str, str(body["attempt_id"]).split(":"))
        if changed_attempt.get("id") != int(remote_submission_id) or changed_attempt.get(
            "attemptnumber"
        ) != int(attempt_number):
            raise SystemExit("B9 source-change test attempt identifiers are inconsistent")
    del real_client

    payload = Path("fixtures/b9/moodle-final.pdf").read_bytes()
    plugin = next(
        item for item in changed_attempt["plugins"] if item.get("type") == "file"
    )
    area = next(
        item for item in plugin["fileareas"] if item.get("area") == "submission_files"
    )
    area["files"] = [
        {
            "filename": "source-change-during-download.pdf",
            "fileurl": (
                "https://moodle-b9.localhost:8443/webservice/pluginfile.php/"
                "9/assignsubmission_file/submission_files/4/final.pdf"
            ),
            "filesize": len(payload),
            "mimetype": "application/pdf",
        }
    ]
    initial_attempt = copy.deepcopy(changed_attempt)
    initial_attempt["timemodified"] = int(changed_attempt["timemodified"]) - 1

    class ChangingMoodleClient:
        def __init__(self) -> None:
            self.submission_reads = 0

        async def verify_functions(self, required: set[str]) -> frozenset[str]:
            return frozenset(required)

        async def call(self, function: str, **_parameters: object) -> object:
            if function == "mod_assign_get_assignments":
                return {
                    "courses": [
                        {
                            "id": mapping_row.moodle_course_id,
                            "assignments": [{"id": mapping_row.moodle_assignment_id}],
                        }
                    ]
                }
            if function == "mod_assign_get_submissions":
                self.submission_reads += 1
                current = initial_attempt if self.submission_reads == 1 else changed_attempt
                return {
                    "assignments": [
                        {
                            "assignmentid": mapping_row.moodle_assignment_id,
                            "submissions": [current],
                        }
                    ]
                }
            if function == "core_enrol_get_enrolled_users":
                return [{"id": student_id, "suspended": False}]
            raise AssertionError(f"unexpected Moodle function in source-change test: {function}")

        async def download_to_path(
            self, _url: str, path: str, *, max_bytes: int, timeout_seconds: float
        ) -> tuple[int, str]:
            if len(payload) > max_bytes or timeout_seconds <= 0:
                raise AssertionError("source-change fixture exceeds the guarded file limits")
            Path(path).write_bytes(payload)
            return len(payload), hashlib.sha256(payload).hexdigest()

    client, csrf = _login_client(actor_email)
    state_before = _import_state(
        mapping_id=str(mapping["id"]),
        attempt_id=str(body["attempt_id"]),
        owner_id=str(body["confirmed_owner_user_id"]),
    )
    staged_before = set(QA_DIR.glob("align-moodle-*"))
    original_factory = integrations._moodle_client
    original_tempdir = tempfile.tempdir
    integrations._moodle_client = lambda *_args: ChangingMoodleClient()
    tempfile.tempdir = str(QA_DIR)
    try:
        response = client.post(
            "/api/v1/integrations/moodle/imports",
            headers={
                "Origin": settings.app_origin,
                "X-CSRF-Token": csrf,
                "Idempotency-Key": key,
            },
            json=body,
        )
    finally:
        try:
            client.post(
                "/api/v1/auth/logout",
                headers={"Origin": settings.app_origin, "X-CSRF-Token": csrf},
            )
        finally:
            integrations._moodle_client = original_factory
            tempfile.tempdir = original_tempdir
            client.close()
    state_after = _import_state(
        mapping_id=str(mapping["id"]),
        attempt_id=str(body["attempt_id"]),
        owner_id=str(body["confirmed_owner_user_id"]),
    )
    staged_after = set(QA_DIR.glob("align-moodle-*"))
    with SessionFactory() as db:
        idempotency = db.scalar(
            select(IdempotencyRecord.id).where(IdempotencyRecord.key == key)
        )
    if (
        response.status_code != 409
        or state_before != state_after
        or idempotency is not None
        or staged_before != staged_after
    ):
        raise SystemExit(
            "B9 changed Moodle attempt during download was not rejected cleanly "
            f"(http={response.status_code}, db_unchanged={state_before == state_after}, "
            f"staging_clean={staged_before == staged_after})"
        )
    return {
        "attempt_change_during_download": True,
        "http_status": response.status_code,
        "database_unchanged": True,
        "staged_file_removed": True,
    }


def _check_live_invalid_moodle_token(platform: dict[str, object]) -> dict[str, object]:
    client = MoodleWebService(
        origin=str(platform["moodle_origin"]),
        token=f"b9-invalid-token-{uuid4()}",
        timeout_seconds=10,
    )
    try:
        asyncio.run(client.call("core_webservice_get_site_info"))
    except MoodleAdapterError as exc:
        if exc.code != "moodle_service_rejected":
            raise SystemExit(f"unexpected Moodle invalid-token response: {exc.code}") from exc
        return {"actual_moodle_rejected_invalid_token": True}
    raise SystemExit("Moodle accepted a deliberately invalid synthetic Web Services token")


def _check_live_moodle_function(
    platform: dict[str, object], *, expect_disabled: bool
) -> dict[str, object]:
    settings = get_settings()
    client = MoodleWebService(
        origin=str(platform["moodle_origin"]),
        token=configured_moodle_token(settings),
        timeout_seconds=10,
    )
    try:
        functions = asyncio.run(client.verify_functions({"mod_assign_get_submissions"}))
    except MoodleAdapterError as exc:
        if expect_disabled and exc.code == "moodle_function_not_enabled":
            return {"live_function_disabled_and_rejected": True}
        raise SystemExit(f"unexpected live Moodle function check failure: {exc.code}") from exc
    if expect_disabled or "mod_assign_get_submissions" not in functions:
        raise SystemExit("Moodle Web Services function availability differed from expectation")
    return {"live_function_restored_and_available": True}


def _run_group_roster_revocation_check(
    *,
    mapping: dict[str, object],
    users: dict[str, str],
    moodle: dict[str, object],
) -> dict[str, object]:
    actor_email = "dosen@align.local"
    roster_ids = [users["mahasiswa@align.local"], users["mahasiswa2@align.local"]]
    body = _import_body(
        mapping=mapping,
        actor_email=actor_email,
        owner_id=None,
        roster_ids=roster_ids,
        moodle_group_id=int(moodle["groups"]["IF101 Kelompok A"]["id"]),
    )
    group_map = mapping["groups"][0]
    local_assignment_id = None
    version_id = None
    with SessionFactory() as db:
        event = db.scalar(
            select(LmsImportEvent)
            .where(
                LmsImportEvent.mapping_id == UUID(str(mapping["id"])),
                LmsImportEvent.moodle_attempt_id == body["attempt_id"],
                LmsImportEvent.status == "imported",
            )
            .order_by(LmsImportEvent.created_at.asc())
        )
        if event is None or event.submission_version_id is None:
            raise SystemExit("B9 group history requires its earlier imported receipt")
        historical_members = sorted(
            str(item.user_id)
            for item in db.scalars(
                select(SubmissionVersionMember).where(
                    SubmissionVersionMember.submission_version_id == event.submission_version_id
                )
            ).all()
        )
        source_version = db.get(SubmissionVersion, event.submission_version_id)
        source_submission = db.get(Submission, source_version.submission_id) if source_version else None
        if source_submission is None:
            raise SystemExit("B9 group assignment history is unavailable")
        local_assignment_id = source_submission.assignment_id
        group_membership = db.scalar(
            select(GroupMembership).where(
                GroupMembership.assignment_id == local_assignment_id,
                GroupMembership.group_id == UUID(str(group_map["local_group_id"])),
                GroupMembership.user_id == UUID(roster_ids[0]),
                GroupMembership.active.is_(True),
            )
        )
        if historical_members != sorted(roster_ids) or group_membership is None:
            raise SystemExit("B9 imported group roster snapshot does not match its fixture")
        version_id = event.submission_version_id

    settings = get_settings()
    client, csrf = _login_client(actor_email)
    key = str(uuid5(NAMESPACE_URL, "align-b9:group-roster-revoked"))
    try:
        with SessionFactory.begin() as db:
            group_membership = db.scalar(
                select(GroupMembership).where(
                    GroupMembership.assignment_id == local_assignment_id,
                    GroupMembership.group_id == UUID(str(group_map["local_group_id"])),
                    GroupMembership.user_id == UUID(roster_ids[0]),
                    GroupMembership.active.is_(True),
                )
            )
            if group_membership is None:
                raise SystemExit("B9 active local group membership disappeared before revocation")
            group_membership.active = False
        response = client.post(
            "/api/v1/integrations/moodle/imports",
            headers={
                "Origin": settings.app_origin,
                "X-CSRF-Token": csrf,
                "Idempotency-Key": key,
            },
            json=body,
        )
        if response.status_code != 409 or response.json().get("qa_detail") != (
            "confirmed roster does not match active ALIGN group"
        ):
            raise SystemExit(
                f"B9 changed local group roster was not rejected safely: {response.status_code}"
            )
    finally:
        client.close()
        with SessionFactory.begin() as db:
            group_membership = db.scalar(
                select(GroupMembership).where(
                    GroupMembership.assignment_id == local_assignment_id,
                    GroupMembership.group_id == UUID(str(group_map["local_group_id"])),
                    GroupMembership.user_id == UUID(roster_ids[0]),
                )
            )
            if group_membership is not None:
                group_membership.active = True

    with SessionFactory() as db:
        after_snapshot = sorted(
            str(item.user_id)
            for item in db.scalars(
                select(SubmissionVersionMember).where(
                    SubmissionVersionMember.submission_version_id == version_id
                )
            ).all()
        )
    if after_snapshot != historical_members:
        raise SystemExit("B9 group membership change altered the historical roster snapshot")
    return {"new_import_blocked_after_roster_change": True, "historical_roster_unchanged": True}


def _run_active_session_revocation_checks(
    *,
    mapping: dict[str, object],
) -> dict[str, object]:
    actor_email = "dosen@align.local"
    actor_id = UUID(str(_load_json(ALIGN_FIXTURE_PATH)["users"][actor_email]))
    mapping_id = UUID(str(mapping["id"]))
    with SessionFactory() as db:
        mapping_row = db.get(LmsCourseAssignmentMapping, mapping_id)
        if mapping_row is None:
            raise SystemExit("B9 active-session test mapping is unavailable")
        platform = db.get(LtiPlatform, mapping_row.platform_id)
        identity_binding = db.scalar(
            select(LtiIdentityBinding).where(
                LtiIdentityBinding.platform_id == mapping_row.platform_id,
                LtiIdentityBinding.user_id == actor_id,
                LtiIdentityBinding.active.is_(True),
            )
        )
        permission = db.scalar(
            select(LtiCoursePermission).where(
                LtiCoursePermission.platform_id == mapping_row.platform_id,
                LtiCoursePermission.course_id == mapping_row.local_course_id,
                LtiCoursePermission.active.is_(True),
            )
        )
        enrollment = db.scalar(
            select(Enrollment).where(
                Enrollment.course_id == mapping_row.local_course_id,
                Enrollment.user_id == actor_id,
                Enrollment.role == "lecturer",
                Enrollment.active.is_(True),
            )
        )
        if (
            not mapping_row.active
            or platform is None
            or not platform.active
            or identity_binding is None
            or permission is None
            or enrollment is None
        ):
            raise SystemExit("B9 active-session revocation fixtures are incomplete")
        mapping_revision = mapping_row.revision
        targets = {
            "identity_binding": (LtiIdentityBinding, identity_binding.id, "active", False, True),
            "platform": (LtiPlatform, platform.id, "active", False, True),
            "mapping": (LmsCourseAssignmentMapping, mapping_row.id, "active", False, True),
            "mapping_revision": (
                LmsCourseAssignmentMapping,
                mapping_row.id,
                "revision",
                mapping_row.revision + 1,
                mapping_row.revision,
            ),
            "course_permission": (LtiCoursePermission, permission.id, "active", False, True),
            "enrollment": (Enrollment, enrollment.id, "active", False, True),
        }

    client, csrf = _login_client(actor_email)
    settings = get_settings()
    try:
        raw_session = client.cookies.get(SESSION_COOKIE)
        if not raw_session:
            raise SystemExit("B9 active-session test could not read its synthetic session cookie")
        with SessionFactory.begin() as db:
            session = db.scalar(
                select(Session).where(Session.token_hash == token_digest(raw_session))
            )
            if session is None:
                raise SystemExit("B9 active-session fixture did not persist the login session")
            db.add(
                LtiSessionBinding(
                    session_id=session.id,
                    platform_id=platform.id,
                    identity_binding_id=identity_binding.id,
                    mapping_id=mapping_id,
                    mapping_revision=mapping_revision,
                )
            )
        if client.get("/api/v1/me").status_code != 200:
            raise SystemExit("B9 active-session fixture was not initially authorized")

        for name, (model, row_id, attribute, revoked_value, original_value) in targets.items():
            try:
                with SessionFactory.begin() as db:
                    record = db.get(model, row_id)
                    if record is None or getattr(record, attribute) != original_value:
                        raise SystemExit(f"B9 {name} revocation fixture changed unexpectedly")
                    setattr(record, attribute, revoked_value)
                if client.get("/api/v1/me").status_code != 401:
                    raise SystemExit(f"active LTI session survived {name} revocation")
            finally:
                with SessionFactory.begin() as db:
                    record = db.get(model, row_id)
                    if record is not None:
                        setattr(record, attribute, original_value)
            if client.get("/api/v1/me").status_code != 200:
                raise SystemExit(f"B9 active LTI session did not recover after restoring {name}")
    finally:
        client.post(
            "/api/v1/auth/logout",
            headers={"Origin": settings.app_origin, "X-CSRF-Token": csrf},
        )
        client.close()
    return {
        "revocations_block_active_session": sorted(targets),
        "session_recovers_after_restore": True,
    }


def _publication_api_call(
    client: TestClient,
    *,
    csrf: str,
    method: str,
    path: str,
    body: dict[str, object] | None = None,
    key: str | None = None,
    expected_status: int = 200,
) -> dict[str, object]:
    settings = get_settings()
    headers = {"Origin": settings.app_origin, "X-CSRF-Token": csrf}
    if key is not None:
        headers["Idempotency-Key"] = key
    response = client.request(method, path, headers=headers, json=body)
    if response.status_code != expected_status:
        try:
            detail = response.json().get("qa_detail", response.text)
        except ValueError:
            detail = response.text
        raise SystemExit(
            f"B9 publication fixture API call failed: {method} {path}: "
            f"{response.status_code} {detail}"
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise SystemExit(f"B9 publication fixture API response is invalid: {method} {path}")
    return payload


def _publication_key(name: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"align-b9:publication-fixture:{name}"))


def _prepare_publication_fixture() -> None:
    data = _load_json(ALIGN_FIXTURE_PATH)
    moodle = _load_json(MOODLE_FIXTURE_PATH)["moodle"]
    assignment_fixture = data["assignments"]["IF101-individual"]
    teacher_email = "dosen@align.local"
    client, csrf = _login_client(teacher_email)
    assignment_id = str(assignment_fixture["id"])
    base_version_id = str(assignment_fixture["version_id"])
    try:
        version = _publication_api_call(
            client,
            csrf=csrf,
            method="GET",
            path=f"/api/v1/assignment-versions/{base_version_id}",
        )
        has_approved_package = (
            version.get("state") == "published"
            and isinstance(version.get("criterion_set"), dict)
            and version["criterion_set"].get("state") == "approved"
            and isinstance(version.get("specification"), dict)
            and version["specification"].get("state") == "approved"
        )
        if not has_approved_package:
            created = _publication_api_call(
                client,
                csrf=csrf,
                method="POST",
                path=f"/api/v1/assignments/{assignment_id}/new-version",
                body={"base_version_id": base_version_id},
                key=_publication_key(f"new-version:{assignment_id}"),
                expected_status=201,
            )
            version_id = str(created["id"])
            version = _publication_api_call(
                client,
                csrf=csrf,
                method="GET",
                path=f"/api/v1/assignment-versions/{version_id}",
            )
            if version.get("state") != "published":
                _publication_api_call(
                    client,
                    csrf=csrf,
                    method="POST",
                    path=f"/api/v1/assignment-versions/{version_id}/criteria-drafts",
                    body={"rubric_file_ids": [], "generate_from_instruction": False},
                    key=_publication_key(f"criteria-draft:{version_id}"),
                    expected_status=201,
                )
                version = _publication_api_call(
                    client,
                    csrf=csrf,
                    method="GET",
                    path=f"/api/v1/assignment-versions/{version_id}",
                )
                criterion_set = version.get("criterion_set")
                if not isinstance(criterion_set, dict):
                    raise SystemExit("B9 publication fixture did not create a criterion set")
                if criterion_set.get("state") == "draft" and not criterion_set.get("criteria"):
                    instruction = str(version["instruction_original"])
                    if not instruction.strip() or len(instruction) > 2000:
                        raise SystemExit("B9 synthetic assignment instruction cannot seed a requirement")
                    _publication_api_call(
                        client,
                        csrf=csrf,
                        method="PUT",
                        path=f"/api/v1/criterion-set-versions/{criterion_set['id']}",
                        body={
                            "expected_revision": int(criterion_set["revision"]),
                            "criteria": [
                                {
                                    "code": "B9-FINAL",
                                    "title": "Tugas final sintetis",
                                    "descriptor": "Submission final ditinjau terhadap instruksi tugas sintetis.",
                                    "display_order": 0,
                                    "outcome_ids": [],
                                    "requirements": [
                                        {
                                            "code": "B9-FINAL-REQ",
                                            "text": instruction,
                                            "mandatory": True,
                                            "obligation_key": "b9_final_submission",
                                            "normative_source_refs": [
                                                {
                                                    "source_kind": "instruction",
                                                    "source_id": version_id,
                                                    "span": {"start": 0, "end": len(instruction)},
                                                }
                                            ],
                                        }
                                    ],
                                }
                            ],
                        },
                        key=_publication_key(f"criterion-set:{version_id}"),
                    )
                version = _publication_api_call(
                    client,
                    csrf=csrf,
                    method="GET",
                    path=f"/api/v1/assignment-versions/{version_id}",
                )
                criterion_set = version.get("criterion_set")
                if not isinstance(criterion_set, dict) or criterion_set.get("state") != "approved":
                    _publication_api_call(
                        client,
                        csrf=csrf,
                        method="POST",
                        path=f"/api/v1/assignment-versions/{version_id}/approve-package",
                        body={
                            "criterion_set_version_id": str(criterion_set["id"]),
                            "expected_revision": int(version["revision"]),
                        },
                        key=_publication_key(f"approve-package:{version_id}"),
                        expected_status=200,
                    )

            version = _publication_api_call(
                client,
                csrf=csrf,
                method="GET",
                path=f"/api/v1/assignment-versions/{version_id}",
            )
            criterion_set = version.get("criterion_set")
            if not isinstance(criterion_set, dict) or criterion_set.get("state") != "approved":
                raise SystemExit("B9 publication fixture package was not approved")
            specification = version.get("specification")
            if not isinstance(specification, dict):
                _publication_api_call(
                    client,
                    csrf=csrf,
                    method="POST",
                    path=f"/api/v1/assignment-versions/{version_id}/specification-drafts",
                    body={"criterion_set_version_id": str(criterion_set["id"])},
                    key=_publication_key(f"spec-draft:{version_id}"),
                    expected_status=201,
                )
                version = _publication_api_call(
                    client,
                    csrf=csrf,
                    method="GET",
                    path=f"/api/v1/assignment-versions/{version_id}",
                )
                specification = version.get("specification")
            if not isinstance(specification, dict):
                raise SystemExit("B9 publication fixture specification draft is unavailable")
            if specification.get("state") == "draft" and not specification.get("payload", {}).get("elements"):
                criteria = criterion_set.get("criteria")
                if not isinstance(criteria, list) or not criteria:
                    raise SystemExit("B9 publication fixture criterion is missing")
                criterion = criteria[0]
                requirements = criterion.get("requirements")
                if not isinstance(requirements, list) or not requirements:
                    raise SystemExit("B9 publication fixture requirement is missing")
                requirement_id = str(requirements[0]["id"])
                specification_payload = {
                    "schema_version": 2,
                    "elements": [
                        {
                            "element_id": str(uuid5(NAMESPACE_URL, f"align-b9:spec-element:{version_id}")),
                            "element_code": "B9-FINAL-REVIEW",
                            "criterion_id": str(criterion["id"]),
                            "requirement_ids": [requirement_id],
                            "obligation_key": "b9_final_submission",
                            "target_kind": "requirement",
                            "decision_question": "Apakah submission memenuhi instruksi tugas final sintetis?",
                            "derivation_refs": [
                                {"source_kind": "requirement", "source_id": requirement_id, "span": None}
                            ],
                            "expected_evidence": "Berkas submission final dan bagian yang relevan untuk ditelaah dosen.",
                            "grounding_contract": {
                                "schema_version": 1,
                                "mode": "human_only",
                                "absence_support": None,
                            },
                            "accepted_evidence_types": ["paragraph"],
                            "relationships": [],
                            "applicability": {"mode": "always", "question": None, "rule": None},
                            "source_hints": [],
                            "operators": ["locate_evidence", "request_human_review"],
                            "dependencies": [],
                            "abstention_conditions": ["unsupported_source_format"],
                            "numeric_policy_version_id": None,
                            "human_judgment_boundary": "Dosen menilai isi submission terhadap instruksi; parser tidak menetapkan pemenuhan secara otomatis.",
                        }
                    ],
                    "operator_registry_version": "operators-v1",
                    "predicate_registry_version": "predicates-v1",
                    "grounding_registry_version": "grounding-v1",
                    "lint_registry_version": "lint-v1",
                    "task_facts": [],
                    "limits": {
                        "draft_max_cost_usd": "0.05",
                        "max_text_calls": 3,
                        "max_vision_calls": 2,
                        "max_input_tokens": 20000,
                        "max_output_tokens": 4000,
                    },
                }
                specification = _publication_api_call(
                    client,
                    csrf=csrf,
                    method="PUT",
                    path=f"/api/v1/specification-versions/{specification['id']}",
                    body={
                        "expected_revision": int(specification["revision"]),
                        "specification": specification_payload,
                    },
                    key=_publication_key(f"specification:{version_id}"),
                )
                if specification.get("effective_status") != "passed":
                    raise SystemExit("B9 human-only specification lint requires B2 review resolution")

            version = _publication_api_call(
                client,
                csrf=csrf,
                method="GET",
                path=f"/api/v1/assignment-versions/{version_id}",
            )
            specification = version.get("specification")
            if not isinstance(specification, dict):
                raise SystemExit("B9 publication fixture specification disappeared")
            if specification.get("state") != "approved":
                lint = specification.get("lint")
                if not isinstance(lint, dict) or lint.get("effective_status") != "passed":
                    raise SystemExit("B9 publication fixture lint is not passed")
                _publication_api_call(
                    client,
                    csrf=csrf,
                    method="POST",
                    path=f"/api/v1/specification-versions/{specification['id']}/approve",
                    body={
                        "expected_revision": int(specification["revision"]),
                        "lint_report_id": str(lint["id"]),
                        "expected_report_revision": int(lint["resolution_revision"]),
                        "fidelity_confirmation": True,
                    },
                    key=_publication_key(f"approve-specification:{version_id}"),
                )
            version = _publication_api_call(
                client,
                csrf=csrf,
                method="GET",
                path=f"/api/v1/assignment-versions/{version_id}",
            )
            if version.get("state") != "published":
                _publication_api_call(
                    client,
                    csrf=csrf,
                    method="POST",
                    path=f"/api/v1/assignment-versions/{version_id}/publish",
                    body={"expected_revision": int(version["revision"])},
                    key=_publication_key(f"publish-assignment:{version_id}"),
                )

        version_id = str(version["id"])
        mapping_fixture = data["mappings"]["IF101-individual"]
        mappings = _publication_api_call(
            client,
            csrf=csrf,
            method="GET",
            path=f"/api/v1/integrations/moodle/mappings?course_id={data['courses']['IF101']['id']}",
        )["items"]
        mapping = next((row for row in mappings if row["id"] == mapping_fixture["id"]), None)
        if not isinstance(mapping, dict):
            raise SystemExit("B9 publication fixture Moodle mapping was not found")
        if mapping["assignment_version_id"] != version_id:
            mapping = _publication_api_call(
                client,
                csrf=csrf,
                method="PATCH",
                path=f"/api/v1/integrations/moodle/mappings/{mapping['id']}",
                body={
                    "expected_revision": int(mapping["revision"]),
                    "assignment_version_id": version_id,
                },
            )
        mapping_fixture.update(mapping)
        assignment_fixture["version_id"] = version_id
        assignment_fixture["publication_fixture"] = True
        data["mappings"]["IF101-individual"] = mapping_fixture
        ALIGN_FIXTURE_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        receipt_body = _import_body(
            mapping=mapping_fixture,
            actor_email=teacher_email,
            owner_id=str(data["users"]["mahasiswa@align.local"]),
            roster_ids=[],
            moodle_user_id=int(moodle["users"]["b9student1"]["id"]),
        )
        receipt = _post_import(
            actor_email=teacher_email,
            mapping=mapping_fixture,
            body=receipt_body,
            key=_publication_key("import-source-v1"),
        )
        if receipt.get("status") != "imported" or not _check_receipt_and_audit(receipt):
            raise SystemExit("B9 publication fixture receipt or audit linkage is inconsistent")
        (QA_DIR / "publication-fixture.json").write_text(
            json.dumps(
                {
                    "status": "ready_for_parser",
                    "assignment_version_id": version_id,
                    "mapping_id": str(mapping["id"]),
                    "mapping_revision": int(mapping["revision"]),
                    "receipt_id": str(receipt["id"]),
                    "submission_id": str(receipt["submission_id"]),
                    "submission_version_id": str(receipt["submission_version_id"]),
                    "job_id": str(receipt["job_id"]),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "status": "ready_for_parser",
                    "assignment_version_id": version_id,
                    "mapping_revision": int(mapping["revision"]),
                    "receipt_id": str(receipt["id"]),
                    "submission_version_id": str(receipt["submission_version_id"]),
                    "job_id": str(receipt["job_id"]),
                    "provider_mode": "off",
                },
                sort_keys=True,
            )
        )
    finally:
        client.close()


def _parsed_submission_version(job_id: str, version_id: str) -> tuple[int, str]:
    with SessionFactory() as db:
        job = db.get(Job, UUID(job_id))
        version = db.get(SubmissionVersion, UUID(version_id))
        artifacts = db.scalars(
            select(ParseArtifact).where(ParseArtifact.submission_version_id == UUID(version_id))
        ).all()
        if (
            job is None
            or job.type != "submission_parse"
            or job.status != "ready_for_review"
            or version is None
            or not artifacts
            or any(item.status != "parsed" for item in artifacts)
        ):
            raise SystemExit("B9 AC-SBX-05 requires a completed parser job for the exact source version")
        return version.version_no, str(version.submission_id)


def _publish_ac05_for_version(
    *,
    submission_id: str,
    submission_version_id: str,
    version_no: int,
    label: str,
) -> dict[str, object]:
    teacher_email = "dosen@align.local"
    client, csrf = _login_client(teacher_email)
    try:
        review_list = _publication_api_call(
            client,
            csrf=csrf,
            method="GET",
            path=f"/api/v1/submission-versions/{submission_version_id}/reviews",
        )
        rows = review_list.get("items", [])
        review_id = str(rows[0]["id"]) if rows else ""
        if not review_id:
            created = _publication_api_call(
                client,
                csrf=csrf,
                method="POST",
                path=f"/api/v1/submission-versions/{submission_version_id}/reviews",
                body={"analysis_run_id": None},
                key=_publication_key(f"review:{submission_version_id}"),
                expected_status=201,
            )
            review_id = str(created["id"])

        review = _publication_api_call(
            client,
            csrf=csrf,
            method="GET",
            path=f"/api/v1/reviews/{review_id}",
        )
        for element in review.get("elements", []):
            if element.get("decision", {}).get("review_status") == "reviewed":
                continue
            decision = _publication_api_call(
                client,
                csrf=csrf,
                method="PUT",
                path=f"/api/v1/reviews/{review_id}/element-decisions/{element['id']}",
                body={
                    "review_status": "reviewed",
                    "resolution": "unable_to_conclude",
                    "applicability_override": None,
                    "reason": "Berkas berisi label fixture sintetis saja; kepatuhan akademik tidak dapat disimpulkan.",
                    "evidence_ids": [],
                    "expected_revision": int(review["revision"]),
                },
                key=_publication_key(
                    f"decision:{review_id}:{element['id']}:{review['revision']}"
                ),
            )
            review = _publication_api_call(
                client,
                csrf=csrf,
                method="GET",
                path=f"/api/v1/reviews/{review_id}",
            )
            if int(review["revision"]) != int(decision["revision"]):
                raise SystemExit("B9 AC-SBX-05 review revision did not advance as expected")

        if review.get("state") != "complete":
            limitations = [
                "Submission PDF ini adalah label uji sintetis dan tidak cukup untuk menilai mutu akademik."
            ]
            if review.get("limitations") != limitations:
                review = _publication_api_call(
                    client,
                    csrf=csrf,
                    method="PUT",
                    path=f"/api/v1/reviews/{review_id}/notes",
                    body={
                        "lecturer_notes": "Pemeriksaan manual fixture B9; tidak ada kesimpulan akademik dari dokumen sintetis.",
                        "limitations": limitations,
                        "expected_revision": int(review["revision"]),
                    },
                    key=_publication_key(f"notes:{review_id}"),
                )
                review = _publication_api_call(
                    client,
                    csrf=csrf,
                    method="GET",
                    path=f"/api/v1/reviews/{review_id}",
                )
            completed = _publication_api_call(
                client,
                csrf=csrf,
                method="POST",
                path=f"/api/v1/reviews/{review_id}/complete",
                body={"expected_revision": int(review["revision"])},
                key=_publication_key(f"complete:{review_id}"),
            )
            review = _publication_api_call(
                client,
                csrf=csrf,
                method="GET",
                path=f"/api/v1/reviews/{review_id}",
            )
            if completed.get("state") != "complete" or review.get("state") != "complete":
                raise SystemExit("B9 AC-SBX-05 review could not be completed")

        drafts = _publication_api_call(
            client,
            csrf=csrf,
            method="GET",
            path=f"/api/v1/reviews/{review_id}/feedback-drafts",
        ).get("items", [])
        draft = drafts[0] if drafts else None
        if not isinstance(draft, dict):
            draft = _publication_api_call(
                client,
                csrf=csrf,
                method="POST",
                path=f"/api/v1/reviews/{review_id}/feedback-drafts",
                body={
                    "mode": "template",
                    "body_markdown": None,
                    "blocks": [],
                    "included_review_finding_ids": [],
                    "exclusion_reasons": {},
                    "expected_revision": int(review["revision"]),
                },
                key=_publication_key(f"feedback:{review_id}"),
                expected_status=201,
            )
        if draft.get("state") != "approved":
            draft = _publication_api_call(
                client,
                csrf=csrf,
                method="POST",
                path=f"/api/v1/feedback-drafts/{draft['id']}/approve",
                body={
                    "expected_revision": int(draft["revision"]),
                    "expected_review_revision": int(review["revision"]),
                },
                key=_publication_key(f"approve-feedback:{draft['id']}"),
            )

        publication_rows = _publication_api_call(
            client,
            csrf=csrf,
            method="GET",
            path=f"/api/v1/submissions/{submission_id}/publications",
        ).get("items", [])
        publication = next(
            (
                item
                for item in publication_rows
                if item.get("review_version_id") == review_id
            ),
            None,
        )
        if not isinstance(publication, dict):
            publication = _publication_api_call(
                client,
                csrf=csrf,
                method="POST",
                path=f"/api/v1/reviews/{review_id}/publish",
                body={
                    "feedback_draft_id": str(draft["id"]),
                    "expected_revision": int(review["revision"]),
                    "expected_feedback_revision": int(draft["revision"]),
                },
                key=_publication_key(f"publish:{review_id}"),
                expected_status=201,
            )
        publication_id = str(publication.get("id", publication.get("publication_id")))
        student, _student_csrf = _login_client("mahasiswa@align.local")
        other_student, _other_csrf = _login_client("mahasiswa2@align.local")
        try:
            result = student.get(f"/api/v1/submissions/{submission_id}/published-result")
            if result.status_code != 200 or result.json().get("status") != "released":
                raise SystemExit("B9 AC-SBX-05 student did not receive the released publication")
            if int(result.json().get("submission_version", -1)) != version_no:
                raise SystemExit("B9 AC-SBX-05 publication snapshot points to the wrong source version")
            if result.json().get("publication_id") != publication_id:
                raise SystemExit("B9 AC-SBX-05 latest publication ID does not match the lecturer receipt")
            denied = other_student.get(f"/api/v1/submissions/{submission_id}/published-result")
            if denied.status_code != 404:
                raise SystemExit("B9 AC-SBX-05 another student could read this publication")
        finally:
            student.close()
            other_student.close()
        return {
            "review_id": review_id,
            "feedback_draft_id": str(draft["id"]),
            "publication_id": publication_id,
            "publication_sequence": int(publication.get("sequence_no", 0)),
            "submission_version_id": submission_version_id,
            "submission_version": version_no,
            "status": "released",
            "other_student_denied": True,
            "provider_mode": "off",
        }
    finally:
        client.close()


def _run_ac05_publish() -> None:
    fixture = _load_json(QA_DIR / "publication-fixture.json")
    version_no, submission_id = _parsed_submission_version(
        str(fixture["job_id"]), str(fixture["submission_version_id"])
    )
    result = _publish_ac05_for_version(
        submission_id=submission_id,
        submission_version_id=str(fixture["submission_version_id"]),
        version_no=version_no,
        label="source-v1",
    )
    fixture.update({"status": "released", "submission_id": submission_id, "first_publication": result})
    (QA_DIR / "publication-fixture.json").write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": "released", **result}, sort_keys=True))


def _run_ac05_import_newer_source() -> None:
    fixture = _load_json(QA_DIR / "publication-fixture.json")
    if fixture.get("status") != "released":
        raise SystemExit("B9 AC-SBX-05 needs its first feedback publication before source replacement")
    data = _load_json(ALIGN_FIXTURE_PATH)
    moodle = _load_json(MOODLE_FIXTURE_PATH)["moodle"]
    mapping = data["mappings"]["IF101-individual"]
    body = _import_body(
        mapping=mapping,
        actor_email="dosen@align.local",
        owner_id=str(data["users"]["mahasiswa@align.local"]),
        roster_ids=[],
        moodle_user_id=int(moodle["users"]["b9student1"]["id"]),
    )
    previous_id = str(fixture["submission_version_id"])
    with SessionFactory() as db:
        previous = db.get(SubmissionVersion, UUID(previous_id))
        if previous is None:
            raise SystemExit("B9 AC-SBX-05 previous imported source is unavailable")
        previous_no = previous.version_no
        previous_fingerprint = db.scalar(
            select(LmsImportEvent.source_fingerprint).where(
                LmsImportEvent.submission_version_id == previous.id
            )
        )
    receipt = _post_import(
        actor_email="dosen@align.local",
        mapping=mapping,
        body=body,
        key=_publication_key("import-source-v2"),
    )
    with SessionFactory() as db:
        event = db.get(LmsImportEvent, UUID(str(receipt["id"])))
        version = db.get(SubmissionVersion, UUID(str(receipt["submission_version_id"])))
        if (
            event is None
            or version is None
            or event.source_fingerprint == previous_fingerprint
            or version.version_no != previous_no + 1
        ):
            raise SystemExit("B9 AC-SBX-05 newer Moodle source did not create the next immutable version")
        job_id = str(event.job_id)
    fixture.update(
        {
            "status": "new_source_imported",
            "newer_receipt_id": str(receipt["id"]),
            "newer_submission_version_id": str(receipt["submission_version_id"]),
            "newer_submission_version": previous_no + 1,
            "newer_job_id": job_id,
            "newer_source_fingerprint_changed": True,
        }
    )
    (QA_DIR / "publication-fixture.json").write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": "new_source_imported",
                "previous_submission_version": previous_no,
                "new_submission_version": previous_no + 1,
                "source_fingerprint_changed": True,
                "job_id": job_id,
            },
            sort_keys=True,
        )
    )


def _run_ac05_newer_release_and_withdraw() -> None:
    fixture = _load_json(QA_DIR / "publication-fixture.json")
    if fixture.get("status") != "new_source_imported":
        raise SystemExit("B9 AC-SBX-05 requires a newer imported source version")
    first_publication = fixture.get("first_publication")
    if not isinstance(first_publication, dict):
        raise SystemExit("B9 AC-SBX-05 first publication receipt is unavailable")
    version_no, submission_id = _parsed_submission_version(
        str(fixture["newer_job_id"]), str(fixture["newer_submission_version_id"])
    )
    first_id = str(first_publication["publication_id"])
    student, _student_csrf = _login_client("mahasiswa@align.local")
    try:
        still_visible = student.get(f"/api/v1/submissions/{submission_id}/published-result")
        if (
            still_visible.status_code != 200
            or still_visible.json().get("status") != "released"
            or still_visible.json().get("publication_id") != first_id
            or int(still_visible.json().get("submission_version", -1))
            != int(first_publication["submission_version"])
        ):
            raise SystemExit("B9 AC-SBX-05 newer import masked the released older feedback snapshot")
    finally:
        student.close()

    second_publication = _publish_ac05_for_version(
        submission_id=submission_id,
        submission_version_id=str(fixture["newer_submission_version_id"]),
        version_no=version_no,
        label="source-v2",
    )
    if int(second_publication["publication_sequence"]) <= int(first_publication["publication_sequence"]):
        raise SystemExit("B9 AC-SBX-05 publication sequence did not advance for the newer source")

    teacher, teacher_csrf = _login_client("dosen@align.local")
    try:
        _publication_api_call(
            teacher,
            csrf=teacher_csrf,
            method="POST",
            path=f"/api/v1/publications/{second_publication['publication_id']}/withdraw",
            body={"reason": "Acceptance B9 sintetis: uji penarikan publikasi."},
            key=_publication_key(f"withdraw:{second_publication['publication_id']}"),
        )
    finally:
        teacher.close()

    student, _student_csrf = _login_client("mahasiswa@align.local")
    other_student, _other_csrf = _login_client("mahasiswa2@align.local")
    try:
        default_result = student.get(f"/api/v1/submissions/{submission_id}/published-result")
        old_result = student.get(
            f"/api/v1/submissions/{submission_id}/published-result",
            params={"publication_id": first_id},
        )
        if (
            default_result.status_code != 200
            or default_result.json().get("status") != "withdrawn"
            or "feedback_markdown" in default_result.json()
        ):
            raise SystemExit("B9 AC-SBX-05 withdrawn result remained visible to its student")
        if (
            old_result.status_code != 200
            or old_result.json().get("status") != "released"
            or old_result.json().get("publication_id") != first_id
            or int(old_result.json().get("submission_version", -1))
            != int(first_publication["submission_version"])
        ):
            raise SystemExit("B9 AC-SBX-05 old immutable publication snapshot was not preserved")
        if other_student.get(
            f"/api/v1/submissions/{submission_id}/published-result",
            params={"publication_id": first_id},
        ).status_code != 404:
            raise SystemExit("B9 AC-SBX-05 another student could read the old publication")
    finally:
        student.close()
        other_student.close()

    moodle = _load_json(MOODLE_FIXTURE_PATH)["platform"]
    enabled_functions = moodle.get("enabled_read_functions", [])
    if any("grade" in str(name).casefold() for name in enabled_functions):
        raise SystemExit("B9 AC-SBX-05 Moodle service token unexpectedly exposes a grade function")
    fixture.update(
        {
            "status": "withdrawn",
            "newer_publication": second_publication,
            "older_publication_remained_visible_after_new_import": True,
            "withdrawn_access_closed": True,
            "other_student_denied": True,
            "moodle_grade_functions_enabled": False,
        }
    )
    (QA_DIR / "publication-fixture.json").write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": "withdrawn",
                "older_publication_remained_visible_after_new_import": True,
                "older_version": int(first_publication["submission_version"]),
                "newer_version": version_no,
                "newer_publication_withdrawn": True,
                "withdrawn_feedback_hidden": True,
                "other_student_denied": True,
                "moodle_grade_functions_enabled": False,
            },
            sort_keys=True,
        )
    )


def _ensure_mapping(
    *,
    client: TestClient,
    csrf: str,
    mapping_spec: dict[str, object],
    platform_id: str,
    actor_id: str,
    local_course_id: str,
    version_id: str,
    local_group_id: str | None = None,
) -> dict[str, object]:
    settings = get_settings()
    course_id = local_course_id
    existing_response = client.get(
        "/api/v1/integrations/moodle/mappings",
        params={"course_id": course_id},
    )
    if existing_response.status_code != 200:
        raise SystemExit(f"B9 mapping lookup failed: {existing_response.status_code}")
    rows = existing_response.json()["items"]
    existing = next(
        (
            row
            for row in rows
            if row["platform_id"] == platform_id
            and row["lti_context_id"] == mapping_spec["lti_context_id"]
            and row["lti_resource_link_id"] == mapping_spec["lti_resource_link_id"]
        ),
        None,
    )
    if existing is None:
        body = MappingCreate(
            platform_id=platform_id,
            lti_context_id=str(mapping_spec["lti_context_id"]),
            lti_resource_link_id=str(mapping_spec["lti_resource_link_id"]),
            moodle_course_id=int(mapping_spec["moodle_course_id"]),
            moodle_assignment_id=int(mapping_spec["moodle_assignment_id"]),
            local_course_id=local_course_id,
            assignment_version_id=version_id,
        )
        response = client.post(
            "/api/v1/integrations/moodle/mappings",
            headers={
                "Origin": settings.app_origin,
                "X-CSRF-Token": csrf,
                "Idempotency-Key": str(
                    uuid5(
                        NAMESPACE_URL,
                        f"align-b9:mapping:{platform_id}:{mapping_spec['lti_resource_link_id']}",
                    )
                ),
            },
            json=body.model_dump(mode="json"),
        )
        if response.status_code != 201:
            raise SystemExit(f"B9 mapping create failed: {response.status_code} {response.text}")
        existing = response.json()
    elif existing["assignment_version_id"] != version_id or existing["active"] is not True:
        raise SystemExit("existing Moodle mapping points to a different B9 assignment version")

    if local_group_id is not None:
        group_exists = any(
            item["moodle_group_id"] == mapping_spec["moodle_group_id"]
            and item["local_group_id"] == local_group_id
            and item["active"] is True
            for item in existing["groups"]
        )
        if not group_exists:
            response = client.post(
                f"/api/v1/integrations/moodle/mappings/{existing['id']}/groups",
                headers={
                    "Origin": settings.app_origin,
                    "X-CSRF-Token": csrf,
                    "Idempotency-Key": str(
                        uuid5(
                            NAMESPACE_URL,
                            f"align-b9:group:{existing['id']}:{mapping_spec['moodle_group_id']}",
                        )
                    ),
                },
                json=GroupMappingCreate(
                    moodle_group_id=int(mapping_spec["moodle_group_id"]),
                    local_group_id=local_group_id,
                ).model_dump(mode="json"),
            )
            if response.status_code != 201:
                raise SystemExit(
                    f"B9 group mapping create failed: {response.status_code} {response.text}"
                )
            refreshed = client.get(
                "/api/v1/integrations/moodle/mappings",
                params={"course_id": course_id},
            )
            existing = next(row for row in refreshed.json()["items"] if row["id"] == existing["id"])
    return existing


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-publication-fixture", action="store_true")
    parser.add_argument("--publish-ac05-feedback", action="store_true")
    parser.add_argument("--import-ac05-newer-source", action="store_true")
    parser.add_argument("--verify-ac05-newer-source-and-withdraw", action="store_true")
    parser.add_argument("--run-imports", action="store_true")
    parser.add_argument("--run-source-change", action="store_true")
    parser.add_argument("--check-group-roster-revocation", action="store_true")
    parser.add_argument("--check-active-session-revocations", action="store_true")
    parser.add_argument("--expect-storage-outage", action="store_true")
    parser.add_argument("--retry-storage-outage", action="store_true")
    parser.add_argument("--import-with-broker-unavailable", action="store_true")
    parser.add_argument("--verify-broker-outbox-recovery", action="store_true")
    parser.add_argument("--check-source-change-during-download", action="store_true")
    parser.add_argument("--check-live-invalid-moodle-token", action="store_true")
    parser.add_argument("--expect-live-moodle-function-disabled", action="store_true")
    parser.add_argument("--check-live-moodle-function-restored", action="store_true")
    args = parser.parse_args()
    if args.prepare_publication_fixture:
        _prepare_publication_fixture()
        return
    if args.publish_ac05_feedback:
        _run_ac05_publish()
        return
    if args.import_ac05_newer_source:
        _run_ac05_import_newer_source()
        return
    if args.verify_ac05_newer_source_and_withdraw:
        _run_ac05_newer_release_and_withdraw()
        return
    fixture_doc = _load_json(MOODLE_FIXTURE_PATH)
    platform = fixture_doc["platform"]
    moodle = fixture_doc["moodle"]
    platform_id = str(platform["platform_id"])

    with SessionFactory.begin() as db:
        users = {
            email: db.scalar(select(User).where(User.normalized_email == email))
            for email in (
                "dosen@align.local",
                "dosen2@align.local",
                "mahasiswa@align.local",
                "mahasiswa2@align.local",
                "mahasiswa3@align.local",
            )
        }
        if any(user is None or not user.active for user in users.values()):
            raise SystemExit("synthetic ALIGN users are unavailable")
        courses = {
            code: db.scalar(select(Course).where(Course.code == code))
            for code in ("IF101", "IF202")
        }
        if any(course is None or not course.active for course in courses.values()):
            raise SystemExit("synthetic ALIGN courses are unavailable")
        lecturer1 = users["dosen@align.local"]
        lecturer2 = users["dosen2@align.local"]
        student1 = users["mahasiswa@align.local"]
        student2 = users["mahasiswa2@align.local"]
        student3 = users["mahasiswa3@align.local"]
        assert lecturer1 and lecturer2 and student1 and student2 and student3
        course1 = courses["IF101"]
        course2 = courses["IF202"]
        assert course1 and course2
        context1 = _context_for(db, course1, lecturer1)
        context2 = _context_for(db, course2, lecturer2)
        assignment1, version1, _ = _ensure_assignment(
            db,
            course=course1,
            lecturer=lecturer1,
            context=context1,
            title="B9 Moodle: Tugas Individu IF101",
            mode="individual",
            students=[student1],
        )
        group_assignment, group_version, local_group = _ensure_assignment(
            db,
            course=course1,
            lecturer=lecturer1,
            context=context1,
            title="B9 Moodle: Tugas Kelompok IF101",
            mode="group",
            students=[student1, student2],
        )
        assignment2, version2, _ = _ensure_assignment(
            db,
            course=course2,
            lecturer=lecturer2,
            context=context2,
            title="B9 Moodle: Tugas Individu IF202",
            mode="individual",
            students=[student3],
        )
        assert local_group is not None
        data = {
            "courses": {
                "IF101": {"id": str(course1.id), "context_version_id": str(context1.id)},
                "IF202": {"id": str(course2.id), "context_version_id": str(context2.id)},
            },
            "assignments": {
                "IF101-individual": {
                    "id": str(assignment1.id),
                    "version_id": str(version1.id),
                    "mode": version1.mode,
                },
                "IF101-group": {
                    "id": str(group_assignment.id),
                    "version_id": str(group_version.id),
                    "mode": group_version.mode,
                    "local_group_id": str(local_group.id),
                },
                "IF202-individual": {
                    "id": str(assignment2.id),
                    "version_id": str(version2.id),
                    "mode": version2.mode,
                },
            },
            "users": {email: str(user.id) for email, user in users.items() if user},
        }

    definitions = [
        {
            "key": "IF101-individual",
            "course_code": "IF101",
            "activity": "individual",
            "assignment": "individual",
            "actor": "dosen@align.local",
            "version": data["assignments"]["IF101-individual"]["version_id"],
        },
        {
            "key": "IF101-group",
            "course_code": "IF101",
            "activity": "group",
            "assignment": "group",
            "actor": "dosen@align.local",
            "version": data["assignments"]["IF101-group"]["version_id"],
            "group_name": "IF101 Kelompok A",
            "local_group_id": data["assignments"]["IF101-group"]["local_group_id"],
        },
        {
            "key": "IF202-individual",
            "course_code": "IF202",
            "activity": "individual",
            "assignment": "individual",
            "actor": "dosen2@align.local",
            "version": data["assignments"]["IF202-individual"]["version_id"],
        },
    ]
    settings = get_settings()
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    mapping_results = {}
    for definition in definitions:
        course_code = definition["course_code"]
        moodle_course = moodle["courses"]["B9-" + course_code]
        activity = moodle_course["activities"][definition["activity"]]
        assignment = moodle_course["assignments"][definition["assignment"]]
        mapping_spec = {
            "lti_context_id": moodle_course["context_id"],
            "lti_resource_link_id": activity["resource_link_id"],
            "moodle_course_id": moodle_course["id"],
            "moodle_assignment_id": assignment["id"],
        }
        if "group_name" in definition:
            mapping_spec["moodle_group_id"] = moodle["groups"][definition["group_name"]]["id"]
            mapping_spec["local_group_id"] = definition["local_group_id"]
        client = TestClient(app, base_url=settings.app_origin)
        password = settings.align_seed_password.get_secret_value()
        login = client.post(
            "/api/v1/auth/login",
            headers={"Origin": settings.app_origin},
            json={"email": definition["actor"], "password": password},
        )
        if login.status_code != 200:
            raise SystemExit(f"synthetic lecturer login failed for {definition['actor']}")
        result = _ensure_mapping(
            client=client,
            csrf=login.json()["csrf_token"],
            mapping_spec=mapping_spec,
            platform_id=platform_id,
            actor_id=str(users[definition["actor"]]),
            local_course_id=data["courses"][course_code]["id"],
            version_id=str(definition["version"]),
            local_group_id=str(definition["local_group_id"])
            if "local_group_id" in definition
            else None,
        )
        mapping_results[definition["key"]] = result
        client.close()

    data["mappings"] = mapping_results
    ALIGN_FIXTURE_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    imports = (
        _run_import_checks(
            mappings=mapping_results,
            users=data["users"],
            moodle=moodle,
        )
        if args.run_imports
        else None
    )
    if imports is not None:
        (QA_DIR / "import-results.json").write_text(
            json.dumps(imports, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    source_change = (
        _run_source_change_check(
            mapping=mapping_results["IF101-individual"],
            users=data["users"],
            moodle=moodle,
        )
        if args.run_source_change
        else None
    )
    group_roster_revocation = (
        _run_group_roster_revocation_check(
            mapping=mapping_results["IF101-group"],
            users=data["users"],
            moodle=moodle,
        )
        if args.check_group_roster_revocation
        else None
    )
    active_session_revocations = (
        _run_active_session_revocation_checks(mapping=mapping_results["IF101-individual"])
        if args.check_active_session_revocations
        else None
    )
    storage_outage = (
        _expect_storage_outage(
            mapping=mapping_results["IF101-individual"],
            users=data["users"],
            moodle=moodle,
        )
        if args.expect_storage_outage
        else None
    )
    storage_retry = _retry_storage_outage() if args.retry_storage_outage else None
    broker_outage = (
        _import_while_broker_unavailable(
            mapping=mapping_results["IF101-individual"],
            users=data["users"],
            moodle=moodle,
        )
        if args.import_with_broker_unavailable
        else None
    )
    broker_recovery = (
        _verify_broker_outbox_recovery() if args.verify_broker_outbox_recovery else None
    )
    source_change_during_download = (
        _check_source_change_during_download(
            mapping=mapping_results["IF101-individual"],
            users=data["users"],
            moodle=moodle,
        )
        if args.check_source_change_during_download
        else None
    )
    invalid_moodle_token = (
        _check_live_invalid_moodle_token(platform)
        if args.check_live_invalid_moodle_token
        else None
    )
    disabled_moodle_function = (
        _check_live_moodle_function(platform, expect_disabled=True)
        if args.expect_live_moodle_function_disabled
        else None
    )
    restored_moodle_function = (
        _check_live_moodle_function(platform, expect_disabled=False)
        if args.check_live_moodle_function_restored
        else None
    )
    print(
        json.dumps(
            {
                "status": "ready",
                "courses": len(data["courses"]),
                "published_assignment_versions": len(data["assignments"]),
                "active_mappings": len(data["mappings"]),
                "group_mappings": sum(len(item["groups"]) for item in mapping_results.values()),
                "imports_run": args.run_imports,
                "source_change_run": args.run_source_change,
                "source_change": source_change,
                "group_roster_revocation": group_roster_revocation,
                "active_session_revocations": active_session_revocations,
                "storage_outage": storage_outage,
                "storage_retry": storage_retry,
                "broker_outage": broker_outage,
                "broker_recovery": broker_recovery,
                "source_change_during_download": source_change_during_download,
                "invalid_moodle_token": invalid_moodle_token,
                "disabled_moodle_function": disabled_moodle_function,
                "restored_moodle_function": restored_moodle_function,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
