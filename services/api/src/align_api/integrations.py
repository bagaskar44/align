from __future__ import annotations

import asyncio
import tempfile
import time
import unicodedata
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Annotated, Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BeforeValidator, Field, StrictBool, StrictInt, StrictStr
from sqlalchemy import func, select, text
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
    FileAsset,
    GroupMembership,
    LmsCourseAssignmentMapping,
    LmsGroupMapping,
    LmsImportEvent,
    LtiCoursePermission,
    LtiIdentityBinding,
    LtiPlatform,
    Submission,
    SubmissionVersion,
    SubmissionVersionFile,
    SubmissionVersionMember,
    User,
)
from align_api.moodle_adapter import (
    MoodleAdapterError,
    MoodleWebService,
    assignment_attempts,
    configured_moodle_token,
    submission_files,
    validate_file_url,
)
from align_api.storage import storage_client
from align_api.submissions import _queue_parse_job

router = APIRouter(tags=["moodle-integrations"])
UUIDInput = Annotated[
    UUID,
    BeforeValidator(lambda value: UUID(value) if isinstance(value, str) else value),
]


class MappingCreate(StrictRequest):
    platform_id: UUIDInput
    lti_context_id: StrictStr = Field(min_length=1, max_length=500)
    lti_resource_link_id: StrictStr = Field(min_length=1, max_length=500)
    moodle_course_id: StrictInt = Field(ge=1)
    moodle_assignment_id: StrictInt = Field(ge=1)
    local_course_id: UUIDInput
    assignment_version_id: UUIDInput


class MappingPatch(StrictRequest):
    expected_revision: StrictInt = Field(ge=1)
    active: StrictBool | None = None
    lti_context_id: StrictStr | None = Field(default=None, min_length=1, max_length=500)
    lti_resource_link_id: StrictStr | None = Field(default=None, min_length=1, max_length=500)
    moodle_course_id: StrictInt | None = Field(default=None, ge=1)
    moodle_assignment_id: StrictInt | None = Field(default=None, ge=1)
    local_course_id: UUIDInput | None = None
    assignment_version_id: UUIDInput | None = None


class GroupMappingCreate(StrictRequest):
    moodle_group_id: StrictInt = Field(ge=1)
    local_group_id: UUIDInput


class GroupMappingPatch(StrictRequest):
    expected_revision: StrictInt = Field(ge=1)
    active: StrictBool


class ImportRequest(StrictRequest):
    mapping_id: UUIDInput
    expected_mapping_revision: StrictInt = Field(ge=1)
    attempt_id: StrictStr = Field(min_length=1, max_length=240)
    confirmed_owner_user_id: UUIDInput | None = None
    confirmed_roster_user_ids: list[UUIDInput] = Field(default_factory=list, max_length=100)


def _utc_from_timestamp(value: Any) -> datetime:
    if type(value) is not int or value <= 0:
        raise HTTPException(
            status_code=409, detail="Moodle attempt has no verified submission time"
        )
    try:
        return datetime.fromtimestamp(value, UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="Moodle attempt time is invalid") from exc


def _moodle_client(settings: Settings, platform: LtiPlatform) -> MoodleWebService:
    parsed = urlsplit(platform.moodle_origin.rstrip("/"))
    allowed_origin = f"https://{(parsed.hostname or '').lower()}" + (
        f":{parsed.port}" if parsed.port else ""
    )
    if allowed_origin not in settings.lti_allowed_origin_set:
        raise MoodleAdapterError("moodle_origin_not_allowlisted")
    return MoodleWebService(
        origin=allowed_origin,
        token=configured_moodle_token(settings),
        timeout_seconds=settings.moodle_ws_timeout_seconds,
        max_response_bytes=settings.moodle_ws_max_response_bytes,
    )


async def _verify_moodle_assignment(
    client: MoodleWebService, mapping: LmsCourseAssignmentMapping
) -> None:
    await client.verify_functions({"mod_assign_get_assignments", "mod_assign_get_submissions"})
    payload = await client.call(
        "mod_assign_get_assignments",
        courseids=[mapping.moodle_course_id],
    )
    courses = payload.get("courses") if isinstance(payload, dict) else None
    if not isinstance(courses, list):
        raise MoodleAdapterError("moodle_assignment_response_invalid")
    matches = [
        assignment
        for course in courses
        if isinstance(course, dict) and course.get("id") == mapping.moodle_course_id
        for assignment in (course.get("assignments") or [])
        if isinstance(assignment, dict) and assignment.get("id") == mapping.moodle_assignment_id
    ]
    if len(matches) != 1:
        raise MoodleAdapterError("moodle_assignment_mapping_mismatch")


async def _final_attempts(
    client: MoodleWebService, mapping: LmsCourseAssignmentMapping
) -> list[dict[str, Any]]:
    await _verify_moodle_assignment(client, mapping)
    payload = await client.call(
        "mod_assign_get_submissions", assignmentids=[mapping.moodle_assignment_id]
    )
    return assignment_attempts(payload, mapping.moodle_assignment_id)


def _candidate_json(
    attempt: dict[str, Any],
    platform: LtiPlatform,
    *,
    owner: dict[str, str] | None = None,
    group: dict[str, Any] | None = None,
    mapping_error: str | None = None,
) -> dict[str, Any]:
    group_id = attempt.get("groupid")
    user_id = attempt.get("userid")
    submitted_at = attempt.get("timemodified")
    files: list[dict[str, Any]] = []
    blocked_reason: str | None = mapping_error
    try:
        for item in submission_files(attempt):
            validate_file_url(str(item.get("fileurl", "")), platform.moodle_origin)
            files.append(
                {
                    "name": PurePosixPath(str(item["filename"])).name,
                    "size_bytes": item["filesize"],
                    "mime": item.get("mimetype"),
                }
            )
    except MoodleAdapterError as exc:
        blocked_reason = blocked_reason or exc.code
    result = {
        "attempt_id": attempt["attemptid"],
        "attempt_number": attempt["attemptnumber"],
        "moodle_submission_id": attempt["id"],
        "moodle_user_id": user_id if type(user_id) is int and user_id > 0 else None,
        "moodle_group_id": group_id if type(group_id) is int and group_id > 0 else None,
        "status": "submitted",
        "submitted_at": datetime.fromtimestamp(submitted_at, UTC).isoformat()
        if type(submitted_at) is int and submitted_at > 0
        else None,
        "files": files,
        "blocked_reason": blocked_reason,
    }
    if owner is not None:
        result["owner"] = owner
    if group is not None:
        result["group"] = group
    return result


