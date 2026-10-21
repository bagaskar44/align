from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import Field, StrictStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from align_api.auth import (
    Identity,
    StrictRequest,
    require_password_change_completed,
    require_verified_active_identity,
)
from align_api.config import Settings, get_settings
from align_api.context_provider_profiles import context_provider_payload
from align_api.db import get_db
from align_api.files import router as files_router
from align_api.idempotency import canonical_hash, replay_or_lock, store_response
from align_api.models import (
    AuditEvent,
    ContextConflict,
    ContextVersion,
    Course,
    Enrollment,
    EvidenceItem,
    FileAsset,
    Job,
    JobStage,
    LearningOutcome,
    NormativeFileLink,
    OutboxEvent,
    ParseArtifact,
    SourceUnit,
)
from align_api.paid_calls import active_paid_release_scope
from align_api.parser_profiles import profile_payload, register_manifest

router = APIRouter(tags=["course context"])
router.include_router(files_router)
ContextSourceRole = Literal["kk", "rpp"]
CONTEXT_FIELDS = {"course_code", "course_title", "context_description", "number_locale", "language"}
CONTEXT_FIELD_MAX_LENGTHS = {
    "course_code": 64,
    "course_title": 240,
    "context_description": 4000,
    "number_locale": 40,
    "language": 40,
}


class SourceInput(StrictRequest):
    file_id: UUID = Field(strict=False)
    source_role: ContextSourceRole


class ContextDraftCreate(StrictRequest):
    sources: list[SourceInput] = Field(default_factory=list, max_length=5)
    manual_fields: dict[str, StrictStr] | None = None
    base_context_version_id: UUID | None = Field(default=None, strict=False)


class ConflictResolution(StrictRequest):
    value: StrictStr = Field(min_length=1, max_length=4000)
    reason: StrictStr = Field(min_length=1, max_length=500)
    source_ref: dict[str, Any] | None = None


class LearningOutcomeInput(StrictRequest):
    code: StrictStr = Field(min_length=1, max_length=80)
    description: StrictStr = Field(min_length=1, max_length=4000)


class ContextDraftUpdate(StrictRequest):
    expected_revision: int = Field(ge=1)
    fields: dict[str, StrictStr] = Field(default_factory=dict)
    conflict_resolutions: dict[str, ConflictResolution] = Field(default_factory=dict)
    learning_outcomes: list[LearningOutcomeInput] | None = Field(default=None, max_length=100)


class ContextApproval(StrictRequest):
    expected_revision: int = Field(ge=1)


def _lecturer_course(db: DbSession, course_id: UUID, identity: Identity, lock: bool = False) -> Course:
    statement = (
        select(Course)
        .join(Enrollment, Enrollment.course_id == Course.id)
        .where(
            Course.id == course_id,
            Course.active.is_(True),
            Enrollment.user_id == identity.user.id,
            Enrollment.role == "lecturer",
            Enrollment.active.is_(True),
        )
    )
    if lock:
        statement = statement.with_for_update()
    course = db.scalar(statement)
    if course is None:
        raise HTTPException(status_code=404, detail="course not found")
    return course


def _lecturer_context_query(context_id: UUID, identity: Identity) -> Any:
    return (
        select(ContextVersion)
        .join(Course, Course.id == ContextVersion.course_id)
        .join(Enrollment, Enrollment.course_id == Course.id)
        .where(
            ContextVersion.id == context_id,
            Course.active.is_(True),
            Enrollment.user_id == identity.user.id,
            Enrollment.role == "lecturer",
            Enrollment.active.is_(True),
        )
    )


def _field(value: str, origin: str, refs: list[dict[str, Any]] | None = None, status: str = "confirmed") -> dict[str, Any]:
    return {"value": value, "origin": origin, "source_refs": refs or [], "resolution_status": status}


