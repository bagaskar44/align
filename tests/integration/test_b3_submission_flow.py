"""B3 finalization, immutable roster and original-source access integration."""

from __future__ import annotations

import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select

from align_api.config import get_settings
from align_api.db import SessionFactory
from align_api.main import create_app
from align_api.models import (
    Assignment,
    AssignmentGroup,
    AssignmentVersion,
    ContextVersion,
    Course,
    Enrollment,
    EvidenceItem,
    FileAsset,
    GroupMembership,
    Job,
    OutboxEvent,
    ParseArtifact,
    ParserCheckpoint,
    ProcessingTimeReservation,
    Submission,
    SubmissionVersion,
    SubmissionVersionFile,
    SubmissionVersionMember,
    User,
)
from align_api.security import hash_password
from align_api.storage import storage_client
from align_domain.assignment_contracts import canonical_hash
from align_worker import context_parser
from align_worker.parser_admission import AdmissionLease
from align_worker.tasks import cleanup_expired_submission_uploads, execute_job, reap_expired_leases

pytestmark = pytest.mark.skipif(
    os.environ.get("ALIGN_RUN_INTEGRATION") != "true",
    reason="requires isolated Compose PostgreSQL, Redis and object storage",
)
PASSWORD = "Synthetic-B3-submission-integration"  # noqa: S105 -- synthetic fixture only


def _headers(csrf: str, key: str | None = None) -> dict[str, str]:
    return {"Origin": "http://testserver", "X-CSRF-Token": csrf, "Idempotency-Key": key or str(uuid4())}


