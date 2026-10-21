"""B1 HTTP contracts against real PostgreSQL/Redis, with synthetic private data."""
from __future__ import annotations

import hashlib
import io
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from align_api.config import get_settings
from align_api.db import SessionFactory
from align_api.main import create_app
from align_api.models import Course, Enrollment, User
from align_api.security import hash_password

pytestmark = pytest.mark.skipif(
    os.environ.get("ALIGN_RUN_INTEGRATION") != "true",
    reason="requires isolated Compose PostgreSQL and Redis",
)
PASSWORD = "Synthetic-B1-test-password-2026"  # noqa: S105 -- synthetic fixture only


@pytest.fixture
def scenario():
    settings = get_settings().model_copy(update={
        "app_origin": "http://testserver", "redis_url": "redis://redis:6379/0",
        "s3_endpoint": "http://object-storage:9090",
        "redis_queue_prefix": f"b1-test-{uuid4().hex}",
    })
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    with SessionFactory.begin() as db:
        users = [User(
            normalized_email=f"b1-{uuid4().hex}@example.test", display_name=role,
            password_hash=hash_password(PASSWORD), global_role="admin" if role == "admin" else "member",
            active=True, must_change_password=False,
        ) for role in ("admin", "lecturer-a", "lecturer-b", "student-a", "student-b")]
        courses = [Course(code=f"B1-{uuid4().hex[:10]}", title=f"Synthetic {i}", active=True) for i in range(2)]
        db.add_all([*users, *courses])
        db.flush()
        db.add_all([Enrollment(user_id=users[u].id, course_id=courses[c].id, role=role, active=True)
                    for u, c, role in ((1, 0, "lecturer"), (2, 1, "lecturer"), (3, 0, "student"), (4, 1, "student"))])
    with ExitStack() as stack:
        clients = [stack.enter_context(TestClient(app)) for _ in users]
        tokens = []
        for client, user in zip(clients, users, strict=True):
            result = client.post("/api/v1/auth/login", headers={"Origin": settings.app_origin},
                                 json={"email": user.normalized_email, "password": PASSWORD})
            assert result.status_code == 200, result.text
            tokens.append(result.json()["csrf_token"])
        yield users, courses, clients, tokens, app
    with SessionFactory.begin() as db:
        for user in users:
            stored = db.get(User, user.id)
            stored.active = False
            stored.global_role = "member"


def mutation(token: str) -> dict[str, str]:
    return {"Origin": "http://testserver", "X-CSRF-Token": token, "Idempotency-Key": str(uuid4())}


def draft(client, token, course, description="Konteks sintetis B1"):
    response = client.post(f"/api/v1/courses/{course.id}/context-drafts", headers=mutation(token),
                           json={"sources": [], "manual_fields": {"context_description": description}})
    assert response.status_code == 201, response.text
    return response.json()


def test_password_rotation_retains_current_session_and_revokes_other_sessions(scenario):
    users, _, clients, tokens, app = scenario
    client = clients[1]
    old_cookie = client.cookies.get("align_session")
    with TestClient(app) as other:
        assert other.post("/api/v1/auth/login", headers={"Origin": "http://testserver"},
                          json={"email": users[1].normalized_email, "password": PASSWORD}).status_code == 200
        response = client.put("/api/v1/me/password", headers=mutation(tokens[1]),
                              json={"current_password": PASSWORD, "new_password": "\u00e9" * 128})
        assert response.status_code == 204, response.text
        assert client.cookies.get("align_session") != old_cookie
        assert client.get("/api/v1/me").status_code == 200
        assert other.get("/api/v1/me").status_code == 401
        assert client.post("/api/v1/auth/logout", headers=mutation(tokens[1])).status_code == 403
        with TestClient(app) as stolen:
            stolen.cookies.set("align_session", old_cookie)
            assert stolen.get("/api/v1/me").status_code == 401
        assert other.post("/api/v1/auth/login", headers={"Origin": "http://testserver"},
                          json={"email": users[1].normalized_email, "password": PASSWORD}).status_code == 401
        assert other.post("/api/v1/auth/login", headers={"Origin": "http://testserver"},
                          json={"email": users[1].normalized_email, "password": "\u00e9" * 128}).status_code == 200


