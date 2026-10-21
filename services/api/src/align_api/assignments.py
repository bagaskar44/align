from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import (
    BeforeValidator,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)
from sqlalchemy import and_, func, or_, select
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
from align_api.idempotency import replay_or_lock, store_response
from align_api.models import (
    Assignment,
    AssignmentGroup,
    AssignmentVersion,
    AuditEvent,
    CheckElement,
    ContextVersion,
    Course,
    Criterion,
    CriterionOutcomeLink,
    CriterionRequirementLink,
    CriterionSetVersion,
    ElementRequirementLink,
    Enrollment,
    EvidenceItem,
    FileAsset,
    GroupMembership,
    Job,
    JobStage,
    LearningOutcome,
    NormativeFileLink,
    NumericPolicyVersion,
    OutboxEvent,
    ParseArtifact,
    Requirement,
    SourceUnit,
    SpecificationVersion,
    SpecLintReport,
    SpecLintResolution,
    User,
)
from align_api.paid_calls import active_paid_release_scope
from align_api.parser_profiles import profile_payload, register_manifest
from align_domain.assignment_contracts import (
    NumericPolicyPayload,
    SpecificationPayload,
    canonical_hash,
    compile_lint,
    lint_effective_status,
    validate_spec_against_sources,
)

router = APIRouter(tags=["assignments", "specifications", "groups"])
DEFAULT_LIMITS = {
    "max_files": 5,
    "max_file_bytes": 20 * 1024 * 1024,
    "accepted_extensions": [".pdf", ".docx", ".xlsx", ".jpg", ".jpeg", ".png"],
    "max_pdf_pages": 100,
    "max_image_pixels": 25_000_000,
    "max_ooxml_entries": 10_000,
    "max_ooxml_expanded_bytes": 512 * 1024 * 1024,
    "max_xlsx_sheets": 25,
    "max_xlsx_nonempty_cells": 100_000,
}
UUIDInput = Annotated[
    UUID,
    BeforeValidator(lambda value: UUID(value) if isinstance(value, str) else value),
]
DateTimeInput = Annotated[
    datetime,
    BeforeValidator(
        lambda value: (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            if isinstance(value, str)
            else value
        )
    ),
]


class AssignmentCreate(StrictRequest):
    course_id: UUIDInput
    title: StrictStr = Field(min_length=1, max_length=240)
    context_version_id: UUIDInput

    @field_validator("title")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title cannot be blank")
        return value.strip()


class AssignmentVersionUpdate(StrictRequest):
    expected_revision: StrictInt = Field(ge=1)
    instruction_original: StrictStr = Field(max_length=30000)
    instruction_summary: StrictStr | None = Field(default=None, max_length=2000)
    mode: Literal["individual", "group"]
    opens_at: DateTimeInput
    due_at: DateTimeInput
    late_policy: Literal["accept_with_label", "reject_after_due"] = "accept_with_label"


class SourceSpan(StrictRequest):
    start: StrictInt = Field(ge=0)
    end: StrictInt = Field(ge=1)

    @model_validator(mode="after")
    def nonempty(self) -> SourceSpan:
        if self.end <= self.start:
            raise ValueError("source span must be nonempty")
        return self


class NormativeSourceRef(StrictRequest):
    source_kind: Literal["instruction", "criterion_descriptor", "rubric_evidence"]
    source_id: UUIDInput
    span: SourceSpan


class RequirementInput(StrictRequest):
    id: UUIDInput | None = None
    code: StrictStr = Field(min_length=1, max_length=80)
    text: StrictStr = Field(min_length=1, max_length=2000)
    mandatory: StrictBool = True
    obligation_key: StrictStr = Field(min_length=1, max_length=240)
    normative_source_refs: list[NormativeSourceRef] = Field(min_length=1, max_length=20)


class CriterionInput(StrictRequest):
    id: UUIDInput | None = None
    code: StrictStr = Field(min_length=1, max_length=80)
    title: StrictStr = Field(min_length=1, max_length=240)
    descriptor: StrictStr = Field(min_length=1, max_length=4000)
    display_order: StrictInt = Field(ge=0, le=1000)
    requirements: list[RequirementInput] = Field(max_length=30)
    outcome_ids: list[UUIDInput] = Field(default_factory=list, max_length=100)


class CriterionSetUpdate(StrictRequest):
    expected_revision: StrictInt = Field(ge=1)
    criteria: list[CriterionInput] = Field(min_length=1, max_length=30)


class PackageApproval(StrictRequest):
    criterion_set_version_id: UUIDInput
    expected_revision: StrictInt = Field(ge=1)


class SpecDraftCreate(StrictRequest):
    criterion_set_version_id: UUIDInput


class CriteriaDraftRequest(StrictRequest):
    rubric_file_ids: list[UUIDInput] = Field(default_factory=list, max_length=10)
    generate_from_instruction: StrictBool = False


class SpecificationUpdate(StrictRequest):
    expected_revision: StrictInt = Field(ge=1)
    specification: dict[str, Any]


class NumericPolicyCreate(StrictRequest):
    policy_key: StrictStr = Field(min_length=1, max_length=80)
    payload: dict[str, Any]


class NumericPolicyUpdate(StrictRequest):
    expected_revision: StrictInt = Field(ge=1)
    payload: dict[str, Any]


class RevisionBody(StrictRequest):
    expected_revision: StrictInt = Field(ge=1)


class SpecApproval(StrictRequest):
    expected_revision: StrictInt = Field(ge=1)
    lint_report_id: UUIDInput
    expected_report_revision: StrictInt = Field(ge=1)
    fidelity_confirmation: Literal[True]


class PublishBody(StrictRequest):
    expected_revision: StrictInt = Field(ge=1)


class LintBody(StrictRequest):
    expected_revision: StrictInt = Field(ge=1)


class ResolutionBody(StrictRequest):
    issue_code: StrictStr = Field(min_length=1, max_length=240)
    resolution: Literal["acknowledged_faithful", "acknowledged_human_boundary"]
    reason: StrictStr = Field(min_length=1, max_length=2000)
    expected_spec_revision: StrictInt = Field(ge=1)
    expected_report_revision: StrictInt = Field(ge=1)

    @field_validator("reason")
    @classmethod
    def reason_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason cannot be blank")
        return value.strip()


class NewVersionBody(StrictRequest):
    base_version_id: UUIDInput


class AssignmentPatch(StrictRequest):
    expected_revision: StrictInt = Field(ge=1)
    title: StrictStr | None = Field(default=None, max_length=240)
    administrative_state: Literal["open", "closed", "archived"] | None = None


class GroupInput(StrictRequest):
    name: StrictStr = Field(min_length=1, max_length=160)
    member_user_ids: list[UUIDInput] = Field(max_length=100)
    active: StrictBool = True

    @field_validator("name")
    @classmethod
    def group_name_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("group name cannot be blank")
        return value.strip()


class GroupUpdate(GroupInput):
    expected_revision: StrictInt = Field(ge=1)


def _assignment_access(
    db: DbSession, assignment_id: UUID, identity: Identity, *, lock: bool = False
) -> tuple[Assignment, str]:
    stmt = (
        select(Assignment)
        .join(Course, Course.id == Assignment.course_id)
        .where(Assignment.id == assignment_id, Course.active.is_(True))
    )
    if lock:
        stmt = stmt.with_for_update()
    assignment = db.scalar(stmt)
    if assignment is None:
        raise HTTPException(status_code=404, detail="assignment not found")
    if identity.user.global_role == "admin":
        return assignment, "admin"
    role = db.scalar(
        select(Enrollment.role).where(
            Enrollment.course_id == assignment.course_id,
            Enrollment.user_id == identity.user.id,
            Enrollment.active.is_(True),
        )
    )
    if role not in {"lecturer", "student"}:
        raise HTTPException(status_code=404, detail="assignment not found")
    return assignment, role


def _lecturer_assignment(
    db: DbSession, assignment_id: UUID, identity: Identity, *, lock: bool = False
) -> Assignment:
    assignment, role = _assignment_access(db, assignment_id, identity, lock=lock)
    if role not in {"lecturer", "admin"}:
        raise HTTPException(status_code=404, detail="assignment not found")
    return assignment


def _version(
    db: DbSession, version_id: UUID, identity: Identity, *, lock: bool = False
) -> tuple[AssignmentVersion, Assignment, str]:
    stmt = select(AssignmentVersion).where(AssignmentVersion.id == version_id)
    if lock:
        stmt = stmt.with_for_update()
    version = db.scalar(stmt)
    if version is None:
        raise HTTPException(status_code=404, detail="assignment version not found")
    assignment, role = _assignment_access(db, version.assignment_id, identity, lock=lock)
    if role == "student" and (
        version.state != "published"
        or assignment.active_version_id != version.id
        or assignment.administrative_state != "open"
        or version.opens_at > datetime.now(UTC)
    ):
        raise HTTPException(status_code=404, detail="assignment version not found")
    return version, assignment, role


def _queue_rubric_parse_job(
    db: DbSession,
    version: AssignmentVersion,
    assets: Sequence[FileAsset],
    settings: Settings,
) -> dict[str, Any] | None:
    if not assets:
        return None
    profiles = {
        str(asset.id): str(
            asset.preflight_json.get("parser_profile", settings.parser_profile_document)
        )
        for asset in assets
    }
    allowed_profiles = {settings.parser_profile_native, settings.parser_profile_document}
    if any(profile not in allowed_profiles for profile in profiles.values()):
        raise HTTPException(status_code=409, detail="rubric parser profile is unavailable")
    resources = {
        profile: register_manifest(db, "parser_resource", profile_payload(settings, profile))
        for profile in set(profiles.values())
    }
    parser_manifest = register_manifest(
        db,
        "parser",
        {
            "schema_version": 1,
            "manifest_id": "b1-shared-source-parser-v1",
            "namespace": "assignment_rubric",
            "routes": {
                key: {"id": str(value.id), "sha256": value.sha256}
                for key, value in resources.items()
            },
        },
    )
    fingerprint = canonical_hash(
        {
            "assignment_version_id": str(version.id),
            "sources": sorted(
                (str(asset.id), asset.sha256, profiles[str(asset.id)]) for asset in assets
            ),
            "parser_manifest_sha256": parser_manifest.sha256,
        }
    )
    existing_job = db.scalar(
        select(Job).where(Job.type == "rubric_draft", Job.input_fingerprint == fingerprint)
    )
    if existing_job is not None:
        return {"job_id": str(existing_job.id), "status": existing_job.status}

    parse_artifact_ids: list[str] = []
    for asset in assets:
        profile = profiles[str(asset.id)]
        resource = resources[profile]
        artifact_fingerprint = canonical_hash(
            {
                "sha256": asset.sha256,
                "parser_manifest": parser_manifest.sha256,
                "resource": resource.sha256,
            }
        )
        artifact = db.scalar(
            select(ParseArtifact).where(
                ParseArtifact.assignment_version_id == version.id,
                ParseArtifact.file_asset_id == asset.id,
                ParseArtifact.fingerprint == artifact_fingerprint,
            )
        )
        if artifact is None:
            artifact = ParseArtifact(
                file_asset_id=asset.id,
                assignment_version_id=version.id,
                fingerprint=artifact_fingerprint,
                owner_scope_hash=canonical_hash(
                    {"assignment_version_id": str(version.id), "file_id": str(asset.id)}
                ),
                parser_manifest_id=parser_manifest.id,
                parser_resource_manifest_id=resource.id,
                status="queued",
                limitations_json=[],
            )
            db.add(artifact)
            db.flush()
        parse_artifact_ids.append(str(artifact.id))

    job_id = uuid4()
    db.add(
        Job(
            id=job_id,
            type="rubric_draft",
            subject_id=version.id,
            scope_json={
                "assignment_version_id": str(version.id),
                "file_ids": [str(asset.id) for asset in assets],
                "parse_artifact_ids": parse_artifact_ids,
                "parser_profiles": profiles,
                "parser_manifest_id": str(parser_manifest.id),
                "parser_resources": {
                    key: {"id": str(value.id), "sha256": value.sha256}
                    for key, value in resources.items()
                },
                "provider_mode": "off",
            },
            input_fingerprint=fingerprint,
            status="queued",
            stage="parse_rubric",
            job_limits_json={
                "max_active_seconds": settings.job_max_active_seconds,
                "max_calls": 0,
                "max_vision_calls": 0,
                "max_text_input_tokens": 0,
                "max_text_output_tokens": 0,
                "budget_usd": "0",
            },
        )
    )
    db.flush()
    stage_hash = hashlib.sha256(f"{fingerprint}:parse_rubric".encode("ascii")).hexdigest()
    db.add(
        JobStage(
            job_id=job_id,
            stage_name="parse_rubric",
            input_hash=stage_hash,
            status="queued",
            output_refs_json={},
            attempt_count=0,
        )
    )
    db.add(
        OutboxEvent(
            event_type="job.parse_rubric",
            subject_id=job_id,
            payload_json={"job_id": str(job_id)},
        )
    )
    return {"job_id": str(job_id), "status": "queued"}