def test_group_finalization_race_replay_roster_and_original_download(monkeypatch: pytest.MonkeyPatch) -> None:
    suffix = uuid4().hex
    users = [
        User(normalized_email=f"b3-{label}-{suffix}@example.test", display_name=f"B3 {label}",
             password_hash=hash_password(PASSWORD), global_role="member", active=True,
             must_change_password=False)
        for label in ("lecturer", "student-a", "student-b", "outsider")
    ]
    lecturer, student_a, student_b, outsider = users
    course = Course(code=f"B3-{suffix[:12]}", title="Synthetic B3 submissions", active=True)
    with SessionFactory.begin() as db:
        db.add_all([*users, course])
        db.flush()
        now = datetime.now(UTC)
        context = ContextVersion(
            course_id=course.id, version_no=1, state="approved",
            fields_json={"context_description": {"value": "Context fixture B3."},
                         "outcomes_status": "not_provided", "learning_outcomes": []},
            instruction_sources_json=[], content_hash="b" * 64,
            approved_by=lecturer.id, approved_at=now, revision=1,
        )
        db.add(context)
        db.flush()
        course.approved_context_version_id = context.id
        strict_due_at = now + timedelta(seconds=12)
        assignment = Assignment(course_id=course.id, title=f"B3 submission {suffix}",
                                administrative_state="open")
        strict_assignment = Assignment(course_id=course.id, title=f"B3 strict-late {suffix}",
                                       administrative_state="open")
        db.add_all([assignment, strict_assignment])
        db.flush()
        assignment_version = AssignmentVersion(
            assignment_id=assignment.id, version_no=1, state="published", context_version_id=context.id,
            instruction_original="Unggah bundle sintetis dan finalisasi satu kali.",
            instruction_hash=canonical_hash("B3 instruction"), mode="group",
            opens_at=now - timedelta(days=1), due_at=now - timedelta(seconds=1),
            late_policy="accept_with_label", limits_json={}, published_by=lecturer.id,
            published_at=now, revision=1,
        )
        db.add(assignment_version)
        db.flush()
        assignment.active_version_id = assignment_version.id
        strict_version = AssignmentVersion(
            assignment_id=strict_assignment.id, version_no=1, state="published", context_version_id=context.id,
            instruction_original="Tugas sintetis dengan kebijakan terlambat ketat.",
            instruction_hash=canonical_hash("B3 strict late instruction"), mode="individual",
            opens_at=now - timedelta(days=1), due_at=strict_due_at,
            late_policy="reject_after_due", limits_json={}, published_by=lecturer.id,
            published_at=now, revision=1,
        )
        db.add(strict_version)
        db.flush()
        strict_assignment.active_version_id = strict_version.id
        group = AssignmentGroup(assignment_id=assignment.id, name="Tim Uji B3", active=True)
        db.add(group)
        db.flush()
        db.add_all([
            Enrollment(course_id=course.id, user_id=lecturer.id, role="lecturer", active=True),
            Enrollment(course_id=course.id, user_id=student_a.id, role="student", active=True),
            Enrollment(course_id=course.id, user_id=student_b.id, role="student", active=True),
            Enrollment(course_id=course.id, user_id=outsider.id, role="student", active=True),
            GroupMembership(group_id=group.id, assignment_id=assignment.id, user_id=student_a.id, active=True),
            GroupMembership(group_id=group.id, assignment_id=assignment.id, user_id=student_b.id, active=True),
        ])

    settings = get_settings().model_copy(update={
        "app_origin": "http://testserver", "redis_url": "redis://redis:6379/0",
        "s3_endpoint": "http://object-storage:9090",
        "redis_queue_prefix": f"b3-test-{suffix}",
    })
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client_a, TestClient(app) as client_b, TestClient(app) as client_lecturer, TestClient(app) as client_outsider:
        clients = {"a": client_a, "b": client_b, "lecturer": client_lecturer, "outsider": client_outsider}
        csrf: dict[str, str] = {}
        for label, user in zip(("a", "b", "lecturer", "outsider"),
                               (student_a, student_b, lecturer, outsider), strict=True):
            response = clients[label].post("/api/v1/auth/login", headers={"Origin": settings.app_origin},
                json={"email": user.normalized_email, "password": PASSWORD})
            assert response.status_code == 200, response.text
            csrf[label] = response.json()["csrf_token"]

        created = client_a.post(f"/api/v1/assignments/{assignment.id}/submissions",
                                headers=_headers(csrf["a"]), json={"group_id": str(group.id)})
        assert created.status_code == 201, created.text
        submission_id = created.json()["id"]
        strict_submission = client_a.post(f"/api/v1/assignments/{strict_assignment.id}/submissions",
            headers=_headers(csrf["a"]), json={})
        assert strict_submission.status_code == 201, strict_submission.text
        strict_id = strict_submission.json()["id"]
        strict_upload = client_a.post(f"/api/v1/submissions/{strict_id}/files",
            headers=_headers(csrf["a"]), data={"expected_revision": "1"},
            files={"file": ("printed.png", Path("tests/fixtures/b1/printed.png").read_bytes(), "image/png")})
        assert strict_upload.status_code == 201, strict_upload.text
        strict_file_id = strict_upload.json()["asset"]["id"]
        fixtures = [
            ("printed.png", "image/png", "tests/fixtures/b1/printed.png"),
            ("complex-submission.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
             "fixtures/b3/complex-submission.xlsx"),
        ]
        file_ids: list[str] = []
        revision = 1
        for filename, mime, path in fixtures:
            response = client_a.post(
                f"/api/v1/submissions/{submission_id}/files", headers=_headers(csrf["a"]),
                data={"expected_revision": str(revision)},
                files={"file": (filename, Path(path).read_bytes(), mime)},
            )
            assert response.status_code == 201, response.text
            file_ids.append(response.json()["asset"]["id"])
            revision = response.json()["draft_revision"]

        time.sleep(max(0, (strict_due_at - datetime.now(UTC)).total_seconds() + 0.1))
        strict_finalize = client_a.post(f"/api/v1/submissions/{strict_id}/finalize",
            headers=_headers(csrf["a"]), json={"file_ids": [strict_file_id], "expected_revision": strict_upload.json()["draft_revision"]})
        assert strict_finalize.status_code == 409
        with SessionFactory() as db:
            strict_thread = db.get(Submission, UUID(strict_id))
            assert strict_thread is not None and strict_thread.active_final_version_id is None
            assert db.scalar(select(func.count()).select_from(SubmissionVersion).where(
                SubmissionVersion.submission_id == UUID(strict_id)
            )) == 0
        stale_bytes = Path("tests/fixtures/b1/printed.png").read_bytes()
        stale_hash = hashlib.sha256(stale_bytes).hexdigest()
        stale_asset_id = uuid4()
        stale_key = f"submission-draft/{strict_id}/{stale_asset_id}"
        storage_client(settings).put_object(Bucket=settings.s3_bucket, Key=stale_key, Body=stale_bytes,
                                            ContentType="image/png", Metadata={"sha256": stale_hash})
        with SessionFactory.begin() as db:
            db.add(FileAsset(
                id=stale_asset_id, course_id=course.id, submission_id=UUID(strict_id), purpose="submission",
                uploaded_by=student_a.id, original_name="expired-synthetic.png", size_bytes=len(stale_bytes),
                mime="image/png", sha256=stale_hash, object_key=stale_key, validation_state="validated",
                preflight_json={"width": 1600, "height": 600, "pixels": 960000, "orientation": 1,
                                "parser_profile": "docling_native_v1", "size_bytes": len(stale_bytes), "mime": "image/png"},
                created_at=datetime.now(UTC) - timedelta(hours=25),
            ))
        monkeypatch.setattr("align_worker.tasks.get_settings", lambda: settings)
        cleanup_result = cleanup_expired_submission_uploads.run()
        assert cleanup_result["deleted"] >= 1 and cleanup_result["storage_failures"] == 0, cleanup_result
        with SessionFactory() as db:
            assert db.get(FileAsset, stale_asset_id) is None
        strict_status = client_a.get(f"/api/v1/submissions/{strict_id}/status")
        assert strict_status.status_code == 200
        assert strict_status.json()["draft_revision"] == strict_upload.json()["draft_revision"] + 1
        assert all(item["name"] != "expired-synthetic.png" for item in strict_status.json()["draft_files"])

        broker_probe: Redis | None = None
        if os.environ.get("ALIGN_B3_WAIT_FOR_BROKER_DOWN") == "true":
            broker_probe = Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
            print("B3_TEST_WAITING_FOR_BROKER_OUTAGE", flush=True)
            outage_deadline = time.monotonic() + 120
            while time.monotonic() < outage_deadline:
                try:
                    broker_probe.ping()
                except RedisError:
                    break
                time.sleep(0.2)
            else:
                pytest.fail("Redis broker was not stopped before finalize")

        key_a, key_b = str(uuid4()), str(uuid4())
        body = {"file_ids": file_ids, "display_order": file_ids, "expected_revision": revision}
        barrier = Barrier(2)

        def finalize(label: str) -> tuple[str, object]:
            barrier.wait(timeout=10)
            response = clients[label].post(f"/api/v1/submissions/{submission_id}/finalize",
                headers=_headers(csrf[label], key_a if label == "a" else key_b), json=body)
            return label, response

        with ThreadPoolExecutor(max_workers=2) as pool:
            raced = list(pool.map(finalize, ("a", "b")))
        winners = [(label, response) for label, response in raced if response.status_code == 201]
        losers = [response for _label, response in raced if response.status_code == 409]
        assert len(winners) == len(losers) == 1, [(label, response.status_code, response.text) for label, response in raced]
        winner_label, receipt = winners[0]
        winner_key = key_a if winner_label == "a" else key_b
        assert receipt.json()["version_no"] == 1
        assert receipt.json()["is_late"] is True
        replay = clients[winner_label].post(f"/api/v1/submissions/{submission_id}/finalize",
            headers=_headers(csrf[winner_label], winner_key), json=body)
        assert replay.status_code == 201 and replay.json() == receipt.json()
        student_job = client_a.get(f"/api/v1/jobs/{receipt.json()['job_id']}")
        assert student_job.status_code == 200
        assert isinstance(student_job.json().get("retry_scheduled"), bool)
        assert "last_error_code" not in student_job.json()
        changed_body = {**body, "display_order": list(reversed(file_ids))}
        conflict = clients[winner_label].post(f"/api/v1/submissions/{submission_id}/finalize",
            headers=_headers(csrf[winner_label], winner_key), json=changed_body)
        assert conflict.status_code == 409

        with SessionFactory.begin() as db:
            final_version = db.scalar(select(SubmissionVersion).where(
                SubmissionVersion.submission_id == submission_id))
            assert final_version is not None
            assert db.scalar(select(func.count()).select_from(SubmissionVersion).where(
                SubmissionVersion.submission_id == submission_id)) == 1
            assert db.scalar(select(func.count()).select_from(Job).where(
                Job.type == "submission_parse", Job.subject_id == final_version.id)) == 1
            assert db.scalar(select(func.count()).select_from(OutboxEvent).where(
                OutboxEvent.event_type == "job.parse_submission",
                OutboxEvent.subject_id == UUID(receipt.json()["job_id"]))) == 1
            assert db.scalar(select(func.count()).select_from(SubmissionVersionFile).where(
                SubmissionVersionFile.submission_version_id == final_version.id)) == 2
            assert db.scalar(select(func.count()).select_from(SubmissionVersionMember).where(
                SubmissionVersionMember.submission_version_id == final_version.id)) == 2
            snapshot_roster = {item.email_snapshot for item in db.scalars(select(SubmissionVersionMember).where(
                SubmissionVersionMember.submission_version_id == final_version.id)).all()}
            assert snapshot_roster == {student_a.normalized_email, student_b.normalized_email}
            membership = db.scalar(select(GroupMembership).where(
                GroupMembership.group_id == group.id, GroupMembership.user_id == student_b.id))
            assert membership is not None
            membership.active = False

        status_b = client_b.get(f"/api/v1/submissions/{submission_id}/status")
        assert status_b.status_code == 200 and len(status_b.json()["roster"]) == 2
        file_response = client_b.get(f"/api/v1/files/{file_ids[0]}/content", headers={"Range": "bytes=0-7"})
        assert file_response.status_code == 206 and file_response.headers["Content-Range"].startswith("bytes 0-7/")
        assert client_outsider.get(f"/api/v1/submissions/{submission_id}/status").status_code == 404
        assert client_outsider.get(f"/api/v1/files/{file_ids[0]}/content").status_code == 404
        queue = client_lecturer.get(f"/api/v1/assignments/{assignment.id}/submissions")
        assert queue.status_code == 200 and queue.json()["items"][0]["active_final_version"]["version_no"] == 1

        if broker_probe is not None:
            with SessionFactory() as db:
                event = db.scalar(select(OutboxEvent).where(
                    OutboxEvent.event_type == "job.parse_submission",
                    OutboxEvent.subject_id == UUID(receipt.json()["job_id"]),
                ))
                assert event is not None and event.delivered_at is None
            print("B3_TEST_WAITING_FOR_BROKER_RECOVERY", flush=True)
            recovery_deadline = time.monotonic() + 120
            while time.monotonic() < recovery_deadline:
                try:
                    broker_probe.ping()
                    break
                except RedisError:
                    time.sleep(0.2)
            else:
                broker_probe.close()
                pytest.fail("Redis broker was not restored")
            broker_probe.close()

        if os.environ.get("ALIGN_B3_FENCE_INTEGRATION") == "true":
            child_started, release_child = Event(), Event()

            # This test exercises fencing after the isolated child boundary. Keep
            # host/VM memory admission out of the race so the child boundary is
            # deterministic even when a real Docling run has consumed cache.
            synthetic_lease = AdmissionLease(
                lease_id=f"b3-fence-{suffix}", fence=1,
                memory_bytes=settings.parser_document_memory_bytes, heavy=True,
                node_id=settings.parser_node_id,
            )
            monkeypatch.setattr(context_parser, "acquire_parser_lease",
                                lambda *_args, **_kwargs: (synthetic_lease, "acquired"))
            monkeypatch.setattr(context_parser, "renew_parser_lease", lambda *_args, **_kwargs: True)
            monkeypatch.setattr(context_parser, "release_parser_lease", lambda *_args, **_kwargs: None)
            real_find_spec = context_parser.importlib.util.find_spec
            monkeypatch.setattr(
                context_parser.importlib.util,
                "find_spec",
                lambda name, *args, **kwargs: object() if name in {"docling", "rtree", "docx", "openpyxl"}
                else real_find_spec(name, *args, **kwargs),
            )
            real_which = context_parser.shutil.which
            monkeypatch.setattr(
                context_parser.shutil,
                "which",
                lambda name, *args, **kwargs: "/usr/bin/tesseract" if name == "tesseract"
                else real_which(name, *args, **kwargs),
            )

            def delayed_child(*_args: object, **_kwargs: object) -> tuple[dict[str, object], None, None]:
                child_started.set()
                assert release_child.wait(timeout=20), "test parser child was not released"
                return {"parse_status": "parsed", "units": [], "evidence": []}, None, None

            monkeypatch.setattr(context_parser, "_download_and_run", delayed_child)
            with ThreadPoolExecutor(max_workers=1) as pool:
                parser_result = pool.submit(execute_job.run, receipt.json()["job_id"])
                assert child_started.wait(timeout=30), (
                    f"parser did not reach the delayed child boundary; result="
                    f"{parser_result.result(timeout=1) if parser_result.done() else 'still-running'}"
                )
                with SessionFactory.begin() as db:
                    running = db.get(Job, UUID(receipt.json()["job_id"]))
                    assert running is not None and running.status == "running"
                    running.lease_until = datetime.now(UTC) - timedelta(seconds=1)
                assert reap_expired_leases.run()["jobs"] == 1
                release_child.set()
                assert parser_result.result(timeout=30) == {"status": "stale", "fenced": True}
            with SessionFactory() as db:
                assert db.scalar(select(func.count()).select_from(EvidenceItem).where(
                    EvidenceItem.submission_version_id == UUID(receipt.json()["version_id"])
                )) == 0
                assert db.scalar(select(func.count()).select_from(ParserCheckpoint).where(
                    ParserCheckpoint.job_id == UUID(receipt.json()["job_id"]), ParserCheckpoint.status == "blocked"
                )) == 1
                assert db.scalar(select(func.count()).select_from(ProcessingTimeReservation).where(
                    ProcessingTimeReservation.job_id == UUID(receipt.json()["job_id"]),
                    ProcessingTimeReservation.state == "settled",
                )) == 1

        if os.environ.get("ALIGN_RUN_PARSER_INTEGRATION") == "true":
            deadline = time.monotonic() + 600
            job_status = "queued"
            while time.monotonic() < deadline:
                status_response = client_lecturer.get(f"/api/v1/jobs/{receipt.json()['job_id']}")
                assert status_response.status_code == 200, status_response.text
                job_payload = status_response.json()
                job_status = job_payload["status"]
                retryable_capacity = job_status == "blocked" and job_payload.get("last_error_code") in {
                    "parser_capacity_unavailable", "parser_storage_unavailable",
                }
                if job_status == "ready_for_review" or job_status == "failed" or (job_status == "blocked" and not retryable_capacity):
                    break
                time.sleep(1)
            assert job_status == "ready_for_review", f"B3 parser job ended with {job_status}"

            png_units = client_lecturer.get(f"/api/v1/files/{file_ids[0]}/source-units?limit=100")
            xlsx_units = client_lecturer.get(f"/api/v1/files/{file_ids[1]}/source-units?limit=100")
            assert png_units.status_code == xlsx_units.status_code == 200
            image_anchors = [item["anchor"] for item in png_units.json()["items"] if item["anchor"].get("format") == "image"]
            assert image_anchors and all(item.get("width", 0) > 0 and item.get("height", 0) > 0 for item in image_anchors)
            xlsx_items = xlsx_units.json()["items"]
            assert any(item["anchor"].get("sheet_name") == "Catatan internal" for item in xlsx_items)
            cells = client_lecturer.get(
                f"/api/v1/files/{file_ids[1]}/spreadsheet-cells?sheet_name=Catatan%20internal&limit=100"
            )
            assert cells.status_code == 200 and cells.json()["sheet"]["state"] == "hidden"
            assert any("SYN-01" in str(item["content"].get("value")) for item in cells.json()["items"])
            summary = client_lecturer.get(
                f"/api/v1/files/{file_ids[1]}/spreadsheet-cells?sheet_name=Ringkasan&limit=100"
            )
            assert summary.status_code == 200
            formulas = [item["content"] for item in summary.json()["items"] if item["content"].get("formula")]
            assert formulas and all(item["cache_verified"] is False for item in formulas)
            assert any(item["anchor"].get("part", "").startswith("xl/charts/") for item in xlsx_items)
            with SessionFactory() as db:
                artifacts = db.scalars(select(ParseArtifact).where(
                    ParseArtifact.submission_version_id == UUID(receipt.json()["version_id"])
                )).all()
                assert len(artifacts) == 2 and all(item.parser_resource_manifest_id for item in artifacts)
                assert all(item.status in {"parsed", "partially_parsed"} for item in artifacts)
                checkpoints = db.scalars(select(ParserCheckpoint).where(
                    ParserCheckpoint.parse_artifact_id.in_([item.id for item in artifacts])
                )).all()
                assert checkpoints and all(item.status == "succeeded" for item in checkpoints)

        lecturer_status = client_lecturer.get(f"/api/v1/submissions/{submission_id}/status")
        assert lecturer_status.status_code == 200
        reopened = client_lecturer.post(f"/api/v1/submissions/{submission_id}/reopen",
            headers=_headers(csrf["lecturer"]), json={
                "reason": "Jendela koreksi sintetis untuk uji B3.",
                "reopen_until": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                "assignment_version_id": str(assignment_version.id),
                "expected_revision": lecturer_status.json()["revision"],
            })
        assert reopened.status_code == 200, reopened.text
        after_reopen = client_b.get(f"/api/v1/submissions/{submission_id}/status")
        assert after_reopen.status_code == 200
        assert after_reopen.json()["active_final_version"]["id"] == receipt.json()["version_id"]
        assert len(after_reopen.json()["roster"]) == 2
        assert after_reopen.json()["draft_files"] == []

    with SessionFactory.begin() as db:
        db.get(Course, course.id).active = False
        for user in users:
            db.get(User, user.id).active = False
