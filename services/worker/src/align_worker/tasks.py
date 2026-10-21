from __future__ import annotations

import hashlib
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from align_api.models import (
    AuditEvent,
    Job,
    JobStage,
    Operation,
    OutboxEvent,
    ParseArtifact,
    ParserCheckpoint,
    ProcessingTimeReservation,
    ReleaseManifest,
)
from align_worker.database import SessionFactory


@shared_task(
    bind=True,
    name="align.foundation_smoke",
    max_retries=5,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_jitter=True,
)
def foundation_smoke(self: Any, operation_id: str) -> dict[str, object]:
    """Execute a deterministic operational stage; it never reads or writes academic content."""
    parsed_id = UUID(operation_id)
    now = datetime.now(UTC)
    lease_seconds = int(os.environ.get("WORKER_LEASE_SECONDS", "180"))
    with SessionFactory.begin() as db:
        operation = db.scalar(select(Operation).where(Operation.id == parsed_id).with_for_update())
        if operation is None:
            return {"status": "missing"}
        if operation.status == "succeeded":
            return {"status": "succeeded", "operation_id": operation_id, "duplicate": True}
        if operation.status == "running" and operation.lease_until and operation.lease_until > now:
            return {"status": "running", "operation_id": operation_id, "duplicate": True}
        operation.status = "running"
        operation.attempt_count += 1
        operation.lease_until = now + timedelta(seconds=lease_seconds)

    input_hash = hashlib.sha256(operation.idempotency_key.encode("utf-8")).hexdigest()
    with SessionFactory.begin() as db:
        operation = db.scalar(select(Operation).where(Operation.id == parsed_id).with_for_update())
        if operation is None:
            return {"status": "missing"}
        if operation.status == "succeeded":
            return {"status": "succeeded", "operation_id": operation_id, "duplicate": True}
        operation.status = "succeeded"
        operation.lease_until = None
        operation.result_json = {
            "stage": "foundation_smoke",
            "input_hash": input_hash,
            "academic_work": False,
            "provider_calls": 0,
        }
        db.add(
            AuditEvent(
                worker_id="align-worker",
                action="operation.foundation_smoke.succeeded",
                object_type="operation",
                object_id=operation.id,
                request_id=operation.id,
                occurred_at=datetime.now(UTC),
            )
        )
    return {"status": "succeeded", "operation_id": operation_id, "duplicate": False}


@shared_task(
    name="align.reap_expired_leases",
    max_retries=5,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_jitter=True,
)
def reap_expired_leases() -> dict[str, int]:
    now = datetime.now(UTC)
    recovered_operations = 0
    recovered_jobs = 0
    with SessionFactory.begin() as db:
        operations = db.scalars(
            select(Operation)
            .where(Operation.status == "running", Operation.lease_until < now)
            .with_for_update(skip_locked=True)
            .limit(100)
        ).all()
        for operation in operations:
            operation.status = "queued"
            operation.lease_until = None
            operation.updated_at = now
            db.add(
                OutboxEvent(
                    event_type="operation.foundation_smoke",
                    subject_id=operation.id,
                    payload_json={"operation_id": str(operation.id)},
                    next_attempt_at=now,
                )
            )
            recovered_operations += 1

        jobs = db.scalars(
            select(Job)
            .where(Job.status == "running", Job.lease_until < now)
            .with_for_update(skip_locked=True)
            .limit(100)
        ).all()
        for job in jobs:
            if job.type in {"context_draft", "rubric_draft"}:
                reservations = db.scalars(
                    select(ProcessingTimeReservation)
                    .where(ProcessingTimeReservation.job_id == job.id, ProcessingTimeReservation.state == "held")
                    .with_for_update()
                ).all()
                for reservation in reservations:
                    reservation.actual_active_ms = reservation.reserved_active_ms
                    reservation.state = "settled"
                    reservation.finished_at = now
                    checkpoint = db.scalar(
                        select(ParserCheckpoint)
                        .where(ParserCheckpoint.job_id == job.id, ParserCheckpoint.task_key == reservation.task_key)
                        .with_for_update()
                    )
                    if checkpoint is not None and checkpoint.status == "running":
                        checkpoint.status = "blocked"
                        checkpoint.finished_at = now
                        checkpoint.actual_active_ms = reservation.actual_active_ms
                        checkpoint.limitations_json = [
                            {"code": "parser_timeout", "message": "Proses parser berhenti sebelum checkpoint selesai."}
                        ]
                        artifact = db.get(ParseArtifact, checkpoint.parse_artifact_id)
                        if artifact is not None:
                            artifact.status = "blocked"
                            artifact.limitations_json = checkpoint.limitations_json
            job.status = "queued"
            job.lease_until = None
            job.lease_fence += 1
            db.add(
                OutboxEvent(
                    event_type=(
                        "job.parse_context"
                        if job.type == "context_draft"
                        else "job.parse_rubric"
                        if job.type == "rubric_draft"
                        else "job.execute"
                    ),
                    subject_id=job.id,
                    payload_json={"job_id": str(job.id)},
                    next_attempt_at=now,
                )
            )
            recovered_jobs += 1
    return {"operations": recovered_operations, "jobs": recovered_jobs}


