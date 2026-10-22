"""Operator-only, guarded recovery for parser messages lost from the broker."""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from align_api.config import get_settings
from align_api.idempotency import canonical_hash
from align_api.models import (
    AssignmentVersion,
    AuditEvent,
    ContextVersion,
    FileAsset,
    Job,
    JobStage,
    MethodManifest,
    NormativeFileLink,
    OutboxEvent,
    ParseArtifact,
    ParserCheckpoint,
    ProcessingTimeReservation,
    Submission,
    SubmissionVersion,
    SubmissionVersionFile,
)
from align_api.parser_profiles import profile_payload
from align_worker.database import SessionFactory

_IDEMPOTENCY_NAMESPACE = UUID("8dbda6ea-d120-4a3d-9d29-9f78d956c1a9")
_PARSER_JOBS = {
    "context_draft": ("parse_context", "job.parse_context", "context_version_id", "context"),
    "rubric_draft": ("parse_rubric", "job.parse_rubric", "assignment_version_id", "assignment"),
    "submission_parse": ("parse_submission", "job.parse_submission", "submission_version_id", "submission"),
}


class RecoveryRejected(ValueError):
    """Raised with a safe, concise reason when a job cannot be reissued."""


def _validate_scope(db: Session, job: Job, namespace_field: str, namespace_kind: str) -> str:
    scope = job.scope_json
    try:
        namespace_id = UUID(str(scope[namespace_field]))
        file_ids = [UUID(str(value)) for value in scope["file_ids"]]
        artifact_ids = [UUID(str(value)) for value in scope["parse_artifact_ids"]]
    except (KeyError, TypeError, ValueError) as exc:
        raise RecoveryRejected("parser scope is incomplete or malformed") from exc
    if not file_ids or len(file_ids) != len(set(file_ids)) or len(artifact_ids) != len(file_ids) or len(set(artifact_ids)) != len(artifact_ids):
        raise RecoveryRejected("parser source and artifact scope does not match")
    if namespace_id != job.subject_id:
        raise RecoveryRejected("parser namespace does not match the job subject")

    if namespace_kind == "context":
        context = db.get(ContextVersion, namespace_id)
        if context is None or context.state != "draft":
            raise RecoveryRejected("context namespace is not an active draft")
        purpose = "context"
    elif namespace_kind == "assignment":
        assignment = db.get(AssignmentVersion, namespace_id)
        if assignment is None or assignment.state != "draft":
            raise RecoveryRejected("assignment namespace is not an active draft")
        purpose = "rubric"
    else:
        submission_version = db.get(SubmissionVersion, namespace_id)
        submission = (
            db.get(Submission, submission_version.submission_id)
            if submission_version is not None else None
        )
        if (
            submission_version is None or submission is None
            or submission.active_final_version_id != submission_version.id
        ):
            raise RecoveryRejected("submission namespace is not the current finalized version")
        purpose = "submission"

    assets = db.scalars(select(FileAsset).where(FileAsset.id.in_(file_ids))).all()
    assets_by_id = {asset.id: asset for asset in assets}
    if set(assets_by_id) != set(file_ids) or any(asset.purpose != purpose for asset in assets):
        raise RecoveryRejected("parser source files do not belong to the expected namespace")
    if namespace_kind == "context":
        linked_file_ids = set(db.scalars(select(NormativeFileLink.file_asset_id).where(
            NormativeFileLink.context_version_id == namespace_id,
            NormativeFileLink.file_asset_id.in_(file_ids),
        )).all())
    elif namespace_kind == "assignment":
        linked_file_ids = set(db.scalars(select(NormativeFileLink.file_asset_id).where(
            NormativeFileLink.assignment_version_id == namespace_id,
            NormativeFileLink.source_role == "rubric",
            NormativeFileLink.file_asset_id.in_(file_ids),
        )).all())
    else:
        linked_file_ids = set(db.scalars(select(SubmissionVersionFile.file_asset_id).where(
            SubmissionVersionFile.submission_version_id == namespace_id,
            SubmissionVersionFile.file_asset_id.in_(file_ids),
        )).all())
    if linked_file_ids != set(file_ids):
        raise RecoveryRejected("parser source files are not linked to this namespace")

    profiles = scope.get("parser_profiles")
    resources = scope.get("parser_resources")
    parser_manifest_id = scope.get("parser_manifest_id")
    if not isinstance(profiles, dict) or not isinstance(resources, dict) or not parser_manifest_id:
        raise RecoveryRejected("parser manifest scope is incomplete")
    settings = get_settings()
    active_profiles = {settings.parser_profile_native, settings.parser_profile_document}
    manifest_ids: set[UUID] = set()
    for file_id in file_ids:
        asset = assets_by_id[file_id]
        profile = profiles.get(str(file_id))
        expected_profile = asset.preflight_json.get("parser_profile", settings.parser_profile_document)
        if profile != expected_profile or profile not in active_profiles:
            raise RecoveryRejected("parser profile differs from the original source manifest")
        resource_ref = resources.get(profile)
        if not isinstance(resource_ref, dict):
            raise RecoveryRejected("parser resource manifest reference is missing")
        try:
            resource_id = UUID(str(resource_ref["id"]))
        except (KeyError, ValueError) as exc:
            raise RecoveryRejected("parser resource manifest reference is malformed") from exc
        expected_payload = profile_payload(settings, profile)
        expected_hash = canonical_hash(expected_payload)
        resource = db.get(MethodManifest, resource_id)
        if (
            resource is None or resource.kind != "parser_resource"
            or resource.sha256 != expected_hash or resource.content_json != expected_payload
            or resource_ref.get("sha256") != expected_hash
        ):
            raise RecoveryRejected("parser resource manifest is incompatible with this worker")
        manifest_ids.add(resource_id)

    parser_manifest = db.get(MethodManifest, UUID(str(parser_manifest_id)))
    if parser_manifest is None or parser_manifest.kind != "parser":
        raise RecoveryRejected("parser implementation manifest is unavailable")
    routes = parser_manifest.content_json.get("routes", {})
    for profile in set(profiles[str(file_id)] for file_id in file_ids):
        resource_ref = resources[profile]
        route = routes.get(profile, {}) if isinstance(routes, dict) else {}
        if route.get("id") != resource_ref.get("id") or route.get("sha256") != resource_ref.get("sha256"):
            raise RecoveryRejected("parser implementation manifest does not bind the source profile")

    artifacts = db.scalars(select(ParseArtifact).where(ParseArtifact.id.in_(artifact_ids))).all()
    artifacts_by_id = {artifact.id: artifact for artifact in artifacts}
    if set(artifacts_by_id) != set(artifact_ids):
        raise RecoveryRejected("one or more parser artifacts are missing")
    namespace_column = {
        "context": "context_version_id",
        "assignment": "assignment_version_id",
        "submission": "submission_version_id",
    }[namespace_kind]
    for file_id, artifact_id in zip(file_ids, artifact_ids, strict=True):
        artifact = artifacts_by_id[artifact_id]
        if (
            artifact.file_asset_id != file_id or getattr(artifact, namespace_column) != namespace_id
            or artifact.status != "queued" or artifact.parser_manifest_id != parser_manifest.id
            or artifact.parser_resource_manifest_id != UUID(str(resources[profiles[str(file_id)]]['id']))
        ):
            raise RecoveryRejected("parser artifact state or namespace differs from the queued job")
    return f"{namespace_kind}:{namespace_id}"