def _ensure_fields(course: Course, supplied: dict[str, Any] | None = None) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "course_code": _field(course.code, "manual"),
        "course_title": _field(course.title, "manual"),
        "context_description": _field("", "manual"),
        "number_locale": _field("id-ID", "manual"),
        "language": _field("id", "manual"),
        "outcomes_status": "not_provided",
        "learning_outcomes": [],
    }
    if supplied:
        unknown = set(supplied) - CONTEXT_FIELDS
        if unknown:
            raise HTTPException(status_code=422, detail="unsupported context field")
        for key, value in supplied.items():
            normalized = value.strip()
            if not normalized:
                raise HTTPException(status_code=422, detail="context field cannot be blank")
            if len(normalized) > CONTEXT_FIELD_MAX_LENGTHS[key]:
                raise HTTPException(status_code=422, detail="context field exceeds its limit")
            expected = course.code if key == "course_code" else course.title if key == "course_title" else normalized
            if normalized != expected:
                raise HTTPException(status_code=422, detail="course identity is managed from course metadata")
            fields[key] = _field(normalized, "manual")
    return fields


def _version_payload(db: DbSession, context: ContextVersion) -> dict[str, Any]:
    job = db.scalar(select(Job).where(
        Job.type == "context_draft", Job.subject_id == context.id,
    ).order_by(Job.created_at.desc()).limit(1))
    stage = db.scalar(select(JobStage).where(JobStage.job_id == job.id, JobStage.stage_name == "parse_context")) if job else None
    provider_status = stage.output_refs_json.get("provider", {}).get("status") if stage else None
    provider_messages = {
        "provider_disabled": "Bantuan model tidak tersedia; lengkapi konteks secara manual.",
        "model_response_invalid": "Usulan model tidak dapat divalidasi; tinjau sumber dan lengkapi konteks secara manual.",
        "processing_limit": "Usulan model mencapai batas pemrosesan; lengkapi konteks secara manual.",
    }
    links = db.execute(
        select(NormativeFileLink, FileAsset)
        .join(FileAsset, FileAsset.id == NormativeFileLink.file_asset_id)
        .where(NormativeFileLink.context_version_id == context.id)
        .order_by(NormativeFileLink.created_at, NormativeFileLink.id)
    ).all()
    conflicts = db.scalars(
        select(ContextConflict).where(ContextConflict.context_version_id == context.id).order_by(ContextConflict.field_path)
    ).all()
    artifacts = db.scalars(
        select(ParseArtifact).where(ParseArtifact.context_version_id == context.id).order_by(ParseArtifact.created_at)
    ).all()
    evidence_rows = db.execute(
        select(EvidenceItem, FileAsset)
        .join(FileAsset, FileAsset.id == EvidenceItem.file_asset_id)
        .join(SourceUnit, SourceUnit.id == EvidenceItem.source_unit_id)
        .where(EvidenceItem.context_version_id == context.id)
        .order_by(FileAsset.id, SourceUnit.source_order, EvidenceItem.id)
        .limit(500)
    ).all()
    evidence_total = db.scalar(select(func.count()).select_from(EvidenceItem).where(EvidenceItem.context_version_id == context.id)) or 0
    unit_counts = dict(db.execute(select(SourceUnit.parse_status, func.count())
        .join(ParseArtifact, ParseArtifact.id == SourceUnit.parse_artifact_id)
        .where(ParseArtifact.context_version_id == context.id).group_by(SourceUnit.parse_status)).all())
    unresolved_units = db.execute(select(SourceUnit, ParseArtifact.file_asset_id)
        .join(ParseArtifact, ParseArtifact.id == SourceUnit.parse_artifact_id)
        .where(ParseArtifact.context_version_id == context.id, SourceUnit.parse_status != "parsed")
        .order_by(ParseArtifact.file_asset_id, SourceUnit.source_order).limit(51)).all()
    return {
        "id": str(context.id),
        "course_id": str(context.course_id),
        "version_no": context.version_no,
        "state": context.state,
        "revision": context.revision,
        "processing": {
            "status": job.status, "completion_kind": job.completion_kind,
            "reason": job.last_error_code,
            "limitations": [{"code": provider_status, "message": provider_messages[provider_status]}] if provider_status in provider_messages else [],
        } if job else None,
        "fields": context.fields_json,
        "instruction_sources": [
            {
                "file_id": str(asset.id),
                "source_role": link.source_role,
                "original_name": asset.original_name,
                "sha256": asset.sha256,
                "mime": asset.mime,
                "size_bytes": asset.size_bytes,
                "download_path": f"/api/v1/context-files/{asset.id}/download",
            }
            for link, asset in links
        ],
        "conflicts": [
            {
                "id": str(conflict.id),
                "field_path": conflict.field_path,
                "candidate_values": conflict.candidate_values_json,
                "resolution": conflict.resolution_json,
                "resolution_history": conflict.resolution_history_json,
                "resolved_by": str(conflict.resolved_by) if conflict.resolved_by else None,
                "resolved_at": conflict.resolved_at.isoformat() if conflict.resolved_at else None,
            }
            for conflict in conflicts
        ],
        "parse_artifacts": [
            {"id": str(item.id), "file_id": str(item.file_asset_id), "status": item.status, "limitations": item.limitations_json}
            for item in artifacts
        ],
        "evidence": [
            {
                "id": str(evidence.id),
                "file_id": str(asset.id),
                "file_name": asset.original_name,
                "source_sha256": evidence.source_sha256,
                "type": evidence.type,
                "anchor": evidence.anchor_json,
                "content": evidence.content_original_json,
                "parse_status": evidence.parse_status,
                "limitations": evidence.limitations_json,
            }
            for evidence, asset in evidence_rows
        ],
        "evidence_total": evidence_total,
        "evidence_truncated": evidence_total > len(evidence_rows),
        "source_unit_summary": {"total": sum(unit_counts.values()), "by_status": unit_counts,
            "unresolved_samples": [{"file_id": str(file_id), "type": unit.unit_type,
                "anchor": unit.anchor_json, "parse_status": unit.parse_status,
                "limitations": unit.limitations_json} for unit, file_id in unresolved_units[:50]],
            "has_more_unresolved": len(unresolved_units) > 50},
        "approved_by": str(context.approved_by) if context.approved_by else None,
        "approved_at": context.approved_at.isoformat() if context.approved_at else None,
        "content_hash": context.content_hash,
        "created_at": context.created_at.isoformat(),
        "updated_at": context.updated_at.isoformat(),
    }