def _criterion_payload(db: DbSession, set_id: UUID) -> list[dict[str, Any]]:
    criteria = db.scalars(
        select(Criterion)
        .where(Criterion.criterion_set_version_id == set_id)
        .order_by(Criterion.display_order, Criterion.id)
    ).all()
    result = []
    for criterion in criteria:
        requirements = db.scalars(
            select(Requirement)
            .join(
                CriterionRequirementLink, CriterionRequirementLink.requirement_id == Requirement.id
            )
            .where(CriterionRequirementLink.criterion_id == criterion.id)
            .order_by(Requirement.code)
        ).all()
        outcomes = db.scalars(
            select(LearningOutcome)
            .join(
                CriterionOutcomeLink, CriterionOutcomeLink.learning_outcome_id == LearningOutcome.id
            )
            .where(CriterionOutcomeLink.criterion_id == criterion.id)
        ).all()
        result.append(
            {
                "id": str(criterion.id),
                "code": criterion.code,
                "title": criterion.title,
                "descriptor": criterion.descriptor,
                "display_order": criterion.display_order,
                "requirements": [
                    {
                        "id": str(r.id),
                        "code": r.code,
                        "text": r.text,
                        "mandatory": r.mandatory,
                        "obligation_key": r.obligation_key,
                        "normative_source_refs": r.normative_source_refs_json,
                    }
                    for r in requirements
                ],
                "outcome_ids": sorted(str(item.id) for item in outcomes),
            }
        )
    return result


def _clone_criterion_set(
    db: DbSession,
    prior: CriterionSetVersion,
    *,
    assignment_id: UUID,
    version_no: int,
    instruction_hash: str,
    instruction_source_id: UUID,
) -> CriterionSetVersion:
    """Clone draft content with fresh row IDs and repaired source lineage IDs."""
    clone = CriterionSetVersion(
        assignment_id=assignment_id,
        context_version_id=prior.context_version_id,
        version_no=version_no,
        instruction_hash=instruction_hash,
        state="draft",
        revision=1,
    )
    db.add(clone)
    db.flush()
    prior_criteria = db.scalars(
        select(Criterion).where(Criterion.criterion_set_version_id == prior.id)
    ).all()
    criterion_ids = {criterion.id: uuid4() for criterion in prior_criteria}
    for criterion in prior_criteria:
        db.add(
            Criterion(
                id=criterion_ids[criterion.id],
                criterion_set_version_id=clone.id,
                code=criterion.code,
                title=criterion.title,
                descriptor=criterion.descriptor,
                display_order=criterion.display_order,
            )
        )
    db.flush()
    requirement_ids: dict[UUID, UUID] = {}
    requirement_links: list[CriterionRequirementLink] = []
    for item in _criterion_payload(db, prior.id):
        new_criterion_id = criterion_ids[UUID(item["id"])]
        for req in item["requirements"]:
            prior_requirement_id = UUID(req["id"])
            new_requirement_id = requirement_ids.get(prior_requirement_id)
            if new_requirement_id is None:
                new_requirement_id = uuid4()
                requirement_ids[prior_requirement_id] = new_requirement_id
                refs = []
                for ref in req["normative_source_refs"]:
                    projected = dict(ref)
                    if projected.get("source_kind") == "instruction":
                        projected["source_id"] = str(instruction_source_id)
                    elif projected.get("source_kind") == "criterion_descriptor":
                        old_ref_id = UUID(projected["source_id"])
                        projected["source_id"] = str(criterion_ids.get(old_ref_id, old_ref_id))
                    refs.append(projected)
                db.add(
                    Requirement(
                        id=new_requirement_id,
                        criterion_set_version_id=clone.id,
                        code=req["code"],
                        text=req["text"],
                        mandatory=req["mandatory"],
                        obligation_key=req["obligation_key"],
                        normative_source_refs_json=refs,
                    )
                )
            requirement_links.append(
                CriterionRequirementLink(
                    criterion_id=new_criterion_id,
                    requirement_id=new_requirement_id,
                    criterion_set_version_id=clone.id,
                )
            )
        for outcome_id in item["outcome_ids"]:
            db.add(
                CriterionOutcomeLink(
                    criterion_id=new_criterion_id, learning_outcome_id=UUID(outcome_id)
                )
            )
    db.flush()
    db.add_all(requirement_links)
    db.flush()
    return clone


def _report_effective(db: DbSession, report: SpecLintReport, spec: SpecificationVersion) -> str:
    resolutions = db.scalars(
        select(SpecLintResolution).where(SpecLintResolution.lint_report_id == report.id)
    ).all()
    if spec.state == "approved" and spec.approved_lint_report_id == report.id:
        if (
            spec.approved_lint_resolution_revision != report.resolution_revision
            or report.source_content_hash != spec.content_hash
            or report.initial_status == "failed"
        ):
            return "stale"
        resolved = {entry.issue_code for entry in resolutions}
        return (
            "passed"
            if all(issue["issue_code"] in resolved for issue in report.issues_json)
            else "needs_review"
        )
    return lint_effective_status(
        {
            "source_spec_revision": report.source_spec_revision,
            "source_content_hash": report.source_content_hash,
            "issues": report.issues_json,
        },
        [{"issue_code": item.issue_code} for item in resolutions],
        current_revision=spec.revision,
        current_hash=spec.content_hash,
    )


def _spec_report(db: DbSession, spec: SpecificationVersion) -> dict[str, Any] | None:
    report = db.scalar(
        select(SpecLintReport)
        .where(SpecLintReport.specification_version_id == spec.id)
        .order_by(SpecLintReport.created_at.desc())
        .limit(1)
    )
    if report is None:
        return None
    resolutions = db.scalars(
        select(SpecLintResolution)
        .where(SpecLintResolution.lint_report_id == report.id)
        .order_by(SpecLintResolution.created_at)
    ).all()
    return {
        "id": str(report.id),
        "source_spec_revision": report.source_spec_revision,
        "source_content_hash": report.source_content_hash,
        "source_package_hashes": report.source_package_hashes_json,
        "registry_versions": report.registry_versions_json,
        "initial_status": report.initial_status,
        "effective_status": _report_effective(db, report, spec),
        "issues": report.issues_json,
        "resolution_revision": report.resolution_revision,
        "resolutions": [
            {
                "issue_code": r.issue_code,
                "resolution": r.resolution,
                "reason": r.reason,
                "actor_id": str(r.actor_id),
                "created_at": r.created_at.isoformat(),
            }
            for r in resolutions
        ],
    }


def _public_version(
    db: DbSession, version: AssignmentVersion, assignment: Assignment
) -> dict[str, Any]:
    criterion_set = (
        db.get(CriterionSetVersion, version.criterion_set_version_id)
        if version.criterion_set_version_id
        else None
    )
    public_criteria = []
    if criterion_set:
        for criterion in db.scalars(
            select(Criterion)
            .where(Criterion.criterion_set_version_id == criterion_set.id)
            .order_by(Criterion.display_order, Criterion.id)
        ).all():
            requirements = db.scalars(
                select(Requirement)
                .join(
                    CriterionRequirementLink,
                    CriterionRequirementLink.requirement_id == Requirement.id,
                )
                .where(CriterionRequirementLink.criterion_id == criterion.id)
                .order_by(Requirement.code)
            ).all()
            public_criteria.append(
                {
                    "code": criterion.code,
                    "title": criterion.title,
                    "descriptor": criterion.descriptor,
                    "requirements": [
                        {"code": r.code, "text": r.text, "mandatory": r.mandatory}
                        for r in requirements
                    ],
                }
            )
    spec = (
        db.get(SpecificationVersion, version.specification_version_id)
        if version.specification_version_id
        else None
    )
    public_evidence = []
    if spec and spec.state == "approved":
        criterion_codes = (
            {
                str(item.id): item.code
                for item in db.scalars(
                    select(Criterion).where(Criterion.criterion_set_version_id == criterion_set.id)
                ).all()
            }
            if criterion_set
            else {}
        )
        for element in spec.payload_json.get("elements", []):
            public_evidence.append(
                {
                    "criterion_code": criterion_codes.get(element["criterion_id"]),
                    "accepted_evidence_types": element["accepted_evidence_types"],
                }
            )
    return {
        "id": str(assignment.id),
        "title": assignment.title,
        "administrative_state": assignment.administrative_state,
        "version": {
            "id": str(version.id),
            "version_no": version.version_no,
            "state": version.state,
            "instruction_original": version.instruction_original,
            "instruction_summary": version.instruction_summary,
            "instruction_summary_label": "Ringkasan, bukan sumber kewajiban"
            if version.instruction_summary
            else None,
            "mode": version.mode,
            "opens_at": version.opens_at.isoformat(),
            "due_at": version.due_at.isoformat(),
            "late_policy": version.late_policy,
            "criteria": public_criteria,
            "accepted_evidence": public_evidence,
        },
    }


def _staff_version(db: DbSession, version: AssignmentVersion) -> dict[str, Any]:
    criterion_set = (
        db.get(CriterionSetVersion, version.criterion_set_version_id)
        if version.criterion_set_version_id
        else None
    )
    spec = (
        db.get(SpecificationVersion, version.specification_version_id)
        if version.specification_version_id
        else None
    )
    policies = db.scalars(
        select(NumericPolicyVersion)
        .where(NumericPolicyVersion.assignment_id == version.assignment_id)
        .order_by(NumericPolicyVersion.policy_key, NumericPolicyVersion.version_no)
    ).all()
    rubric_sources = db.execute(
        select(FileAsset.id, FileAsset.original_name, FileAsset.sha256)
        .join(NormativeFileLink, NormativeFileLink.file_asset_id == FileAsset.id)
        .where(
            NormativeFileLink.assignment_version_id == version.id,
            NormativeFileLink.source_role == "rubric",
        )
        .order_by(FileAsset.original_name, FileAsset.id)
    ).all()
    return {
        "id": str(version.id),
        "assignment_id": str(version.assignment_id),
        "version_no": version.version_no,
        "state": version.state,
        "revision": version.revision,
        "context_version_id": str(version.context_version_id),
        "instruction_original": version.instruction_original,
        "instruction_hash": version.instruction_hash,
        "instruction_summary": version.instruction_summary,
        "mode": version.mode,
        "opens_at": version.opens_at.isoformat(),
        "due_at": version.due_at.isoformat(),
        "late_policy": version.late_policy,
        "limits": version.limits_json,
        "rubric_sources": [
            {"id": str(item.id), "name": item.original_name, "sha256": item.sha256}
            for item in rubric_sources
        ],
        "criterion_set": (
            {
                "id": str(criterion_set.id),
                "revision": criterion_set.revision,
                "state": criterion_set.state,
                "content_hash": criterion_set.content_hash,
                "approved_by": str(criterion_set.approved_by)
                if criterion_set.approved_by
                else None,
                "criteria": _criterion_payload(db, criterion_set.id),
            }
            if criterion_set
            else None
        ),
        "specification": (
            {
                "id": str(spec.id),
                "version_no": spec.version_no,
                "schema_version": spec.schema_version,
                "revision": spec.revision,
                "state": spec.state,
                "content_hash": spec.content_hash,
                "payload": spec.payload_json,
                "relationship_role_requirements": _relationship_role_requirements(spec),
                "lint": _spec_report(db, spec),
            }
            if spec
            else None
        ),
        "numeric_policies": [
            {
                "id": str(p.id),
                "policy_key": p.policy_key,
                "version_no": p.version_no,
                "revision": p.revision,
                "state": p.state,
                "payload": p.payload_json,
                "content_hash": p.content_hash,
            }
            for p in policies
        ],
        "package_approved_at": version.package_approved_at.isoformat()
        if version.package_approved_at
        else None,
        "published_at": version.published_at.isoformat() if version.published_at else None,
    }


def _audit(
    db: DbSession,
    identity: Identity,
    request: Request,
    action: str,
    object_type: str,
    object_id: UUID,
    refs: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditEvent(
            actor_user_id=identity.user.id,
            action=action,
            object_type=object_type,
            object_id=object_id,
            new_version_refs_json=refs or {},
            request_id=request.state.request_id,
        )
    )


def _mutate_cached(
    db: DbSession, identity: Identity, route: str, key: str, body: Any
) -> dict[str, Any] | None:
    return replay_or_lock(db, identity.user.id, "POST", route, key, body)


def _queue_provider_draft(
    db: DbSession,
    settings: Settings,
    version: AssignmentVersion,
    target_id: UUID,
    task_type: Literal["criteria_draft", "specification_draft"],
) -> UUID | None:
    if settings.provider_mode == "off" or (
        settings.provider_mode == "mock" and settings.data_mode != "synthetic"
    ):
        return None
    criterion_set = db.get(CriterionSetVersion, target_id)
    if criterion_set is None or criterion_set.assignment_id != version.assignment_id:
        raise HTTPException(status_code=409, detail="criterion set snapshot is unavailable")
    stage_name = "draft_criteria" if task_type == "criteria_draft" else "draft_specification"
    model_payload = {
        "schema_version": 1,
        "purpose": task_type,
        "provider_mode": settings.provider_mode,
        "data_mode": settings.data_mode,
        "role": "efficient",
        "model": settings.model_efficient,
        "output_schema": "DraftAssistanceOutput-v1",
        "implementation": {
            path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
            for path in (
                "packages/domain/src/align_domain/drafting.py",
                "packages/domain/src/align_domain/providers.py",
                "services/worker/src/align_worker/drafting.py",
            )
        },
        "live_calls": "blocked_before_network",
        "limits": {"max_text_calls": 3, "max_input_tokens": 20000, "max_output_tokens": 4000},
        "budget_usd": str(settings.spec_draft_budget_usd),
    }
    model_manifest = register_manifest(db, "model", model_payload)
    policy_payload = json.loads(Path(settings.routing_policy_file).read_text(encoding="utf-8"))
    policy_manifest = register_manifest(db, "routing", policy_payload)
    scope = {
        "assignment_id": str(version.assignment_id),
        "assignment_version_id": str(version.id),
        "target_id": str(target_id),
        "target_revision": criterion_set.revision,
        "target_content_hash": criterion_set.content_hash,
        "task_type": task_type,
        "provider_mode": settings.provider_mode,
        "data_mode": settings.data_mode,
        "model_manifest_id": str(model_manifest.id),
        "model_manifest_sha256": model_manifest.sha256,
        "routing_manifest_id": str(policy_manifest.id),
        "routing_manifest_sha256": policy_manifest.sha256,
        "provider_model": settings.model_efficient,
    }
    scope.update(active_paid_release_scope(db, enabled=settings.paid_calls_enabled))
    fingerprint = canonical_hash({"scope": scope, "instruction_hash": version.instruction_hash})
    existing = db.scalar(select(Job).where(Job.type == task_type, Job.input_fingerprint == fingerprint))
    if existing is not None:
        return existing.id
    job_id = uuid4()
    db.add(Job(
        id=job_id,
        type=task_type,
        subject_id=target_id,
        scope_json=scope,
        input_fingerprint=fingerprint,
        status="queued",
        stage=stage_name,
        job_limits_json={
            "max_active_seconds": settings.job_max_active_seconds,
            "max_calls": 3,
            "max_vision_calls": 2,
            "max_text_input_tokens": 20000,
            "max_text_output_tokens": 4000,
            "budget_usd": str(settings.spec_draft_budget_usd),
        },
    ))
    db.flush()
    stage_hash = hashlib.sha256(f"{fingerprint}:{stage_name}".encode("ascii")).hexdigest()
    db.add(JobStage(
        job_id=job_id,
        stage_name=stage_name,
        input_hash=stage_hash,
        status="queued",
        output_refs_json={},
        attempt_count=0,
    ))
    db.add(OutboxEvent(
        event_type="job.execute",
        subject_id=job_id,
        payload_json={"job_id": str(job_id)},
    ))
    return job_id