def recover_queued_parser_job(
    job_id: UUID,
    *,
    reason: str,
    execute: bool = False,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Diagnose or transactionally reissue an eligible, never-started parser job."""
    normalized_reason = reason.strip()
    if not normalized_reason or len(normalized_reason) > 1000:
        raise RecoveryRejected("reason must contain 1 to 1000 non-whitespace characters")
    if execute and (not idempotency_key or not idempotency_key.strip() or len(idempotency_key) > 512):
        raise RecoveryRejected("--execute requires a non-empty idempotency key of at most 512 characters")

    settings = get_settings()
    now = datetime.now(UTC)
    visibility_seconds = max(300, settings.job_max_active_seconds + 120)
    request_id = uuid5(_IDEMPOTENCY_NAMESPACE, idempotency_key or "diagnosis")
    advisory_key = int.from_bytes(hashlib.sha256((idempotency_key or "").encode()).digest()[:8], "big", signed=True)

    with SessionFactory.begin() as db:
        # Serialize both retries of one job and reuse of an idempotency key.
        db.execute(select(func.pg_advisory_xact_lock(advisory_key)))
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if job is None:
            raise RecoveryRejected("job was not found")

        if execute:
            prior = db.scalar(select(AuditEvent).where(
                AuditEvent.request_id == request_id,
                AuditEvent.worker_id == "align-parser-recovery",
                AuditEvent.action == "parser.queued_job.reissued",
            ))
            if prior is not None:
                if prior.object_id != job_id:
                    raise RecoveryRejected("idempotency key was already used for a different job")
                refs = prior.new_version_refs_json
                if not isinstance(refs, dict) or not isinstance(refs.get("event_id"), str):
                    raise RecoveryRejected("existing recovery audit record is incomplete")
                return {"status": "already_reissued", "job_id": str(job_id), "event_id": refs["event_id"]}

        spec = _PARSER_JOBS.get(job.type)
        if spec is None:
            raise RecoveryRejected("job is not a supported parser job")
        stage_name, event_type, namespace_field, namespace_kind = spec
        if job.status != "queued" or job.stage != stage_name or job.lease_until is not None:
            raise RecoveryRejected("job is active, complete, or in a non-recoverable state")
        if job.created_at is None or job.created_at > now - timedelta(seconds=visibility_seconds):
            raise RecoveryRejected("job has not passed the broker visibility window")

        pending = db.scalar(select(OutboxEvent.id).where(
            OutboxEvent.event_type == event_type,
            OutboxEvent.subject_id == job.id,
            OutboxEvent.delivered_at.is_(None),
        ).limit(1))
        if pending is not None:
            raise RecoveryRejected("a parser outbox event is already pending")
        if db.scalar(select(JobStage.id).where(
            JobStage.job_id == job.id,
            (JobStage.status != "queued") | (JobStage.attempt_count != 0)
                | JobStage.started_at.is_not(None) | JobStage.finished_at.is_not(None),
        ).limit(1)) is not None:
            raise RecoveryRejected("a parser stage has already started")
        if db.scalar(select(ProcessingTimeReservation.id).where(ProcessingTimeReservation.job_id == job.id).limit(1)) is not None:
            raise RecoveryRejected("a parser processing reservation already exists")
        if db.scalar(select(ParserCheckpoint.id).where(ParserCheckpoint.job_id == job.id).limit(1)) is not None:
            raise RecoveryRejected("a parser checkpoint already exists")

        namespace_ref = _validate_scope(db, job, namespace_field, namespace_kind)
        if not execute:
            return {
                "status": "eligible",
                "job_id": str(job.id),
                "job_type": job.type,
                "namespace": namespace_ref,
                "visibility_window_seconds": visibility_seconds,
            }

        assert idempotency_key is not None
        event = OutboxEvent(
            event_type=event_type,
            subject_id=job.id,
            payload_json={"job_id": str(job.id)},
            next_attempt_at=now,
        )
        db.add(event)
        db.flush()
        db.add(AuditEvent(
            worker_id="align-parser-recovery",
            action="parser.queued_job.reissued",
            object_type="job",
            object_id=job.id,
            old_version_refs_json={"status": job.status, "stage": job.stage},
            new_version_refs_json={
                "event_id": str(event.id),
                "event_type": event_type,
                "namespace": namespace_ref,
                "reason": normalized_reason,
                "idempotency_key": idempotency_key,
            },
            request_id=request_id,
            occurred_at=now,
        ))
        return {"status": "reissued", "job_id": str(job.id), "event_id": str(event.id)}