def test_temporary_account_reset_scope_and_one_time_credential(scenario):
    users, courses, clients, tokens, app = scenario
    admin = clients[0]
    headers = mutation(tokens[0])
    body = {"email": f"Temp-{uuid4().hex}@example.test", "display_name": "Temporary synthetic", "role": "member"}
    created = admin.post("/api/v1/admin/users", headers=headers, json=body)
    assert created.status_code == 201
    credential = created.json()["temporary_password"]
    account = created.json()["user"]
    assert len(credential) >= 20
    replay = admin.post("/api/v1/admin/users", headers=headers, json=body)
    assert replay.json()["temporary_password"] is None
    assert credential not in replay.text
    with TestClient(app) as temporary:
        login = temporary.post("/api/v1/auth/login", headers={"Origin": "http://testserver"},
                               json={"email": body["email"].lower(), "password": credential})
        assert login.status_code == 200
        assert temporary.get("/api/v1/courses").status_code == 403
        assert temporary.put("/api/v1/me/password", headers=mutation(login.json()["csrf_token"]),
                             json={"current_password": credential, "new_password": PASSWORD}).status_code == 204
        assert temporary.get("/api/v1/courses").status_code == 200
        reset = admin.post(f"/api/v1/admin/users/{account['id']}/reset-password", headers=mutation(tokens[0]),
                           json={"expected_revision": 2})
        assert reset.status_code == 201
        assert temporary.get("/api/v1/me").status_code == 401
    # Admin privilege alone never confers access to academic content.
    for client in (admin, clients[2], clients[3], clients[4]):
        assert client.get(f"/api/v1/courses/{courses[0].id}/context-versions").status_code == 404
    assert clients[1].get(f"/api/v1/courses/{courses[1].id}").status_code == 404
    assert clients[1].post("/api/v1/admin/users", headers=mutation(tokens[1]), json=body).status_code == 403


def test_revision_approval_pagination_and_immediate_enrollment_revocation(scenario):
    users, courses, clients, tokens, _ = scenario
    client, token, course = clients[1], tokens[1], courses[0]
    initial = draft(client, token, course)
    context_id = initial["context"]["id"]
    route = f"/api/v1/context-versions/{context_id}"
    response = client.put(route, headers=mutation(token), json={"expected_revision": 1, "fields": {"context_description": "Diperbarui"}})
    assert response.status_code == 200, response.text
    assert client.put(route, headers=mutation(token), json={"expected_revision": 1, "fields": {"language": "en"}}).status_code == 409
    assert client.post(route + "/approve", headers=mutation(token), json={"expected_revision": 1}).status_code == 409
    approved = client.post(route + "/approve", headers=mutation(token), json={"expected_revision": 2})
    assert approved.status_code == 200, approved.text
    result = client.get(route).json()
    assert result["fields"]["learning_outcomes"] == []
    assert result["fields"]["outcomes_status"] == "not_provided"
    assert result["content_hash"] and result["approved_by"] == str(users[1].id)
    assert client.put(route, headers=mutation(token), json={"expected_revision": 3, "fields": {"language": "en"}}).status_code == 409
    draft(client, token, course)
    first = client.get(f"/api/v1/courses/{course.id}/context-versions?limit=1").json()
    assert len(first["items"]) == 1 and first["next_cursor"]
    second = client.get(f"/api/v1/courses/{course.id}/context-versions?limit=1&cursor={first['next_cursor']}").json()
    assert second["items"][0]["id"] == context_id
    assert second["next_cursor"] is None
    for outsider in (clients[0], clients[2], clients[3], clients[4]):
        assert outsider.get(route).status_code == 404
    with SessionFactory.begin() as db:
        enrollment = db.scalar(select(Enrollment).where(Enrollment.course_id == course.id, Enrollment.user_id == users[1].id))
        enrollment.active = False
    assert client.get(route).status_code == 404
    assert client.get(f"/api/v1/courses/{course.id}/context-versions").status_code == 404