@router.post("/assignments", status_code=201)
def create_assignment(
    body: AssignmentCreate,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, Any]:
    course = db.scalar(
        select(Course).where(Course.id == body.course_id, Course.active.is_(True)).with_for_update()
    )
    if course is None:
        raise HTTPException(status_code=404, detail="course not found")
    if (
        identity.user.global_role != "admin"
        and db.scalar(
            select(Enrollment.id).where(
                Enrollment.course_id == course.id,
                Enrollment.user_id == identity.user.id,
                Enrollment.role == "lecturer",
                Enrollment.active.is_(True),
            )
        )
        is None
    ):
        raise HTTPException(status_code=404, detail="course not found")
    route = "/api/v1/assignments"
    cached = _mutate_cached(db, identity, route, idempotency_key, body.model_dump(mode="json"))
    if cached is not None:
        return cached
    context = db.scalar(
        select(ContextVersion).where(
            ContextVersion.id == body.context_version_id,
            ContextVersion.course_id == course.id,
            ContextVersion.state == "approved",
        )
    )
    if context is None:
        raise HTTPException(status_code=404, detail="approved context not found")
    assignment = Assignment(course_id=course.id, title=body.title, administrative_state="open")
    db.add(assignment)
    db.flush()
    criteria = CriterionSetVersion(
        assignment_id=assignment.id,
        context_version_id=context.id,
        version_no=1,
        instruction_hash=canonical_hash(""),
        state="draft",
        revision=1,
    )
    db.add(criteria)
    db.flush()
    now = datetime.now(UTC)
    version = AssignmentVersion(
        assignment_id=assignment.id,
        version_no=1,
        state="draft",
        context_version_id=context.id,
        instruction_original="",
        instruction_hash=canonical_hash(""),
        mode="individual",
        opens_at=now,
        due_at=now + timedelta(days=7),
        late_policy="accept_with_label",
        limits_json=dict(DEFAULT_LIMITS),
        criterion_set_version_id=criteria.id,
        revision=1,
    )
    db.add(version)
    db.flush()
    response = {
        "id": str(assignment.id),
        "title": assignment.title,
        "revision": assignment.revision,
        "active_version_id": str(version.id),
        "version": _staff_version(db, version),
    }
    _audit(
        db,
        identity,
        request,
        "assignment.create",
        "assignment",
        assignment.id,
        {"version_id": str(version.id)},
    )
    store_response(db, identity.user.id, "POST", route, idempotency_key, response)
    db.commit()
    return response


