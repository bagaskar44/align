"""Opt-in real parser smoke; requires the complete isolated Compose stack.

This is synthetic format/anchor verification, not a full parser benchmark.
"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select

from align_api.db import SessionFactory
from align_api.models import (
    Course,
    Enrollment,
    EvidenceItem,
    Job,
    JobStage,
    OutboxEvent,
    ParseArtifact,
    ParserCheckpoint,
    ProcessingTimeReservation,
    User,
)
from align_api.security import hash_password

pytestmark = pytest.mark.skipif(
    os.environ.get("ALIGN_RUN_PARSER_INTEGRATION") != "true",
    reason="opt-in: requires real parser-worker with locked models and sufficient admission capacity",
)


@pytest.fixture
def lecturer():
    password = "Synthetic-format-fixture-B1"  # noqa: S105
    with SessionFactory.begin() as db:
        user = User(normalized_email=f"parser-{uuid4().hex}@example.test", display_name="Synthetic parser lecturer",
                    password_hash=hash_password(password), active=True, global_role="member", must_change_password=False)
        course = Course(code=f"PARSER-{uuid4().hex[:12]}", title="Synthetic B1 formats", active=True)
        db.add_all([user, course])
        db.flush()
        db.add(Enrollment(user_id=user.id, course_id=course.id, role="lecturer", active=True))
    with httpx.Client(base_url="http://api:8000", headers={"Origin": os.environ["APP_ORIGIN"]}, timeout=30) as client:
        response = client.post("/api/v1/auth/login", json={"email": user.normalized_email, "password": password})
        assert response.status_code == 200
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        yield client, course
    with SessionFactory.begin() as db:
        db.get(User, user.id).active = False


def await_context(client, context_id):
    deadline = time.monotonic() + 240
    context = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/context-versions/{context_id}")
        assert response.status_code == 200
        context = response.json()
        if context["processing"]["status"] == "ready_for_review":
            return context
        if context["processing"]["status"] == "failed":
            break
        time.sleep(1)
    raise AssertionError({"processing": context.get("processing"), "artifacts": context.get("parse_artifacts")})


@pytest.mark.parametrize("filename,mime", [
    ("native-two-pages.pdf", "application/pdf"), ("scanned.pdf", "application/pdf"),
    ("printed.png", "image/png"), ("printed.jpg", "image/jpeg"),
    ("printed.jpeg", "image/jpeg"), ("rotated.jpeg", "image/jpeg"),
])
def test_real_docling_source_hash_original_anchor_and_checkpoint_replay(lecturer, filename, mime):
    client, course = lecturer
    payload = (Path("tests/fixtures/b1") / filename).read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    upload = client.post(f"/api/v1/courses/{course.id}/context-files", files={"file": (filename, payload, mime)})
    assert upload.status_code == 201, upload.text
    file_id = upload.json()["asset"]["id"]
    created = client.post(f"/api/v1/courses/{course.id}/context-drafts", headers={"Idempotency-Key": str(uuid4())},
                          json={"sources": [{"file_id": file_id, "source_role": "kk"}],
                                "manual_fields": {"context_description": "Dokumen sintetis B1"}})
    assert created.status_code == 201, created.text
    context_id = created.json()["context"]["id"]
    job_id = UUID(created.json()["job_id"])
    context = await_context(client, context_id)
    assert all(item["status"] in {"parsed", "partially_parsed"} for item in context["parse_artifacts"]), context["parse_artifacts"]
    assert context["evidence"], context["parse_artifacts"]
    for item in context["evidence"]:
        assert item["source_sha256"] == digest and item["file_id"] == file_id
        anchor = item["anchor"]
        if mime == "application/pdf":
            assert anchor["format"] == "pdf" and anchor["page"] in ({1, 2} if filename.startswith("native") else {1})
            assert anchor["coordinate_system"] == "pdf_points_top_left"
        else:
            assert anchor["format"] == "image" and anchor["coordinate_system"] == "original_pixels_top_left"
            left, top, right, bottom = anchor["bbox"]
            assert 0 <= left <= right <= anchor["width"] and 0 <= top <= bottom <= anchor["height"]
            if filename == "rotated.jpeg":
                assert anchor["transform_id"]
    with SessionFactory.begin() as db:
        artifact = db.scalar(select(ParseArtifact).where(ParseArtifact.context_version_id == context_id))
        assert artifact.parser_manifest_id and artifact.parser_resource_manifest_id
        stage = db.scalar(select(JobStage).where(JobStage.job_id == job_id))
        assert stage.output_refs_json["provider"]["status"] == "mocked"
        assert stage.output_refs_json["provider"]["simulated"] is True
        checkpoints = db.scalars(select(ParserCheckpoint).where(ParserCheckpoint.job_id == job_id)).all()
        assert len(checkpoints) == (2 if filename.startswith("native") else 1)
        assert all(cp.status == "succeeded" and cp.parser_resource_manifest_id for cp in checkpoints)
        before = db.scalar(select(func.count()).select_from(ProcessingTimeReservation).where(ProcessingTimeReservation.job_id == job_id))
        evidence_ids = {item["id"] for item in context["evidence"]}
        # Controlled recovery fixture: output checkpoints committed, coordinator
        # assembly interrupted. The real outbox/worker must reuse the output.
        job = db.get(Job, job_id)
        job.status = "queued"
        stage = db.scalar(select(JobStage).where(JobStage.job_id == job_id))
        stage.status = "blocked"
        db.add(OutboxEvent(event_type="job.parse_context", subject_id=job_id, payload_json={"job_id": str(job_id)}))
    replay = await_context(client, context_id)
    assert {item["id"] for item in replay["evidence"]} == evidence_ids
    with SessionFactory() as db:
        after = db.scalar(select(func.count()).select_from(ProcessingTimeReservation).where(ProcessingTimeReservation.job_id == job_id))
        assert after == before
        assert db.scalar(select(func.count()).select_from(EvidenceItem).where(EvidenceItem.context_version_id == context_id)) == len(evidence_ids)