def test_concurrent_idempotency_and_context_revision(scenario):
    _, courses, clients, tokens, app = scenario
    admin = clients[0]
    cookie = admin.cookies.get("align_session")
    headers = mutation(tokens[0])
    body = {"email": f"race-{uuid4().hex}@example.test", "display_name": "Race", "role": "member"}
    def create(_):
        with TestClient(app) as client:
            client.cookies.set("align_session", cookie)
            return client.post("/api/v1/admin/users", headers=headers, json=body)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(create, range(2)))
    assert [r.status_code for r in responses] == [201, 201]
    assert responses[0].json()["user"]["id"] == responses[1].json()["user"]["id"]
    assert sum(r.json()["temporary_password"] is not None for r in responses) == 1
    item = draft(clients[1], tokens[1], courses[0])["context"]
    cookie = clients[1].cookies.get("align_session")
    def edit(language):
        with TestClient(app) as client:
            client.cookies.set("align_session", cookie)
            return client.put(f"/api/v1/context-versions/{item['id']}", headers=mutation(tokens[1]),
                              json={"expected_revision": item["revision"], "fields": {"language": language}})
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(r.status_code for r in pool.map(edit, ("id", "en")))
    assert statuses == [200, 409]


def test_last_active_admin_is_retained_under_concurrent_demotion(scenario):
    users, _, clients, tokens, app = scenario
    # This suite runs only on an isolated synthetic database. Preserve seed
    # administrator state while restricting this scenario to two test admins.
    created = clients[0].post("/api/v1/admin/users", headers=mutation(tokens[0]), json={
        "email": f"admin-{uuid4().hex}@example.test", "display_name": "Second synthetic admin", "role": "admin",
    })
    assert created.status_code == 201
    second = created.json()["user"]["id"]
    with SessionFactory.begin() as db:
        existing = db.scalars(select(User).where(User.active.is_(True), User.global_role == "admin", User.id.not_in([users[0].id, UUID(second)]))).all()
        existing_ids = [user.id for user in existing]
        for user in existing:
            user.global_role = "member"
    cookie = clients[0].cookies.get("align_session")
    def demote(user_id):
        with TestClient(app) as client:
            client.cookies.set("align_session", cookie)
            return client.patch(f"/api/v1/admin/users/{user_id}", headers=mutation(tokens[0]),
                                json={"expected_revision": 1, "global_role": "member"})
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(demote, (str(users[0].id), second)))
    finally:
        with SessionFactory.begin() as db:
            active = db.scalars(select(User).where(User.active.is_(True), User.global_role == "admin")).all()
            remaining = len(active)
            for user in active:
                user.active = False
                user.global_role = "member"
            for user_id in existing_ids:
                db.get(User, user_id).global_role = "admin"
    assert sorted(r.status_code for r in results) in ([200, 409], [200, 403])
    assert remaining == 1


def test_private_source_hash_foreign_source_and_download_revocation(scenario):
    from pypdf import PdfWriter
    users, courses, clients, tokens, _ = scenario
    source = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(source)
    payload = source.getvalue()
    response = clients[1].post(f"/api/v1/courses/{courses[0].id}/context-files", headers=mutation(tokens[1]),
                               files={"file": ("synthetic.pdf", payload, "application/pdf")})
    assert response.status_code == 201, response.text
    asset = response.json()["asset"]
    file_route = f"/api/v1/context-files/{asset['id']}"
    assert asset["sha256"] == hashlib.sha256(payload).hexdigest()
    assert clients[1].get(file_route + "/download").content == payload
    for outsider in (clients[0], clients[2], clients[3], clients[4]):
        assert outsider.get(file_route).status_code == 404
        assert outsider.get(file_route + "/download").status_code == 404
    foreign = clients[2].post(f"/api/v1/courses/{courses[1].id}/context-drafts", headers=mutation(tokens[2]),
                              json={"sources": [{"file_id": asset["id"], "source_role": "kk"}]})
    assert foreign.status_code == 404
    own = clients[1].post(f"/api/v1/courses/{courses[0].id}/context-drafts", headers=mutation(tokens[1]),
                          json={"sources": [{"file_id": asset["id"], "source_role": "kk"}],
                                "manual_fields": {"context_description": "Sumber privat"}})
    assert own.status_code == 201, own.text
    assert own.json()["job_id"]
    from align_api.models import FileAsset
    from align_api.storage import storage_client
    settings = get_settings().model_copy(update={"s3_endpoint": "http://object-storage:9090"})
    with SessionFactory() as db:
        object_key = db.get(FileAsset, UUID(asset["id"])).object_key
    storage = storage_client(settings)
    try:
        storage.put_object(Bucket=settings.s3_bucket, Key=object_key, Body=b"controlled-integrity-failure")
        assert clients[1].get(file_route + "/download").status_code == 409
    finally:
        storage.put_object(Bucket=settings.s3_bucket, Key=object_key, Body=payload)
    with SessionFactory.begin() as db:
        enrollment = db.scalar(select(Enrollment).where(Enrollment.course_id == courses[0].id, Enrollment.user_id == users[1].id))
        enrollment.active = False
    assert clients[1].get(file_route).status_code == 404
    assert clients[1].get(file_route + "/download").status_code == 404


