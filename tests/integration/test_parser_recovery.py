"""Guard and idempotency checks for lost parser broker messages."""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from align_api.config import get_settings
from align_api.contexts import canonical_hash
from align_api.db import SessionFactory as ApiSessionFactory
from align_api.models import (
    AuditEvent,
    ContextVersion,
    Course,
    FileAsset,
    Job,
    NormativeFileLink,
    OutboxEvent,
    ParseArtifact,
    User,
)
from align_api.parser_profiles import profile_payload, register_manifest
from align_worker.database import SessionFactory as WorkerSessionFactory
from align_worker.recovery import RecoveryRejected, recover_queued_parser_job

pytestmark = pytest.mark.skipif(
    os.environ.get("ALIGN_RUN_INTEGRATION") != "true",
    reason="requires isolated PostgreSQL and worker-role connections",
)


def _queued_context_job(
    *,
    resource_hash_override: str | None = None,
    status: str = "queued",
    pending_event: bool = False,
) -> Job:
    settings = get_settings()
    now = datetime.now(UTC)
    with ApiSessionFactory.begin() as db:
        user = User(
            normalized_email=f"recover-{uuid4().hex}@example.test",
            display_name="Synthetic recovery fixture",
            password_hash="placeholder_hash",  # noqa: S106 -- user is never authenticated
            global_role="member",
            active=True,
        )
        course = Course(code=f"RECOVER-{uuid4().hex[:12]}", title="Recovery fixture", active=True)
        db.add_all([user, course])
        db.flush()
        context = ContextVersion(
            course_id=course.id,
            version_no=1,
            state="draft",
            fields_json={"context_description": "Synthetic parser recovery test."},
            instruction_sources_json=[],
        )
        db.add(context)
        db.flush()
        asset = FileAsset(
            course_id=course.id,
            purpose="context",
            uploaded_by=user.id,
            original_name="synthetic-recovery.docx",
            size_bytes=1,
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            sha256="a" * 64,
            object_key=f"synthetic/recovery/{uuid4().hex}",
            validation_state="validated",
            preflight_json={"parser_profile": settings.parser_profile_native, "pages": 1},
        )
        db.add(asset)
        db.flush()
        db.add(NormativeFileLink(
            file_asset_id=asset.id,
            context_version_id=context.id,
            source_role="kk",
        ))

        resource_payload = profile_payload(settings, settings.parser_profile_native)
        resource = register_manifest(db, "parser_resource", resource_payload)
        parser = register_manifest(db, "parser", {
            "schema_version": 1,
            "manifest_id": "b1-context-parser-v1",
            "routes": {settings.parser_profile_native: {"id": str(resource.id), "sha256": resource.sha256}},
        })
        artifact = ParseArtifact(
            file_asset_id=asset.id,
            context_version_id=context.id,
            fingerprint=canonical_hash({"synthetic": uuid4().hex}),
            parser_manifest_id=parser.id,
            parser_resource_manifest_id=resource.id,
            owner_scope_hash=canonical_hash({"context": str(context.id), "file": str(asset.id)}),
            status="queued",
            limitations_json=[],
        )
        db.add(artifact)
        db.flush()

        job_id = uuid4()
        resource_ref = {"id": str(resource.id), "sha256": resource_hash_override or resource.sha256}
        job = Job(
            id=job_id,
            type="context_draft",
            subject_id=context.id,
            scope_json={
                "course_id": str(course.id),
                "context_version_id": str(context.id),
                "file_ids": [str(asset.id)],
                "parse_artifact_ids": [str(artifact.id)],
                "parser_profiles": {str(asset.id): settings.parser_profile_native},
                "parser_manifest_id": str(parser.id),
                "parser_resources": {settings.parser_profile_native: resource_ref},
            },
            input_fingerprint=canonical_hash({"job": str(job_id)}),
            status=status,
            stage="parse_context",
            lease_until=now + timedelta(minutes=5) if status == "running" else None,
            job_limits_json={"max_active_seconds": settings.job_max_active_seconds},
            created_at=now - timedelta(seconds=max(300, settings.job_max_active_seconds + 120) + 60),
        )
        db.add(job)
        db.flush()
        db.add(OutboxEvent(
            event_type="job.parse_context",
            subject_id=job.id,
            payload_json={"job_id": str(job.id)},
            delivered_at=None if pending_event else now - timedelta(minutes=5),
        ))
        db.flush()
        return job


def test_lost_parser_message_recovery_is_guarded_and_idempotent() -> None:
    good = _queued_context_job()
    active = _queued_context_job(status="running")
    finished = _queued_context_job(status="ready_for_review")
    pending = _queued_context_job(pending_event=True)
    incompatible = _queued_context_job(resource_hash_override="f" * 64)
    diagnosis = recover_queued_parser_job(good.id, reason="synthetic lost broker message")
    assert diagnosis["status"] == "eligible"
    with WorkerSessionFactory() as db:
        assert db.scalar(select(OutboxEvent.id).where(
            OutboxEvent.subject_id == good.id, OutboxEvent.delivered_at.is_(None)
        )) is None
        assert db.scalar(select(AuditEvent.id).where(AuditEvent.object_id == good.id)) is None

    with pytest.raises(RecoveryRejected, match="active, complete"):
        recover_queued_parser_job(active.id, reason="active job must not be reissued")
    with pytest.raises(RecoveryRejected, match="active, complete"):
        recover_queued_parser_job(finished.id, reason="finished job must not be reissued")
    with pytest.raises(RecoveryRejected, match="already pending"):
        recover_queued_parser_job(pending.id, reason="pending event must not be duplicated")
    with pytest.raises(RecoveryRejected, match="manifest is incompatible"):
        recover_queued_parser_job(incompatible.id, reason="mismatched manifest must not be reissued")

    key = str(uuid4())
    first = recover_queued_parser_job(
        good.id, reason="synthetic lost broker message", execute=True, idempotency_key=key
    )
    second = recover_queued_parser_job(
        good.id, reason="repeat command", execute=True, idempotency_key=key
    )
    assert first["status"] == "reissued"
    assert second == {"status": "already_reissued", "job_id": str(good.id), "event_id": first["event_id"]}
    with pytest.raises(RecoveryRejected, match="different job"):
        recover_queued_parser_job(
            active.id, reason="key reuse", execute=True, idempotency_key=key
        )

    with WorkerSessionFactory() as db:
        events = db.scalars(select(OutboxEvent).where(
            OutboxEvent.event_type == "job.parse_context", OutboxEvent.subject_id == good.id
        ).order_by(OutboxEvent.created_at)).all()
        audits = db.scalars(select(AuditEvent).where(
            AuditEvent.worker_id == "align-parser-recovery",
            AuditEvent.object_id == good.id,
        )).all()
        assert len(events) == 2
        assert events[0].delivered_at is not None
        assert events[1].id.hex == first["event_id"].replace("-", "")
        assert len(audits) == 1
        assert audits[0].new_version_refs_json["reason"] == "synthetic lost broker message"