def _attempt_metadata(attempt: dict[str, Any]) -> str:
    file_meta: list[dict[str, Any]] = []
    try:
        for item in submission_files(attempt):
            file_meta.append(
                {
                    "filename": item.get("filename"),
                    "filesize": item.get("filesize"),
                    "fileurl": item.get("fileurl"),
                    "timemodified": item.get("timemodified"),
                }
            )
    except MoodleAdapterError:
        file_meta = []
    return canonical_hash(
        {
            "id": attempt.get("id"),
            "attemptnumber": attempt.get("attemptnumber"),
            "userid": attempt.get("userid"),
            "groupid": attempt.get("groupid"),
            "status": attempt.get("status"),
            "timemodified": attempt.get("timemodified"),
            "files": file_meta,
        }
    )


def _import_receipt(event: LmsImportEvent) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "status": event.status,
        "attempt_id": event.moodle_attempt_id,
        "submission_id": str(event.local_submission_id) if event.local_submission_id else None,
        "submission_version_id": str(event.submission_version_id)
        if event.submission_version_id
        else None,
        "job_id": str(event.job_id) if event.job_id else None,
        "mapping_revision": event.mapping_revision,
        "submitted_at": event.moodle_submitted_at.isoformat()
        if event.moodle_submitted_at
        else None,
        "imported_at": event.created_at.isoformat() if event.created_at else None,
        "detail": event.safe_detail,
    }


def _integration_error(exc: MoodleAdapterError) -> HTTPException:
    if exc.code in {"moodle_web_service_not_configured", "moodle_origin_not_allowlisted"}:
        return HTTPException(status_code=503, detail=exc.code)
    if exc.code in {"moodle_service_unavailable", "moodle_response_too_large"}:
        return HTTPException(status_code=503, detail=exc.code)
    if exc.code == "moodle_operation_timeout":
        return HTTPException(status_code=504, detail=exc.code)
    return HTTPException(status_code=409, detail=exc.code)


async def _load_current_attempts(
    settings: Settings, platform: LtiPlatform, mapping: LmsCourseAssignmentMapping
) -> tuple[MoodleWebService, list[dict[str, Any]]]:
    client = _moodle_client(settings, platform)
    return client, await _final_attempts(client, mapping)


async def _external_roster(
    client: MoodleWebService,
    mapping: LmsCourseAssignmentMapping,
    attempt: dict[str, Any],
) -> tuple[int | None, list[int]]:
    course_users = await client.call(
        "core_enrol_get_enrolled_users", courseid=mapping.moodle_course_id
    )
    if not isinstance(course_users, list):
        raise MoodleAdapterError("moodle_enrollment_response_invalid")
    enrolled_ids: set[int] = set()
    for item in course_users:
        if not isinstance(item, dict) or type(item.get("id")) is not int:
            raise MoodleAdapterError("moodle_enrollment_response_invalid")
        if item.get("suspended") is not True:
            enrolled_ids.add(item["id"])

    moodle_group_id = attempt.get("groupid")
    if type(moodle_group_id) is not int or moodle_group_id <= 0:
        user_id = attempt.get("userid")
        if type(user_id) is not int or user_id <= 0 or user_id not in enrolled_ids:
            raise HTTPException(status_code=409, detail="Moodle owner is not actively enrolled")
        return None, [user_id]

    await client.verify_functions({"core_group_get_course_groups", "core_group_get_group_members"})
    groups_response = await client.call(
        "core_group_get_course_groups", courseid=mapping.moodle_course_id
    )
    groups = groups_response.get("groups") if isinstance(groups_response, dict) else groups_response
    if not isinstance(groups, list) or not any(
        isinstance(group, dict) and group.get("id") == moodle_group_id for group in groups
    ):
        raise HTTPException(status_code=409, detail="Moodle group is not in the mapped course")
    members_response = await client.call("core_group_get_group_members", groupids=[moodle_group_id])
    member_groups = (
        members_response.get("groups") if isinstance(members_response, dict) else members_response
    )
    if not isinstance(member_groups, list):
        raise MoodleAdapterError("moodle_group_response_invalid")
    matching = [
        group
        for group in member_groups
        if isinstance(group, dict) and group.get("id") == moodle_group_id
    ]
    if len(matching) != 1 or not isinstance(matching[0].get("users"), list):
        raise HTTPException(status_code=409, detail="Moodle group roster is unavailable")
    member_ids: list[int] = []
    for member in matching[0]["users"]:
        if not isinstance(member, dict) or type(member.get("id")) is not int:
            raise MoodleAdapterError("moodle_group_response_invalid")
        if member["id"] not in enrolled_ids:
            raise HTTPException(status_code=409, detail="Moodle group has a non-enrolled member")
        member_ids.append(member["id"])
    if not member_ids or len(member_ids) != len(set(member_ids)):
        raise HTTPException(status_code=409, detail="Moodle group roster is ambiguous")
    attempt_user_id = attempt.get("userid")
    if type(attempt_user_id) is int and attempt_user_id > 0 and attempt_user_id not in member_ids:
        raise HTTPException(status_code=409, detail="Moodle attempt owner does not match its group")
    return moodle_group_id, sorted(member_ids)