@router.get("/courses/{course_id}/context-versions")
def list_context_versions(
    course_id: UUID,
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, object]:
    _lecturer_course(db, course_id, identity)
    statement = select(ContextVersion).where(ContextVersion.course_id == course_id)
    if cursor is not None:
        boundary = db.scalar(select(ContextVersion).where(
            ContextVersion.id == cursor, ContextVersion.course_id == course_id,
        ))
        if boundary is None:
            raise HTTPException(status_code=404, detail="context cursor not found")
        statement = statement.where(ContextVersion.version_no < boundary.version_no)
    versions = db.scalars(
        statement.order_by(ContextVersion.version_no.desc()).limit(limit + 1)
    ).all()
    return {
        "items": [
            {
                "id": str(item.id),
                "version_no": item.version_no,
                "state": item.state,
                "revision": item.revision,
                "content_hash": item.content_hash,
                "created_at": item.created_at.isoformat(),
                "updated_at": item.updated_at.isoformat(),
            }
            for item in versions[:limit]
        ],
        "next_cursor": str(versions[limit - 1].id) if len(versions) > limit else None,
    }


@router.post("/courses/{course_id}/context-drafts", status_code=201)
def create_context_draft(
    course_id: UUID,
    body: ContextDraftCreate,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, Any]:
    route = f"/api/v1/courses/{course_id}/context-drafts"
    course = _lecturer_course(db, course_id, identity, lock=True)
    cached = replay_or_lock(db, identity.user.id, "POST", route, idempotency_key, body.model_dump(mode="json"))
    if cached is not None:
        return cached
    if not body.sources and not body.manual_fields and body.base_context_version_id is None:
        raise HTTPException(status_code=422, detail="manual fields or source files are required")
    requested_sources = list(body.sources)
    source_ids = [item.file_id for item in requested_sources]
    if len(source_ids) != len(set(source_ids)):
        raise HTTPException(status_code=422, detail="source file repeated")
    base_fields: dict[str, Any] | None = None
    if body.base_context_version_id is not None:
        base = db.scalar(
            select(ContextVersion).where(
                ContextVersion.id == body.base_context_version_id,
                ContextVersion.course_id == course_id,
                ContextVersion.state == "approved",
            )
        )
        if base is None:
            raise HTTPException(status_code=404, detail="approved context not found")
        base_fields = base.fields_json
        if not requested_sources:
            inherited = db.execute(
                select(NormativeFileLink, FileAsset)
                .join(FileAsset, FileAsset.id == NormativeFileLink.file_asset_id)
                .where(NormativeFileLink.context_version_id == base.id)
            ).all()
            if any(link.source_role not in {"kk", "rpp"} for link, _asset in inherited):
                raise HTTPException(status_code=409, detail="base context has an unsupported source role")
            requested_sources = [
                SourceInput(file_id=asset.id, source_role=cast(ContextSourceRole, link.source_role))
                for link, asset in inherited
            ]
            source_ids = [item.file_id for item in requested_sources]
            if len(source_ids) != len(set(source_ids)):
                raise HTTPException(status_code=409, detail="base context contains repeated source files")
    source_assets = db.scalars(
        select(FileAsset).where(
            FileAsset.id.in_(source_ids) if source_ids else FileAsset.id.is_(None),
            FileAsset.course_id == course_id,
            FileAsset.purpose == "context",
            FileAsset.validation_state == "validated",
        )
    ).all()
    by_id = {asset.id: asset for asset in source_assets}
    if len(by_id) != len(source_ids):
        raise HTTPException(status_code=404, detail="source file not found")
    source_page_count = sum(
        int(asset.preflight_json.get("pages", 0))
        + int("width" in asset.preflight_json and "height" in asset.preflight_json)
        for asset in source_assets
    )
    if source_page_count > settings.max_pdf_image_pages_per_bundle:
        raise HTTPException(status_code=413, detail="context source page limit exceeded")
    fields = _ensure_fields(course, body.manual_fields)
    if base_fields is not None:
        fields = dict(base_fields)
        for key, value in (body.manual_fields or {}).items():
            if key not in CONTEXT_FIELDS:
                raise HTTPException(status_code=422, detail="unsupported context field")
            normalized = value.strip()
            if not normalized:
                raise HTTPException(status_code=422, detail="context field cannot be blank")
            if len(normalized) > CONTEXT_FIELD_MAX_LENGTHS[key]:
                raise HTTPException(status_code=422, detail="context field exceeds its limit")
            if key in {"course_code", "course_title"}:
                expected = course.code if key == "course_code" else course.title
                if normalized != expected:
                    raise HTTPException(status_code=422, detail="course identity is managed from course metadata")
            prior = fields.get(key)
            fields[key] = {**prior, "resolution_status": "confirmed"} if isinstance(prior, dict) and prior.get("value") == normalized else _field(normalized, "manual")
    max_version = db.scalar(select(func.max(ContextVersion.version_no)).where(ContextVersion.course_id == course_id)) or 0
    context = ContextVersion(
        course_id=course_id,
        version_no=int(max_version) + 1,
        state="draft",
        fields_json=fields,
        instruction_sources_json=[],
        revision=1,
    )
    db.add(context)
    db.flush()
    for source in requested_sources:
        asset = by_id[source.file_id]
        db.add(
            NormativeFileLink(
                file_asset_id=asset.id,
                context_version_id=context.id,
                assignment_version_id=None,
                source_role=source.source_role,
            )
        )
        context.instruction_sources_json = [
            *context.instruction_sources_json,
            {"file_id": str(asset.id), "source_role": source.source_role, "sha256": asset.sha256},
        ]
    job_id: UUID | None = None
    if requested_sources:
        job_id = uuid4()
        parser_profiles = {
            str(asset.id): str(asset.preflight_json.get("parser_profile", settings.parser_profile_document))
            for asset in source_assets
        }
        resources = {key: register_manifest(db, "parser_resource", profile_payload(settings, key))
                     for key in set(parser_profiles.values())}
        parser_manifest = register_manifest(db, "parser", {
            "schema_version": 1, "manifest_id": "b1-context-parser-v1",
            "routes": {key: {"id": str(value.id), "sha256": value.sha256} for key, value in resources.items()},
        })
        provider_manifest = register_manifest(db, "model", context_provider_payload(settings))
        provider_policy = register_manifest(db, "routing", json.loads(Path(settings.routing_policy_file).read_text()))
        paid_release_scope = active_paid_release_scope(db, enabled=settings.paid_calls_enabled)
        fingerprint = canonical_hash(
            {
                "context_version_id": str(context.id),
                "sources": sorted((str(asset.id), asset.sha256, parser_profiles[str(asset.id)]) for asset in source_assets),
                "parser_manifest_sha256": parser_manifest.sha256,
                "provider_manifest_sha256": provider_manifest.sha256,
                "provider_policy_sha256": provider_policy.sha256,
                "paid_release": paid_release_scope,
            }
        )
        parse_artifact_ids: list[str] = []
        for source in requested_sources:
            asset = by_id[source.file_id]
            scoped = canonical_hash({"course_id": str(course_id), "context_version_id": str(context.id), "file_id": str(asset.id)})
            artifact = ParseArtifact(
                file_asset_id=asset.id,
                context_version_id=context.id,
                fingerprint=canonical_hash({"sha256": asset.sha256, "parser_manifest": parser_manifest.sha256,
                                            "resource": resources[parser_profiles[str(asset.id)]].sha256}),
                parser_manifest_id=parser_manifest.id,
                parser_resource_manifest_id=resources[parser_profiles[str(asset.id)]].id,
                owner_scope_hash=scoped,
                status="queued",
                limitations_json=[],
            )
            db.add(artifact)
            db.flush()
            parse_artifact_ids.append(str(artifact.id))
        db.add(
            Job(
                id=job_id,
                type="context_draft",
                subject_id=context.id,
                scope_json={
                    "course_id": str(course.id),
                    "context_version_id": str(context.id),
                    "file_ids": [str(item.file_id) for item in requested_sources],
                    "parse_artifact_ids": parse_artifact_ids,
                    "parser_profiles": parser_profiles,
                    "parser_manifest_id": str(parser_manifest.id),
                    "parser_resources": {key: {"id": str(value.id), "sha256": value.sha256} for key, value in resources.items()},
                    "context_provider": {"mode": settings.provider_mode, "data_mode": settings.data_mode,
                        "manifest_id": str(provider_manifest.id), "manifest_sha256": provider_manifest.sha256,
                        "policy_id": str(provider_policy.id), "policy_sha256": provider_policy.sha256},
                    **paid_release_scope,
                },
                input_fingerprint=fingerprint,
                status="queued",
                stage="parse_context",
                job_limits_json={
                    "max_active_seconds": settings.job_max_active_seconds,
                    "max_calls": 3,
                    "max_vision_calls": 2,
                    "max_text_input_tokens": 20_000,
                    "max_text_output_tokens": 4_000,
                    "budget_usd": str(settings.context_draft_budget_usd),
                },
            )
        )
        db.add(OutboxEvent(event_type="job.parse_context", subject_id=job_id, payload_json={"job_id": str(job_id)}))
    for item in fields.get("learning_outcomes", []):
        if isinstance(item, dict) and item.get("code") and item.get("description"):
            db.add(
                LearningOutcome(
                    context_version_id=context.id,
                    code=str(item["code"]),
                    description=str(item["description"]),
                    origin=str(item.get("origin", "manual")),
                    source_refs_json=item.get("source_refs", []),
                    resolution_status=str(item.get("resolution_status", "confirmed")),
                )
            )
    db.add(
        AuditEvent(
            actor_user_id=identity.user.id,
            action="context.draft.created",
            object_type="context_version",
            object_id=context.id,
            new_version_refs_json={"revision": context.revision, "version_no": context.version_no},
            request_id=request.state.request_id,
        )
    )
    db.flush()
    payload = {"context": _version_payload(db, context), "job_id": str(job_id) if job_id else None}
    store_response(db, identity.user.id, "POST", route, idempotency_key, payload)
    db.commit()
    return payload