def test_approved_provenance_and_worker_approval_are_protected_in_postgres(scenario):
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import DBAPIError

    from align_api.models import (
        ContextVersion,
        EvidenceItem,
        FileAsset,
        NormativeFileLink,
        ParseArtifact,
        SourceUnit,
    )
    users, courses, clients, tokens, _ = scenario
    context = draft(clients[1], tokens[1], courses[0])["context"]
    context_id = context["id"]
    with SessionFactory.begin() as db:
        asset = FileAsset(course_id=courses[0].id, uploaded_by=users[1].id, purpose="context", original_name="synthetic.docx",
                          size_bytes=1, mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                          sha256="a" * 64, object_key=f"synthetic/{uuid4()}", validation_state="validated", preflight_json={})
        db.add(asset)
        db.flush()
        link = NormativeFileLink(file_asset_id=asset.id, context_version_id=context_id, source_role="kk")
        artifact = ParseArtifact(file_asset_id=asset.id, context_version_id=context_id, fingerprint="b" * 64,
                                 owner_scope_hash=hashlib.sha256(context_id.encode()).hexdigest(), status="parsed", limitations_json=[])
        db.add_all([link, artifact])
        db.flush()
        unit = SourceUnit(parse_artifact_id=artifact.id, locator_key="d" * 64, unit_type="root", source_order=0,
                          anchor_json={"format": "file", "locator_note": "synthetic"}, parse_status="parsed", limitations_json=[])
        db.add(unit)
        db.flush()
        with pytest.raises(DBAPIError, match="EVIDENCE_INTEGRITY_MISMATCH"), db.begin_nested():
            db.add(EvidenceItem(source_unit_id=unit.id, file_asset_id=asset.id, context_version_id=context_id,
                                source_sha256="e" * 64, type="paragraph", anchor_json=unit.anchor_json,
                                content_original_json={}, content_normalized_json={}, parse_status="parsed", limitations_json=[]))
            db.flush()
        stored = db.get(ContextVersion, context_id)
        stored.state = "approved"
    with SessionFactory.begin() as db:
        for statement, params in (
            ("UPDATE source_units SET parse_status='failed' WHERE id=:id", {"id": unit.id}),
            ("INSERT INTO normative_file_links(id,file_asset_id,context_version_id,source_role) VALUES(:id,:file,:context,'rpp')",
             {"id": uuid4(), "file": asset.id, "context": context_id}),
            ("UPDATE parse_artifacts SET status='failed' WHERE id=:id", {"id": artifact.id}),
        ):
            with pytest.raises(DBAPIError, match="APPROVED_CONTEXT_IMMUTABLE|CONTEXT_PARSE_SCOPE_MISMATCH"), db.begin_nested():
                db.execute(text(statement), params)
    another = draft(clients[1], tokens[1], courses[0])["context"]
    engine = create_engine(get_settings().worker_database_url)
    try:
        with engine.begin() as connection:
            assert connection.execute(text("SELECT has_table_privilege(current_user,'context_versions','INSERT')")).scalar_one() is False
            with pytest.raises(DBAPIError, match="APPROVED_CONTEXT_IMMUTABLE"), connection.begin_nested():
                connection.execute(text("UPDATE context_versions SET fields_json='{}'::jsonb WHERE id=:id"), {"id": context_id})
            with pytest.raises(DBAPIError, match="WORKER_CANNOT_APPROVE_CONTEXT"), connection.begin_nested():
                connection.execute(text("UPDATE context_versions SET state='approved' WHERE id=:id"), {"id": another["id"]})
    finally:
        engine.dispose()