def _resolve_roster(
    db: DbSession,
    platform_id: UUID,
    moodle_user_ids: list[int],
    confirmed_user_ids: list[UUID],
    course_id: UUID,
    assignment_id: UUID,
    local_group_id: UUID | None,
    *,
    lock: bool = False,
) -> list[User]:
    if len(confirmed_user_ids) != len(set(confirmed_user_ids)):
        raise HTTPException(status_code=422, detail="duplicate user in confirmed roster")
    binding_query = select(LtiIdentityBinding).where(
        LtiIdentityBinding.platform_id == platform_id,
        LtiIdentityBinding.moodle_user_id.in_(moodle_user_ids),
        LtiIdentityBinding.active.is_(True),
    )
    bindings = db.scalars(binding_query.with_for_update() if lock else binding_query).all()
    by_moodle_id = {binding.moodle_user_id: binding for binding in bindings}
    if len(by_moodle_id) != len(moodle_user_ids) or set(by_moodle_id) != set(moodle_user_ids):
        raise HTTPException(status_code=409, detail="Moodle roster contains an unlinked identity")
    expected_user_ids = {binding.user_id for binding in bindings}
    if set(confirmed_user_ids) != expected_user_ids:
        raise HTTPException(
            status_code=409, detail="confirmed roster does not match current Moodle roster"
        )
    user_query = select(User).where(User.id.in_(expected_user_ids), User.active.is_(True))
    users = db.scalars(user_query.with_for_update() if lock else user_query).all()
    if len(users) != len(expected_user_ids):
        raise HTTPException(
            status_code=409, detail="confirmed roster contains an unavailable account"
        )
    enrollment_query = select(Enrollment.user_id).where(
        Enrollment.course_id == course_id,
        Enrollment.user_id.in_(expected_user_ids),
        Enrollment.role == "student",
        Enrollment.active.is_(True),
    )
    enrollments = set(
        db.scalars(enrollment_query.with_for_update() if lock else enrollment_query).all()
    )
    if enrollments != expected_user_ids:
        raise HTTPException(
            status_code=409, detail="confirmed roster does not match active ALIGN enrollment"
        )
    if local_group_id is not None:
        membership_query = select(GroupMembership.user_id).where(
            GroupMembership.assignment_id == assignment_id,
            GroupMembership.group_id == local_group_id,
            GroupMembership.user_id.in_(expected_user_ids),
            GroupMembership.active.is_(True),
        )
        memberships = set(
            db.scalars(membership_query.with_for_update() if lock else membership_query).all()
        )
        if memberships != expected_user_ids:
            raise HTTPException(
                status_code=409, detail="confirmed roster does not match active ALIGN group"
            )
    return sorted(users, key=lambda user: str(user.id))


def _safe_filename(value: Any) -> str:
    if not isinstance(value, str):
        raise HTTPException(status_code=409, detail="Moodle file name is invalid")
    name = unicodedata.normalize("NFC", value).strip()
    if (
        not name
        or len(name.encode("utf-8")) > 255
        or PurePosixPath(name).name != name
        or "/" in name
        or "\\" in name
        or any(ord(char) < 32 for char in name)
    ):
        raise HTTPException(status_code=409, detail="Moodle file name is invalid")
    return name


def _upload_object(settings: Settings, path: str, object_key: str, mime: str, digest: str) -> None:
    with Path(path).open("rb") as source:
        storage_client(settings).upload_fileobj(
            source,
            settings.s3_bucket,
            object_key,
            ExtraArgs={"ContentType": mime, "Metadata": {"sha256": digest}},
        )


def _delete_object(settings: Settings, object_key: str) -> None:
    storage_client(settings).delete_object(Bucket=settings.s3_bucket, Key=object_key)


async def _download_validated_files(
    client: MoodleWebService,
    platform: LtiPlatform,
    attempt: dict[str, Any],
    settings: Settings,
) -> list[dict[str, Any]]:
    files = submission_files(attempt)
    if len(files) > settings.max_files_per_bundle:
        raise HTTPException(status_code=413, detail="Moodle bundle file limit exceeded")
    deadline = time.monotonic() + settings.moodle_ws_timeout_seconds
    staged: list[dict[str, Any]] = []
    try:
        for item in files:
            name = _safe_filename(item.get("filename"))
            expected_size = item.get("filesize")
            if type(expected_size) is not int or expected_size < 1:
                raise HTTPException(status_code=409, detail="Moodle file metadata is invalid")
            if expected_size > settings.max_file_bytes:
                raise HTTPException(status_code=413, detail="Moodle file limit exceeded")
            url = validate_file_url(str(item.get("fileurl", "")), platform.moodle_origin)
            suffix = Path(name).suffix.lower()
            with tempfile.NamedTemporaryFile(
                prefix="align-moodle-", suffix=suffix, delete=False
            ) as temp:
                staged_path = temp.name
            staged.append({"path": staged_path})
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HTTPException(status_code=504, detail="Moodle import download timed out")
            size, digest = await client.download_to_path(
                url, staged_path, max_bytes=settings.max_file_bytes, timeout_seconds=remaining
            )
            if size != expected_size:
                raise HTTPException(status_code=409, detail="Moodle file changed during download")
            try:
                safe_name, mime, preflight = validate_context_file(
                    staged_path,
                    name,
                    str(item.get("mimetype") or "application/octet-stream"),
                    settings.max_file_bytes,
                    settings.max_image_pixels,
                    settings.max_docx_nodes,
                    settings.max_docx_media,
                    settings.max_ooxml_entries,
                    settings.max_ooxml_expanded_bytes,
                    settings.max_xlsx_sheets,
                    settings.max_xlsx_nonempty_cells,
                    allowed_suffixes={".pdf", ".docx", ".xlsx", ".jpg", ".jpeg", ".png"},
                )
            except (OSError, ValueError) as exc:
                raise HTTPException(
                    status_code=422, detail="Moodle file failed ALIGN validation"
                ) from exc
            staged[-1].update(
                {
                    "name": safe_name,
                    "mime": mime,
                    "size_bytes": size,
                    "sha256": digest,
                    "preflight": preflight,
                }
            )
        page_count = sum(
            int(item["preflight"].get("pages", 1 if item["mime"].startswith("image/") else 0))
            for item in staged
        )
        if page_count > settings.max_pdf_image_pages_per_bundle:
            raise HTTPException(status_code=413, detail="Moodle bundle page limit exceeded")
        return staged
    except BaseException:
        for item in staged:
            Path(item["path"]).unlink(missing_ok=True)
        raise