@shared_task(
    bind=True,
    name="align.execute_job",
    max_retries=5,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_jitter=True,
)
def execute_job(self: Any, job_id: str) -> dict[str, object]:
    """Acquire/fence a contract job; B0 validates the release and blocks unavailable stages."""
    parsed_id = UUID(job_id)
    now = datetime.now(UTC)
    lease_seconds = int(os.environ.get("WORKER_LEASE_SECONDS", "180"))
    with SessionFactory.begin() as db:
        job = db.scalar(select(Job).where(Job.id == parsed_id).with_for_update())
        if job is None:
            return {"status": "missing"}
        if not re.fullmatch(r"[a-f0-9]{64}", job.input_fingerprint):
            job.status = "failed"
            job.last_error_code = "INPUT_HASH_INVALID"
            job.lease_until = None
            return {"status": "failed", "reason": "INPUT_HASH_INVALID"}
        if job.status == "ready_for_review":
            return {"status": "ready_for_review", "duplicate": True}
        if job.status == "running" and job.lease_until and job.lease_until > now:
            return {"status": "running", "duplicate": True}
        if job.status not in {"queued", "blocked"}:
            return {"status": job.status, "duplicate": True}

        stage_name = job.stage or "validate_manifest"
        stage_hash = hashlib.sha256(f"{job.input_fingerprint}:{stage_name}".encode("ascii")).hexdigest()
        stage = db.scalar(
            select(JobStage)
            .where(JobStage.job_id == job.id, JobStage.stage_name == stage_name, JobStage.input_hash == stage_hash)
            .with_for_update()
        )
        if stage is not None and stage.status == "succeeded":
            return {"status": job.status, "duplicate": True, "stage_reused": True}
        if stage is None:
            stage = JobStage(
                job_id=job.id,
                stage_name=stage_name,
                input_hash=stage_hash,
                status="queued",
                output_refs_json={},
                attempt_count=0,
            )
            db.add(stage)
            db.flush()
        stage.status = "running"
        stage.attempt_count += 1
        stage.started_at = now
        job.status = "running"
        job.stage = stage_name
        job.lease_until = now + timedelta(seconds=lease_seconds)
        job.lease_fence += 1
        fence = job.lease_fence
        fingerprint = job.input_fingerprint

    if stage_name == "parse_context" and job.type == "context_draft":
        from align_worker.context_parser import execute_context_job

        return execute_context_job(parsed_id, fence, fingerprint)
    if stage_name == "parse_rubric" and job.type == "rubric_draft":
        from align_worker.context_parser import execute_rubric_job

        return execute_rubric_job(parsed_id, fence, fingerprint)

    # Do not let an unavailable B0 capability become a fabricated academic result.
    failure_code = "STAGE_HANDLER_UNAVAILABLE_B0"
    if stage_name == "validate_manifest":
        release_id = job.scope_json.get("release_manifest_id")
        if not isinstance(release_id, str):
            failure_code = "RELEASE_MANIFEST_REQUIRED"
        else:
            try:
                parsed_release_id = UUID(release_id)
            except ValueError:
                failure_code = "RELEASE_MANIFEST_INVALID"
            else:
                with SessionFactory() as db:
                    release = db.get(ReleaseManifest, parsed_release_id)
                    if release is None or release.state != "active":
                        failure_code = "RELEASE_NOT_ACTIVE"
                    elif job.type == "analysis" and "native_parser" in release.manifest_payload.get(
                        "capabilities_unavailable", []
                    ):
                        failure_code = "CAPABILITY_UNAVAILABLE_PARSER"

    with SessionFactory.begin() as db:
        job = db.scalar(select(Job).where(Job.id == parsed_id).with_for_update())
        if job is None:
            return {"status": "missing"}
        if job.input_fingerprint != fingerprint or job.lease_fence != fence:
            return {"status": "stale", "fenced": True}
        stage = db.scalar(
            select(JobStage)
            .where(JobStage.job_id == job.id, JobStage.stage_name == stage_name, JobStage.input_hash == stage_hash)
            .with_for_update()
        )
        if stage is None:
            job.status = "failed"
            job.lease_until = None
            job.last_error_code = "STAGE_RECORD_MISSING"
            return {"status": "failed", "reason": "STAGE_RECORD_MISSING"}
        stage.status = "blocked"
        stage.failure_code = failure_code
        stage.finished_at = datetime.now(UTC)
        job.status = "blocked"
        job.lease_until = None
        job.last_error_code = failure_code
    return {"status": "blocked", "reason": failure_code, "academic_result": False}


@shared_task(
    name="align.parse_context",
    soft_time_limit=int(os.environ.get("JOB_MAX_ACTIVE_SECONDS", "600")) + 30,
    time_limit=int(os.environ.get("JOB_MAX_ACTIVE_SECONDS", "600")) + 60,
    max_retries=5,
    autoretry_for=(OperationalError,), retry_backoff=True, retry_jitter=True,
)
def parse_context(job_id: str) -> dict[str, object]:
    # The coordinator can run several bounded children; each child retains the
    # 120/150-second guard and all attempts share the 600-second reservation cap.
    return execute_job.run(job_id)  # type: ignore[no-any-return]


@shared_task(
    name="align.parse_rubric",
    soft_time_limit=int(os.environ.get("JOB_MAX_ACTIVE_SECONDS", "600")) + 30,
    time_limit=int(os.environ.get("JOB_MAX_ACTIVE_SECONDS", "600")) + 60,
    max_retries=5,
    autoretry_for=(OperationalError,), retry_backoff=True, retry_jitter=True,
)
def parse_rubric(job_id: str) -> dict[str, object]:
    return execute_job.run(job_id)  # type: ignore[no-any-return]