@router.get("/courses/{course_id}/assignments")
def list_assignments(
    course_id: UUID,
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    course = db.get(Course, course_id)
    if course is None or not course.active:
        raise HTTPException(status_code=404, detail="course not found")
    role = (
        "admin"
        if identity.user.global_role == "admin"
        else db.scalar(
            select(Enrollment.role).where(
                Enrollment.course_id == course_id,
                Enrollment.user_id == identity.user.id,
                Enrollment.active.is_(True),
            )
        )
    )
    if role not in {"admin", "lecturer", "student"}:
        raise HTTPException(status_code=404, detail="course not found")
    stmt = (
        select(Assignment)
        .where(Assignment.course_id == course_id)
        .order_by(Assignment.created_at.desc(), Assignment.id)
    )
    if cursor is not None:
        boundary = db.scalar(
            select(Assignment).where(Assignment.id == cursor, Assignment.course_id == course_id)
        )
        if boundary is None:
            raise HTTPException(status_code=404, detail="assignment cursor not found")
        stmt = stmt.where(
            or_(
                Assignment.created_at < boundary.created_at,
                and_(Assignment.created_at == boundary.created_at, Assignment.id > boundary.id),
            )
        )
    if role == "student":
        stmt = stmt.where(
            Assignment.active_version_id.is_not(None), Assignment.administrative_state == "open"
        )
    rows = db.scalars(stmt.limit(limit + 1)).all()
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    items = []
    for assignment in page_rows:
        version = (
            db.get(AssignmentVersion, assignment.active_version_id)
            if assignment.active_version_id
            else None
        )
        if role == "student":
            if (
                version is None
                or version.state != "published"
                or version.opens_at > datetime.now(UTC)
            ):
                continue
            items.append(
                {
                    "id": str(assignment.id),
                    "title": assignment.title,
                    "version_id": str(version.id),
                    "version_no": version.version_no,
                    "state": version.state,
                    "mode": version.mode,
                    "opens_at": version.opens_at.isoformat(),
                    "due_at": version.due_at.isoformat(),
                }
            )
        else:
            draft = db.scalar(
                select(AssignmentVersion)
                .where(
                    AssignmentVersion.assignment_id == assignment.id,
                    AssignmentVersion.state == "draft",
                )
                .order_by(AssignmentVersion.version_no.desc())
                .limit(1)
            )
            items.append(
                {
                    "id": str(assignment.id),
                    "title": assignment.title,
                    "administrative_state": assignment.administrative_state,
                    "revision": assignment.revision,
                    "active_version_id": str(assignment.active_version_id)
                    if assignment.active_version_id
                    else None,
                    "version_no": version.version_no if version else None,
                    "version_state": version.state if version else None,
                    "draft_version_id": str(draft.id) if draft else None,
                    "draft_version_no": draft.version_no if draft else None,
                }
            )
    return {
        "items": items,
        "next_cursor": str(page_rows[-1].id) if has_more and page_rows else None,
    }


@router.get("/assignments/{assignment_id}")
def get_assignment(
    assignment_id: UUID,
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
    version_id: UUID | None = None,
) -> dict[str, Any]:
    assignment, role = _assignment_access(db, assignment_id, identity)
    if version_id is not None:
        selected = db.get(AssignmentVersion, version_id)
    elif role == "student":
        selected = (
            db.get(AssignmentVersion, assignment.active_version_id)
            if assignment.active_version_id
            else None
        )
    else:
        selected = db.scalar(
            select(AssignmentVersion)
            .where(
                AssignmentVersion.assignment_id == assignment.id,
                AssignmentVersion.state == "draft",
            )
            .order_by(AssignmentVersion.version_no.desc())
            .limit(1)
        ) or (
            db.get(AssignmentVersion, assignment.active_version_id)
            if assignment.active_version_id
            else None
        )
    if selected is None or selected.assignment_id != assignment.id:
        if role == "student":
            raise HTTPException(status_code=404, detail="assignment not found")
        return {
            "id": str(assignment.id),
            "title": assignment.title,
            "administrative_state": assignment.administrative_state,
            "revision": assignment.revision,
            "active_version_id": None,
            "version": None,
        }
    if role == "student":
        if (
            selected.state != "published"
            or assignment.active_version_id != selected.id
            or assignment.administrative_state != "open"
            or selected.opens_at > datetime.now(UTC)
        ):
            raise HTTPException(status_code=404, detail="assignment not found")
        return _public_version(db, selected, assignment)
    return {
        "id": str(assignment.id),
        "title": assignment.title,
        "administrative_state": assignment.administrative_state,
        "revision": assignment.revision,
        "active_version_id": str(assignment.active_version_id)
        if assignment.active_version_id
        else None,
        "version": _staff_version(db, selected),
    }


@router.get("/assignment-versions/{version_id}")
def get_assignment_version(
    version_id: UUID,
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
) -> dict[str, Any]:
    version, assignment, role = _version(db, version_id, identity)
    if role == "student":
        return _public_version(db, version, assignment)
    return _staff_version(db, version)


@router.get("/assignment-versions/{version_id}/publish-preview")
def get_assignment_publish_preview(
    version_id: UUID,
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
) -> dict[str, Any]:
    version, assignment, role = _version(db, version_id, identity)
    if role == "student" or version.state != "draft":
        raise HTTPException(status_code=404, detail="assignment preview not found")
    criterion_set = (
        db.get(CriterionSetVersion, version.criterion_set_version_id)
        if version.criterion_set_version_id
        else None
    )
    spec = (
        db.get(SpecificationVersion, version.specification_version_id)
        if version.specification_version_id
        else None
    )
    context = db.get(ContextVersion, version.context_version_id)
    if (
        context is None
        or context.state != "approved"
        or criterion_set is None
        or criterion_set.state != "approved"
        or criterion_set.instruction_hash != version.instruction_hash
        or spec is None
        or spec.schema_version != 2
        or spec.state != "approved"
        or spec.criterion_set_version_id != criterion_set.id
        or spec.context_version_id != version.context_version_id
        or spec.instruction_hash != version.instruction_hash
    ):
        raise HTTPException(status_code=409, detail="approved package and specification are required for preview")
    return _public_version(db, version, assignment)


@router.put("/assignment-versions/{version_id}")
def update_assignment_version(
    version_id: UUID,
    body: AssignmentVersionUpdate,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, Any]:
    version = db.scalar(
        select(AssignmentVersion).where(AssignmentVersion.id == version_id).with_for_update()
    )
    if version is None:
        raise HTTPException(status_code=404, detail="assignment version not found")
    assignment = _lecturer_assignment(db, version.assignment_id, identity, lock=True)
    route = f"/api/v1/assignment-versions/{version_id}"
    cached = replay_or_lock(
        db, identity.user.id, "PUT", route, idempotency_key, body.model_dump(mode="json")
    )
    if cached is not None:
        return cached
    if version.state != "draft":
        raise HTTPException(status_code=409, detail="published version is immutable")
    if body.expected_revision != version.revision:
        raise HTTPException(status_code=409, detail="revision mismatch")
    normalized = body.instruction_original.replace("\r\n", "\n")
    if not normalized.strip():
        raise HTTPException(status_code=422, detail="instruction cannot be blank")
    if body.due_at <= body.opens_at:
        raise HTTPException(status_code=422, detail="due_at must be after opens_at")
    if body.opens_at.tzinfo is None or body.due_at.tzinfo is None:
        raise HTTPException(status_code=422, detail="schedule timestamps require timezone")
    new_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    old_hash = version.instruction_hash
    version_changed = (
        old_hash != new_hash
        or version.instruction_summary != body.instruction_summary
        or version.mode != body.mode
        or version.opens_at != body.opens_at.astimezone(UTC)
        or version.due_at != body.due_at.astimezone(UTC)
        or version.late_policy != body.late_policy
    )
    version.instruction_original = normalized
    version.instruction_hash = new_hash
    version.instruction_summary = body.instruction_summary
    version.mode = body.mode
    version.opens_at = body.opens_at.astimezone(UTC)
    version.due_at = body.due_at.astimezone(UTC)
    version.late_policy = body.late_policy
    if version_changed and version.criterion_set_version_id is not None:
        prior = db.get(CriterionSetVersion, version.criterion_set_version_id)
        if prior is not None and prior.state == "draft":
            if old_hash != new_hash:
                prior.instruction_hash = new_hash
                prior.revision += 1
                prior.content_hash = None
        elif prior is not None and prior.state == "approved":
            max_version = (
                db.scalar(
                    select(func.max(CriterionSetVersion.version_no)).where(
                        CriterionSetVersion.assignment_id == assignment.id
                    )
                )
                or 0
            )
            clone = _clone_criterion_set(
                db,
                prior,
                assignment_id=assignment.id,
                version_no=max_version + 1,
                instruction_hash=new_hash,
                instruction_source_id=version.id,
            )
            version.criterion_set_version_id = clone.id
            version.package_approved_by = None
            version.package_approved_at = None
            version.specification_version_id = None
    elif version_changed:
        version.package_approved_by = None
        version.package_approved_at = None
        version.specification_version_id = None
    version.revision += 1
    db.flush()
    response = _staff_version(db, version)
    _audit(
        db,
        identity,
        request,
        "assignment.version.updated",
        "assignment_version",
        version.id,
        {"instruction_hash": version.instruction_hash},
    )
    store_response(db, identity.user.id, "PUT", route, idempotency_key, response)
    db.commit()
    return response


@router.patch("/assignments/{assignment_id}")
def patch_assignment(
    assignment_id: UUID,
    body: AssignmentPatch,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, Any]:
    assignment = _lecturer_assignment(db, assignment_id, identity, lock=True)
    route = f"/api/v1/assignments/{assignment_id}"
    cached = replay_or_lock(
        db, identity.user.id, "PATCH", route, idempotency_key, body.model_dump(mode="json")
    )
    if cached is not None:
        return cached
    if assignment.revision != body.expected_revision:
        raise HTTPException(status_code=409, detail="revision mismatch")
    if body.title is not None:
        if not body.title.strip():
            raise HTTPException(status_code=422, detail="title cannot be blank")
        assignment.title = body.title.strip()
    if body.administrative_state is not None:
        assignment.administrative_state = body.administrative_state
    assignment.revision += 1
    response = {
        "id": str(assignment.id),
        "title": assignment.title,
        "administrative_state": assignment.administrative_state,
        "revision": assignment.revision,
    }
    _audit(db, identity, request, "assignment.updated", "assignment", assignment.id)
    store_response(db, identity.user.id, "PATCH", route, idempotency_key, response)
    db.commit()
    return response


@router.post("/assignments/{assignment_id}/new-version", status_code=201)
def new_assignment_version(
    assignment_id: UUID,
    body: NewVersionBody,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, Any]:
    assignment = _lecturer_assignment(db, assignment_id, identity, lock=True)
    route = f"/api/v1/assignments/{assignment_id}/new-version"
    cached = _mutate_cached(db, identity, route, idempotency_key, body.model_dump(mode="json"))
    if cached is not None:
        return cached
    base = db.get(AssignmentVersion, body.base_version_id)
    if base is None or base.assignment_id != assignment.id or base.state != "published":
        raise HTTPException(status_code=404, detail="published base version not found")
    version_no = (
        db.scalar(
            select(func.max(AssignmentVersion.version_no)).where(
                AssignmentVersion.assignment_id == assignment.id
            )
        )
        or 0
    ) + 1
    criterion_set_id = None
    new_version_id = uuid4()
    if base.criterion_set_version_id:
        prior = db.get(CriterionSetVersion, base.criterion_set_version_id)
        if prior is None:
            raise HTTPException(status_code=409, detail="published criterion set is missing")
        next_set = (
            db.scalar(
                select(func.max(CriterionSetVersion.version_no)).where(
                    CriterionSetVersion.assignment_id == assignment.id
                )
            )
            or 0
        ) + 1
        clone = _clone_criterion_set(
            db,
            prior,
            assignment_id=assignment.id,
            version_no=next_set,
            instruction_hash=base.instruction_hash,
            instruction_source_id=new_version_id,
        )
        criterion_set_id = clone.id
    version = AssignmentVersion(
        id=new_version_id,
        assignment_id=assignment.id,
        version_no=version_no,
        state="draft",
        context_version_id=base.context_version_id,
        instruction_original=base.instruction_original,
        instruction_hash=base.instruction_hash,
        instruction_summary=base.instruction_summary,
        mode=base.mode,
        opens_at=base.opens_at,
        due_at=base.due_at,
        late_policy=base.late_policy,
        limits_json=dict(base.limits_json),
        criterion_set_version_id=criterion_set_id,
        revision=1,
    )
    db.add(version)
    db.flush()
    for link in db.scalars(
        select(NormativeFileLink).where(
            NormativeFileLink.assignment_version_id == base.id,
            NormativeFileLink.source_role == "rubric",
        )
    ).all():
        db.add(
            NormativeFileLink(
                file_asset_id=link.file_asset_id,
                assignment_version_id=version.id,
                context_version_id=None,
                source_role="rubric",
            )
        )
    db.flush()
    response = _staff_version(db, version)
    _audit(
        db,
        identity,
        request,
        "assignment.version.created",
        "assignment_version",
        version.id,
        {"base_version_id": str(base.id)},
    )
    store_response(db, identity.user.id, "POST", route, idempotency_key, response)
    db.commit()
    return response


@router.put("/criterion-set-versions/{set_id}")
def update_criterion_set(
    set_id: UUID,
    body: CriterionSetUpdate,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, Any]:
    criterion_set = db.scalar(
        select(CriterionSetVersion).where(CriterionSetVersion.id == set_id).with_for_update()
    )
    if criterion_set is None:
        raise HTTPException(status_code=404, detail="criterion set not found")
    assignment = _lecturer_assignment(db, criterion_set.assignment_id, identity, lock=True)
    route = f"/api/v1/criterion-set-versions/{set_id}"
    cached = replay_or_lock(
        db, identity.user.id, "PUT", route, idempotency_key, body.model_dump(mode="json")
    )
    if cached is not None:
        return cached
    if criterion_set.state != "draft":
        raise HTTPException(status_code=409, detail="approved criterion set is immutable")
    if criterion_set.revision != body.expected_revision:
        raise HTTPException(status_code=409, detail="revision mismatch")
    current_version = db.scalar(
        select(AssignmentVersion).where(
            AssignmentVersion.assignment_id == assignment.id,
            AssignmentVersion.criterion_set_version_id == criterion_set.id,
            AssignmentVersion.state == "draft",
        )
    )
    if (
        current_version is None
        or criterion_set.instruction_hash != current_version.instruction_hash
    ):
        raise HTTPException(
            status_code=409, detail="criterion set is stale against its instruction"
        )
    codes = [criterion.code.strip() for criterion in body.criteria]
    if len(set(codes)) != len(codes):
        raise HTTPException(status_code=422, detail="criterion codes must be unique")
    context_outcomes = {
        item.id
        for item in db.scalars(
            select(LearningOutcome).where(
                LearningOutcome.context_version_id == current_version.context_version_id,
                LearningOutcome.resolution_status == "confirmed",
            )
        )
    }
    supplied_outcomes = {outcome_id for item in body.criteria for outcome_id in item.outcome_ids}
    if not supplied_outcomes <= context_outcomes:
        raise HTTPException(
            status_code=422, detail="outcome mapping must use this assignment context"
        )
    existing_criteria = db.scalars(
        select(Criterion).where(Criterion.criterion_set_version_id == set_id)
    ).all()
    existing_criterion_ids = {item.id for item in existing_criteria}
    existing_requirement_ids = set(
        db.scalars(select(Requirement.id).where(Requirement.criterion_set_version_id == set_id))
    )
    criterion_ids = [item.id or uuid4() for item in body.criteria]
    supplied_criterion_ids = [item.id for item in body.criteria if item.id is not None]
    if (
        len(supplied_criterion_ids) != len(set(supplied_criterion_ids))
        or not set(supplied_criterion_ids) <= existing_criterion_ids
    ):
        raise HTTPException(status_code=422, detail="criterion ID is outside this draft set")
    supplied_requirement_ids = [
        req.id for item in body.criteria for req in item.requirements if req.id is not None
    ]
    if not set(supplied_requirement_ids) <= existing_requirement_ids:
        raise HTTPException(status_code=422, detail="requirement ID is outside this draft set")
    requirement_ids_by_criterion = [
        [req.id or uuid4() for req in item.requirements] for item in body.criteria
    ]
    refs_by_requirement: dict[UUID, list[dict[str, Any]]] = {}
    requirement_content: dict[UUID, tuple[str, str, bool, str]] = {}
    linked_requirements: dict[UUID, set[UUID]] = {}
    descriptor_by_criterion = {
        criterion_id: item.descriptor.replace("\r\n", "\n")
        for item, criterion_id in zip(body.criteria, criterion_ids, strict=True)
    }
    for criterion_input, criterion_id, requirement_ids in zip(
        body.criteria, criterion_ids, requirement_ids_by_criterion, strict=True
    ):
        descriptor = criterion_input.descriptor.replace("\r\n", "\n")
        if not criterion_input.title.strip() or not descriptor.strip():
            raise HTTPException(
                status_code=422, detail="criterion title and descriptor cannot be blank"
            )
        for req, requirement_id in zip(criterion_input.requirements, requirement_ids, strict=True):
            if not req.text.strip() or not req.code.strip() or not req.obligation_key.strip():
                raise HTTPException(status_code=422, detail="requirement fields cannot be blank")
            content = (
                req.code.strip(),
                req.text.replace("\r\n", "\n"),
                req.mandatory,
                req.obligation_key.strip(),
            )
            if (
                requirement_id in requirement_content
                and requirement_content[requirement_id] != content
            ):
                raise HTTPException(
                    status_code=422, detail="shared requirement payload must be identical"
                )
            requirement_content[requirement_id] = content
            linked_requirements.setdefault(requirement_id, set()).add(criterion_id)
            projected: list[dict[str, Any]] = []
            for ref in req.normative_source_refs:
                try:
                    kind, source_id = ref.source_kind, ref.source_id
                    evidence: EvidenceItem | None = None
                    source_text: str
                    if kind == "instruction":
                        source_text = current_version.instruction_original
                        if source_id != current_version.id:
                            raise ValueError
                    elif kind == "criterion_descriptor":
                        projected_source = descriptor_by_criterion.get(source_id)
                        if projected_source is None:
                            raise ValueError
                        source_text = projected_source
                    elif kind == "rubric_evidence":
                        evidence = db.scalar(
                            select(EvidenceItem)
                            .join(FileAsset, FileAsset.id == EvidenceItem.file_asset_id)
                            .join(NormativeFileLink, NormativeFileLink.file_asset_id == FileAsset.id)
                            .where(
                                EvidenceItem.id == source_id,
                                FileAsset.assignment_id == assignment.id,
                                FileAsset.purpose == "rubric",
                                EvidenceItem.source_sha256 == FileAsset.sha256,
                                NormativeFileLink.assignment_version_id == current_version.id,
                                NormativeFileLink.source_role == "rubric",
                            )
                        )
                        evidence_content = evidence.content_original_json if evidence else None
                        evidence_text = evidence_content.get("text") if isinstance(evidence_content, dict) else None
                        if not isinstance(evidence_text, str):
                            raise ValueError
                        source_text = evidence_text
                    else:
                        raise ValueError
                    span = ref.span
                    if span.end > len(source_text):
                        raise ValueError
                    projection = ref.model_dump(mode="json")
                    projection["source_hash"] = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
                    projection["quote"] = source_text[span.start : span.end]
                    if kind == "rubric_evidence":
                        assert evidence is not None
                        projection.update(
                            {
                                "file_id": str(evidence.file_asset_id),
                                "source_sha256": evidence.source_sha256,
                                "parser_manifest_id": str(evidence.parser_manifest_id)
                                if evidence.parser_manifest_id
                                else None,
                                "anchor": evidence.anchor_json,
                            }
                        )
                    projected.append(projection)
                except (KeyError, TypeError, ValueError):
                    raise HTTPException(
                        status_code=422, detail="requirement source reference is invalid"
                    ) from None
            if (
                requirement_id in refs_by_requirement
                and refs_by_requirement[requirement_id] != projected
            ):
                raise HTTPException(
                    status_code=422, detail="shared requirement source refs must be identical"
                )
            refs_by_requirement[requirement_id] = projected
    requirement_codes = [content[0] for content in requirement_content.values()]
    if len(requirement_codes) != len(set(requirement_codes)):
        raise HTTPException(status_code=422, detail="requirement codes must be unique within a set")
    db.query(CriterionRequirementLink).filter(
        CriterionRequirementLink.criterion_set_version_id == set_id
    ).delete(synchronize_session=False)
    db.query(Requirement).filter(Requirement.criterion_set_version_id == set_id).delete(
        synchronize_session=False
    )
    db.query(CriterionOutcomeLink).filter(
        CriterionOutcomeLink.criterion_id.in_(
            select(Criterion.id).where(Criterion.criterion_set_version_id == set_id)
        )
    ).delete(synchronize_session=False)
    db.query(Criterion).filter(Criterion.criterion_set_version_id == set_id).delete(
        synchronize_session=False
    )
    db.flush()
    for item, criterion_id in zip(body.criteria, criterion_ids, strict=True):
        criterion = Criterion(
            id=criterion_id,
            criterion_set_version_id=set_id,
            code=item.code.strip(),
            title=item.title.strip(),
            descriptor=item.descriptor.replace("\r\n", "\n"),
            display_order=item.display_order,
        )
        db.add(criterion)
        for outcome_id in item.outcome_ids:
            db.add(CriterionOutcomeLink(criterion_id=criterion.id, learning_outcome_id=outcome_id))
    db.flush()
    for requirement_id, content in requirement_content.items():
        db.add(
            Requirement(
                id=requirement_id,
                criterion_set_version_id=set_id,
                code=content[0],
                text=content[1],
                mandatory=content[2],
                obligation_key=content[3],
                normative_source_refs_json=refs_by_requirement[requirement_id],
            )
        )
    db.flush()
    for requirement_id in requirement_content:
        for criterion_id in linked_requirements[requirement_id]:
            db.add(
                CriterionRequirementLink(
                    criterion_id=criterion_id,
                    requirement_id=requirement_id,
                    criterion_set_version_id=set_id,
                )
            )
    db.flush()
    criterion_set.revision += 1
    criterion_set.instruction_hash = current_version.instruction_hash
    criterion_set.content_hash = None
    criterion_set.approved_by = None
    criterion_set.approved_at = None
    current_version.package_approved_by = None
    current_version.package_approved_at = None
    current_version.specification_version_id = None
    current_version.criterion_set_version_id = set_id
    current_version.revision += 1
    response = {
        "id": str(criterion_set.id),
        "revision": criterion_set.revision,
        "state": criterion_set.state,
        "criteria": _criterion_payload(db, set_id),
    }
    _audit(db, identity, request, "assignment.criteria.updated", "criterion_set_version", set_id)
    store_response(db, identity.user.id, "PUT", route, idempotency_key, response)
    db.commit()
    return response


@router.get("/criterion-set-versions/{set_id}")
def get_criterion_set(
    set_id: UUID,
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
) -> dict[str, Any]:
    item = db.get(CriterionSetVersion, set_id)
    if item is None:
        raise HTTPException(status_code=404, detail="criterion set not found")
    _lecturer_assignment(db, item.assignment_id, identity)
    return {
        "id": str(item.id),
        "assignment_id": str(item.assignment_id),
        "version_no": item.version_no,
        "revision": item.revision,
        "state": item.state,
        "instruction_hash": item.instruction_hash,
        "content_hash": item.content_hash,
        "criteria": _criterion_payload(db, item.id),
    }


@router.post("/assignment-versions/{version_id}/criteria-drafts", status_code=201)
def create_criteria_draft(
    version_id: UUID,
    body: CriteriaDraftRequest,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, Any]:
    version = db.scalar(
        select(AssignmentVersion).where(AssignmentVersion.id == version_id).with_for_update()
    )
    if version is None:
        raise HTTPException(status_code=404, detail="assignment version not found")
    _lecturer_assignment(db, version.assignment_id, identity, lock=True)
    route = f"/api/v1/assignment-versions/{version_id}/criteria-drafts"
    cached = _mutate_cached(db, identity, route, idempotency_key, body.model_dump(mode="json"))
    if cached is not None:
        return cached
    if version.state != "draft":
        raise HTTPException(status_code=409, detail="published version is immutable")
    if len(set(body.rubric_file_ids)) != len(body.rubric_file_ids):
        raise HTTPException(status_code=422, detail="rubric file repeated")
    assets = (
        db.scalars(
            select(FileAsset).where(
                FileAsset.id.in_(body.rubric_file_ids),
                FileAsset.assignment_id == version.assignment_id,
                FileAsset.purpose == "rubric",
                FileAsset.validation_state == "validated",
            ).order_by(FileAsset.id)
        ).all()
        if body.rubric_file_ids
        else []
    )
    if len(assets) != len(set(body.rubric_file_ids)):
        raise HTTPException(status_code=404, detail="rubric file not found")
    criterion_set = (
        db.get(CriterionSetVersion, version.criterion_set_version_id)
        if version.criterion_set_version_id
        else None
    )
    if criterion_set is None:
        set_no = (
            db.scalar(
                select(func.max(CriterionSetVersion.version_no)).where(
                    CriterionSetVersion.assignment_id == version.assignment_id
                )
            )
            or 0
        ) + 1
        criterion_set = CriterionSetVersion(
            assignment_id=version.assignment_id,
            context_version_id=version.context_version_id,
            version_no=set_no,
            instruction_hash=version.instruction_hash,
            state="draft",
            revision=1,
        )
        db.add(criterion_set)
        db.flush()
        version.criterion_set_version_id = criterion_set.id
        version.revision += 1
    if criterion_set.state != "draft" or criterion_set.instruction_hash != version.instruction_hash:
        raise HTTPException(status_code=409, detail="criterion set is not an editable draft")
    prior_rubric_links = db.scalars(
        select(NormativeFileLink).where(
            NormativeFileLink.assignment_version_id == version.id,
            NormativeFileLink.source_role == "rubric",
        )
    ).all()
    desired_file_ids = set(body.rubric_file_ids)
    existing_file_ids = {item.file_asset_id for item in prior_rubric_links}
    for link in prior_rubric_links:
        if link.file_asset_id not in desired_file_ids:
            db.delete(link)
    for asset in assets:
        if asset.id not in existing_file_ids:
            db.add(
                NormativeFileLink(
                    file_asset_id=asset.id,
                    assignment_version_id=version.id,
                    context_version_id=None,
                    source_role="rubric",
                )
            )
    if desired_file_ids != existing_file_ids:
        version.revision += 1
    rubric_parser_job = _queue_rubric_parse_job(db, version, assets, settings)
    proposals, proposals_truncated = (
        _extractive_instruction_proposals(version)
        if body.generate_from_instruction
        else ([], False)
    )
    generation_status = (
        "extractive_mock"
        if proposals
        else ("manual_review_required" if body.generate_from_instruction else "manual")
    )
    provider_draft_job_id = (
        _queue_provider_draft(db, settings, version, criterion_set.id, "criteria_draft")
        if body.generate_from_instruction
        else None
    )
    response = {
        "assignment_version_id": str(version.id),
        "criterion_set_version_id": str(criterion_set.id),
        "revision": criterion_set.revision,
        "state": criterion_set.state,
        "criteria": _criterion_payload(db, criterion_set.id),
        "rubric_sources": [
            {"id": str(asset.id), "name": asset.original_name, "sha256": asset.sha256}
            for asset in assets
        ],
        "generation": {
            "requested": body.generate_from_instruction,
            "mode": "exact_instruction_excerpt" if body.generate_from_instruction else "manual",
            "status": generation_status,
            "provider_calls": 0,
            "cost_usd": "0",
            "proposals": proposals,
            "proposals_truncated": proposals_truncated,
            "provider_draft_job_id": str(provider_draft_job_id) if provider_draft_job_id else None,
            "provider_draft_status": "queued" if provider_draft_job_id else "manual_only",
            "rubric_parser_status": rubric_parser_job["status"] if rubric_parser_job else "not_requested",
            "rubric_parser_job_id": rubric_parser_job["job_id"] if rubric_parser_job else None,
            "message": (
                "Usulan hanya kutipan persis dari instruksi; dosen tetap menetapkan makna dan kewajiban."
                if proposals
                else "Editor draft tersedia; usulan otomatis tidak mengesahkan tuntutan."
            ),
        },
    }
    _audit(
        db,
        identity,
        request,
        "assignment.criteria.draft_opened",
        "criterion_set_version",
        criterion_set.id,
    )
    store_response(db, identity.user.id, "POST", route, idempotency_key, response)
    db.commit()
    return response


@router.get("/assignment-versions/{version_id}/rubric-parse")
def get_rubric_parse(
    version_id: UUID,
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
) -> dict[str, Any]:
    version = db.get(AssignmentVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="assignment version not found")
    _lecturer_assignment(db, version.assignment_id, identity)
    files = db.execute(
        select(FileAsset)
        .join(NormativeFileLink, NormativeFileLink.file_asset_id == FileAsset.id)
        .where(
            NormativeFileLink.assignment_version_id == version.id,
            NormativeFileLink.source_role == "rubric",
            FileAsset.purpose == "rubric",
            FileAsset.validation_state == "validated",
        )
        .order_by(FileAsset.original_name, FileAsset.id)
    ).scalars().all()
    artifacts = db.scalars(
        select(ParseArtifact).where(ParseArtifact.assignment_version_id == version.id)
    ).all()
    artifacts_by_file = {item.file_asset_id: item for item in artifacts}
    job = db.scalar(
        select(Job)
        .where(Job.type == "rubric_draft", Job.subject_id == version.id)
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(1)
    )
    result_files: list[dict[str, Any]] = []
    for asset in files:
        artifact = artifacts_by_file.get(asset.id)
        evidence_count = (
            db.scalar(
                select(func.count(EvidenceItem.id)).where(
                    EvidenceItem.assignment_version_id == version.id,
                    EvidenceItem.file_asset_id == asset.id,
                )
            )
            or 0
        )
        rows = db.execute(
            select(EvidenceItem, SourceUnit.source_order)
            .join(SourceUnit, SourceUnit.id == EvidenceItem.source_unit_id)
            .where(
                EvidenceItem.assignment_version_id == version.id,
                EvidenceItem.file_asset_id == asset.id,
            )
            .order_by(SourceUnit.source_order, EvidenceItem.id)
            .limit(501)
        ).all()
        evidence: list[dict[str, Any]] = []
        for item, _source_order in rows[:500]:
            content = item.content_original_json
            text = content.get("text", "") if isinstance(content, dict) else ""
            if not isinstance(text, str):
                text = ""
            evidence.append(
                {
                    "id": str(item.id),
                    "type": item.type,
                    "anchor": item.anchor_json,
                    "text": text[:6000],
                    "text_truncated": len(text) > 6000,
                    "source_sha256": item.source_sha256,
                    "parse_status": item.parse_status,
                    "limitations": item.limitations_json,
                }
            )
        result_files.append(
            {
                "id": str(asset.id),
                "name": asset.original_name,
                "sha256": asset.sha256,
                "parse_status": artifact.status if artifact else "not_requested",
                "limitations": artifact.limitations_json if artifact else [],
                "evidence_count": evidence_count,
                "evidence_truncated": evidence_count > len(evidence),
                "evidence": evidence,
            }
        )
    return {
        "assignment_version_id": str(version.id),
        "status": job.status if job else ("not_requested" if not files else "queued"),
        "job_id": str(job.id) if job else None,
        "files": result_files,
    }


@router.post("/assignment-versions/{version_id}/approve-package")
def approve_package(
    version_id: UUID,
    body: PackageApproval,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, Any]:
    version = db.scalar(
        select(AssignmentVersion).where(AssignmentVersion.id == version_id).with_for_update()
    )
    if version is None:
        raise HTTPException(status_code=404, detail="assignment version not found")
    assignment = _lecturer_assignment(db, version.assignment_id, identity, lock=True)
    route = f"/api/v1/assignment-versions/{version_id}/approve-package"
    cached = _mutate_cached(db, identity, route, idempotency_key, body.model_dump(mode="json"))
    if cached is not None:
        return cached
    criterion_set = db.scalar(
        select(CriterionSetVersion)
        .where(
            CriterionSetVersion.id == body.criterion_set_version_id,
            CriterionSetVersion.assignment_id == version.assignment_id,
        )
        .with_for_update()
    )
    if (
        version.state != "draft"
        or criterion_set is None
        or criterion_set.state != "draft"
        or criterion_set.context_version_id != version.context_version_id
    ):
        raise HTTPException(status_code=409, detail="package is not an editable draft")
    if (
        version.revision != body.expected_revision
        or criterion_set.instruction_hash != version.instruction_hash
    ):
        raise HTTPException(
            status_code=409, detail="package revision or instruction binding is stale"
        )
    criteria = db.scalars(
        select(Criterion).where(Criterion.criterion_set_version_id == criterion_set.id)
    ).all()
    requirements = db.scalars(
        select(Requirement).where(Requirement.criterion_set_version_id == criterion_set.id)
    ).all()
    requirement_criteria: dict[UUID, set[UUID]] = {}
    for link in db.scalars(
        select(CriterionRequirementLink).where(
            CriterionRequirementLink.criterion_set_version_id == criterion_set.id
        )
    ):
        requirement_criteria.setdefault(link.requirement_id, set()).add(link.criterion_id)
    if not 1 <= len(criteria) <= 30:
        raise HTTPException(status_code=422, detail="package needs 1 to 30 criteria")
    for req in requirements:
        if (
            not req.text.strip()
            or not req.obligation_key.strip()
            or not req.normative_source_refs_json
        ):
            raise HTTPException(
                status_code=422, detail="requirements need text, obligation key and source refs"
            )
        for ref in req.normative_source_refs_json:
            try:
                span = ref["span"]
                kind = ref["source_kind"]
                source_id = UUID(ref["source_id"])
                if kind == "instruction" and source_id == version.id:
                    source_text = version.instruction_original
                elif kind == "criterion_descriptor":
                    criterion = db.scalar(
                        select(Criterion).where(
                            Criterion.id == source_id,
                            Criterion.criterion_set_version_id == criterion_set.id,
                        )
                    )
                    linked = db.scalar(
                        select(CriterionRequirementLink.criterion_id).where(
                            CriterionRequirementLink.criterion_id == source_id,
                            CriterionRequirementLink.requirement_id == req.id,
                            CriterionRequirementLink.criterion_set_version_id == criterion_set.id,
                        )
                    )
                    if criterion is None or linked is None:
                        raise ValueError
                    source_text = criterion.descriptor
                elif kind == "rubric_evidence":
                    evidence = db.scalar(
                        select(EvidenceItem)
                        .join(FileAsset, FileAsset.id == EvidenceItem.file_asset_id)
                        .join(NormativeFileLink, NormativeFileLink.file_asset_id == FileAsset.id)
                        .where(
                            EvidenceItem.id == source_id,
                            FileAsset.assignment_id == assignment.id,
                            FileAsset.purpose == "rubric",
                            FileAsset.sha256 == ref["source_sha256"],
                            EvidenceItem.source_sha256 == FileAsset.sha256,
                            EvidenceItem.file_asset_id == UUID(ref["file_id"]),
                            EvidenceItem.parser_manifest_id == UUID(ref["parser_manifest_id"]),
                            NormativeFileLink.assignment_version_id == version.id,
                            NormativeFileLink.source_role == "rubric",
                        )
                    )
                    if evidence is None:
                        raise ValueError
                    evidence_content = evidence.content_original_json
                    evidence_text = evidence_content.get("text") if isinstance(evidence_content, dict) else None
                    if not isinstance(evidence_text, str) or evidence.anchor_json != ref["anchor"]:
                        raise ValueError
                    source_text = evidence_text
                else:
                    raise ValueError
                if (
                    ref["source_hash"] != hashlib.sha256(source_text.encode("utf-8")).hexdigest()
                    or ref["quote"] != source_text[span["start"] : span["end"]]
                    or span["start"] < 0
                    or span["end"] <= span["start"]
                    or span["end"] > len(source_text)
                ):
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                raise HTTPException(
                    status_code=422, detail="requirement source projection is stale or invalid"
                ) from None
    rubric_sources = db.execute(
        select(FileAsset.id, FileAsset.sha256)
        .join(NormativeFileLink, NormativeFileLink.file_asset_id == FileAsset.id)
        .where(
            NormativeFileLink.assignment_version_id == version.id,
            NormativeFileLink.source_role == "rubric",
        )
        .order_by(FileAsset.id)
    ).all()
    package_hash = canonical_hash(
        {
            "instruction_hash": version.instruction_hash,
            "criteria": _criterion_payload(db, criterion_set.id),
            "rubric_sources": [
                {"id": str(source.id), "sha256": source.sha256} for source in rubric_sources
            ],
        }
    )
    criterion_set.content_hash = package_hash
    criterion_set.state = "approved"
    criterion_set.approved_by = identity.user.id
    criterion_set.approved_at = datetime.now(UTC)
    version.criterion_set_version_id = criterion_set.id
    version.package_approved_by = identity.user.id
    version.package_approved_at = datetime.now(UTC)
    version.revision += 1
    response = {
        "assignment_version_id": str(version.id),
        "criterion_set_version_id": str(criterion_set.id),
        "content_hash": package_hash,
        "approved_by": str(identity.user.id),
        "approved_at": criterion_set.approved_at.isoformat(),
        "assignment_revision": version.revision,
    }
    _audit(
        db,
        identity,
        request,
        "assignment.package.approved",
        "criterion_set_version",
        criterion_set.id,
        {"content_hash": package_hash},
    )
    store_response(db, identity.user.id, "POST", route, idempotency_key, response)
    db.commit()
    return response


@router.post("/assignment-versions/{version_id}/specification-drafts", status_code=201)
def create_spec_draft(
    version_id: UUID,
    body: SpecDraftCreate,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, Any]:
    version = db.scalar(
        select(AssignmentVersion).where(AssignmentVersion.id == version_id).with_for_update()
    )
    if version is None:
        raise HTTPException(status_code=404, detail="assignment version not found")
    _lecturer_assignment(db, version.assignment_id, identity, lock=True)
    route = f"/api/v1/assignment-versions/{version_id}/specification-drafts"
    cached = _mutate_cached(db, identity, route, idempotency_key, body.model_dump(mode="json"))
    if cached is not None:
        return cached
    criterion_set = db.get(CriterionSetVersion, body.criterion_set_version_id)
    if (
        version.state != "draft"
        or version.package_approved_by is None
        or criterion_set is None
        or criterion_set.state != "approved"
        or criterion_set.assignment_id != version.assignment_id
        or criterion_set.context_version_id != version.context_version_id
        or criterion_set.instruction_hash != version.instruction_hash
    ):
        raise HTTPException(status_code=409, detail="approved package required")
    spec_no = (
        db.scalar(
            select(func.max(SpecificationVersion.version_no)).where(
                SpecificationVersion.assignment_id == version.assignment_id
            )
        )
        or 0
    ) + 1
    empty = {
        "schema_version": 2,
        "elements": [],
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
    spec = SpecificationVersion(
        assignment_id=version.assignment_id,
        version_no=spec_no,
        schema_version=2,
        criterion_set_version_id=criterion_set.id,
        context_version_id=version.context_version_id,
        instruction_hash=version.instruction_hash,
        payload_json=empty,
        content_hash=canonical_hash(empty),
        state="draft",
        revision=1,
    )
    db.add(spec)
    db.flush()
    version.criterion_set_version_id = criterion_set.id
    version.specification_version_id = spec.id
    version.revision += 1
    provider_draft_job_id = _queue_provider_draft(
        db, settings, version, criterion_set.id, "specification_draft"
    )
    response = {
        "id": str(spec.id),
        "assignment_version_id": str(version.id),
        "version_no": spec_no,
        "revision": spec.revision,
        "schema_version": 2,
        "state": spec.state,
        "content_hash": spec.content_hash,
        "payload": empty,
        "lint": None,
        "provider_draft_job_id": str(provider_draft_job_id) if provider_draft_job_id else None,
    }
    _audit(
        db, identity, request, "assignment.specification.created", "specification_version", spec.id
    )
    store_response(db, identity.user.id, "POST", route, idempotency_key, response)
    db.commit()
    return response


@router.get("/assignment-draft-jobs/{job_id}")
def get_assignment_draft_job(
    job_id: UUID,
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
) -> dict[str, Any]:
    job = db.get(Job, job_id)
    if job is None or job.type not in {"criteria_draft", "specification_draft"}:
        raise HTTPException(status_code=404, detail="draft job not found")
    try:
        assignment_id = UUID(str(job.scope_json.get("assignment_id")))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="draft job not found") from exc
    _lecturer_assignment(db, assignment_id, identity)
    stage = db.scalar(
        select(JobStage)
        .where(JobStage.job_id == job.id)
        .order_by(JobStage.created_at.desc(), JobStage.id)
    )
    output = stage.output_refs_json if stage and isinstance(stage.output_refs_json, dict) else {}
    return {
        "id": str(job.id),
        "type": job.type,
        "status": job.status,
        "stage": job.stage,
        "generation": output,
        "limitation_code": job.last_error_code,
    }


def _load_spec(
    db: DbSession, spec_id: UUID, identity: Identity, lock: bool = False
) -> tuple[SpecificationVersion, AssignmentVersion]:
    stmt = select(SpecificationVersion).where(SpecificationVersion.id == spec_id)
    if lock:
        stmt = stmt.with_for_update()
    spec = db.scalar(stmt)
    if spec is None:
        raise HTTPException(status_code=404, detail="specification not found")
    _lecturer_assignment(db, spec.assignment_id, identity, lock=lock)
    version = db.scalar(
        select(AssignmentVersion)
        .where(
            AssignmentVersion.assignment_id == spec.assignment_id,
            AssignmentVersion.criterion_set_version_id == spec.criterion_set_version_id,
            AssignmentVersion.context_version_id == spec.context_version_id,
            AssignmentVersion.instruction_hash == spec.instruction_hash,
            AssignmentVersion.state == "draft",
        )
        .order_by(AssignmentVersion.version_no.desc())
        .limit(1)
    )
    if version is None:
        version = db.scalar(
            select(AssignmentVersion)
            .where(
                AssignmentVersion.assignment_id == spec.assignment_id,
                AssignmentVersion.criterion_set_version_id == spec.criterion_set_version_id,
                AssignmentVersion.context_version_id == spec.context_version_id,
                AssignmentVersion.instruction_hash == spec.instruction_hash,
            )
            .order_by(AssignmentVersion.version_no.desc())
            .limit(1)
        )
    if version is None:
        raise HTTPException(status_code=409, detail="specification package binding is stale")
    return spec, version


def _require_schema_v2(spec: SpecificationVersion) -> None:
    if spec.schema_version != 2:
        raise HTTPException(
            status_code=409,
            detail="schema 1 specification history is read-only; create a new schema 2 draft",
        )


def _report_package_hashes(db: DbSession, version: AssignmentVersion) -> dict[str, str]:
    context = db.get(ContextVersion, version.context_version_id)
    criterion_set = (
        db.get(CriterionSetVersion, version.criterion_set_version_id)
        if version.criterion_set_version_id
        else None
    )
    context_hash = context.content_hash if context is not None else ""
    return {
        "context": context_hash or "",
        "instruction": version.instruction_hash,
        "criterion_set": criterion_set.content_hash or "" if criterion_set else "",
    }


def _validate_spec_payload(
    db: DbSession,
    spec: SpecificationPayload,
    version: AssignmentVersion,
    criterion_set: CriterionSetVersion,
) -> None:
    criteria = db.scalars(
        select(Criterion).where(Criterion.criterion_set_version_id == criterion_set.id)
    ).all()
    requirements = db.scalars(
        select(Requirement).where(Requirement.criterion_set_version_id == criterion_set.id)
    ).all()
    requirement_criteria: dict[UUID, set[UUID]] = {}
    for link in db.scalars(
        select(CriterionRequirementLink).where(
            CriterionRequirementLink.criterion_set_version_id == criterion_set.id
        )
    ):
        requirement_criteria.setdefault(link.requirement_id, set()).add(link.criterion_id)
    policy_ids = {
        p.id
        for p in db.scalars(
            select(NumericPolicyVersion).where(
                NumericPolicyVersion.assignment_id == version.assignment_id,
                NumericPolicyVersion.state == "approved",
            )
        )
    }
    try:
        validate_spec_against_sources(
            spec,
            criteria={item.id: item.descriptor for item in criteria},
            requirements={
                item.id: (
                    requirement_criteria.get(item.id, set()),
                    item.text,
                    item.obligation_key,
                )
                for item in requirements
            },
            instruction_id=version.id,
            instruction=version.instruction_original,
            numeric_policy_ids=policy_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _persist_spec_payload(
    db: DbSession, spec_row: SpecificationVersion, parsed: SpecificationPayload
) -> None:
    db.query(ElementRequirementLink).filter(
        ElementRequirementLink.check_element_id.in_(
            select(CheckElement.id).where(CheckElement.specification_version_id == spec_row.id)
        )
    ).delete(synchronize_session=False)
    db.query(CheckElement).filter(CheckElement.specification_version_id == spec_row.id).delete(
        synchronize_session=False
    )
    db.flush()
    for element in parsed.elements:
        row = CheckElement(
            specification_version_id=spec_row.id,
            element_id=element.element_id,
            element_code=element.element_code,
            criterion_id=element.criterion_id,
            obligation_key=element.obligation_key,
            target_kind=element.target_kind,
            decision_question=element.decision_question,
            grounding_mode=element.grounding_contract.mode,
            payload_json=element.model_dump(mode="json"),
        )
        db.add(row)
        db.flush()
        for requirement_id in element.requirement_ids:
            db.add(ElementRequirementLink(check_element_id=row.id, requirement_id=requirement_id))


def _create_lint_report(
    db: DbSession,
    spec_row: SpecificationVersion,
    parsed: SpecificationPayload,
    version: AssignmentVersion,
) -> SpecLintReport:
    mandatory_requirement_ids = {
        str(item.id)
        for item in db.scalars(
            select(Requirement).where(
                Requirement.criterion_set_version_id == spec_row.criterion_set_version_id,
                Requirement.mandatory.is_(True),
            )
        )
    }
    result = compile_lint(
        parsed,
        revision=spec_row.revision,
        spec_hash=spec_row.content_hash,
        package_hashes=_report_package_hashes(db, version),
        required_requirement_ids=mandatory_requirement_ids,
    )
    report = SpecLintReport(
        specification_version_id=spec_row.id,
        source_spec_revision=result["source_spec_revision"],
        source_content_hash=result["source_content_hash"],
        source_package_hashes_json=result["source_package_hashes"],
        registry_versions_json=result["registry_versions"],
        initial_status=result["initial_status"],
        issues_json=result["issues"],
        resolution_revision=result["resolution_revision"],
    )
    db.add(report)
    db.flush()
    return report


@router.put("/specification-versions/{spec_id}")
def update_specification(
    spec_id: UUID,
    body: SpecificationUpdate,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, Any]:
    spec_row, version = _load_spec(db, spec_id, identity, lock=True)
    _require_schema_v2(spec_row)
    route = f"/api/v1/specification-versions/{spec_id}"
    cached = replay_or_lock(
        db, identity.user.id, "PUT", route, idempotency_key, body.model_dump(mode="json")
    )
    if cached is not None:
        return cached
    if spec_row.state != "draft" or version.state != "draft":
        raise HTTPException(
            status_code=409, detail="approved specification is immutable; create a new draft"
        )
    if spec_row.revision != body.expected_revision:
        raise HTTPException(status_code=409, detail="revision mismatch")
    criterion_set = db.get(CriterionSetVersion, spec_row.criterion_set_version_id)
    if (
        criterion_set is None
        or criterion_set.state != "approved"
        or criterion_set.context_version_id != version.context_version_id
        or criterion_set.content_hash is None
    ):
        raise HTTPException(status_code=409, detail="approved criterion set required")
    if (
        version.criterion_set_version_id != criterion_set.id
        or version.instruction_hash != spec_row.instruction_hash
    ):
        raise HTTPException(status_code=409, detail="specification source package changed")
    try:
        parsed = SpecificationPayload.model_validate_json(
            json.dumps(body.specification, ensure_ascii=False)
        )
    except Exception as exc:
        raise HTTPException(
            status_code=422, detail="specification schema or registry contract is invalid"
        ) from exc

    _validate_spec_payload(db, parsed, version, criterion_set)
    # Set references are canonicalized so equivalent unordered IDs hash identically.
    payload = parsed.model_dump(mode="json")
    for item in payload["elements"]:
        item["requirement_ids"] = sorted(item["requirement_ids"])
        item["dependencies"] = sorted(item["dependencies"])
        item["accepted_evidence_types"] = sorted(item["accepted_evidence_types"])
        item["operators"] = sorted(item["operators"])
        item["derivation_refs"] = sorted(
            item["derivation_refs"],
            key=lambda ref: (ref["source_kind"], ref["source_id"], ref.get("span") or {}),
        )
    payload["elements"] = sorted(payload["elements"], key=lambda item: item["element_code"])
    spec_row.payload_json = payload
    spec_row.revision += 1
    context = db.get(ContextVersion, version.context_version_id)
    if context is None:
        raise HTTPException(status_code=409, detail="bound context is missing")
    spec_row.content_hash = canonical_hash(
        {
            "schema_version": 2,
            "context_version_id": str(version.context_version_id),
            "context_hash": context.content_hash or "",
            "criterion_set_version_id": str(criterion_set.id),
            "criterion_set_hash": criterion_set.content_hash,
            "instruction_hash": version.instruction_hash,
            "payload": payload,
        }
    )
    _persist_spec_payload(db, spec_row, parsed)
    report = _create_lint_report(db, spec_row, parsed, version)
    response = {
        "id": str(spec_row.id),
        "revision": spec_row.revision,
        "schema_version": 2,
        "content_hash": spec_row.content_hash,
        "payload": payload,
        "derivation_projections": _derivation_projections(db, spec_row, version),
        "relationship_role_requirements": _relationship_role_requirements(spec_row),
        "lint_report_id": str(report.id),
        "effective_status": report.initial_status,
        "resolution_revision": report.resolution_revision,
    }
    _audit(
        db,
        identity,
        request,
        "assignment.specification.updated",
        "specification_version",
        spec_row.id,
        {"content_hash": spec_row.content_hash, "lint_report_id": str(report.id)},
    )
    store_response(db, identity.user.id, "PUT", route, idempotency_key, response)
    db.commit()
    return response


def _derivation_projections(
    db: DbSession, spec_row: SpecificationVersion, version: AssignmentVersion
) -> list[dict[str, Any]]:
    if spec_row.schema_version != 2:
        return []
    projections: list[dict[str, Any]] = []
    for element in spec_row.payload_json.get("elements", []):
        refs: list[dict[str, Any]] = []
        for ref in element.get("derivation_refs", []):
            source_id = UUID(ref["source_id"])
            kind = ref["source_kind"]
            if kind == "instruction" and source_id == version.id:
                source = version.instruction_original
            elif kind == "criterion_descriptor":
                criterion = db.get(Criterion, source_id)
                if (
                    criterion is None
                    or criterion.criterion_set_version_id != spec_row.criterion_set_version_id
                ):
                    continue
                source = criterion.descriptor
            elif kind == "requirement":
                requirement = db.get(Requirement, source_id)
                if (
                    requirement is None
                    or requirement.criterion_set_version_id != spec_row.criterion_set_version_id
                ):
                    continue
                source = requirement.text
            else:
                continue
            span = ref.get("span")
            quote = source if span is None else source[span["start"] : span["end"]]
            refs.append(
                {
                    "source_kind": kind,
                    "source_id": str(source_id),
                    "span": span,
                    "source_hash": hashlib.sha256(source.encode("utf-8")).hexdigest(),
                    "quote": quote,
                }
            )
        projections.append({"element_id": element.get("element_id"), "derivation_refs": refs})
    return projections


def _extractive_instruction_proposals(
    version: AssignmentVersion,
) -> tuple[list[dict[str, Any]], bool]:
    proposals: list[dict[str, Any]] = []
    offset = 0
    for line in version.instruction_original.splitlines(keepends=True):
        content_end = len(line.rstrip("\r\n"))
        content = line[:content_end]
        leading = len(content) - len(content.lstrip())
        trailing = len(content.rstrip())
        start = offset + leading
        end = offset + trailing
        quote = version.instruction_original[start:end]
        if quote:
            if len(proposals) >= 30:
                return proposals, True
            proposals.append(
                {
                    "proposal_code": f"EXCERPT-{len(proposals) + 1:02d}",
                    "source_kind": "instruction",
                    "source_id": str(version.id),
                    "source_span": {"start": start, "end": end},
                    "source_hash": version.instruction_hash,
                    "quote": quote,
                    "mandatory": None,
                    "status": "needs_lecturer_decision",
                }
            )
        offset += len(line)
    return proposals, False


def _relationship_role_requirements(spec_row: SpecificationVersion) -> list[dict[str, Any]]:
    if spec_row.schema_version != 2:
        return []
    try:
        parsed = SpecificationPayload.model_validate_json(
            json.dumps(spec_row.payload_json, ensure_ascii=False)
        )
    except Exception:
        return []
    return [
        {
            "element_id": str(element.element_id),
            "relationships": element.required_support_roles,
        }
        for element in parsed.elements
        if element.relationships
    ]


@router.get("/specification-versions/{spec_id}")
def get_specification(
    spec_id: UUID,
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
) -> dict[str, Any]:
    spec_row, version = _load_spec(db, spec_id, identity)
    return {
        "id": str(spec_row.id),
        "assignment_id": str(spec_row.assignment_id),
        "version_no": spec_row.version_no,
        "schema_version": spec_row.schema_version,
        "revision": spec_row.revision,
        "state": spec_row.state,
        "content_hash": spec_row.content_hash,
        "payload": spec_row.payload_json,
        "derivation_projections": _derivation_projections(db, spec_row, version),
        "relationship_role_requirements": _relationship_role_requirements(spec_row),
        "lint": _spec_report(db, spec_row),
        "approved_by": str(spec_row.approved_by) if spec_row.approved_by else None,
        "approved_at": spec_row.approved_at.isoformat() if spec_row.approved_at else None,
    }


@router.post("/specification-versions/{spec_id}/lint")
def lint_specification(
    spec_id: UUID,
    body: LintBody,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, Any]:
    spec_row, version = _load_spec(db, spec_id, identity, lock=True)
    _require_schema_v2(spec_row)
    route = f"/api/v1/specification-versions/{spec_id}/lint"
    cached = _mutate_cached(db, identity, route, idempotency_key, body.model_dump(mode="json"))
    if cached is not None:
        return cached
    if spec_row.state != "draft" or spec_row.revision != body.expected_revision:
        raise HTTPException(status_code=409, detail="specification revision or state changed")
    try:
        parsed = SpecificationPayload.model_validate_json(
            json.dumps(spec_row.payload_json, ensure_ascii=False)
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail="draft specification is incomplete") from exc
    report = _create_lint_report(db, spec_row, parsed, version)
    response = {
        "id": str(report.id),
        "source_spec_revision": report.source_spec_revision,
        "source_content_hash": report.source_content_hash,
        "initial_status": report.initial_status,
        "effective_status": report.initial_status,
        "issues": report.issues_json,
        "resolution_revision": report.resolution_revision,
    }
    _audit(
        db,
        identity,
        request,
        "assignment.specification.linted",
        "spec_lint_report",
        report.id,
        {"source_content_hash": report.source_content_hash},
    )
    store_response(db, identity.user.id, "POST", route, idempotency_key, response)
    db.commit()
    return response


@router.get("/specification-versions/{spec_id}/lint-reports")
def list_lint_reports(
    spec_id: UUID,
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    spec_row, _ = _load_spec(db, spec_id, identity)
    rows = db.scalars(
        select(SpecLintReport)
        .where(SpecLintReport.specification_version_id == spec_row.id)
        .order_by(SpecLintReport.created_at.desc())
        .limit(limit + 1)
    ).all()
    return {
        "items": [
            {
                "id": str(r.id),
                "source_spec_revision": r.source_spec_revision,
                "source_content_hash": r.source_content_hash,
                "initial_status": r.initial_status,
                "effective_status": _report_effective(db, r, spec_row),
                "resolution_revision": r.resolution_revision,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows[:limit]
        ],
        "next_cursor": None,
    }


@router.get("/spec-lint-reports/{report_id}")
def get_lint_report(
    report_id: UUID,
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
) -> dict[str, Any]:
    report = db.get(SpecLintReport, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="lint report not found")
    spec_row, _ = _load_spec(db, report.specification_version_id, identity)
    return {
        "id": str(report.id),
        "source_spec_revision": report.source_spec_revision,
        "source_content_hash": report.source_content_hash,
        "source_package_hashes": report.source_package_hashes_json,
        "registry_versions": report.registry_versions_json,
        "initial_status": report.initial_status,
        "effective_status": _report_effective(db, report, spec_row),
        "issues": report.issues_json,
        "resolution_revision": report.resolution_revision,
        "resolutions": [
            {
                "issue_code": item.issue_code,
                "resolution": item.resolution,
                "reason": item.reason,
                "actor_id": str(item.actor_id),
                "created_at": item.created_at.isoformat(),
            }
            for item in db.scalars(
                select(SpecLintResolution)
                .where(SpecLintResolution.lint_report_id == report.id)
                .order_by(SpecLintResolution.created_at)
            ).all()
        ],
    }


@router.post("/spec-lint-reports/{report_id}/resolutions")
def resolve_lint_issue(
    report_id: UUID,
    body: ResolutionBody,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, Any]:
    report = db.get(SpecLintReport, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="lint report not found")
    spec_row, _version = _load_spec(db, report.specification_version_id, identity, lock=True)
    _require_schema_v2(spec_row)
    report = db.scalar(
        select(SpecLintReport).where(SpecLintReport.id == report_id).with_for_update()
    )
    if report is None:
        raise HTTPException(status_code=404, detail="lint report not found")
    route = f"/api/v1/spec-lint-reports/{report_id}/resolutions"
    cached = _mutate_cached(db, identity, route, idempotency_key, body.model_dump(mode="json"))
    if cached is not None:
        return cached
    if (
        spec_row.state != "draft"
        or spec_row.revision != body.expected_spec_revision
        or report.resolution_revision != body.expected_report_revision
    ):
        raise HTTPException(status_code=409, detail="lint resolution is stale")
    if (
        report.source_spec_revision != spec_row.revision
        or report.source_content_hash != spec_row.content_hash
    ):
        raise HTTPException(status_code=409, detail="lint report is stale")
    issue = next(
        (item for item in report.issues_json if item["issue_code"] == body.issue_code), None
    )
    if issue is None or issue["severity"] != "review":
        raise HTTPException(status_code=422, detail="only review issues can be resolved")
    if (
        db.scalar(
            select(SpecLintResolution.id).where(
                SpecLintResolution.lint_report_id == report.id,
                SpecLintResolution.issue_code == body.issue_code,
            )
        )
        is not None
    ):
        raise HTTPException(status_code=409, detail="lint issue is already resolved")
    if body.resolution == "acknowledged_human_boundary":
        wanted = {UUID(value) for value in issue["element_ids"]}
        current = {UUID(item["element_id"]): item for item in spec_row.payload_json["elements"]}
        if not wanted or any(
            current[element]["grounding_contract"]["mode"] != "human_only" for element in wanted
        ):
            raise HTTPException(
                status_code=422, detail="human boundary resolution requires human_only elements"
            )
    resolution = SpecLintResolution(
        lint_report_id=report.id,
        issue_code=body.issue_code,
        resolution=body.resolution,
        reason=body.reason,
        actor_id=identity.user.id,
    )
    db.add(resolution)
    report.resolution_revision += 1
    db.flush()
    response = {
        "id": str(resolution.id),
        "issue_code": resolution.issue_code,
        "resolution": resolution.resolution,
        "reason": resolution.reason,
        "actor_id": str(resolution.actor_id),
        "resolution_revision": report.resolution_revision,
    }
    _audit(
        db,
        identity,
        request,
        "assignment.specification.lint_resolved",
        "spec_lint_report",
        report.id,
        {"issue_code": body.issue_code, "resolution": body.resolution},
    )
    store_response(db, identity.user.id, "POST", route, idempotency_key, response)
    db.commit()
    return response


@router.post("/assignments/{assignment_id}/numeric-policy-drafts", status_code=201)
def create_numeric_policy(
    assignment_id: UUID,
    body: NumericPolicyCreate,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, Any]:
    assignment = _lecturer_assignment(db, assignment_id, identity, lock=True)
    route = f"/api/v1/assignments/{assignment_id}/numeric-policy-drafts"
    cached = _mutate_cached(db, identity, route, idempotency_key, body.model_dump(mode="json"))
    if cached is not None:
        return cached
    try:
        payload = NumericPolicyPayload.model_validate_json(
            json.dumps(body.payload, ensure_ascii=False)
        ).model_dump(mode="json", by_alias=True)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="NumericPolicy schema is invalid") from exc
    next_no = (
        db.scalar(
            select(func.max(NumericPolicyVersion.version_no)).where(
                NumericPolicyVersion.assignment_id == assignment.id,
                NumericPolicyVersion.policy_key == body.policy_key,
            )
        )
        or 0
    ) + 1
    policy = NumericPolicyVersion(
        assignment_id=assignment.id,
        policy_key=body.policy_key,
        version_no=next_no,
        state="draft",
        payload_json=payload,
        content_hash=canonical_hash(payload),
        revision=1,
    )
    db.add(policy)
    db.flush()
    response = {
        "id": str(policy.id),
        "policy_key": policy.policy_key,
        "version_no": policy.version_no,
        "revision": policy.revision,
        "state": policy.state,
        "payload": policy.payload_json,
        "content_hash": policy.content_hash,
    }
    _audit(
        db,
        identity,
        request,
        "assignment.numeric_policy.created",
        "numeric_policy_version",
        policy.id,
    )
    store_response(db, identity.user.id, "POST", route, idempotency_key, response)
    db.commit()
    return response


@router.get("/numeric-policy-versions/{policy_id}")
def get_numeric_policy(
    policy_id: UUID,
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
) -> dict[str, Any]:
    policy = db.get(NumericPolicyVersion, policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="NumericPolicy not found")
    _lecturer_assignment(db, policy.assignment_id, identity)
    return {
        "id": str(policy.id),
        "policy_key": policy.policy_key,
        "version_no": policy.version_no,
        "revision": policy.revision,
        "state": policy.state,
        "payload": policy.payload_json,
        "content_hash": policy.content_hash,
        "approved_by": str(policy.approved_by) if policy.approved_by else None,
        "approved_at": policy.approved_at.isoformat() if policy.approved_at else None,
    }


@router.put("/numeric-policy-versions/{policy_id}")
def update_numeric_policy(
    policy_id: UUID,
    body: NumericPolicyUpdate,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, Any]:
    policy = db.scalar(
        select(NumericPolicyVersion).where(NumericPolicyVersion.id == policy_id).with_for_update()
    )
    if policy is None:
        raise HTTPException(status_code=404, detail="NumericPolicy not found")
    _lecturer_assignment(db, policy.assignment_id, identity, lock=True)
    route = f"/api/v1/numeric-policy-versions/{policy_id}"
    cached = replay_or_lock(
        db, identity.user.id, "PUT", route, idempotency_key, body.model_dump(mode="json")
    )
    if cached is not None:
        return cached
    if policy.state != "draft" or policy.revision != body.expected_revision:
        raise HTTPException(status_code=409, detail="NumericPolicy revision or state changed")
    try:
        payload = NumericPolicyPayload.model_validate_json(
            json.dumps(body.payload, ensure_ascii=False)
        ).model_dump(mode="json", by_alias=True)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="NumericPolicy schema is invalid") from exc
    policy.payload_json = payload
    policy.content_hash = canonical_hash(payload)
    policy.revision += 1
    response = {
        "id": str(policy.id),
        "revision": policy.revision,
        "payload": payload,
        "content_hash": policy.content_hash,
    }
    _audit(
        db,
        identity,
        request,
        "assignment.numeric_policy.updated",
        "numeric_policy_version",
        policy.id,
    )
    store_response(db, identity.user.id, "PUT", route, idempotency_key, response)
    db.commit()
    return response


@router.post("/numeric-policy-versions/{policy_id}/approve")
def approve_numeric_policy(
    policy_id: UUID,
    body: RevisionBody,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, Any]:
    policy = db.scalar(
        select(NumericPolicyVersion).where(NumericPolicyVersion.id == policy_id).with_for_update()
    )
    if policy is None:
        raise HTTPException(status_code=404, detail="NumericPolicy not found")
    _lecturer_assignment(db, policy.assignment_id, identity, lock=True)
    route = f"/api/v1/numeric-policy-versions/{policy_id}/approve"
    cached = _mutate_cached(db, identity, route, idempotency_key, body.model_dump(mode="json"))
    if cached is not None:
        return cached
    if policy.state != "draft" or policy.revision != body.expected_revision:
        raise HTTPException(status_code=409, detail="NumericPolicy revision or state changed")
    policy.state = "approved"
    policy.approved_by = identity.user.id
    policy.approved_at = datetime.now(UTC)
    response = {
        "id": str(policy.id),
        "state": policy.state,
        "content_hash": policy.content_hash,
        "approved_by": str(identity.user.id),
        "approved_at": policy.approved_at.isoformat(),
    }
    _audit(
        db,
        identity,
        request,
        "assignment.numeric_policy.approved",
        "numeric_policy_version",
        policy.id,
        {"content_hash": policy.content_hash},
    )
    store_response(db, identity.user.id, "POST", route, idempotency_key, response)
    db.commit()
    return response


@router.post("/specification-versions/{spec_id}/approve")
def approve_specification(
    spec_id: UUID,
    body: SpecApproval,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, Any]:
    spec_row, version = _load_spec(db, spec_id, identity, lock=True)
    _require_schema_v2(spec_row)
    report = db.scalar(
        select(SpecLintReport).where(SpecLintReport.id == body.lint_report_id).with_for_update()
    )
    route = f"/api/v1/specification-versions/{spec_id}/approve"
    cached = _mutate_cached(db, identity, route, idempotency_key, body.model_dump(mode="json"))
    if cached is not None:
        return cached
    if report is None or report.specification_version_id != spec_row.id:
        raise HTTPException(status_code=404, detail="lint report not found")
    if (
        spec_row.state != "draft"
        or spec_row.revision != body.expected_revision
        or report.resolution_revision != body.expected_report_revision
    ):
        raise HTTPException(status_code=409, detail="approval revisions are stale")
    if (
        report.source_spec_revision != spec_row.revision
        or report.source_content_hash != spec_row.content_hash
    ):
        raise HTTPException(status_code=409, detail="lint report is stale")
    if _report_effective(db, report, spec_row) != "passed":
        raise HTTPException(status_code=422, detail="lint report must be effectively passed")
    criterion_set = db.get(CriterionSetVersion, spec_row.criterion_set_version_id)
    if (
        criterion_set is None
        or criterion_set.state != "approved"
        or criterion_set.content_hash is None
        or version.package_approved_at is None
        or criterion_set.instruction_hash != version.instruction_hash
    ):
        raise HTTPException(status_code=409, detail="approved normative package binding required")
    required_policies = {
        UUID(item["numeric_policy_version_id"])
        for item in spec_row.payload_json["elements"]
        if item.get("numeric_policy_version_id")
    }
    if required_policies:
        approved = {
            p.id
            for p in db.scalars(
                select(NumericPolicyVersion).where(
                    NumericPolicyVersion.id.in_(required_policies),
                    NumericPolicyVersion.assignment_id == version.assignment_id,
                    NumericPolicyVersion.state == "approved",
                )
            )
        }
        if approved != required_policies:
            raise HTTPException(
                status_code=422, detail="all referenced NumericPolicies must be approved"
            )
    spec_row.state = "approved"
    spec_row.approved_by = identity.user.id
    spec_row.approved_at = datetime.now(UTC)
    spec_row.approved_lint_report_id = report.id
    spec_row.approved_lint_resolution_revision = report.resolution_revision
    spec_row.fidelity_confirmed_by = identity.user.id
    spec_row.fidelity_confirmed_at = datetime.now(UTC)
    spec_row.revision += 1
    version.specification_version_id = spec_row.id
    version.revision += 1
    response = {
        "id": str(spec_row.id),
        "state": spec_row.state,
        "content_hash": spec_row.content_hash,
        "lint_report_id": str(report.id),
        "resolution_revision": report.resolution_revision,
        "fidelity_confirmation": True,
        "approved_by": str(identity.user.id),
        "approved_at": spec_row.approved_at.isoformat(),
    }
    _audit(
        db,
        identity,
        request,
        "assignment.specification.approved",
        "specification_version",
        spec_row.id,
        {"content_hash": spec_row.content_hash, "lint_report_id": str(report.id)},
    )
    store_response(db, identity.user.id, "POST", route, idempotency_key, response)
    db.commit()
    return response


@router.post("/assignment-versions/{version_id}/publish")
def publish_assignment(
    version_id: UUID,
    body: PublishBody,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, Any]:
    version = db.scalar(
        select(AssignmentVersion).where(AssignmentVersion.id == version_id).with_for_update()
    )
    if version is None:
        raise HTTPException(status_code=404, detail="assignment version not found")
    assignment = _lecturer_assignment(db, version.assignment_id, identity, lock=True)
    route = f"/api/v1/assignment-versions/{version_id}/publish"
    cached = _mutate_cached(db, identity, route, idempotency_key, body.model_dump(mode="json"))
    if cached is not None:
        return cached
    if version.state != "draft" or version.revision != body.expected_revision:
        raise HTTPException(status_code=409, detail="assignment version revision or state changed")
    context = db.get(ContextVersion, version.context_version_id)
    criterion_set = (
        db.get(CriterionSetVersion, version.criterion_set_version_id)
        if version.criterion_set_version_id
        else None
    )
    spec_row = (
        db.get(SpecificationVersion, version.specification_version_id)
        if version.specification_version_id
        else None
    )
    if (
        context is None
        or context.state != "approved"
        or criterion_set is None
        or criterion_set.state != "approved"
        or criterion_set.instruction_hash != version.instruction_hash
        or criterion_set.content_hash is None
        or spec_row is None
        or spec_row.schema_version != 2
        or spec_row.state != "approved"
        or spec_row.criterion_set_version_id != criterion_set.id
        or spec_row.context_version_id != context.id
        or spec_row.instruction_hash != version.instruction_hash
    ):
        raise HTTPException(
            status_code=422, detail="approved context, package and specification are required"
        )
    report = (
        db.get(SpecLintReport, spec_row.approved_lint_report_id)
        if spec_row.approved_lint_report_id
        else None
    )
    if (
        report is None
        or report.resolution_revision != spec_row.approved_lint_resolution_revision
        or report.source_content_hash != spec_row.content_hash
        or report.source_spec_revision != spec_row.revision - 1
        or _report_effective(db, report, spec_row) != "passed"
    ):
        raise HTTPException(status_code=409, detail="approved lint binding is inconsistent")
    version.state = "published"
    version.published_by = identity.user.id
    version.published_at = datetime.now(UTC)
    version.revision += 1
    assignment.active_version_id = version.id
    assignment.revision += 1
    response = {
        "assignment_id": str(assignment.id),
        "assignment_version_id": str(version.id),
        "version_no": version.version_no,
        "state": version.state,
        "published_at": version.published_at.isoformat(),
        "context_version_id": str(context.id),
        "criterion_set_hash": criterion_set.content_hash,
        "specification_hash": spec_row.content_hash,
    }
    _audit(
        db, identity, request, "assignment.published", "assignment_version", version.id, response
    )
    store_response(db, identity.user.id, "POST", route, idempotency_key, response)
    db.commit()
    return response


@router.post("/assignments/{assignment_id}/groups", status_code=201)
def create_group(
    assignment_id: UUID,
    body: GroupInput,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, Any]:
    assignment = _lecturer_assignment(db, assignment_id, identity, lock=True)
    route = f"/api/v1/assignments/{assignment_id}/groups"
    cached = _mutate_cached(db, identity, route, idempotency_key, body.model_dump(mode="json"))
    if cached is not None:
        return cached
    active = (
        db.get(AssignmentVersion, assignment.active_version_id)
        if assignment.active_version_id
        else None
    )
    if active is not None and active.mode != "group":
        raise HTTPException(status_code=409, detail="groups require a group assignment version")
    if len(set(body.member_user_ids)) != len(body.member_user_ids):
        raise HTTPException(status_code=422, detail="member repeated")
    students = set(
        db.scalars(
            select(Enrollment.user_id).where(
                Enrollment.course_id == assignment.course_id,
                Enrollment.role == "student",
                Enrollment.active.is_(True),
                Enrollment.user_id.in_(body.member_user_ids),
            )
        )
    )
    if students != set(body.member_user_ids):
        raise HTTPException(
            status_code=422, detail="all group members must be active students in the course"
        )
    group = AssignmentGroup(assignment_id=assignment.id, name=body.name, active=body.active)
    db.add(group)
    db.flush()
    db.add_all(
        [
            GroupMembership(
                group_id=group.id, assignment_id=assignment.id, user_id=user_id, active=True
            )
            for user_id in body.member_user_ids
        ]
        if body.active
        else []
    )
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="student already belongs to another active group in this assignment",
        ) from exc
    response = _group_response(db, group)
    _audit(db, identity, request, "assignment.group.created", "group", group.id)
    store_response(db, identity.user.id, "POST", route, idempotency_key, response)
    db.commit()
    return response


def _group_response(db: DbSession, group: AssignmentGroup) -> dict[str, Any]:
    memberships = db.scalars(
        select(GroupMembership).where(
            GroupMembership.group_id == group.id,
            GroupMembership.active.is_(True),
        )
    ).all()
    users_by_id = {
        user.id: user
        for user in db.scalars(
            select(User).where(User.id.in_([item.user_id for item in memberships]))
        )
    }
    return {
        "id": str(group.id),
        "assignment_id": str(group.assignment_id),
        "name": group.name,
        "active": group.active,
        "revision": group.revision,
        "members": [
            {
                "user_id": str(item.user_id),
                "display_name": users_by_id[item.user_id].display_name,
            }
            for item in memberships
        ],
    }


@router.get("/assignments/{assignment_id}/groups")
def list_groups(
    assignment_id: UUID,
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
) -> dict[str, Any]:
    assignment, role = _assignment_access(db, assignment_id, identity)
    groups = db.scalars(
        select(AssignmentGroup)
        .where(AssignmentGroup.assignment_id == assignment.id, AssignmentGroup.active.is_(True))
        .order_by(AssignmentGroup.name)
    ).all()
    if role == "student":
        own_group_ids = set(
            db.scalars(
                select(GroupMembership.group_id).where(
                    GroupMembership.assignment_id == assignment.id,
                    GroupMembership.user_id == identity.user.id,
                    GroupMembership.active.is_(True),
                )
            )
        )
        groups = [group for group in groups if group.id in own_group_ids]
        return {"items": [_group_response(db, group) for group in groups]}
    return {"items": [_group_response(db, group) for group in groups]}


@router.put("/groups/{group_id}")
def update_group(
    group_id: UUID,
    body: GroupUpdate,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, Any]:
    group = db.scalar(
        select(AssignmentGroup).where(AssignmentGroup.id == group_id).with_for_update()
    )
    if group is None:
        raise HTTPException(status_code=404, detail="group not found")
    assignment = _lecturer_assignment(db, group.assignment_id, identity, lock=True)
    route = f"/api/v1/groups/{group_id}"
    cached = replay_or_lock(
        db, identity.user.id, "PUT", route, idempotency_key, body.model_dump(mode="json")
    )
    if cached is not None:
        return cached
    if group.revision != body.expected_revision:
        raise HTTPException(status_code=409, detail="group revision mismatch")
    if len(set(body.member_user_ids)) != len(body.member_user_ids):
        raise HTTPException(status_code=422, detail="member repeated")
    students = set(
        db.scalars(
            select(Enrollment.user_id).where(
                Enrollment.course_id == assignment.course_id,
                Enrollment.role == "student",
                Enrollment.active.is_(True),
                Enrollment.user_id.in_(body.member_user_ids),
            )
        )
    )
    if students != set(body.member_user_ids):
        raise HTTPException(
            status_code=422, detail="all group members must be active students in the course"
        )
    existing = db.scalars(select(GroupMembership).where(GroupMembership.group_id == group.id)).all()
    desired = set(body.member_user_ids) if body.active else set()
    for membership in existing:
        membership.active = membership.user_id in desired
    existing_ids = {item.user_id for item in existing}
    for member_id in desired - existing_ids:
        db.add(
            GroupMembership(
                group_id=group.id, assignment_id=assignment.id, user_id=member_id, active=True
            )
        )
    group.name = body.name
    group.active = body.active
    group.revision += 1
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="student already belongs to another active group in this assignment",
        ) from exc
    response = _group_response(db, group)
    _audit(db, identity, request, "assignment.group.updated", "group", group.id)
    store_response(db, identity.user.id, "PUT", route, idempotency_key, response)
    db.commit()
    return response