@router.get("/context-versions/{context_id}")
def get_context_version(
    context_id: UUID,
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
) -> dict[str, Any]:
    context = db.scalar(_lecturer_context_query(context_id, identity))
    if context is None:
        raise HTTPException(status_code=404, detail="context not found")
    return _version_payload(db, context)


@router.put("/context-versions/{context_id}")
def update_context_draft(
    context_id: UUID,
    body: ContextDraftUpdate,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
) -> dict[str, Any]:
    if not body.fields and not body.conflict_resolutions and body.learning_outcomes is None:
        raise HTTPException(status_code=422, detail="at least one context change is required")
    context = db.scalar(_lecturer_context_query(context_id, identity).with_for_update())
    if context is None:
        raise HTTPException(status_code=404, detail="context not found")
    if context.state != "draft":
        raise HTTPException(status_code=409, detail="approved context is immutable")
    if context.revision != body.expected_revision:
        raise HTTPException(status_code=409, detail="revision mismatch")
    if set(body.fields) - CONTEXT_FIELDS:
        raise HTTPException(status_code=422, detail="unsupported context field")
    fields = dict(context.fields_json)
    for key, value in body.fields.items():
        normalized = value.strip()
        if not normalized:
            raise HTTPException(status_code=422, detail="context field cannot be blank")
        if len(normalized) > CONTEXT_FIELD_MAX_LENGTHS[key]:
            raise HTTPException(status_code=422, detail="context field exceeds its limit")
        conflict = db.scalar(
            select(ContextConflict).where(
                ContextConflict.context_version_id == context.id,
                ContextConflict.field_path == key,
            ).with_for_update()
        )
        if conflict is not None and key not in body.conflict_resolutions:
            raise HTTPException(status_code=422, detail="a conflict value requires an explicit resolution reason")
        if key in {"course_code", "course_title"} and conflict is None:
            course = db.get(Course, context.course_id)
            if course is None:
                raise HTTPException(status_code=404, detail="course not found")
            expected = course.code if key == "course_code" else course.title
            if normalized != expected:
                raise HTTPException(status_code=422, detail="course identity is managed from course metadata")
        if conflict is not None:
            if normalized != body.conflict_resolutions[key].value.strip():
                raise HTTPException(status_code=422, detail="field value differs from its conflict resolution")
            continue
        previous = fields.get(key)
        fields[key] = (
            {**previous, "resolution_status": "confirmed"}
            if isinstance(previous, dict) and previous.get("value") == normalized
            else _field(normalized, "manual")
        )
    for key, resolution in body.conflict_resolutions.items():
        conflict = db.scalar(
            select(ContextConflict)
            .where(ContextConflict.context_version_id == context.id, ContextConflict.field_path == key)
            .with_for_update()
        )
        if conflict is None:
            raise HTTPException(status_code=422, detail="conflict does not exist for field")
        if not resolution.value.strip() or not resolution.reason.strip():
            raise HTTPException(status_code=422, detail="conflict value and reason cannot be blank")
        if len(resolution.value.strip()) > CONTEXT_FIELD_MAX_LENGTHS[key]:
            raise HTTPException(status_code=422, detail="conflict value exceeds its field limit")
        candidates = conflict.candidate_values_json
        source_refs = [
            candidate["source_ref"]
            for candidate in candidates
            if candidate.get("value") == resolution.value and isinstance(candidate.get("source_ref"), dict)
        ]
        if resolution.source_ref is not None and resolution.source_ref not in source_refs:
            raise HTTPException(status_code=422, detail="conflict source does not match a candidate")
        refs: list[dict[str, Any]] = [resolution.source_ref] if resolution.source_ref is not None else source_refs
        origin = "extracted" if any(ref.get("file_id") for ref in refs) else "manual"
        fields[key] = _field(resolution.value.strip(), origin, refs, "confirmed")
        conflict.resolution_json = {
            "value": resolution.value.strip(),
            "reason": resolution.reason.strip(),
            "source_refs": refs,
        }
        conflict.resolved_by = identity.user.id
        conflict.resolved_at = datetime.now(UTC)
    if body.learning_outcomes is not None:
        codes = [item.code.strip().casefold() for item in body.learning_outcomes]
        if len(codes) != len(set(codes)):
            raise HTTPException(status_code=422, detail="learning outcome code repeated")
        outcomes = [
            {
                "code": item.code.strip(),
                "description": item.description.strip(),
                "origin": "manual",
                "source_refs": [],
                "resolution_status": "confirmed",
            }
            for item in body.learning_outcomes
        ]
        if any(not item["code"] or not item["description"] for item in outcomes):
            raise HTTPException(status_code=422, detail="learning outcome code and description cannot be blank")
        fields["learning_outcomes"] = outcomes
        fields["outcomes_status"] = "available" if outcomes else "not_provided"
        db.query(LearningOutcome).filter(LearningOutcome.context_version_id == context.id).delete(synchronize_session=False)
        for item in outcomes:
            db.add(
                LearningOutcome(
                    context_version_id=context.id,
                    code=item["code"],
                    description=item["description"],
                    origin=item["origin"],
                    source_refs_json=item["source_refs"],
                    resolution_status=item["resolution_status"],
                )
            )
    context.fields_json = fields
    context.revision += 1
    db.add(
        AuditEvent(
            actor_user_id=identity.user.id,
            action="context.draft.updated",
            object_type="context_version",
            object_id=context.id,
            old_version_refs_json={"revision": body.expected_revision},
            new_version_refs_json={"revision": context.revision},
            request_id=request.state.request_id,
        )
    )
    db.commit()
    db.refresh(context)
    return {"context": _version_payload(db, context)}