def test_login_failure_limits_and_immediate_account_deactivation(scenario):
    users, _, clients, tokens, _ = scenario
    for _ in range(5):
        assert clients[1].post("/api/v1/auth/login", headers={"Origin": "http://testserver"},
                               json={"email": users[1].normalized_email, "password": "incorrect"}).status_code == 401
    assert clients[1].post("/api/v1/auth/login", headers={"Origin": "http://testserver"},
                           json={"email": users[1].normalized_email, "password": PASSWORD}).status_code == 429
    for _ in range(15):
        assert clients[1].post("/api/v1/auth/login", headers={"Origin": "http://testserver"},
                               json={"email": f"absent-{uuid4().hex}@example.test", "password": "incorrect"}).status_code == 401
    assert clients[1].post("/api/v1/auth/login", headers={"Origin": "http://testserver"},
                           json={"email": f"absent-{uuid4().hex}@example.test", "password": "incorrect"}).status_code == 429
    assert clients[0].patch(f"/api/v1/admin/users/{users[1].id}", headers=mutation(tokens[0]),
                            json={"expected_revision": 1, "active": False}).status_code == 200
    assert clients[1].get("/api/v1/me").status_code == 401


def test_enrollment_admin_transaction_revision_and_next_request_access(scenario):
    users, courses, clients, tokens, _ = scenario
    route = f"/api/v1/courses/{courses[0].id}/enrollments"
    revoked = clients[0].put(route, headers=mutation(tokens[0]), json={"expected_revision": 1, "enrollments": []})
    assert revoked.status_code == 200
    assert clients[1].get(f"/api/v1/courses/{courses[0].id}").status_code == 404
    assert clients[1].get(route).status_code == 404
    restored = clients[0].put(route, headers=mutation(tokens[0]), json={"expected_revision": 2, "enrollments": [
        {"user_id": str(users[1].id), "role": "lecturer", "active": True},
        {"user_id": str(users[3].id), "role": "student", "active": True},
    ]})
    assert restored.status_code == 200
    assert clients[1].get(route).status_code == 200
    assert clients[0].put(route, headers=mutation(tokens[0]), json={"expected_revision": 1, "enrollments": []}).status_code == 409


def test_conflict_requires_reason_rejects_foreign_reference_and_accepts_manual_correction(scenario):
    from align_api.models import ContextConflict
    _, courses, clients, tokens, _ = scenario
    context = draft(clients[1], tokens[1], courses[0])["context"]
    route = f"/api/v1/context-versions/{context['id']}"
    # Controlled conflict fixture; actual DOCX candidate extraction is covered
    # separately by the browser test, not simulated by this test.
    with SessionFactory.begin() as db:
        db.add(ContextConflict(context_version_id=context["id"], field_path="course_code",
                               candidate_values_json=[{"value": courses[0].code, "source_ref": {"kind": "course_catalog"}},
                                                      {"value": "IF-SOURCE", "source_ref": {"kind": "synthetic_fixture"}}]))
    assert clients[1].post(route + "/approve", headers=mutation(tokens[1]), json={"expected_revision": 1}).status_code == 422
    correction = {"expected_revision": 1, "conflict_resolutions": {"course_code": {
        "value": "IF-SOURCE", "reason": "Sumber harus diperiksa", "source_ref": {"file_id": str(uuid4())},
    }}}
    assert clients[1].put(route, headers=mutation(tokens[1]), json=correction).status_code == 422
    correction["conflict_resolutions"]["course_code"] = {"value": "IF-MANUAL", "reason": ""}
    assert clients[1].put(route, headers=mutation(tokens[1]), json=correction).status_code == 422
    correction["conflict_resolutions"]["course_code"]["reason"] = "Dikoreksi dosen dari tinjauan manual."
    resolved = clients[1].put(route, headers=mutation(tokens[1]), json=correction)
    assert resolved.status_code == 200
    assert resolved.json()["context"]["fields"]["course_code"] == {
        "value": "IF-MANUAL", "origin": "manual", "source_refs": [], "resolution_status": "confirmed",
    }
    assert clients[1].post(route + "/approve", headers=mutation(tokens[1]), json={"expected_revision": 2}).status_code == 200


