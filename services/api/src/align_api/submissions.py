from __future__ import annotations

import hashlib
import math
import re
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote
from uuid import UUID, uuid4

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from pydantic import BeforeValidator, Field, StrictInt, StrictStr
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from align_api.auth import (
    Identity,
    StrictRequest,
    require_password_change_completed,
    require_verified_active_identity,
)
from align_api.config import Settings, get_settings
from align_api.db import get_db
from align_api.file_validation import validate_context_file
from align_api.idempotency import canonical_hash, replay_or_lock, store_response
from align_api.models import (
    Assignment,
    AssignmentGroup,
    AssignmentVersion,
    AuditEvent,
    Course,
    Enrollment,
    EvidenceItem,
    FileAsset,
    GroupMembership,
    Job,
    JobStage,
    OutboxEvent,
    ParseArtifact,
    ProcessingTimeReservation,
    SourceUnit,
    Submission,
    SubmissionVersion,
    SubmissionVersionFile,
    SubmissionVersionMember,
    User,
)
from align_api.parser_profiles import profile_payload, register_manifest
from align_api.retention import raise_source_purged, source_purged
from align_api.storage import storage_client

router = APIRouter(tags=["submissions", "evidence"])


def _json_uuid(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            return value
    return value


UUIDInput = Annotated[UUID, BeforeValidator(_json_uuid)]


def _json_datetime(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    return value


DateTimeInput = Annotated[datetime, BeforeValidator(_json_datetime)]


class CreateSubmission(StrictRequest):
    group_id: UUIDInput | None = None


class FinalizeSubmission(StrictRequest):
    file_ids: list[UUIDInput] = Field(min_length=1, max_length=5)
    expected_revision: StrictInt = Field(ge=1)
    display_order: list[UUIDInput] | None = None


class ReopenSubmission(StrictRequest):
    reason: StrictStr = Field(min_length=1, max_length=2000)
    reopen_until: DateTimeInput
    assignment_version_id: UUIDInput
    expected_revision: StrictInt = Field(ge=1)


def _role(db: DbSession, assignment: Assignment, user_id: UUID) -> str | None:
    return db.scalar(select(Enrollment.role).where(
        Enrollment.course_id == assignment.course_id,
        Enrollment.user_id == user_id,
        Enrollment.active.is_(True),
    ))


def _submission_access(
    db: DbSession,
    submission: Submission,
    identity: Identity,
    *,
    version_id: UUID | None = None,
    require_current_membership: bool = False,
) -> tuple[Assignment, str]:
    assignment = db.scalar(
        select(Assignment).join(Course, Course.id == Assignment.course_id)
        .where(Assignment.id == submission.assignment_id, Course.active.is_(True))
    )
    if assignment is None:
        raise HTTPException(status_code=404, detail="submission not found")
    # Global operations privileges do not grant access to academic content.
    role = _role(db, assignment, identity.user.id)
    if role == "student":
        allowed = submission.individual_user_id == identity.user.id
        if submission.group_id is not None:
            snapshot_id = version_id or submission.active_final_version_id
            if require_current_membership or snapshot_id is None:
                allowed = db.scalar(select(GroupMembership.id).where(
                    GroupMembership.group_id == submission.group_id,
                    GroupMembership.assignment_id == assignment.id,
                    GroupMembership.user_id == identity.user.id,
                    GroupMembership.active.is_(True),
                )) is not None
            else:
                allowed = db.scalar(select(SubmissionVersionMember.user_id).where(
                    SubmissionVersionMember.submission_version_id == snapshot_id,
                    SubmissionVersionMember.user_id == identity.user.id,
                )) is not None
        if not allowed:
            raise HTTPException(status_code=404, detail="submission not found")
    elif role != "lecturer" and role != "admin":
        raise HTTPException(status_code=404, detail="submission not found")
    return assignment, role


def _retry_scheduled(job: Job) -> bool:
    return job.status == "blocked" and job.last_error_code in {
        "parser_capacity_unavailable",
        "parser_storage_unavailable",
    }


def _assignment_for_student(db: DbSession, assignment_id: UUID, identity: Identity) -> tuple[Assignment, AssignmentVersion]:
    assignment = db.scalar(select(Assignment).join(Course).where(
        Assignment.id == assignment_id, Course.active.is_(True), Assignment.administrative_state == "open"
    ))
    if assignment is None or _role(db, assignment, identity.user.id) != "student" or not assignment.active_version_id:
        raise HTTPException(status_code=404, detail="assignment not found")
    version = db.get(AssignmentVersion, assignment.active_version_id)
    if version is None or version.state != "published" or version.opens_at > datetime.now(UTC):
        raise HTTPException(status_code=404, detail="assignment not found")
    return assignment, version


def _current_draft_allowed(submission: Submission, version: AssignmentVersion, now: datetime) -> None:
    if submission.draft_assignment_version_id != version.id:
        raise HTTPException(status_code=409, detail="assignment version changed; reload submission")
    if submission.active_final_version_id is not None and (submission.reopen_until is None or submission.reopen_until < now):
        raise HTTPException(status_code=409, detail="submission is finalized")
    if version.opens_at > now:
        raise HTTPException(status_code=409, detail="submission is not open")
    reopened = submission.reopen_until is not None and now <= submission.reopen_until
    if now > version.due_at and version.late_policy == "reject_after_due" and not reopened:
        raise HTTPException(status_code=409, detail="late submission is closed")
    if submission.reopen_until is not None and now > submission.reopen_until:
        raise HTTPException(status_code=409, detail="reopened submission window expired")


def _submission_summary(db: DbSession, submission: Submission, *, staff: bool) -> dict[str, Any]:
    version = db.get(SubmissionVersion, submission.active_final_version_id) if submission.active_final_version_id else None
    final_files = []
    if version is not None:
        final_files = [
            {"id": str(asset.id), "name": asset.original_name, "size_bytes": asset.size_bytes,
             "mime": asset.mime, "sha256": asset.sha256}
            for asset in db.scalars(
                select(FileAsset).join(
                    SubmissionVersionFile, SubmissionVersionFile.file_asset_id == FileAsset.id,
                ).where(SubmissionVersionFile.submission_version_id == version.id)
                .order_by(SubmissionVersionFile.display_order)
            ).all()
        ]
    job_id = db.scalar(select(Job.id).where(
        Job.type == "submission_parse", Job.subject_id == version.id
    )) if version is not None else None
    files = db.scalars(select(FileAsset).where(
        FileAsset.submission_id == submission.id,
        FileAsset.validation_state == "validated",
        ~FileAsset.id.in_(select(SubmissionVersionFile.file_asset_id)),
    ).order_by(FileAsset.created_at, FileAsset.id)).all()
    payload: dict[str, Any] = {
        "id": str(submission.id),
        "assignment_id": str(submission.assignment_id),
        "revision": submission.revision,
        "owner": {"individual_user_id": str(submission.individual_user_id) if submission.individual_user_id else None,
                  "group_id": str(submission.group_id) if submission.group_id else None},
        "active_final_version": None if version is None else {
            "id": str(version.id), "version_no": version.version_no,
            "manifest_hash": version.manifest_hash, "assignment_version_id": str(version.assignment_version_id),
            "finalized_at": version.finalized_at.isoformat(), "is_late": version.is_late,
            "job_id": str(job_id) if job_id else None,
        },
        "final_files": final_files,
        "draft_revision": submission.draft_revision,
        "reopen_until": submission.reopen_until.isoformat() if submission.reopen_until else None,
        "draft_files": [{"id": str(asset.id), "name": asset.original_name, "size_bytes": asset.size_bytes,
                         "mime": asset.mime, "sha256": asset.sha256, "preflight": asset.preflight_json}
                        for asset in files],
    }
    if submission.group_id is not None:
        group = db.get(AssignmentGroup, submission.group_id)
        payload["owner"]["label"] = group.name if group else "Kelompok"
    elif submission.individual_user_id is not None:
        student = db.get(User, submission.individual_user_id)
        payload["owner"]["label"] = student.display_name if student else "Mahasiswa"
    if version is not None:
        payload["roster"] = [member.display_name_snapshot for member in db.scalars(
            select(SubmissionVersionMember).where(SubmissionVersionMember.submission_version_id == version.id)
            .order_by(SubmissionVersionMember.display_name_snapshot)
        ).all()]
    if staff and version:
        parsed = db.execute(
            select(FileAsset, ParseArtifact).join(SubmissionVersionFile, SubmissionVersionFile.file_asset_id == FileAsset.id)
            .outerjoin(ParseArtifact, ParseArtifact.file_asset_id == FileAsset.id)
            .where(SubmissionVersionFile.submission_version_id == version.id)
            .order_by(SubmissionVersionFile.display_order)
        ).all()
        payload["files"] = [{"id": str(asset.id), "name": asset.original_name, "sha256": asset.sha256,
                             "mime": asset.mime, "preflight": asset.preflight_json,
                             "content_url": f"/api/v1/files/{asset.id}/content",
                             "parse_status": artifact.status if artifact else "queued",
                             "limitations": artifact.limitations_json if artifact else []}
                            for asset, artifact in parsed]
    else:
        if version is None:
            payload["processing"] = "not_submitted"
        else:
            payload["processing"] = db.scalar(select(Job.status).where(
                Job.type == "submission_parse", Job.subject_id == version.id
            )) or "queued"
    return payload


@router.post("/assignments/{assignment_id}/submissions", status_code=201)
def create_submission(
    assignment_id: UUID,
    body: CreateSubmission,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
) -> dict[str, Any]:
    assignment, version = _assignment_for_student(db, assignment_id, identity)
    now = datetime.now(UTC)
    if version.mode == "individual":
        if body.group_id is not None:
            raise HTTPException(status_code=422, detail="individual assignment cannot use a group")
        submission = db.scalar(select(Submission).where(
            Submission.assignment_id == assignment.id,
            Submission.individual_user_id == identity.user.id,
        ))
        if submission is None:
            submission = Submission(assignment_id=assignment.id, individual_user_id=identity.user.id,
                                   draft_assignment_version_id=version.id)
    else:
        if body.group_id is None:
            raise HTTPException(status_code=422, detail="group assignment requires a group")
        membership = db.scalar(select(GroupMembership).join(Assignment, Assignment.id == GroupMembership.assignment_id)
            .where(GroupMembership.assignment_id == assignment.id, GroupMembership.group_id == body.group_id,
                   GroupMembership.user_id == identity.user.id, GroupMembership.active.is_(True)))
        if membership is None:
            raise HTTPException(status_code=404, detail="group not found")
        submission = db.scalar(select(Submission).where(
            Submission.assignment_id == assignment.id, Submission.group_id == body.group_id,
        ))
        if submission is None:
            submission = Submission(assignment_id=assignment.id, group_id=body.group_id,
                                   draft_assignment_version_id=version.id)
    _current_draft_allowed(submission, version, now)
    if submission.id is None:
        db.add(submission)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            submission = db.scalar(select(Submission).where(
                Submission.assignment_id == assignment.id,
                Submission.individual_user_id == identity.user.id if version.mode == "individual"
                else Submission.group_id == body.group_id,
            ))
            if submission is None:
                raise HTTPException(status_code=409, detail="submission changed concurrently") from exc
    return _submission_summary(db, submission, staff=False)


@router.post("/submissions/{submission_id}/files", status_code=201)
async def upload_submission_file(
    submission_id: UUID,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    expected_revision: Annotated[int, Form(ge=1)],
    file: Annotated[UploadFile, File()],
) -> dict[str, Any]:
    submission = db.scalar(select(Submission).where(Submission.id == submission_id).with_for_update())
    if submission is None:
        raise HTTPException(status_code=404, detail="submission not found")
    assignment, role = _submission_access(db, submission, identity, require_current_membership=True)
    if role != "student":
        raise HTTPException(status_code=403, detail="students upload their own submission")
    version = db.get(AssignmentVersion, assignment.active_version_id) if assignment.active_version_id else None
    if version is None:
        raise HTTPException(status_code=404, detail="assignment not found")
    now = datetime.now(UTC)
    _current_draft_allowed(submission, version, now)
    if expected_revision != submission.draft_revision:
        raise HTTPException(status_code=409, detail="draft revision changed")
    used = db.scalar(select(func.count()).select_from(FileAsset).where(
        FileAsset.submission_id == submission.id, FileAsset.validation_state == "validated",
        ~FileAsset.id.in_(select(SubmissionVersionFile.file_asset_id)),
    )) or 0
    if used >= settings.max_files_per_bundle:
        raise HTTPException(status_code=413, detail="bundle file limit exceeded")
    suffix = Path((file.filename or "").replace("\\", "/")).suffix.lower()
    staged_path: str | None = None
    object_key: str | None = None
    with tempfile.NamedTemporaryFile(prefix="align-submission-", suffix=suffix, delete=False) as staged:
        staged_path = staged.name
        digest = hashlib.sha256()
        size = 0
        try:
            while chunk := await file.read(64 * 1024):
                size += len(chunk)
                if size > settings.max_file_bytes:
                    raise HTTPException(status_code=413, detail="file limit exceeded")
                digest.update(chunk)
                staged.write(chunk)
            staged.flush()
        except Exception:
            Path(staged_path).unlink(missing_ok=True)
            raise
    try:
        safe_name, mime, preflight = validate_context_file(
            staged_path, file.filename, file.content_type, settings.max_file_bytes,
            settings.max_image_pixels, settings.max_docx_nodes, settings.max_docx_media,
            settings.max_ooxml_entries, settings.max_ooxml_expanded_bytes,
            settings.max_xlsx_sheets, settings.max_xlsx_nonempty_cells,
            allowed_suffixes={".pdf", ".docx", ".xlsx", ".jpg", ".jpeg", ".png"},
        )
        # Recheck the draft after slow upload validation; stale uploads are removed from quarantine.
        db.refresh(submission)
        if expected_revision != submission.draft_revision:
            raise HTTPException(status_code=409, detail="draft revision changed")
        asset_id = uuid4()
        object_key = f"submission-draft/{submission.id}/{asset_id}"
        with open(staged_path, "rb") as source:
            storage_client(settings).upload_fileobj(
                source, settings.s3_bucket, object_key,
                ExtraArgs={"ContentType": mime, "Metadata": {"sha256": digest.hexdigest()}},
            )
        asset = FileAsset(
            id=asset_id, course_id=assignment.course_id, submission_id=submission.id,
            purpose="submission", uploaded_by=identity.user.id, original_name=safe_name,
            size_bytes=size, mime=mime, sha256=digest.hexdigest(), object_key=object_key,
            validation_state="validated", preflight_json=preflight,
        )
        db.add(asset)
        submission.draft_revision += 1
        submission.revision += 1
        db.add(AuditEvent(actor_user_id=identity.user.id, action="submission.file.uploaded",
                          object_type="file_asset", object_id=asset.id,
                          new_version_refs_json={"submission_id": str(submission.id), "sha256": asset.sha256},
                          request_id=request.state.request_id))
        db.commit()
        db.refresh(asset)
        return {"asset": {"id": str(asset.id), "name": asset.original_name, "mime": asset.mime,
                           "size_bytes": asset.size_bytes, "sha256": asset.sha256,
                           "preflight": asset.preflight_json}, "draft_revision": submission.draft_revision}
    except HTTPException:
        db.rollback()
        if object_key:
            try:
                storage_client(settings).delete_object(Bucket=settings.s3_bucket, Key=object_key)
            except (BotoCoreError, ClientError):
                pass
        raise
    except (BotoCoreError, ClientError, OSError) as exc:
        db.rollback()
        if object_key:
            try:
                storage_client(settings).delete_object(Bucket=settings.s3_bucket, Key=object_key)
            except (BotoCoreError, ClientError):
                pass
        raise HTTPException(status_code=503, detail="storage unavailable") from exc
    finally:
        if staged_path:
            Path(staged_path).unlink(missing_ok=True)
        await file.close()


def _queue_parse_job(db: DbSession, version: SubmissionVersion, assets: list[FileAsset], settings: Settings) -> UUID:
    profiles = {str(asset.id): str(asset.preflight_json.get("parser_profile", settings.parser_profile_document)) for asset in assets}
    resources = {profile: register_manifest(db, "parser_resource", profile_payload(settings, profile)) for profile in set(profiles.values())}
    parser_manifest = register_manifest(db, "parser", {
        "schema_version": 1, "manifest_id": "b3-shared-source-parser-v1", "namespace": "submission",
        "routes": {key: {"id": str(value.id), "sha256": value.sha256} for key, value in resources.items()},
    })
    fingerprint = canonical_hash({"submission_version_id": str(version.id), "manifest_hash": version.manifest_hash,
                                  "parser_manifest_sha256": parser_manifest.sha256})
    artifacts: list[str] = []
    for asset in assets:
        profile = profiles[str(asset.id)]
        resource = resources[profile]
        artifact_fingerprint = canonical_hash({"submission_version": str(version.id), "sha256": asset.sha256,
                                                "parser_manifest": parser_manifest.sha256, "resource": resource.sha256})
        artifact = ParseArtifact(
            file_asset_id=asset.id, submission_version_id=version.id,
            fingerprint=artifact_fingerprint,
            owner_scope_hash=canonical_hash({"submission_version_id": str(version.id), "file_id": str(asset.id)}),
            parser_manifest_id=parser_manifest.id, parser_resource_manifest_id=resource.id,
            status="queued", limitations_json=[],
        )
        db.add(artifact)
        db.flush()
        artifacts.append(str(artifact.id))
    job_id = uuid4()
    db.add(Job(
        id=job_id, type="submission_parse", subject_id=version.id,
        scope_json={"submission_version_id": str(version.id), "file_ids": [str(asset.id) for asset in assets],
                    "parse_artifact_ids": artifacts, "parser_profiles": profiles,
                    "parser_manifest_id": str(parser_manifest.id),
                    "parser_resources": {key: {"id": str(value.id), "sha256": value.sha256} for key, value in resources.items()},
                    "provider_mode": "off"},
        input_fingerprint=fingerprint, status="queued", stage="parse_submission",
        job_limits_json={"max_active_seconds": settings.job_max_active_seconds, "max_calls": 0,
                         "max_vision_calls": 0, "max_text_input_tokens": 0,
                         "max_text_output_tokens": 0, "budget_usd": "0"},
    ))
    stage_hash = hashlib.sha256(f"{fingerprint}:parse_submission".encode("ascii")).hexdigest()
    db.add(JobStage(job_id=job_id, stage_name="parse_submission", input_hash=stage_hash,
                    status="queued", output_refs_json={}, attempt_count=0))
    db.add(OutboxEvent(event_type="job.parse_submission", subject_id=job_id,
                       payload_json={"job_id": str(job_id)}, next_attempt_at=datetime.now(UTC)))
    return job_id


@router.post("/submissions/{submission_id}/finalize", status_code=201)
def finalize_submission(
    submission_id: UUID,
    body: FinalizeSubmission,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    route = f"/api/v1/submissions/{submission_id}/finalize"
    cached = replay_or_lock(db, identity.user.id, "POST", route, idempotency_key, body.model_dump(mode="json"))
    if cached is not None:
        db.commit()
        return cached
    submission = db.scalar(select(Submission).where(Submission.id == submission_id).with_for_update())
    if submission is None:
        raise HTTPException(status_code=404, detail="submission not found")
    assignment, role = _submission_access(db, submission, identity, require_current_membership=True)
    if role != "student":
        raise HTTPException(status_code=403, detail="students finalize their own submission")
    version = db.get(AssignmentVersion, assignment.active_version_id) if assignment.active_version_id else None
    if version is None:
        raise HTTPException(status_code=404, detail="assignment not found")
    now = datetime.now(UTC)
    _current_draft_allowed(submission, version, now)
    if body.expected_revision != submission.draft_revision:
        raise HTTPException(status_code=409, detail="draft revision changed")
    files = body.file_ids
    if len(set(files)) != len(files):
        raise HTTPException(status_code=422, detail="duplicate file id")
    order = body.display_order or files
    if len(order) != len(files) or set(order) != set(files):
        raise HTTPException(status_code=422, detail="display order must contain each selected file once")
    assets = db.scalars(select(FileAsset).where(
        FileAsset.id.in_(files), FileAsset.submission_id == submission.id,
        FileAsset.purpose == "submission", FileAsset.validation_state == "validated",
        ~FileAsset.id.in_(select(SubmissionVersionFile.file_asset_id)),
    ).with_for_update()).all()
    if len(assets) != len(files):
        raise HTTPException(status_code=422, detail="one or more files are unavailable")
    page_count = sum(int(asset.preflight_json.get("pages", 1 if asset.mime.startswith("image/") else 0))
                     for asset in assets)
    if page_count > settings.max_pdf_image_pages_per_bundle:
        raise HTTPException(status_code=413, detail="bundle page limit exceeded")
    late = now > version.due_at
    if late and version.late_policy == "reject_after_due" and not (submission.reopen_until and now <= submission.reopen_until):
        raise HTTPException(status_code=409, detail="late submission is closed")
    if submission.draft_assignment_version_id != version.id:
        raise HTTPException(status_code=409, detail="assignment version changed")
    manifest: list[dict[str, Any]] = [
        {"file_id": str(asset.id), "sha256": asset.sha256, "size_bytes": asset.size_bytes, "mime": asset.mime}
        for asset in assets
    ]
    manifest.sort(key=lambda item: str(item["file_id"]))
    manifest_hash = canonical_hash(manifest)
    version_no = int(db.scalar(
        select(func.max(SubmissionVersion.version_no)).where(
            SubmissionVersion.submission_id == submission.id
        )
    ) or 0) + 1
    final_version = SubmissionVersion(
        submission_id=submission.id, version_no=version_no, assignment_version_id=version.id,
        manifest_hash=manifest_hash, manifest_json={"files": manifest, "display_order": [str(item) for item in order]},
        finalized_by=identity.user.id, finalized_at=now, is_late=late,
    )
    db.add(final_version)
    db.flush()
    asset_by_id = {asset.id: asset for asset in assets}
    for position, asset_id in enumerate(order):
        db.add(SubmissionVersionFile(submission_version_id=final_version.id, submission_id=submission.id,
                                     file_asset_id=asset_id, display_order=position))
    if submission.individual_user_id is not None:
        individual = db.get(User, submission.individual_user_id)
        if individual is None:
            raise HTTPException(status_code=409, detail="submission owner is unavailable")
        members: list[User] = [individual]
    else:
        members = list(db.scalars(select(User).join(GroupMembership, GroupMembership.user_id == User.id)
            .join(Enrollment, Enrollment.user_id == User.id)
            .where(GroupMembership.assignment_id == assignment.id, GroupMembership.group_id == submission.group_id,
                   GroupMembership.active.is_(True), Enrollment.course_id == assignment.course_id,
                   Enrollment.role == "student", Enrollment.active.is_(True))
            .order_by(User.id)).all())
        if not members:
            raise HTTPException(status_code=409, detail="group roster is empty")
    for member in members:
        db.add(SubmissionVersionMember(submission_version_id=final_version.id, user_id=member.id,
                                       display_name_snapshot=member.display_name,
                                       email_snapshot=member.normalized_email))
    job_id = _queue_parse_job(db, final_version, [asset_by_id[item] for item in order], settings)
    submission.active_final_version_id = final_version.id
    submission.reopen_until = None
    submission.draft_revision += 1
    submission.revision += 1
    result = {"submission_id": str(submission.id), "version_id": str(final_version.id),
              "version_no": version_no, "manifest_hash": manifest_hash,
              "finalized_at": now.isoformat(), "is_late": late, "job_id": str(job_id),
              "draft_revision": submission.draft_revision}
    db.add(AuditEvent(actor_user_id=identity.user.id, action="submission.finalized", object_type="submission_version",
                      object_id=final_version.id, new_version_refs_json={"manifest_hash": manifest_hash,
                      "assignment_version_id": str(version.id), "is_late": late}, request_id=request.state.request_id))
    store_response(db, identity.user.id, "POST", route, idempotency_key, result)
    db.commit()
    return result


@router.get("/submissions/{submission_id}/status")
def get_submission_status(
    submission_id: UUID,
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
) -> dict[str, Any]:
    submission = db.get(Submission, submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="submission not found")
    _, role = _submission_access(db, submission, identity)
    return _submission_summary(db, submission, staff=role in {"lecturer", "admin"})


@router.get("/assignments/{assignment_id}/submissions")
def list_submissions(
    assignment_id: UUID,
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
    cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> dict[str, Any]:
    assignment = db.get(Assignment, assignment_id)
    if assignment is None:
        raise HTTPException(status_code=404, detail="assignment not found")
    role = _role(db, assignment, identity.user.id)
    if role not in {"admin", "lecturer", "student"}:
        raise HTTPException(status_code=404, detail="assignment not found")
    query = select(Submission).where(Submission.assignment_id == assignment.id)
    if role == "student":
        query = query.where(or_(
            Submission.individual_user_id == identity.user.id,
            Submission.group_id.in_(select(GroupMembership.group_id).where(
                GroupMembership.assignment_id == assignment.id,
                GroupMembership.user_id == identity.user.id,
                GroupMembership.active.is_(True),
            )),
            Submission.id.in_(
                select(SubmissionVersion.submission_id)
                .join(SubmissionVersionMember,
                      SubmissionVersionMember.submission_version_id == SubmissionVersion.id)
                .where(
                    SubmissionVersion.id == Submission.active_final_version_id,
                    SubmissionVersionMember.user_id == identity.user.id,
                )
            ),
        ))
    rows = db.scalars(query.order_by(Submission.created_at, Submission.id).offset(cursor).limit(limit + 1)).all()
    has_more = len(rows) > limit
    page = rows[:limit]
    return {
        "items": [_submission_summary(db, item, staff=role in {"lecturer", "admin"}) for item in page],
        "next_cursor": cursor + limit if has_more else None,
    }


@router.delete("/submissions/{submission_id}/draft-files/{file_id}", status_code=204)
def delete_draft_file(
    submission_id: UUID,
    file_id: UUID,
    expected_revision: Annotated[int, Query(ge=1)],
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    submission = db.scalar(select(Submission).where(Submission.id == submission_id).with_for_update())
    if submission is None:
        raise HTTPException(status_code=404, detail="submission not found")
    assignment, role = _submission_access(db, submission, identity, require_current_membership=True)
    version = db.get(AssignmentVersion, assignment.active_version_id) if assignment.active_version_id else None
    if role != "student" or version is None:
        raise HTTPException(status_code=404, detail="submission not found")
    _current_draft_allowed(submission, version, datetime.now(UTC))
    if expected_revision != submission.draft_revision:
        raise HTTPException(status_code=409, detail="draft revision changed")
    asset = db.scalar(select(FileAsset).where(FileAsset.id == file_id, FileAsset.submission_id == submission.id,
        ~FileAsset.id.in_(select(SubmissionVersionFile.file_asset_id))).with_for_update())
    if asset is None:
        raise HTTPException(status_code=404, detail="draft file not found")
    try:
        storage_client(settings).delete_object(Bucket=settings.s3_bucket, Key=asset.object_key)
    except (BotoCoreError, ClientError) as exc:
        raise HTTPException(status_code=503, detail="storage unavailable") from exc
    db.delete(asset)
    submission.draft_revision += 1
    submission.revision += 1
    db.add(AuditEvent(actor_user_id=identity.user.id, action="submission.draft_file.deleted",
                      object_type="file_asset", object_id=file_id,
                      old_version_refs_json={"sha256": asset.sha256}, request_id=uuid4()))
    db.commit()


@router.post("/submissions/{submission_id}/reopen", status_code=200)
def reopen_submission(
    submission_id: UUID,
    body: ReopenSubmission,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
) -> dict[str, Any]:
    submission = db.scalar(select(Submission).where(Submission.id == submission_id).with_for_update())
    if submission is None:
        raise HTTPException(status_code=404, detail="submission not found")
    assignment, role = _submission_access(db, submission, identity, require_current_membership=True)
    if role not in {"lecturer", "admin"} or submission.active_final_version_id is None:
        raise HTTPException(status_code=404, detail="submission not found")
    if submission.reopen_until is not None and submission.reopen_until > datetime.now(UTC):
        raise HTTPException(status_code=409, detail="submission already has an open draft")
    if submission.revision != body.expected_revision:
        raise HTTPException(status_code=409, detail="submission revision changed")
    version = db.get(AssignmentVersion, body.assignment_version_id)
    now = datetime.now(UTC)
    deadline = body.reopen_until.astimezone(UTC) if body.reopen_until.tzinfo else body.reopen_until.replace(tzinfo=UTC)
    if (version is None or version.assignment_id != assignment.id or version.state != "published"
        or assignment.active_version_id != version.id or version.opens_at > now
        or deadline <= now or not body.reason.strip()):
        raise HTTPException(status_code=422, detail="reopen parameters invalid")
    submission.draft_assignment_version_id = version.id
    submission.reopen_until = deadline
    submission.draft_revision += 1
    submission.revision += 1
    db.add(AuditEvent(actor_user_id=identity.user.id, action="submission.reopened", object_type="submission",
                      object_id=submission.id, new_version_refs_json={"reason": body.reason.strip(),
                      "assignment_version_id": str(version.id), "reopen_until": deadline.isoformat()},
                      request_id=request.state.request_id))
    db.commit()
    return {"submission_id": str(submission.id), "draft_revision": submission.draft_revision,
            "revision": submission.revision, "reopen_until": deadline.isoformat(),
            "assignment_version_id": str(version.id)}


def _valid_bbox(bbox: Any, width: float, height: float) -> bool:
    if not isinstance(bbox, list) or len(bbox) != 4:
        return False
    try:
        x1, y1, x2, y2 = (float(value) for value in bbox)
    except (TypeError, ValueError, OverflowError):
        return False
    return (
        all(math.isfinite(value) for value in (x1, y1, x2, y2))
        and 0 <= x1 <= x2 <= width
        and 0 <= y1 <= y2 <= height
    )


def _xlsx_range_within_bounds(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]*)(?::([A-Z]{1,3})([1-9][0-9]*))?", value)
    if match is None:
        return False

    def column_number(column: str) -> int:
        number = 0
        for character in column:
            number = number * 26 + ord(character) - ord("A") + 1
        return number

    col1, row1, col2, row2 = match.groups()
    start = column_number(col1), int(row1)
    end = column_number(col2 or col1), int(row2 or row1)
    return 1 <= start[0] <= end[0] <= 16_384 and 1 <= start[1] <= end[1] <= 1_048_576


def _verify_source_anchor(anchor: dict[str, Any], asset: FileAsset) -> bool:
    if anchor.get("format") == "file":
        note = anchor.get("locator_note")
        return isinstance(note, str) and 1 <= len(note.strip()) <= 240 and not any(ord(char) < 32 for char in note)
    if anchor.get("format") == "pdf":
        page = anchor.get("page")
        sizes = asset.preflight_json.get("page_sizes", [])
        if not isinstance(page, int) or page < 1 or page > int(asset.preflight_json.get("pages", 0)):
            return False
        bbox = anchor.get("bbox")
        if bbox is None:
            return True
        if page > len(sizes):
            return False
        width, height = sizes[page - 1]
        return _valid_bbox(bbox, float(width), float(height))
    if anchor.get("format") == "image":
        width, height = int(asset.preflight_json.get("width", 0)), int(asset.preflight_json.get("height", 0))
        bbox = anchor.get("bbox")
        if (anchor.get("width"), anchor.get("height")) != (width, height):
            return False
        return _valid_bbox(bbox, width, height)
    if anchor.get("format") == "xlsx":
        sheet_name = anchor.get("sheet_name")
        sheets = asset.preflight_json.get("sheets", [])
        valid_sheets = {item.get("name") for item in sheets if isinstance(item, dict)}
        part = anchor.get("part")
        package_parts = asset.preflight_json.get("xlsx_package_parts", [])
        return (
            isinstance(sheet_name, str) and sheet_name in valid_sheets
            and _xlsx_range_within_bounds(anchor.get("cell_range"))
            and (part is None or isinstance(part, str) and part in package_parts)
        )
    if anchor.get("format") == "docx":
        part = anchor.get("part")
        allowed = set(asset.preflight_json.get("docx_package_parts", []))
        structural = anchor.get("structural_path")
        item_kind = anchor.get("item_kind")
        item_index = anchor.get("item_index")
        return (
            isinstance(part, str) and part in allowed
            and isinstance(structural, str) and structural.startswith("/") and ".." not in structural
            and item_kind in {"paragraph", "table", "cell", "media"}
            and isinstance(item_index, int) and not isinstance(item_index, bool) and item_index >= 0
        )
    return False


def _verified_object(db: DbSession, asset: FileAsset, settings: Settings) -> Any:
    staged = tempfile.TemporaryFile()
    try:
        response = storage_client(settings).get_object(Bucket=settings.s3_bucket, Key=asset.object_key)
        body = response["Body"]
        digest = hashlib.sha256()
        size = 0
        try:
            for chunk in body.iter_chunks(chunk_size=64 * 1024):
                if chunk:
                    size += len(chunk)
                    if size > min(asset.size_bytes, settings.max_file_bytes):
                        break
                    digest.update(chunk)
                    staged.write(chunk)
        finally:
            body.close()
        if size != asset.size_bytes or digest.hexdigest() != asset.sha256:
            artifacts = db.scalars(select(ParseArtifact).where(ParseArtifact.file_asset_id == asset.id).with_for_update()).all()
            for artifact in artifacts:
                artifact.status = "failed"
                artifact.limitations_json = [*artifact.limitations_json, {"code": "source_integrity_failed", "message": "Hash sumber tidak cocok; artefak parsing tidak dapat dipakai."}]
            db.commit()
            staged.close()
            raise HTTPException(status_code=409, detail="source integrity check failed")
        staged.seek(0)
        return staged
    except HTTPException:
        raise
    except (BotoCoreError, ClientError, OSError) as exc:
        staged.close()
        raise HTTPException(status_code=503, detail="storage unavailable") from exc


@router.get("/files/{file_id}/content")
def get_submission_content(
    file_id: UUID,
    request: Request,
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    row = db.execute(
        select(FileAsset, SubmissionVersion, Submission)
        .join(SubmissionVersionFile, SubmissionVersionFile.file_asset_id == FileAsset.id)
        .join(SubmissionVersion, SubmissionVersion.id == SubmissionVersionFile.submission_version_id)
        .join(Submission, Submission.id == SubmissionVersion.submission_id)
        .where(FileAsset.id == file_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="file not found")
    asset, version, submission = row
    assignment, role = _submission_access(db, submission, identity, version_id=version.id)
    if role == "student":
        if db.scalar(select(SubmissionVersionMember.user_id).where(
            SubmissionVersionMember.submission_version_id == version.id,
            SubmissionVersionMember.user_id == identity.user.id,
        )) is None:
            raise HTTPException(status_code=404, detail="file not found")
    if source_purged(db, file_asset_id=asset.id):
        raise_source_purged()
    if asset.validation_state != "validated":
        raise HTTPException(status_code=404, detail="file not found")
    staged = _verified_object(db, asset, settings)
    range_header = request.headers.get("range")
    start, end, status_code = 0, asset.size_bytes - 1, 200
    if range_header:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
        if not match or not (match.group(1) or match.group(2)):
            staged.close()
            raise HTTPException(status_code=416, detail="range not satisfiable", headers={"Content-Range": f"bytes */{asset.size_bytes}"})
        first, last = match.groups()
        if first:
            start = int(first)
            end = min(int(last), asset.size_bytes - 1) if last else asset.size_bytes - 1
        else:
            length = int(last)
            start, end = max(0, asset.size_bytes - length), asset.size_bytes - 1
        if start >= asset.size_bytes or end < start:
            staged.close()
            raise HTTPException(status_code=416, detail="range not satisfiable", headers={"Content-Range": f"bytes */{asset.size_bytes}"})
        status_code = 206
    staged.seek(start)
    length = end - start + 1

    def content() -> Any:
        remaining = length
        try:
            while remaining:
                chunk = staged.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
        finally:
            staged.close()

    headers = {
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(asset.original_name)}",
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
        "Accept-Ranges": "bytes", "Content-Length": str(length), "ETag": f'"{asset.sha256}"',
    }
    if status_code == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{asset.size_bytes}"
    return StreamingResponse(content(), status_code=status_code, media_type=asset.mime, headers=headers)


@router.get("/evidence/{evidence_id}")
def get_submission_evidence(
    evidence_id: UUID,
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    evidence = db.get(EvidenceItem, evidence_id)
    if evidence is None or evidence.submission_version_id is None:
        raise HTTPException(status_code=404, detail="evidence not found")
    row = db.execute(select(SubmissionVersion, Submission).join(
        Submission, Submission.id == SubmissionVersion.submission_id
    ).where(SubmissionVersion.id == evidence.submission_version_id)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="evidence not found")
    version, submission = row
    _assignment, role = _submission_access(db, submission, identity, version_id=version.id)
    if role != "lecturer":
        raise HTTPException(status_code=404, detail="evidence not found")
    asset = db.get(FileAsset, evidence.file_asset_id)
    unit = db.get(SourceUnit, evidence.source_unit_id)
    if asset is not None and source_purged(db, file_asset_id=asset.id):
        raise_source_purged()
    if asset is None or unit is None or evidence.source_sha256 != asset.sha256 or evidence.anchor_json != unit.anchor_json or not _verify_source_anchor(unit.anchor_json, asset):
        raise HTTPException(status_code=409, detail="source integrity check failed")
    _verified_object(db, asset, settings).close()
    return {"id": str(evidence.id), "type": evidence.type, "source_unit_id": str(unit.id),
            "file_id": str(asset.id), "source_sha256": evidence.source_sha256,
            "anchor": evidence.anchor_json, "content": evidence.content_original_json,
            "parse_status": evidence.parse_status, "limitations": evidence.limitations_json,
            "original_url": f"/api/v1/files/{asset.id}/content"}


@router.get("/files/{file_id}/source-units")
def list_submission_source_units(
    file_id: UUID,
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
    cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    row = db.execute(select(FileAsset, SubmissionVersion, Submission)
        .join(SubmissionVersionFile, SubmissionVersionFile.file_asset_id == FileAsset.id)
        .join(SubmissionVersion, SubmissionVersion.id == SubmissionVersionFile.submission_version_id)
        .join(Submission, Submission.id == SubmissionVersion.submission_id)
        .where(FileAsset.id == file_id)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="file not found")
    asset, version, submission = row
    assignment, role = _submission_access(db, submission, identity, version_id=version.id)
    if role not in {"lecturer", "admin"}:
        raise HTTPException(status_code=404, detail="file not found")
    if source_purged(db, file_asset_id=asset.id):
        raise_source_purged()
    artifact = db.scalar(select(ParseArtifact).where(
        ParseArtifact.file_asset_id == asset.id, ParseArtifact.submission_version_id == version.id
    ))
    if artifact is None:
        return {"items": [], "next_cursor": None, "status": "queued", "parse_status": "queued", "limitations": [],
                "file": {"id": str(asset.id), "name": asset.original_name, "mime": asset.mime,
                         "sha256": asset.sha256, "preflight": asset.preflight_json,
                         "content_url": f"/api/v1/files/{asset.id}/content"}}
    units = db.scalars(select(SourceUnit).where(SourceUnit.parse_artifact_id == artifact.id)
        .order_by(SourceUnit.source_order, SourceUnit.id).offset(cursor).limit(limit + 1)).all()
    has_more = len(units) > limit
    page = units[:limit]
    return {"items": [{"id": str(unit.id), "unit_type": unit.unit_type, "anchor": unit.anchor_json,
                        "parse_status": unit.parse_status, "limitations": unit.limitations_json}
                       for unit in page],
            "next_cursor": cursor + limit if has_more else None,
            "parse_status": artifact.status, "limitations": artifact.limitations_json,
            "file": {"id": str(asset.id), "name": asset.original_name, "mime": asset.mime,
                     "sha256": asset.sha256, "preflight": asset.preflight_json,
                     "content_url": f"/api/v1/files/{asset.id}/content"}}


@router.get("/files/{file_id}/spreadsheet-cells")
def list_submission_spreadsheet_cells(
    file_id: UUID,
    sheet_name: Annotated[str, Query(min_length=1, max_length=31)],
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict[str, Any]:
    row = db.execute(select(FileAsset, SubmissionVersion, Submission)
        .join(SubmissionVersionFile, SubmissionVersionFile.file_asset_id == FileAsset.id)
        .join(SubmissionVersion, SubmissionVersion.id == SubmissionVersionFile.submission_version_id)
        .join(Submission, Submission.id == SubmissionVersion.submission_id)
        .where(FileAsset.id == file_id, FileAsset.validation_state == "validated")).first()
    if row is None:
        raise HTTPException(status_code=404, detail="file not found")
    asset, version, submission = row
    _assignment, role = _submission_access(db, submission, identity, version_id=version.id)
    if role != "lecturer" or asset.mime != "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        raise HTTPException(status_code=404, detail="spreadsheet source not found")
    if source_purged(db, file_asset_id=asset.id):
        raise_source_purged()
    sheets = asset.preflight_json.get("sheets", [])
    sheet = next((item for item in sheets if isinstance(item, dict) and item.get("name") == sheet_name), None)
    if sheet is None:
        raise HTTPException(status_code=404, detail="spreadsheet sheet not found")
    _verified_object(db, asset, settings).close()
    rows = db.execute(
        select(EvidenceItem, SourceUnit)
        .join(SourceUnit, SourceUnit.id == EvidenceItem.source_unit_id)
        .join(ParseArtifact, ParseArtifact.id == SourceUnit.parse_artifact_id)
        .where(
            EvidenceItem.file_asset_id == asset.id,
            EvidenceItem.submission_version_id == version.id,
            EvidenceItem.type == "spreadsheet_cell",
            EvidenceItem.source_sha256 == asset.sha256,
            EvidenceItem.anchor_json["sheet_name"].as_string() == sheet_name,
            ParseArtifact.file_asset_id == asset.id,
            ParseArtifact.submission_version_id == version.id,
        )
        .order_by(
            EvidenceItem.content_original_json["row"].as_integer(),
            EvidenceItem.content_original_json["column"].as_integer(),
        )
        .offset(cursor).limit(limit + 1)
    ).all()
    has_more = len(rows) > limit
    page = rows[:limit]
    items: list[dict[str, Any]] = []
    for evidence, unit in page:
        if evidence.anchor_json != unit.anchor_json or not _verify_source_anchor(unit.anchor_json, asset):
            raise HTTPException(status_code=409, detail="source integrity check failed")
        items.append({"source_unit_id": str(unit.id), "anchor": unit.anchor_json,
                      "content": evidence.content_original_json, "parse_status": evidence.parse_status,
                      "limitations": evidence.limitations_json})
    return {"sheet": {"name": sheet_name, "state": sheet.get("state", "visible")},
            "items": items, "next_cursor": cursor + limit if has_more else None,
            "total_preflight_nonempty_cells": asset.preflight_json.get("nonempty_cells", 0)}


@router.get("/source-units/{unit_id}/render")
def render_submission_source_unit(
    unit_id: UUID,
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    row = db.execute(select(SourceUnit, ParseArtifact, FileAsset, SubmissionVersion, Submission)
        .join(ParseArtifact, ParseArtifact.id == SourceUnit.parse_artifact_id)
        .join(FileAsset, FileAsset.id == ParseArtifact.file_asset_id)
        .join(SubmissionVersion, SubmissionVersion.id == ParseArtifact.submission_version_id)
        .join(Submission, Submission.id == SubmissionVersion.submission_id)
        .where(SourceUnit.id == unit_id)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="source unit not found")
    unit, artifact, asset, version, submission = row
    _assignment, role = _submission_access(db, submission, identity, version_id=version.id)
    if role != "lecturer":
        raise HTTPException(status_code=404, detail="source unit not found")
    if source_purged(db, file_asset_id=asset.id):
        raise_source_purged()
    if (
        artifact.status not in {"parsed", "partially_parsed", "unreadable", "unsupported", "failed", "blocked"}
        or not _verify_source_anchor(unit.anchor_json, asset)
    ):
        raise HTTPException(status_code=409, detail="source anchor unavailable")
    evidence = db.scalars(select(EvidenceItem).where(EvidenceItem.source_unit_id == unit.id,
        EvidenceItem.submission_version_id == version.id).order_by(EvidenceItem.created_at)).all()
    if any(item.source_sha256 != asset.sha256 or item.anchor_json != unit.anchor_json for item in evidence):
        raise HTTPException(status_code=409, detail="source integrity check failed")
    _verified_object(db, asset, settings).close()
    return {"source_unit": {"id": str(unit.id), "type": unit.unit_type, "anchor": unit.anchor_json,
                            "parse_status": unit.parse_status, "limitations": unit.limitations_json},
            "evidence": [{"id": str(item.id), "type": item.type, "content": item.content_original_json,
                          "parse_status": item.parse_status, "limitations": item.limitations_json}
                         for item in evidence],
            "file": {"id": str(asset.id), "name": asset.original_name, "mime": asset.mime,
                     "sha256": asset.sha256, "content_url": f"/api/v1/files/{asset.id}/content"}}


@router.get("/jobs/{job_id}")
def get_submission_job(
    job_id: UUID,
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
) -> dict[str, Any]:
    job = db.get(Job, job_id)
    if job is None or job.type != "submission_parse":
        raise HTTPException(status_code=404, detail="job not found")
    version = db.get(SubmissionVersion, job.subject_id)
    submission = db.get(Submission, version.submission_id) if version else None
    if submission is None or version is None:
        raise HTTPException(status_code=404, detail="job not found")
    _, role = _submission_access(db, submission, identity, version_id=version.id)
    result: dict[str, Any] = {
        "id": str(job.id),
        "status": job.status,
        "completion_kind": job.completion_kind,
        "retry_scheduled": _retry_scheduled(job),
    }
    if role in {"lecturer", "admin"}:
        result.update({"stage": job.stage, "last_error_code": job.last_error_code,
                       "submission_version_id": str(version.id),
                       "manifest_hash": version.manifest_hash})
    return result


@router.post("/jobs/{job_id}/retry", status_code=202)
def retry_submission_job(
    job_id: UUID,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    route = f"/api/v1/jobs/{job_id}/retry"
    cached = replay_or_lock(db, identity.user.id, "POST", route, idempotency_key, {})
    if cached is not None:
        db.commit()
        return cached
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job is None or job.type != "submission_parse":
        raise HTTPException(status_code=404, detail="job not found")
    version = db.get(SubmissionVersion, job.subject_id)
    submission = db.get(Submission, version.submission_id) if version else None
    if submission is None or version is None:
        raise HTTPException(status_code=404, detail="job not found")
    assignment, role = _submission_access(db, submission, identity)
    if role not in {"lecturer", "admin"}:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status != "blocked" or job.last_error_code not in {
        "parser_capacity_unavailable", "parser_storage_unavailable"
    }:
        raise HTTPException(status_code=409, detail="job has no recoverable parser stage")
    reservations = db.scalars(select(ProcessingTimeReservation).where(
        ProcessingTimeReservation.job_id == job.id,
        ProcessingTimeReservation.state.in_(["held", "settled"]),
    )).all()
    consumed_ms = sum(item.actual_active_ms if item.actual_active_ms is not None else item.reserved_active_ms for item in reservations)
    active_cap_ms = int(job.job_limits_json.get("max_active_seconds", settings.job_max_active_seconds)) * 1000
    if consumed_ms >= active_cap_ms:
        raise HTTPException(status_code=409, detail="aggregate parser time cap is exhausted")
    course = db.scalar(select(Course).where(Course.id == assignment.course_id).with_for_update())
    if course is None:
        raise HTTPException(status_code=404, detail="job not found")
    retry_count = db.scalar(select(func.count(AuditEvent.id)).where(
        AuditEvent.action == "submission.parse.retry",
        AuditEvent.occurred_at >= datetime.now(UTC) - timedelta(hours=1),
        AuditEvent.new_version_refs_json["course_id"].as_string() == str(course.id),
    )) or 0
    if int(retry_count) >= 10:
        raise HTTPException(status_code=429, detail="course parser retry limit exceeded")
    stage = db.scalar(select(JobStage).where(JobStage.job_id == job.id,
        JobStage.stage_name == "parse_submission").with_for_update())
    if stage is None:
        raise HTTPException(status_code=409, detail="parser stage is unavailable")
    job.status = "queued"
    job.last_error_code = None
    job.lease_until = None
    job.lease_fence += 1
    stage.status = "queued"
    stage.failure_code = None
    stage.finished_at = None
    db.add(OutboxEvent(event_type="job.parse_submission", subject_id=job.id,
                       payload_json={"job_id": str(job.id)}, next_attempt_at=datetime.now(UTC)))
    result = {"job_id": str(job.id), "status": "queued", "remaining_active_ms": active_cap_ms - consumed_ms}
    db.add(AuditEvent(actor_user_id=identity.user.id, action="submission.parse.retry", object_type="job",
                      object_id=job.id, new_version_refs_json={"course_id": str(course.id),
                      "submission_version_id": str(version.id)}, request_id=request.state.request_id))
    store_response(db, identity.user.id, "POST", route, idempotency_key, result)
    db.commit()
    return result