@router.post("/context-versions/{context_id}/approve")
def approve_context(
    context_id: UUID,
    body: ContextApproval,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, Any]:
    route = f"/api/v1/context-versions/{context_id}/approve"
    context = db.scalar(_lecturer_context_query(context_id, identity).with_for_update())
    if context is None:
        raise HTTPException(status_code=404, detail="context not found")
    cached = replay_or_lock(db, identity.user.id, "POST", route, idempotency_key, body.model_dump())
    if cached is not None:
        return cached
    if context.state != "draft":
        raise HTTPException(status_code=409, detail="context is no longer a draft")
    if context.revision != body.expected_revision:
        raise HTTPException(status_code=409, detail="revision mismatch")
    pending_artifact = db.scalar(
        select(ParseArtifact.id)
        .where(
            ParseArtifact.context_version_id == context.id,
            ParseArtifact.status.in_(["queued", "running"]),
        )
        .limit(1)
    )
    if pending_artifact is not None:
        raise HTTPException(status_code=409, detail="context parsing must finish before approval")
    artifacts = db.scalars(select(ParseArtifact).where(ParseArtifact.context_version_id == context.id)).all()
    if any(item.get("code") == "source_integrity_failed" for artifact in artifacts for item in artifact.limitations_json):
        raise HTTPException(status_code=409, detail="source integrity check failed")
    unresolved = db.scalar(
        select(ContextConflict.id).where(
            ContextConflict.context_version_id == context.id,
            ContextConflict.resolution_json.is_(None),
        ).limit(1)
    )
    if unresolved is not None:
        raise HTTPException(status_code=422, detail="context conflicts must be resolved")
    fields = context.fields_json
    missing = [
        key
        for key in ("course_code", "course_title", "context_description", "number_locale", "language")
        if not isinstance(fields.get(key), dict) or not str(fields[key].get("value", "")).strip()
    ]
    if missing:
        raise HTTPException(status_code=422, detail="required context fields are missing: " + ", ".join(missing))
    for key in ("course_code", "course_title", "context_description", "number_locale", "language"):
        if fields[key].get("resolution_status") != "confirmed":
            raise HTTPException(status_code=422, detail="required context fields must be confirmed")
    outcomes = fields.get("learning_outcomes", [])
    if not isinstance(outcomes, list):
        raise HTTPException(status_code=422, detail="learning outcomes are invalid")
    if not outcomes:
        fields["outcomes_status"] = "not_provided"
        fields["learning_outcomes"] = []
    else:
        fields["outcomes_status"] = "available"
    links = db.execute(
        select(NormativeFileLink, FileAsset)
        .join(FileAsset, FileAsset.id == NormativeFileLink.file_asset_id)
        .where(NormativeFileLink.context_version_id == context.id)
    ).all()
    for link, asset in links:
        if asset.course_id != context.course_id or asset.validation_state != "validated" or asset.purpose != "context":
            raise HTTPException(status_code=422, detail="context source is not valid for this course")
        ref = next((item for item in context.instruction_sources_json if item.get("file_id") == str(asset.id)), None)
        if ref is None or ref.get("sha256") != asset.sha256 or ref.get("source_role") != link.source_role:
            raise HTTPException(status_code=409, detail="context source version changed")
    linked_sources = {str(asset.id): (link.source_role, asset.sha256) for link, asset in links}
    anchors = {
        (str(item.file_asset_id), item.source_sha256, canonical_hash(item.anchor_json))
        for item in db.scalars(select(EvidenceItem).where(EvidenceItem.context_version_id == context.id))
    }
    for field in [*(fields[key] for key in CONTEXT_FIELDS), *outcomes]:
        for ref in field.get("source_refs", []):
            if ref.get("kind") == "course_catalog":
                continue
            source = linked_sources.get(ref.get("file_id"))
            if source is None or source != (ref.get("source_role"), ref.get("source_sha256")):
                raise HTTPException(status_code=422, detail="field source is outside this context")
            if (ref["file_id"], ref["source_sha256"], canonical_hash(ref.get("anchor"))) not in anchors:
                raise HTTPException(status_code=422, detail="field source anchor is not verified")
    source_snapshot = [
        {"file_id": str(asset.id), "source_role": link.source_role, "sha256": asset.sha256}
        for link, asset in sorted(links, key=lambda row: (row[0].source_role, str(row[1].id)))
    ]
    content_hash = canonical_hash({"fields": fields, "sources": source_snapshot})
    context.state = "approved"
    context.fields_json = fields
    context.content_hash = content_hash
    context.approved_by = identity.user.id
    context.approved_at = datetime.now(UTC)
    context.revision += 1
    course = db.scalar(select(Course).where(Course.id == context.course_id).with_for_update())
    if course is None or not course.active:
        raise HTTPException(status_code=404, detail="course not found")
    course.approved_context_version_id = context.id
    course.revision += 1
    db.add(
        AuditEvent(
            actor_user_id=identity.user.id,
            action="context.approved",
            object_type="context_version",
            object_id=context.id,
            old_version_refs_json={"revision": body.expected_revision},
            new_version_refs_json={"revision": context.revision, "content_hash": content_hash},
            request_id=request.state.request_id,
        )
    )
    payload = {"context": _version_payload(db, context)}
    store_response(db, identity.user.id, "POST", route, idempotency_key, payload)
    db.commit()
    return payload