@pytest.mark.parametrize("failure", ["provider_off", "refusal", "schema", "parser_timeout", "parser_failed"])
def test_worker_failure_retains_inventory_and_manual_approval(scenario, monkeypatch, failure):
    """Controlled parser/provider outputs; real worker DB role, admission and private upload."""
    from pathlib import Path

    from align_api.models import Job, JobStage, ParseArtifact, ParserCheckpoint, SourceUnit
    from align_domain.providers import MockProvider
    from align_worker import context_parser
    from align_worker.context_provider import propose_context_fields
    from align_worker.tasks import execute_job

    _, courses, clients, tokens, app = scenario
    settings = app.dependency_overrides[get_settings]()
    if failure == "provider_off":
        settings = settings.model_copy(update={"provider_mode": "off"})
        app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr(context_parser, "get_settings", lambda: settings)
    find_spec = context_parser.importlib.util.find_spec
    monkeypatch.setattr(context_parser.importlib.util, "find_spec", lambda name: True if name == "docx" else find_spec(name))
    def synthetic_parser(asset, *_args, **_kwargs):
        units = context_parser._fallback_inventory(asset, "parser_pending")
        for unit in units:
            unit["limitations"] = []
            unit["parse_status"] = "parsed"
        return {"units": units, "evidence": [], "candidate_fields": [], "parse_status": "parsed"}, None, "0"
    monkeypatch.setattr(context_parser, "_download_and_run", synthetic_parser)
    if failure in {"parser_timeout", "parser_failed"}:
        monkeypatch.setattr(context_parser, "_download_and_run", lambda *a, **kw: (None, failure, None))
    elif failure in {"refusal", "schema"}:
        class BrokenProvider(MockProvider):
            def extract_fields(self, call, payload):
                result = super().extract_fields(call, payload)
                return result.model_copy(update={"status": "refused"}) if failure == "refusal" else result.model_copy(update={"payload": {"invalid": True}})
        monkeypatch.setattr(context_parser, "propose_context_fields",
                            lambda *args: propose_context_fields(*args, provider=BrokenProvider()))
    payload = Path("apps/web/e2e/fixtures/kk_context.docx").read_bytes()
    upload = clients[1].post(f"/api/v1/courses/{courses[0].id}/context-files", headers=mutation(tokens[1]),
                             files={"file": ("failure.docx", payload, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    assert upload.status_code == 201, upload.text
    created = clients[1].post(f"/api/v1/courses/{courses[0].id}/context-drafts", headers=mutation(tokens[1]), json={
        "sources": [{"file_id": upload.json()["asset"]["id"], "source_role": "kk"}],
        "manual_fields": {"context_description": "Konteks manual tetap sah"},
    })
    assert created.status_code == 201, created.text
    job_id = UUID(created.json()["job_id"])
    result = execute_job.run(str(job_id))
    assert result["status"] == "ready_for_review", result
    context_id = created.json()["context"]["id"]
    with SessionFactory() as db:
        job = db.get(Job, job_id)
        stage = db.scalar(select(JobStage).where(JobStage.job_id == job_id))
        assert job.completion_kind == "completed_with_limits"
        expected = "provider_disabled" if failure == "provider_off" else "model_response_invalid" if failure in {"refusal", "schema"} else failure
        assert expected in {item["code"] for item in stage.output_refs_json["limitations"]}
        artifact = db.scalar(select(ParseArtifact).where(ParseArtifact.context_version_id == context_id))
        assert db.scalars(select(SourceUnit).where(SourceUnit.parse_artifact_id == artifact.id)).all()
        checkpoint = db.scalar(select(ParserCheckpoint).where(ParserCheckpoint.job_id == job_id))
        assert checkpoint.covered_unit_refs_json
    context = clients[1].get(f"/api/v1/context-versions/{context_id}").json()
    assert context["fields"]["context_description"]["value"] == "Konteks manual tetap sah"
    if failure in {"provider_off", "refusal", "schema"}:
        assert context["processing"]["limitations"][0]["code"] == expected
        assert "manual" in context["processing"]["limitations"][0]["message"]
    approved = clients[1].post(f"/api/v1/context-versions/{context_id}/approve", headers=mutation(tokens[1]),
                               json={"expected_revision": context["revision"]})
    assert approved.status_code == 200, approved.text