@router.get("/integrations/moodle/imports")
async def list_imports(
    mapping_id: UUID,
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    _require_enabled(settings)
    mapping = db.get(LmsCourseAssignmentMapping, mapping_id)
    if mapping is None:
        raise HTTPException(status_code=404, detail="mapping not found")
    _lecturer_course(db, identity, mapping.local_course_id)
    _platform_course_allowed(db, mapping.platform_id, mapping.local_course_id)
    events = db.scalars(
        select(LmsImportEvent)
        .where(LmsImportEvent.mapping_id == mapping.id)
        .order_by(LmsImportEvent.created_at.desc())
        .limit(100)
    ).all()
    try:
        platform = db.get(LtiPlatform, mapping.platform_id)
        if platform is None or not platform.active:
            raise MoodleAdapterError("moodle_platform_disabled")
        client, attempts = await _load_current_attempts(settings, platform, mapping)
        del client
        candidates = []
        version = db.get(AssignmentVersion, mapping.assignment_version_id)
        assignment = db.get(Assignment, version.assignment_id) if version else None
        for attempt in attempts[:100]:
            moodle_user_id = attempt.get("userid")
            moodle_group_id = attempt.get("groupid")
            owner_payload = None
            group_payload = None
            mapping_error = None
            if version is None or assignment is None:
                mapping_error = "published_assignment_unavailable"
            elif version.mode == "individual":
                binding = (
                    db.scalar(
                        select(LtiIdentityBinding).where(
                            LtiIdentityBinding.platform_id == platform.id,
                            LtiIdentityBinding.moodle_user_id == moodle_user_id,
                            LtiIdentityBinding.active.is_(True),
                        )
                    )
                    if type(moodle_user_id) is int
                    else None
                )
                user = db.get(User, binding.user_id) if binding else None
                enrolled = None
                if binding:
                    enrolled = db.scalar(
                        select(Enrollment.id).where(
                            Enrollment.course_id == assignment.course_id,
                            Enrollment.user_id == binding.user_id,
                            Enrollment.role == "student",
                            Enrollment.active.is_(True),
                        )
                    )
                if user is None or not user.active or enrolled is None:
                    mapping_error = "moodle_owner_not_linked_or_enrolled"
                else:
                    owner_payload = {"user_id": str(user.id), "display_name": user.display_name}
            elif type(moodle_group_id) is int and moodle_group_id > 0:
                group_mapping = db.scalar(
                    select(LmsGroupMapping).where(
                        LmsGroupMapping.mapping_id == mapping.id,
                        LmsGroupMapping.moodle_group_id == moodle_group_id,
                        LmsGroupMapping.active.is_(True),
                    )
                )
                local = (
                    db.get(AssignmentGroup, group_mapping.local_group_id) if group_mapping else None
                )
                if local is None or not local.active:
                    mapping_error = "moodle_group_not_mapped"
                else:
                    rows = db.execute(
                        select(User.id, User.display_name)
                        .join(GroupMembership, GroupMembership.user_id == User.id)
                        .join(
                            Enrollment,
                            (Enrollment.user_id == User.id)
                            & (Enrollment.course_id == assignment.course_id),
                        )
                        .where(
                            GroupMembership.assignment_id == assignment.id,
                            GroupMembership.group_id == local.id,
                            GroupMembership.active.is_(True),
                            Enrollment.role == "student",
                            Enrollment.active.is_(True),
                            User.active.is_(True),
                        )
                        .order_by(User.display_name, User.id)
                    ).all()
                    if not rows:
                        mapping_error = "align_group_roster_empty"
                    else:
                        group_payload = {
                            "moodle_group_id": moodle_group_id,
                            "local_group_id": str(local.id),
                            "local_group_name": local.name,
                            "members": [
                                {"user_id": str(row.id), "display_name": row.display_name}
                                for row in rows
                            ],
                        }
            else:
                mapping_error = "moodle_attempt_owner_invalid"
            candidates.append(
                _candidate_json(
                    attempt,
                    platform,
                    owner=owner_payload,
                    group=group_payload,
                    mapping_error=mapping_error,
                )
            )
        candidate_status = "ready"
    except MoodleAdapterError as exc:
        candidates = []
        candidate_status = exc.code
    return {
        "candidate_status": candidate_status,
        "candidates": candidates,
        "items": [_import_receipt(event) for event in events],
    }


@router.get("/integrations/moodle/imports/{import_id}")
def get_import(
    import_id: UUID,
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    _require_enabled(settings)
    event = db.get(LmsImportEvent, import_id)
    mapping = db.get(LmsCourseAssignmentMapping, event.mapping_id) if event else None
    if event is None or mapping is None:
        raise HTTPException(status_code=404, detail="import not found")
    _lecturer_course(db, identity, mapping.local_course_id)
    _platform_course_allowed(db, mapping.platform_id, mapping.local_course_id)
    return _import_receipt(event)


@router.post("/integrations/moodle/imports", status_code=201)
async def import_attempt(
    body: ImportRequest,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    _require_enabled(settings)
    mapping = db.get(LmsCourseAssignmentMapping, body.mapping_id)
    if mapping is None or not mapping.active:
        raise HTTPException(status_code=404, detail="mapping not found")
    _lecturer_course(db, identity, mapping.local_course_id)
    _platform_course_allowed(db, mapping.platform_id, mapping.local_course_id)
    if mapping.revision != body.expected_mapping_revision:
        raise HTTPException(status_code=409, detail="mapping revision mismatch")
    platform = db.get(LtiPlatform, mapping.platform_id)
    version = db.get(AssignmentVersion, mapping.assignment_version_id)
    assignment = db.get(Assignment, version.assignment_id) if version else None
    if (
        platform is None
        or not platform.active
        or version is None
        or assignment is None
        or version.state != "published"
    ):
        raise HTTPException(status_code=409, detail="Moodle mapping is no longer active")
    try:
        client, attempts = await _load_current_attempts(settings, platform, mapping)
    except MoodleAdapterError as exc:
        raise _integration_error(exc) from exc
    selected = [attempt for attempt in attempts if attempt["attemptid"] == body.attempt_id]
    if len(selected) != 1:
        raise HTTPException(status_code=409, detail="submitted Moodle attempt is unavailable")
    attempt = selected[0]
    try:
        group_id, moodle_user_ids = await _external_roster(client, mapping, attempt)
    except MoodleAdapterError as exc:
        raise _integration_error(exc) from exc
    group_mapping: LmsGroupMapping | None = None
    local_group: AssignmentGroup | None = None
    if version.mode == "individual":
        if group_id is not None or len(moodle_user_ids) != 1:
            raise HTTPException(
                status_code=409, detail="Moodle attempt ownership does not match individual mapping"
            )
        if body.confirmed_owner_user_id is None or body.confirmed_roster_user_ids:
            raise HTTPException(status_code=422, detail="confirm the individual submission owner")
        owner_binding = db.scalar(
            select(LtiIdentityBinding).where(
                LtiIdentityBinding.platform_id == platform.id,
                LtiIdentityBinding.moodle_user_id == moodle_user_ids[0],
                LtiIdentityBinding.active.is_(True),
            )
        )
        if owner_binding is None or owner_binding.user_id != body.confirmed_owner_user_id:
            raise HTTPException(
                status_code=409, detail="confirmed owner does not match Moodle identity binding"
            )
        owners = _resolve_roster(
            db,
            platform.id,
            moodle_user_ids,
            [body.confirmed_owner_user_id],
            assignment.course_id,
            assignment.id,
            None,
        )
        owner = owners[0]
        if (
            db.scalar(
                select(Enrollment.id).where(
                    Enrollment.course_id == assignment.course_id,
                    Enrollment.user_id == owner.id,
                    Enrollment.role == "student",
                    Enrollment.active.is_(True),
                )
            )
            is None
        ):
            raise HTTPException(status_code=409, detail="owner is not actively enrolled in ALIGN")
    else:
        if (
            body.confirmed_owner_user_id is not None
            or not body.confirmed_roster_user_ids
            or group_id is None
        ):
            raise HTTPException(status_code=422, detail="confirm the complete Moodle group roster")
        group_mapping = db.scalar(
            select(LmsGroupMapping).where(
                LmsGroupMapping.mapping_id == mapping.id,
                LmsGroupMapping.moodle_group_id == group_id,
                LmsGroupMapping.active.is_(True),
            )
        )
        if group_mapping is None:
            raise HTTPException(status_code=409, detail="Moodle group has no active ALIGN mapping")
        local_group = db.get(AssignmentGroup, group_mapping.local_group_id)
        if local_group is None or not local_group.active:
            raise HTTPException(status_code=409, detail="mapped ALIGN group is unavailable")
        owners = _resolve_roster(
            db,
            platform.id,
            moodle_user_ids,
            body.confirmed_roster_user_ids,
            assignment.course_id,
            assignment.id,
            local_group.id,
        )
        owner = None

    submitted_at = _utc_from_timestamp(attempt.get("timemodified"))
    if submitted_at > version.due_at and version.late_policy == "reject_after_due":
        raise HTTPException(
            status_code=409, detail="Moodle attempt is late under the published ALIGN policy"
        )
    try:
        staged_files = await _download_validated_files(client, platform, attempt, settings)
    except MoodleAdapterError as exc:
        raise _integration_error(exc) from exc
    uploaded_keys: list[str] = []
    committed = False
    try:
        try:
            refreshed_attempts = await _final_attempts(client, mapping)
        except MoodleAdapterError as exc:
            raise _integration_error(exc) from exc
        refreshed = [item for item in refreshed_attempts if item["attemptid"] == body.attempt_id]
        if len(refreshed) != 1 or _attempt_metadata(refreshed[0]) != _attempt_metadata(attempt):
            raise HTTPException(status_code=409, detail="Moodle attempt changed during download")
        try:
            refreshed_group_id, refreshed_user_ids = await _external_roster(
                client, mapping, refreshed[0]
            )
        except MoodleAdapterError as exc:
            raise _integration_error(exc) from exc
        if (refreshed_group_id, refreshed_user_ids) != (group_id, moodle_user_ids):
            raise HTTPException(status_code=409, detail="Moodle roster changed during download")
        file_hashes = sorted(item["sha256"] for item in staged_files)
        source_fingerprint = canonical_hash(
            {
                "attempt_id": body.attempt_id,
                "attempt_metadata": _attempt_metadata(attempt),
                "files": file_hashes,
            }
        )
        owner_key = (
            str(owner.id)
            if owner is not None
            else ",".join(sorted(str(member.id) for member in owners))
        )
        fingerprint = canonical_hash(
            {
                "platform_id": str(platform.id),
                "mapping_id": str(mapping.id),
                "attempt_id": body.attempt_id,
                "assignment_version_id": str(version.id),
                "owner": owner_key,
                "source_fingerprint": source_fingerprint,
            }
        )
        route = "/api/v1/integrations/moodle/imports"
        cached = replay_or_lock(
            db, identity.user.id, "POST", route, idempotency_key, body.model_dump(mode="json")
        )
        if cached is not None:
            db.commit()
            return cached
        mapping = db.scalar(
            select(LmsCourseAssignmentMapping)
            .where(LmsCourseAssignmentMapping.id == body.mapping_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            mapping is None
            or not mapping.active
            or mapping.revision != body.expected_mapping_revision
        ):
            raise HTTPException(status_code=409, detail="Moodle mapping changed during import")
        platform = db.scalar(
            select(LtiPlatform)
            .where(LtiPlatform.id == mapping.platform_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if platform is None or not platform.active:
            raise HTTPException(
                status_code=409, detail="Moodle platform was disabled during import"
            )
        course = _lecturer_course(db, identity, mapping.local_course_id, lock=True)
        permission = db.scalar(
            select(LtiCoursePermission)
            .where(
                LtiCoursePermission.platform_id == platform.id,
                LtiCoursePermission.course_id == course.id,
                LtiCoursePermission.active.is_(True),
            )
            .with_for_update()
        )
        if permission is None:
            raise HTTPException(status_code=409, detail="course integration permission was revoked")
        version = db.scalar(
            select(AssignmentVersion)
            .where(
                AssignmentVersion.id == mapping.assignment_version_id,
                AssignmentVersion.state == "published",
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        assignment = db.get(Assignment, version.assignment_id) if version else None
        if version is None or assignment is None or assignment.course_id != course.id:
            raise HTTPException(status_code=409, detail="published assignment mapping changed")
        if version.mode == "individual":
            assert body.confirmed_owner_user_id is not None
            owners = _resolve_roster(
                db,
                platform.id,
                moodle_user_ids,
                [body.confirmed_owner_user_id],
                assignment.course_id,
                assignment.id,
                None,
                lock=True,
            )
            owner = owners[0]
        else:
            group_mapping = db.scalar(
                select(LmsGroupMapping)
                .where(
                    LmsGroupMapping.mapping_id == mapping.id,
                    LmsGroupMapping.moodle_group_id == group_id,
                    LmsGroupMapping.active.is_(True),
                )
                .with_for_update()
            )
            if group_mapping is None:
                raise HTTPException(status_code=409, detail="Moodle group mapping was revoked")
            local_group = db.scalar(
                select(AssignmentGroup)
                .where(
                    AssignmentGroup.id == group_mapping.local_group_id,
                    AssignmentGroup.active.is_(True),
                )
                .with_for_update()
            )
            if local_group is None:
                raise HTTPException(status_code=409, detail="mapped ALIGN group was disabled")
            owners = _resolve_roster(
                db,
                platform.id,
                moodle_user_ids,
                body.confirmed_roster_user_ids,
                assignment.course_id,
                assignment.id,
                local_group.id,
                lock=True,
            )
            owner = None
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key), 0)"),
            {"key": f"lms-import:{fingerprint}"},
        )
        existing = db.scalar(
            select(LmsImportEvent).where(LmsImportEvent.fingerprint == fingerprint)
        )
        if existing is not None:
            receipt = _import_receipt(existing)
            store_response(db, identity.user.id, "POST", route, idempotency_key, receipt)
            db.commit()
            return receipt

        owner_lock = f"lms-owner:{assignment.id}:{owner_key}"
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key), 0)"), {"key": owner_lock})
        if owner is not None:
            submission = db.scalar(
                select(Submission)
                .where(
                    Submission.assignment_id == assignment.id,
                    Submission.individual_user_id == owner.id,
                )
                .with_for_update()
            )
        else:
            assert local_group is not None
            submission = db.scalar(
                select(Submission)
                .where(
                    Submission.assignment_id == assignment.id,
                    Submission.group_id == local_group.id,
                )
                .with_for_update()
            )
        if submission is None:
            submission = Submission(
                assignment_id=assignment.id,
                individual_user_id=owner.id if owner is not None else None,
                group_id=local_group.id if local_group is not None else None,
                draft_assignment_version_id=version.id,
                draft_revision=1,
            )
            db.add(submission)
            db.flush()

        version_no = (
            int(
                db.scalar(
                    select(func.max(SubmissionVersion.version_no)).where(
                        SubmissionVersion.submission_id == submission.id
                    )
                )
                or 0
            )
            + 1
        )
        assets: list[FileAsset] = []
        for staged in staged_files:
            asset_id = uuid4()
            object_key = f"submission-import/{submission.id}/{asset_id}"
            uploaded_keys.append(object_key)
            try:
                await asyncio.to_thread(
                    _upload_object,
                    settings,
                    staged["path"],
                    object_key,
                    staged["mime"],
                    staged["sha256"],
                )
            except (BotoCoreError, ClientError, OSError) as exc:
                raise HTTPException(status_code=503, detail="private storage unavailable") from exc
            asset = FileAsset(
                id=asset_id,
                course_id=assignment.course_id,
                submission_id=submission.id,
                purpose="submission",
                uploaded_by=identity.user.id,
                original_name=staged["name"],
                size_bytes=staged["size_bytes"],
                mime=staged["mime"],
                sha256=staged["sha256"],
                object_key=object_key,
                validation_state="validated",
                preflight_json=staged["preflight"],
            )
            db.add(asset)
            assets.append(asset)
        db.flush()

        manifest_files: list[dict[str, str | int]] = [
            {
                "file_id": str(asset.id),
                "sha256": asset.sha256,
                "size_bytes": asset.size_bytes,
                "mime": asset.mime,
            }
            for asset in assets
        ]
        manifest_files.sort(key=lambda item: str(item["file_id"]))
        manifest_hash = canonical_hash(manifest_files)
        final_version = SubmissionVersion(
            submission_id=submission.id,
            version_no=version_no,
            assignment_version_id=version.id,
            manifest_hash=manifest_hash,
            manifest_json={
                "files": manifest_files,
                "display_order": [str(asset.id) for asset in assets],
                "source": {
                    "kind": "moodle",
                    "platform_id": str(platform.id),
                    "mapping_id": str(mapping.id),
                    "mapping_revision": mapping.revision,
                    "attempt_id": body.attempt_id,
                    "source_fingerprint": source_fingerprint,
                },
            },
            finalized_by=identity.user.id,
            finalized_at=submitted_at,
            is_late=submitted_at > version.due_at,
        )
        db.add(final_version)
        db.flush()
        for index, asset in enumerate(assets):
            db.add(
                SubmissionVersionFile(
                    submission_version_id=final_version.id,
                    submission_id=submission.id,
                    file_asset_id=asset.id,
                    display_order=index,
                )
            )
        for member in owners:
            db.add(
                SubmissionVersionMember(
                    submission_version_id=final_version.id,
                    user_id=member.id,
                    display_name_snapshot=member.display_name,
                    email_snapshot=member.normalized_email,
                )
            )
        existing_final = (
            db.get(SubmissionVersion, submission.active_final_version_id)
            if submission.active_final_version_id
            else None
        )
        if existing_final is None or submitted_at >= existing_final.finalized_at:
            submission.active_final_version_id = final_version.id
        submission.revision += 1
        job_id = _queue_parse_job(db, final_version, assets, settings)
        event = LmsImportEvent(
            platform_id=platform.id,
            mapping_id=mapping.id,
            mapping_revision=mapping.revision,
            moodle_attempt_id=body.attempt_id,
            moodle_user_id=moodle_user_ids[0] if group_id is None else None,
            moodle_group_id=group_id,
            moodle_submitted_at=submitted_at,
            source_fingerprint=source_fingerprint,
            fingerprint=fingerprint,
            local_submission_id=submission.id,
            submission_version_id=final_version.id,
            job_id=job_id,
            imported_by=identity.user.id,
            status="imported",
        )
        db.add(event)
        db.add(
            AuditEvent(
                actor_user_id=identity.user.id,
                action="submission.moodle_imported",
                object_type="submission_version",
                object_id=final_version.id,
                new_version_refs_json={
                    "import_event_id": str(event.id),
                    "source_fingerprint": source_fingerprint,
                    "assignment_version_id": str(version.id),
                    "is_late": final_version.is_late,
                },
                request_id=request.state.request_id,
            )
        )
        db.flush()
        receipt = _import_receipt(event)
        store_response(db, identity.user.id, "POST", route, idempotency_key, receipt)
        db.commit()
        committed = True
        return receipt
    except (IntegrityError, BotoCoreError, ClientError, OSError) as exc:
        db.rollback()
        if isinstance(exc, IntegrityError):
            raise HTTPException(
                status_code=409,
                detail="Moodle import changed concurrently; retry with a new idempotency key",
            ) from exc
        raise HTTPException(status_code=503, detail="Moodle import storage failed") from exc
    except MoodleAdapterError as exc:
        db.rollback()
        raise _integration_error(exc) from exc
    except Exception:
        db.rollback()
        raise
    finally:
        for item in staged_files:
            Path(item["path"]).unlink(missing_ok=True)
        if not committed:
            for object_key in uploaded_keys:
                try:
                    await asyncio.to_thread(_delete_object, settings, object_key)
                except (BotoCoreError, ClientError, OSError):
                    pass


def _require_enabled(settings: Settings) -> None:
    if not settings.lms_adapter_enabled:
        raise HTTPException(status_code=404, detail="LMS integration is disabled")


def _lecturer_course(
    db: DbSession, identity: Identity, course_id: UUID, *, lock: bool = False
) -> Course:
    course = (
        db.scalar(select(Course).where(Course.id == course_id).with_for_update())
        if lock
        else db.get(Course, course_id)
    )
    if course is None or not course.active:
        raise HTTPException(status_code=404, detail="course not found")
    enrollment_query = select(Enrollment.id).where(
        Enrollment.course_id == course_id,
        Enrollment.user_id == identity.user.id,
        Enrollment.role == "lecturer",
        Enrollment.active.is_(True),
    )
    enrollment = db.scalar(enrollment_query.with_for_update() if lock else enrollment_query)
    if enrollment is None:
        raise HTTPException(status_code=404, detail="course not found")
    return course


def _platform_course_allowed(db: DbSession, platform_id: UUID, course_id: UUID) -> None:
    allowed = db.scalar(
        select(LtiCoursePermission.id).where(
            LtiCoursePermission.platform_id == platform_id,
            LtiCoursePermission.course_id == course_id,
            LtiCoursePermission.active.is_(True),
        )
    )
    if allowed is None:
        raise HTTPException(status_code=404, detail="course integration is not enabled")


def _validate_mapping_target(
    db: DbSession, platform_id: UUID, course_id: UUID, version_id: UUID
) -> tuple[AssignmentVersion, Assignment]:
    platform = db.get(LtiPlatform, platform_id)
    if platform is None or not platform.active:
        raise HTTPException(status_code=404, detail="LTI platform not found")
    _platform_course_allowed(db, platform_id, course_id)
    version = db.get(AssignmentVersion, version_id)
    assignment = db.get(Assignment, version.assignment_id) if version else None
    if (
        version is None
        or version.state != "published"
        or assignment is None
        or assignment.course_id != course_id
    ):
        raise HTTPException(
            status_code=422,
            detail="mapping target must be a published assignment in the allowed course",
        )
    return version, assignment


def _mapping_json(db: DbSession, mapping: LmsCourseAssignmentMapping) -> dict[str, Any]:
    groups = db.scalars(
        select(LmsGroupMapping)
        .where(LmsGroupMapping.mapping_id == mapping.id)
        .order_by(LmsGroupMapping.moodle_group_id)
    ).all()
    return {
        "id": str(mapping.id),
        "platform_id": str(mapping.platform_id),
        "lti_context_id": mapping.lti_context_id,
        "lti_resource_link_id": mapping.lti_resource_link_id,
        "moodle_course_id": mapping.moodle_course_id,
        "moodle_assignment_id": mapping.moodle_assignment_id,
        "local_course_id": str(mapping.local_course_id),
        "assignment_version_id": str(mapping.assignment_version_id),
        "active": mapping.active,
        "revision": mapping.revision,
        "groups": [
            {
                "id": str(group.id),
                "moodle_group_id": group.moodle_group_id,
                "local_group_id": str(group.local_group_id),
                "active": group.active,
                "revision": group.revision,
            }
            for group in groups
        ],
    }


@router.get("/integrations/moodle/platforms")
def list_platforms_for_course(
    course_id: UUID,
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    _require_enabled(settings)
    _lecturer_course(db, identity, course_id)
    rows = db.scalars(
        select(LtiPlatform)
        .join(LtiCoursePermission, LtiCoursePermission.platform_id == LtiPlatform.id)
        .where(
            LtiCoursePermission.course_id == course_id,
            LtiCoursePermission.active.is_(True),
            LtiPlatform.active.is_(True),
        )
        .order_by(LtiPlatform.issuer, LtiPlatform.id)
    ).all()
    return {
        "items": [
            {"id": str(row.id), "issuer": row.issuer, "moodle_origin": row.moodle_origin}
            for row in rows
        ]
    }


@router.get("/integrations/moodle/mappings")
def list_mappings(
    course_id: UUID,
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    _require_enabled(settings)
    _lecturer_course(db, identity, course_id)
    rows = db.scalars(
        select(LmsCourseAssignmentMapping)
        .join(
            LtiCoursePermission,
            LtiCoursePermission.platform_id == LmsCourseAssignmentMapping.platform_id,
        )
        .where(
            LmsCourseAssignmentMapping.local_course_id == course_id,
            LtiCoursePermission.course_id == course_id,
            LtiCoursePermission.active.is_(True),
        )
        .order_by(LmsCourseAssignmentMapping.created_at, LmsCourseAssignmentMapping.id)
    ).all()
    return {"items": [_mapping_json(db, row) for row in rows]}


@router.post("/integrations/moodle/mappings", status_code=201)
def create_mapping(
    body: MappingCreate,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    _require_enabled(settings)
    route = "/api/v1/integrations/moodle/mappings"
    cached = replay_or_lock(
        db, identity.user.id, "POST", route, idempotency_key, body.model_dump(mode="json")
    )
    if cached is not None:
        db.commit()
        return cached
    _lecturer_course(db, identity, body.local_course_id)
    _validate_mapping_target(db, body.platform_id, body.local_course_id, body.assignment_version_id)
    mapping = LmsCourseAssignmentMapping(
        platform_id=body.platform_id,
        lti_context_id=body.lti_context_id,
        lti_resource_link_id=body.lti_resource_link_id,
        moodle_course_id=body.moodle_course_id,
        moodle_assignment_id=body.moodle_assignment_id,
        local_course_id=body.local_course_id,
        assignment_version_id=body.assignment_version_id,
        active=True,
        changed_by=identity.user.id,
        revision=1,
    )
    db.add(mapping)
    try:
        db.flush()
        result = _mapping_json(db, mapping)
        db.add(
            AuditEvent(
                actor_user_id=identity.user.id,
                action="lms.mapping.created",
                object_type="lms_mapping",
                object_id=mapping.id,
                new_version_refs_json={
                    "revision": mapping.revision,
                    "assignment_version_id": str(body.assignment_version_id),
                },
                request_id=request.state.request_id,
            )
        )
        store_response(db, identity.user.id, "POST", route, idempotency_key, result)
        db.commit()
        return result
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="an active mapping already uses this LTI resource"
        ) from exc


@router.patch("/integrations/moodle/mappings/{mapping_id}")
def patch_mapping(
    mapping_id: UUID,
    body: MappingPatch,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    _require_enabled(settings)
    mapping = db.scalar(
        select(LmsCourseAssignmentMapping)
        .where(LmsCourseAssignmentMapping.id == mapping_id)
        .with_for_update()
    )
    if mapping is None:
        raise HTTPException(status_code=404, detail="mapping not found")
    _lecturer_course(db, identity, mapping.local_course_id)
    _platform_course_allowed(db, mapping.platform_id, mapping.local_course_id)
    if mapping.revision != body.expected_revision:
        raise HTTPException(status_code=409, detail="mapping revision mismatch")
    changes = body.model_dump(exclude={"expected_revision"}, exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="mapping update is empty")
    if any(value is None for value in changes.values()):
        raise HTTPException(status_code=422, detail="mapping fields cannot be cleared")
    old_refs = {
        "revision": mapping.revision,
        "assignment_version_id": str(mapping.assignment_version_id),
        "active": mapping.active,
    }
    target_course_id = changes.get("local_course_id", mapping.local_course_id)
    target_version_id = changes.get("assignment_version_id", mapping.assignment_version_id)
    _lecturer_course(db, identity, target_course_id)
    _validate_mapping_target(db, mapping.platform_id, target_course_id, target_version_id)
    for field, value in changes.items():
        setattr(mapping, field, value)
    mapping.revision += 1
    mapping.changed_by = identity.user.id
    db.add(
        AuditEvent(
            actor_user_id=identity.user.id,
            action="lms.mapping.updated",
            object_type="lms_mapping",
            object_id=mapping.id,
            old_version_refs_json=old_refs,
            new_version_refs_json={
                "revision": mapping.revision,
                "assignment_version_id": str(target_version_id),
                "active": mapping.active,
            },
            request_id=request.state.request_id,
        )
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="an active mapping already uses this LTI resource"
        ) from exc
    return _mapping_json(db, mapping)


@router.post("/integrations/moodle/mappings/{mapping_id}/groups", status_code=201)
def create_group_mapping(
    mapping_id: UUID,
    body: GroupMappingCreate,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    _require_enabled(settings)
    mapping = db.get(LmsCourseAssignmentMapping, mapping_id)
    if mapping is None or not mapping.active:
        raise HTTPException(status_code=404, detail="mapping not found")
    _lecturer_course(db, identity, mapping.local_course_id)
    _platform_course_allowed(db, mapping.platform_id, mapping.local_course_id)
    version = db.get(AssignmentVersion, mapping.assignment_version_id)
    assignment = db.get(Assignment, version.assignment_id) if version else None
    local_group = db.get(AssignmentGroup, body.local_group_id)
    if (
        local_group is None
        or not local_group.active
        or assignment is None
        or local_group.assignment_id != assignment.id
    ):
        raise HTTPException(status_code=422, detail="local group is outside the mapped assignment")
    row = LmsGroupMapping(
        mapping_id=mapping.id,
        moodle_group_id=body.moodle_group_id,
        local_group_id=local_group.id,
        active=True,
        changed_by=identity.user.id,
        revision=1,
    )
    db.add(row)
    try:
        db.flush()
        db.add(
            AuditEvent(
                actor_user_id=identity.user.id,
                action="lms.group_mapping.created",
                object_type="lms_group_mapping",
                object_id=row.id,
                new_version_refs_json={
                    "mapping_id": str(mapping.id),
                    "moodle_group_id": row.moodle_group_id,
                    "local_group_id": str(local_group.id),
                },
                request_id=request.state.request_id,
            )
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="group mapping already exists") from exc
    return {
        "id": str(row.id),
        "mapping_id": str(row.mapping_id),
        "moodle_group_id": row.moodle_group_id,
        "local_group_id": str(row.local_group_id),
        "revision": row.revision,
        "active": row.active,
    }


@router.patch("/integrations/moodle/mappings/{mapping_id}/groups/{group_mapping_id}")
def patch_group_mapping(
    mapping_id: UUID,
    group_mapping_id: UUID,
    body: GroupMappingPatch,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    _require_enabled(settings)
    mapping = db.get(LmsCourseAssignmentMapping, mapping_id)
    group_mapping = db.scalar(
        select(LmsGroupMapping)
        .where(LmsGroupMapping.id == group_mapping_id, LmsGroupMapping.mapping_id == mapping_id)
        .with_for_update()
    )
    if mapping is None or group_mapping is None:
        raise HTTPException(status_code=404, detail="group mapping not found")
    _lecturer_course(db, identity, mapping.local_course_id)
    if group_mapping.revision != body.expected_revision:
        raise HTTPException(status_code=409, detail="group mapping revision mismatch")
    previous = group_mapping.active
    group_mapping.active = body.active
    group_mapping.revision += 1
    group_mapping.changed_by = identity.user.id
    db.add(
        AuditEvent(
            actor_user_id=identity.user.id,
            action="lms.group_mapping.updated",
            object_type="lms_group_mapping",
            object_id=group_mapping.id,
            old_version_refs_json={"revision": body.expected_revision, "active": previous},
            new_version_refs_json={"revision": group_mapping.revision, "active": body.active},
            request_id=request.state.request_id,
        )
    )
    db.commit()
    return {
        "id": str(group_mapping.id),
        "mapping_id": str(mapping.id),
        "moodle_group_id": group_mapping.moodle_group_id,
        "local_group_id": str(group_mapping.local_group_id),
        "active": group_mapping.active,
        "revision": group_mapping.revision,
    }
