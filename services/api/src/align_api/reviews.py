from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BeforeValidator, Field, StrictInt, StrictStr
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session as DbSession

from align_api.auth import (
    Identity,
    StrictRequest,
    require_password_change_completed,
    require_verified_active_identity,
)
from align_api.config import get_settings
from align_api.db import get_db
from align_api.idempotency import canonical_hash, replay_or_lock, store_response
from align_api.models import (
    AnalysisRun,
    AnalysisRunStage,
    Assignment,
    AssignmentVersion,
    AuditEvent,
    CheckElement,
    Course,
    CoverageInspection,
    CoverageRecord,
    Criterion,
    CriterionSetVersion,
    ElementCheck,
    ElementRequirementLink,
    Enrollment,
    EvidenceItem,
    EvidenceLink,
    FeedbackDraft,
    FileAsset,
    FindingCandidate,
    FindingCriterionLink,
    FindingElementLink,
    FindingEvidenceLink,
    FindingRequirementLink,
    GroundingEvaluation,
    Job,
    JobStage,
    ManualEvidence,
    MethodManifest,
    NumericCheck,
    NumericObservation,
    NumericPolicyVersion,
    OutboxEvent,
    ParseArtifact,
    ProviderCallRecord,
    Publication,
    PublicationEvent,
    ReleaseManifest,
    Requirement,
    ReviewAnnotation,
    ReviewAnnotationRevision,
    ReviewAnnotationTarget,
    ReviewElementDecision,
    ReviewElementDecisionEvent,
    ReviewElementDecisionEvidence,
    ReviewFinding,
    ReviewFindingDecision,
    ReviewFindingTarget,
    ReviewVersion,
    RoutingDecision,
    SourceUnit,
    SpecificationVersion,
    Submission,
    SubmissionVersion,
    SubmissionVersionFile,
    SubmissionVersionMember,
    SupportBinding,
    User,
)
from align_api.submissions import _verify_source_anchor
from align_domain.assignment_contracts import REQUIRED_ROLES
from align_domain.executor import FeedbackDraftPayload
from align_domain.providers import (
    FEEDBACK_DRAFT_JSON_SCHEMA,
    MockProvider,
    OpenAIResponsesProvider,
    PaidCallBlocked,
    ProviderContext,
)
from align_domain.review_numeric import NumericPolicyError, compare_numeric_policy

router = APIRouter(tags=["reviews", "feedback", "publications"])
Category = Literal["missing_requirement", "rubric_coverage_gap", "numerical_inconsistency"]
Resolution = Literal["observed_no_issue", "confirmed_issue", "not_applicable", "unable_to_conclude"]
Action = Literal["accept", "edit", "reject", "defer"]


def _uuid(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            return value
    return value


UUIDInput = Annotated[UUID, BeforeValidator(_uuid)]


class CreateReview(StrictRequest):
    analysis_run_id: UUIDInput | None = None


class ElementDecisionBody(StrictRequest):
    review_status: Literal["reviewed"]
    resolution: Resolution
    applicability_override: Literal["applicable", "not_applicable", "unresolved"] | None = None
    reason: StrictStr | None = Field(default=None, max_length=2000)
    evidence_ids: list[UUIDInput] = Field(default_factory=list, max_length=100)
    expected_revision: StrictInt = Field(ge=1)


class FindingBody(StrictRequest):
    action: Action = "accept"
    primary_category: Category | None = None
    primary_criterion_id: UUIDInput | None = None
    claim: StrictStr | None = Field(default=None, max_length=4000)
    element_ids: list[UUIDInput] | None = Field(default=None, max_length=100)
    requirement_ids: list[UUIDInput] | None = Field(default=None, max_length=100)
    criterion_ids: list[UUIDInput] | None = Field(default=None, max_length=100)
    evidence_ids: list[UUIDInput] | None = Field(default=None, max_length=100)
    coverage_attestation: dict[str, Any] | None = None
    numeric: dict[str, Any] | None = None
    reason: StrictStr | None = Field(default=None, max_length=2000)
    expected_revision: StrictInt = Field(ge=1)


class ManualFindingBody(StrictRequest):
    action: Literal["accept"] = "accept"
    primary_category: Category
    primary_criterion_id: UUIDInput
    claim: StrictStr = Field(min_length=1, max_length=4000)
    element_ids: list[UUIDInput] = Field(min_length=1, max_length=100)
    requirement_ids: list[UUIDInput] = Field(default_factory=list, max_length=100)
    criterion_ids: list[UUIDInput] = Field(default_factory=list, max_length=100)
    evidence_ids: list[UUIDInput] = Field(default_factory=list, max_length=100)
    coverage_attestation: dict[str, Any] | None = None
    numeric: dict[str, Any] | None = None
    reason: StrictStr | None = Field(default=None, max_length=2000)
    expected_revision: StrictInt = Field(ge=1)


class NotesBody(StrictRequest):
    lecturer_notes: StrictStr = Field(max_length=20000)
    limitations: list[StrictStr] = Field(default_factory=list, max_length=100)
    expected_revision: StrictInt = Field(ge=1)


class RevisionBody(StrictRequest):
    expected_revision: StrictInt = Field(ge=1)


class ManualEvidenceBody(StrictRequest):
    file_id: UUIDInput
    source_sha256: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")
    anchor: dict[str, Any]
    transcription: StrictStr | None = Field(default=None, max_length=20000)
    original_evidence_id: UUIDInput | None = None
    reason: StrictStr | None = Field(default=None, max_length=2000)
    element_ids: list[UUIDInput] = Field(default_factory=list, max_length=100)
    review_finding_id: UUIDInput | None = None
    relationship_code: StrictStr | None = Field(default=None, max_length=120)
    role: StrictStr | None = Field(default=None, max_length=120)
    expected_revision: StrictInt = Field(ge=1)


class AnnotationBody(StrictRequest):
    kind: Literal["span_selection", "transcription", "evidence_binding"]
    file_id: UUIDInput | None = None
    source_sha256: StrictStr | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    anchor: dict[str, Any] | None = None
    original_evidence_id: UUIDInput | None = None
    evidence_item_id: UUIDInput | None = None
    transcription: StrictStr | None = Field(default=None, max_length=20000)
    element_ids: list[UUIDInput] = Field(default_factory=list, max_length=100)
    review_finding_id: UUIDInput | None = None
    relationship_code: StrictStr | None = Field(default=None, max_length=120)
    role: StrictStr | None = Field(default=None, max_length=120)
    reason: StrictStr | None = Field(default=None, max_length=2000)
    expected_revision: StrictInt = Field(ge=1)


class AnnotationRevisionBody(AnnotationBody):
    operation: Literal["upsert", "remove"]


class FeedbackBody(StrictRequest):
    mode: Literal["model", "template", "manual"]
    body_markdown: StrictStr | None = Field(default=None, max_length=20000)
    blocks: list[dict[str, Any]] | None = Field(default=None, max_length=100)
    included_review_finding_ids: list[UUIDInput] | None = Field(default=None, max_length=200)
    exclusion_reasons: dict[str, StrictStr] = Field(default_factory=dict)
    expected_revision: StrictInt = Field(ge=1)


class FeedbackUpdateBody(StrictRequest):
    body_markdown: StrictStr = Field(max_length=20000)
    blocks: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    included_review_finding_ids: list[UUIDInput] | None = Field(default=None, max_length=200)
    exclusion_reasons: dict[str, StrictStr] = Field(default_factory=dict)
    expected_revision: StrictInt = Field(ge=1)


class ManualFindingPatchBody(StrictRequest):
    action: Literal["edit", "reject"]
    primary_category: Category | None = None
    primary_criterion_id: UUIDInput | None = None
    claim: StrictStr | None = Field(default=None, max_length=4000)
    element_ids: list[UUIDInput] | None = Field(default=None, max_length=100)
    requirement_ids: list[UUIDInput] | None = Field(default=None, max_length=100)
    criterion_ids: list[UUIDInput] | None = Field(default=None, max_length=100)
    evidence_ids: list[UUIDInput] | None = Field(default=None, max_length=100)
    coverage_attestation: dict[str, Any] | None = None
    numeric: dict[str, Any] | None = None
    reason: StrictStr | None = Field(default=None, max_length=2000)
    expected_revision: StrictInt = Field(ge=1)


class FeedbackApproveBody(StrictRequest):
    expected_revision: StrictInt = Field(ge=1)
    expected_review_revision: StrictInt = Field(ge=1)


class PublishBody(StrictRequest):
    feedback_draft_id: UUIDInput
    expected_revision: StrictInt = Field(ge=1)
    expected_feedback_revision: StrictInt = Field(ge=1)


class WithdrawBody(StrictRequest):
    reason: StrictStr = Field(min_length=1, max_length=2000)


def _err(message: str, status: int = 422) -> HTTPException:
    return HTTPException(status_code=status, detail=message)


def _hidden() -> HTTPException:
    return HTTPException(status_code=404, detail="review resource not found")


def _source(db: DbSession, review: ReviewVersion) -> tuple[SubmissionVersion, Submission, Assignment, AssignmentVersion]:
    sv = db.get(SubmissionVersion, review.submission_version_id)
    submission = db.get(Submission, sv.submission_id) if sv else None
    assignment = db.get(Assignment, submission.assignment_id) if submission else None
    version = db.get(AssignmentVersion, sv.assignment_version_id) if sv else None
    if not sv or not submission or not assignment or not version:
        raise _hidden()
    return sv, submission, assignment, version


def _access(db: DbSession, review: ReviewVersion, identity: Identity) -> tuple[SubmissionVersion, Submission, Assignment, AssignmentVersion]:
    sv, submission, assignment, version = _source(db, review)
    role = db.scalar(select(Enrollment.role).where(Enrollment.course_id == assignment.course_id,
        Enrollment.user_id == identity.user.id, Enrollment.active.is_(True), User.active.is_(True))
        .join(User, User.id == Enrollment.user_id))
    if role != "lecturer" or not db.scalar(select(Course.active).where(Course.id == assignment.course_id)):
        raise _hidden()
    return sv, submission, assignment, version


def _approved_spec(db: DbSession, sv: SubmissionVersion, av: AssignmentVersion) -> SpecificationVersion:
    if av.state != "published" or not av.criterion_set_version_id or not av.specification_version_id:
        raise _err("B2 approved package is required", 409)
    criteria = db.get(CriterionSetVersion, av.criterion_set_version_id)
    spec = db.get(SpecificationVersion, av.specification_version_id)
    if (criteria is None or criteria.state != "approved" or spec is None or spec.state != "approved"
        or spec.criterion_set_version_id != criteria.id or spec.context_version_id != av.context_version_id
        or spec.instruction_hash != av.instruction_hash):
        raise _err("B2 package and specification snapshot do not match", 409)
    return spec


def _audit(db: DbSession, identity: Identity, request: Request, action: str, obj: UUID,
           old_revision: int | None, new_revision: int | None) -> None:
    db.add(AuditEvent(actor_user_id=identity.user.id, action=action, object_type="review_version",
        object_id=obj, old_version_refs_json={"revision": old_revision} if old_revision else None,
        new_version_refs_json={"revision": new_revision} if new_revision else None,
        request_id=request.state.request_id))


def _review(db: DbSession, review_id: UUID, lock: bool = False) -> ReviewVersion:
    q = select(ReviewVersion).where(ReviewVersion.id == review_id)
    if lock:
        q = q.with_for_update()
    row = db.scalar(q)
    if row is None:
        raise _hidden()
    return row


def _evidence(db: DbSession, source: SubmissionVersion, ids: list[UUID],
              review: ReviewVersion | None = None) -> list[EvidenceItem]:
    if len(set(ids)) != len(ids):
        raise _err("evidence IDs must be unique")
    files = {row.file_asset_id for row in db.scalars(select(SubmissionVersionFile).where(
        SubmissionVersionFile.submission_version_id == source.id)).all()}
    items = []
    for item_id in ids:
        item = db.get(EvidenceItem, item_id)
        asset = db.get(FileAsset, item.file_asset_id) if item else None
        if (item is None or item.submission_version_id != source.id or item.file_asset_id not in files
            or asset is None or asset.validation_state != "validated" or item.source_sha256 != asset.sha256):
            raise _hidden()
        manual = db.scalar(select(ManualEvidence).where(ManualEvidence.evidence_item_id == item.id))
        if manual:
            revision = db.get(ReviewAnnotationRevision, manual.annotation_revision_id)
            annotation = db.get(ReviewAnnotation, revision.annotation_id) if revision else None
            if (review is None or manual.review_version_id != review.id or not revision or not annotation
                or annotation.current_revision_id != revision.id
                or revision.operation != "upsert"):
                raise _err("removed manual evidence must be reselected in a new annotation revision", 409)
        items.append(item)
    return items


def _active_links(db: DbSession, review: ReviewVersion, finding_id: UUID) -> list[EvidenceLink]:
    rows = db.scalars(select(EvidenceLink).where(EvidenceLink.review_finding_id == finding_id)
                      .order_by(EvidenceLink.created_at, EvidenceLink.id)).all()
    result: list[EvidenceLink] = []
    seen: set[UUID] = set()
    for row in rows:
        if row.evidence_item_id in seen:
            continue
        if row.annotation_revision_id:
            rev = db.get(ReviewAnnotationRevision, row.annotation_revision_id)
            annotation = db.get(ReviewAnnotation, rev.annotation_id) if rev else None
            if not rev or not annotation or annotation.current_revision_id != rev.id or rev.operation != "upsert":
                continue
        item = db.get(EvidenceItem, row.evidence_item_id)
        if item and item.submission_version_id == review.submission_version_id:
            result.append(row)
            seen.add(item.id)
    return result


def _active_decision_evidence(db: DbSession, decision_id: UUID) -> list[ReviewElementDecisionEvidence]:
    rows = db.scalars(select(ReviewElementDecisionEvidence).where(
        ReviewElementDecisionEvidence.review_element_decision_id == decision_id)
        .order_by(ReviewElementDecisionEvidence.evidence_item_id)).all()
    result = []
    for row in rows:
        if row.annotation_revision_id:
            revision = db.get(ReviewAnnotationRevision, row.annotation_revision_id)
            annotation = db.get(ReviewAnnotation, revision.annotation_id) if revision else None
            if not revision or not annotation or annotation.current_revision_id != revision.id or revision.operation != "upsert":
                continue
        result.append(row)
    return result


def _targets(db: DbSession, review: ReviewVersion, primary_criterion: UUID,
             element_ids: list[UUID], requirement_ids: list[UUID], criterion_ids: list[UUID]
             ) -> tuple[list[CheckElement], list[Requirement], list[Criterion]]:
    sv, _, _, av = _source(db, review)
    spec = _approved_spec(db, sv, av)
    elements = db.scalars(select(CheckElement).where(CheckElement.specification_version_id == spec.id,
        CheckElement.element_id.in_(element_ids))).all() if element_ids else []
    requirements = db.scalars(select(Requirement).where(Requirement.criterion_set_version_id == spec.criterion_set_version_id,
        Requirement.id.in_(requirement_ids))).all() if requirement_ids else []
    criteria = db.scalars(select(Criterion).where(Criterion.criterion_set_version_id == spec.criterion_set_version_id,
        Criterion.id.in_(criterion_ids))).all() if criterion_ids else []
    primary = db.get(Criterion, primary_criterion)
    if len(elements) != len(set(element_ids)) or len(requirements) != len(set(requirement_ids)) or len(criteria) != len(set(criterion_ids)):
        raise _err("finding target is outside this approved specification")
    if primary is None or primary.criterion_set_version_id != spec.criterion_set_version_id:
        raise _err("primary criterion is outside this approved specification")
    if not (elements or requirements or criteria):
        raise _err("finding needs an approved criterion, element, or requirement target")
    return list(elements), list(requirements), list(criteria)


def _target_value(db: DbSession, row: ReviewFindingTarget) -> dict[str, str]:
    if row.check_element_id:
        element = db.get(CheckElement, row.check_element_id)
        return {"kind": "element", "id": str(element.element_id if element else row.check_element_id)}
    if row.requirement_id:
        return {"kind": "requirement", "id": str(row.requirement_id)}
    return {"kind": "criterion", "id": str(row.criterion_id)}


def _finding_json(db: DbSession, review: ReviewVersion, finding: ReviewFinding) -> dict[str, Any]:
    targets = db.scalars(select(ReviewFindingTarget).where(ReviewFindingTarget.review_finding_id == finding.id)
                         .order_by(ReviewFindingTarget.id)).all()
    candidate = db.get(FindingCandidate, finding.candidate_id) if finding.candidate_id else None
    numeric = db.get(NumericCheck, finding.numeric_check_id) if finding.numeric_check_id else None
    return {"id": str(finding.id), "candidate_id": str(finding.candidate_id) if finding.candidate_id else None,
        "origin": finding.origin, "action": finding.action, "category": finding.primary_category,
        "criterion_id": str(finding.primary_criterion_id), "claim": finding.claim,
        "targets": [_target_value(db, item) for item in targets],
        "evidence_ids": sorted(str(item.evidence_item_id) for item in _active_links(db, review, finding.id)),
        "coverage_attestation": finding.coverage_attestation_json,
        "numeric_check_id": str(finding.numeric_check_id) if finding.numeric_check_id else None,
        "numeric_check": ({"policy_id": str(numeric.numeric_policy_version_id),
            "comparability": numeric.comparability_status, "operation": numeric.operation,
            "context": numeric.normalized_context_json, "operands": numeric.operands_snapshot_json,
            "result": numeric.result_decimal_text, "difference": numeric.difference_text,
            "allowed_difference": numeric.allowed_difference_text, "inconsistent": numeric.inconsistent}
            if numeric else None),
        "reason": finding.reason, "technical_validation": candidate.technical_validation if candidate else "lecturer",
        "semantic_support": candidate.semantic_support if candidate else "lecturer"}


def _review_hash(db: DbSession, review: ReviewVersion) -> str:
    sv, _, _, av = _source(db, review)
    spec = db.get(SpecificationVersion, av.specification_version_id) if av.specification_version_id else None
    decisions = db.scalars(select(ReviewElementDecision).where(ReviewElementDecision.review_version_id == review.id)
                          .order_by(ReviewElementDecision.check_element_id)).all()
    decisions_json = []
    for row in decisions:
        ev_ids = [link.evidence_item_id for link in _active_decision_evidence(db, row.id)]
        decisions_json.append({"element": str(row.check_element_id), "status": row.review_status,
            "resolution": row.resolution, "override": row.applicability_override, "reason": row.reason,
            "evidence": [str(item) for item in ev_ids], "revision": row.revision})
    findings = db.scalars(select(ReviewFinding).where(ReviewFinding.review_version_id == review.id)
                          .order_by(ReviewFinding.id)).all()
    finding_json = []
    for finding in findings:
        item = _finding_json(db, review, finding)
        numeric = db.get(NumericCheck, finding.numeric_check_id) if finding.numeric_check_id else None
        item["numeric_check"] = ({"policy_id": str(numeric.numeric_policy_version_id),
            "comparability": numeric.comparability_status, "operation": numeric.operation,
            "context": numeric.normalized_context_json, "operands": numeric.operands_snapshot_json,
            "result": numeric.result_decimal_text, "difference": numeric.difference_text,
            "allowed_difference": numeric.allowed_difference_text, "inconsistent": numeric.inconsistent}
            if numeric else None)
        finding_json.append(item)
    annotations = []
    for annotation in db.scalars(select(ReviewAnnotation).where(ReviewAnnotation.review_version_id == review.id)
                                 .order_by(ReviewAnnotation.id)).all():
        rev = db.get(ReviewAnnotationRevision, annotation.current_revision_id) if annotation.current_revision_id else None
        if rev:
            annotations.append({"id": str(annotation.id), "kind": annotation.kind, "revision_id": str(rev.id),
                "revision_no": rev.revision_no, "operation": rev.operation, "file_id": str(rev.file_asset_id),
                "source_sha256": rev.source_sha256, "anchor": rev.anchor_json,
                "original_evidence_id": str(rev.original_evidence_id) if rev.original_evidence_id else None,
                "corrected_evidence_id": str(rev.corrected_evidence_id) if rev.corrected_evidence_id else None,
                "transcription": rev.transcription, "reason": rev.reason})
    return canonical_hash({"submission_version_id": str(sv.id), "assignment_version_id": str(av.id),
        "specification_version_id": str(spec.id) if spec else None,
        "specification_content_hash": spec.content_hash if spec else None,
        "analysis_run_id": str(review.analysis_run_id) if review.analysis_run_id else None,
        "decisions": decisions_json, "findings": finding_json, "annotations": annotations,
        "lecturer_notes": review.lecturer_notes, "limitations": review.limitations_json})


def _touch(db: DbSession, review: ReviewVersion) -> tuple[int, int]:
    old = review.revision
    review.revision += 1
    review.state = "draft"
    review.completed_by = None
    review.completed_at = None
    db.flush()
    review.content_hash = _review_hash(db, review)
    db.flush()
    return old, review.revision


def _coverage(db: DbSession, source: SubmissionVersion, value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    file_ids, note = value.get("source_file_ids"), value.get("inspection_note")
    if not isinstance(file_ids, list):
        raise _err("coverage attestation has invalid final source IDs")
    try:
        ids = [UUID(str(item)) for item in file_ids]
    except (ValueError, TypeError) as exc:
        raise _err("coverage attestation has invalid final source IDs") from exc
    manifest = {row.file_asset_id for row in db.scalars(select(SubmissionVersionFile).where(
        SubmissionVersionFile.submission_version_id == source.id)).all()}
    if not ids or len(ids) > 100 or len(set(ids)) != len(ids) or not set(ids) <= manifest:
        raise _err("coverage attestation must cite files in this final manifest")
    if not isinstance(note, str) or not 1 <= len(note.strip()) <= 2000:
        raise _err("coverage attestation requires a 1-2,000 character inspection note")
    return {"source_file_ids": [str(item) for item in sorted(ids)], "inspection_note": note.strip(),
            "attested_by": "lecturer"}


def _numeric_check(db: DbSession, review: ReviewVersion, assignment: Assignment,
                   payload: dict[str, Any], evidence: list[EvidenceItem]) -> NumericCheck:
    try:
        policy_id = UUID(str(payload.get("numeric_policy_version_id")))
    except ValueError as exc:
        raise _err("numeric policy ID is invalid") from exc
    policy = db.get(NumericPolicyVersion, policy_id)
    if not policy or policy.assignment_id != assignment.id or policy.state != "approved":
        raise _err("an approved NumericPolicy for this assignment is required")
    by_id = {item.id: item for item in evidence}
    operands = payload.get("operands")
    if not isinstance(operands, list) or not operands or len(operands) > 20:
        raise _err("numeric operands are required")
    checked: list[dict[str, Any]] = []
    observations: list[NumericObservation] = []
    for operand in operands:
        if not isinstance(operand, dict):
            raise _err("numeric operand is invalid")
        try:
            evidence_id = UUID(str(operand.get("evidence_id")))
        except ValueError as exc:
            raise _err("numeric evidence ID is invalid") from exc
        item = by_id.get(evidence_id)
        literal, context, quality = operand.get("literal"), operand.get("context"), operand.get("source_quality")
        if item is None or not isinstance(literal, str) or not isinstance(context, dict):
            raise _err("each numeric operand needs selected evidence, literal, and context")
        source_values = [item.content_original_json.get(key) for key in ("text", "value", "display_value")]
        source_text = "\n".join(str(value) for value in source_values if value is not None)
        bad_formula = item.content_original_json.get("formula") is not None and item.content_original_json.get("cache_verified") is not True
        bad_ocr = item.content_original_json.get("ocr_unverified") is True
        if quality == "native_literal" and (bad_formula or bad_ocr or literal not in source_text):
            raise _err("numeric literal is not verified by the selected native evidence")
        if quality not in {"native_literal", "lecturer_confirmed"}:
            raise _err("only native literals or lecturer-confirmed sources can verify a numeric finding")
        if quality == "lecturer_confirmed" and not str(payload.get("verification_note", "")).strip():
            raise _err("lecturer-confirmed operands require a verification note")
        checked.append({"evidence_id": str(item.id), "literal": literal, "context": context,
                        "role": operand.get("role"), "source_quality": quality})
        observation = NumericObservation(review_version_id=review.id, evidence_item_id=item.id,
            literal=literal, context_json=context, source_quality=quality,
            verification_ref_json={"actor_confirmed": quality == "lecturer_confirmed"})
        db.add(observation)
        observations.append(observation)
    try:
        result = compare_numeric_policy(policy.payload_json, checked)
    except NumericPolicyError as exc:
        raise _err(f"numeric comparison is not verified: {exc}") from exc
    for observation, snapshot in zip(observations, result["operands_snapshot"], strict=False):
        if isinstance(snapshot, dict):
            observation.canonical_decimal_text = snapshot.get("normalized_decimal")
    check = NumericCheck(review_version_id=review.id, numeric_policy_version_id=policy.id,
        comparability_status=result["comparability_status"], operation=result["operation"],
        normalized_context_json=result["normalized_context"], operands_snapshot_json=result["operands_snapshot"],
        result_decimal_text=result["result_decimal_text"], difference_text=result["difference_text"],
        allowed_difference_text=result["allowed_difference_text"], inconsistent=result["inconsistent"])
    db.add(check)
    db.flush()
    return check


def _set_targets(db: DbSession, finding: ReviewFinding, elements: list[CheckElement],
                 requirements: list[Requirement], criteria: list[Criterion]) -> None:
    db.query(ReviewFindingTarget).filter(ReviewFindingTarget.review_finding_id == finding.id).delete(synchronize_session=False)
    for element_row in elements:
        db.add(ReviewFindingTarget(review_finding_id=finding.id, check_element_id=element_row.id))
    for requirement_row in requirements:
        db.add(ReviewFindingTarget(review_finding_id=finding.id, requirement_id=requirement_row.id))
    for criterion_row in criteria:
        db.add(ReviewFindingTarget(review_finding_id=finding.id, criterion_id=criterion_row.id))


def _decision_event(db: DbSession, finding: ReviewFinding, evidence_ids: list[UUID], identity: Identity) -> None:
    targets = db.scalars(select(ReviewFindingTarget).where(ReviewFindingTarget.review_finding_id == finding.id)
                         .order_by(ReviewFindingTarget.id)).all()
    db.add(ReviewFindingDecision(review_finding_id=finding.id, revision_no=finding.revision,
        action=finding.action, payload_json={"category": finding.primary_category,
        "criterion_id": str(finding.primary_criterion_id), "claim": finding.claim,
        "targets": [_target_value(db, item) for item in targets],
        "evidence_ids": [str(item) for item in sorted(evidence_ids)],
        "coverage_attestation": finding.coverage_attestation_json,
        "numeric_check_id": str(finding.numeric_check_id) if finding.numeric_check_id else None,
        "reason": finding.reason}, actor_id=identity.user.id))


def _candidate_defaults(db: DbSession, finding: ReviewFinding) -> tuple[list[UUID], list[UUID], list[UUID], list[UUID]]:
    candidate = db.get(FindingCandidate, finding.candidate_id) if finding.candidate_id else None
    if candidate is None:
        return [], [], [], []
    return (
        list(db.scalars(select(CheckElement.element_id).join(FindingElementLink,
            FindingElementLink.check_element_id == CheckElement.id).where(
            FindingElementLink.finding_candidate_id == candidate.id)).all()),
        list(db.scalars(select(FindingRequirementLink.requirement_id).where(FindingRequirementLink.finding_candidate_id == candidate.id)).all()),
        list(db.scalars(select(FindingCriterionLink.criterion_id).where(FindingCriterionLink.finding_candidate_id == candidate.id)).all()),
        list(db.scalars(select(FindingEvidenceLink.evidence_item_id).where(FindingEvidenceLink.finding_candidate_id == candidate.id)).all()),
    )


def _apply_decision(db: DbSession, review: ReviewVersion, source: SubmissionVersion, assignment: Assignment,
                    finding: ReviewFinding, body: FindingBody | ManualFindingBody | ManualFindingPatchBody,
                    identity: Identity, *, manual: bool = False, initial: bool = False) -> None:
    if body.action == "reject":
        finding.action, finding.reason = "reject", body.reason.strip() if body.reason else None
        finding.actor_id, finding.acted_at = identity.user.id, datetime.now(UTC)
        if not initial:
            finding.revision += 1
        db.flush()
        _decision_event(db, finding, [], identity)
        return
    if body.action == "defer":
        if not body.reason or not body.reason.strip():
            raise _err("defer requires a limitation/reason")
        finding.action, finding.reason = "defer", body.reason.strip()
        finding.actor_id, finding.acted_at = identity.user.id, datetime.now(UTC)
        if not initial:
            finding.revision += 1
        review.limitations_json = list(dict.fromkeys([*review.limitations_json, finding.reason]))
        db.flush()
        _decision_event(db, finding, [], identity)
        return
    default_elements, default_requirements, default_criteria, default_evidence = _candidate_defaults(db, finding)
    elements_ids = body.element_ids if body.element_ids is not None else default_elements
    requirement_ids = body.requirement_ids if body.requirement_ids is not None else default_requirements
    criterion_ids = body.criterion_ids if body.criterion_ids is not None else default_criteria
    evidence_ids = body.evidence_ids if body.evidence_ids is not None else default_evidence
    category = body.primary_category or finding.primary_category
    criterion_id = body.primary_criterion_id or finding.primary_criterion_id
    claim = body.claim.strip() if body.claim and body.claim.strip() else finding.claim
    if not claim:
        raise _err("accepted/edit finding requires a claim")
    elements, requirements, criteria = _targets(db, review, criterion_id, elements_ids, requirement_ids, criterion_ids)
    evidence = _evidence(db, source, evidence_ids, review)
    coverage = _coverage(db, source, body.coverage_attestation)
    if not evidence and coverage is None:
        raise _err("finding needs source evidence or a coverage attestation")
    numeric = None
    if category == "numerical_inconsistency":
        if body.numeric is None:
            raise _err("numeric findings require verified operands and an approved NumericPolicy")
        numeric = _numeric_check(db, review, assignment, body.numeric, evidence)
        if numeric.inconsistent is not True:
            raise _err("numeric operands do not exceed the approved tolerance")
    candidate = db.get(FindingCandidate, finding.candidate_id) if finding.candidate_id else None
    if candidate and candidate.technical_validation == "invalid" and body.action != "edit":
        raise _err("invalid candidate must be edited and re-grounded before acceptance")
    finding.action = body.action
    finding.primary_category, finding.primary_criterion_id, finding.claim = category, criterion_id, claim
    finding.coverage_attestation_json, finding.numeric_check_id = coverage, numeric.id if numeric else None
    finding.reason, finding.actor_id, finding.acted_at = body.reason, identity.user.id, datetime.now(UTC)
    if not initial:
        finding.revision += 1
    _set_targets(db, finding, elements, requirements, criteria)
    db.flush()
    for item in evidence:
        # Manual evidence remains tied to the exact annotation revision that created it.
        manual_row = db.scalar(select(ManualEvidence).where(ManualEvidence.evidence_item_id == item.id))
        active = db.get(ReviewAnnotationRevision, manual_row.annotation_revision_id) if manual_row else None
        if active is not None:
            annotation = db.get(ReviewAnnotation, active.annotation_id)
            if (not annotation or annotation.review_version_id != review.id
                or annotation.current_revision_id != active.id or active.operation != "upsert"):
                active = None
        if active is None and manual_row is None:
            target_elements = [row.id for row in elements]
            candidates = db.scalars(select(ReviewAnnotationRevision).join(ReviewAnnotation,
                ReviewAnnotation.current_revision_id == ReviewAnnotationRevision.id)
                .where(ReviewAnnotation.review_version_id == review.id,
                    ReviewAnnotationRevision.operation == "upsert",
                    ReviewAnnotationRevision.original_evidence_id == item.id)).all()
            for annotation_revision in candidates:
                matched = db.scalar(select(ReviewAnnotationTarget.id).where(
                    ReviewAnnotationTarget.annotation_revision_id == annotation_revision.id,
                    or_(ReviewAnnotationTarget.review_finding_id == finding.id,
                        ReviewAnnotationTarget.check_element_id.in_(target_elements)) if target_elements
                    else ReviewAnnotationTarget.review_finding_id == finding.id,
                ).limit(1))
                if matched:
                    active = annotation_revision
                    break
        existing = db.scalar(select(EvidenceLink.id).where(EvidenceLink.review_finding_id == finding.id,
            EvidenceLink.evidence_item_id == item.id,
            EvidenceLink.annotation_revision_id == (active.id if active else None)).limit(1))
        if not existing:
            db.add(EvidenceLink(evidence_item_id=item.id, review_finding_id=finding.id,
                annotation_revision_id=active.id if active else None))
    db.flush()
    _decision_event(db, finding, [item.id for item in evidence], identity)


def _review_json(db: DbSession, review: ReviewVersion) -> dict[str, Any]:
    sv, _, _, av = _source(db, review)
    spec = db.get(SpecificationVersion, av.specification_version_id) if av.specification_version_id else None
    run = db.get(AnalysisRun, review.analysis_run_id) if review.analysis_run_id else None
    elements = db.scalars(select(CheckElement).where(CheckElement.specification_version_id == spec.id)
                          .order_by(CheckElement.element_code, CheckElement.id)).all() if spec else []
    decisions = {item.check_element_id: item for item in db.scalars(select(ReviewElementDecision)
        .where(ReviewElementDecision.review_version_id == review.id)).all()}
    checks = {item.check_element_id: item for item in db.scalars(select(ElementCheck)
        .where(ElementCheck.analysis_run_id == run.id)).all()} if run else {}
    findings = db.scalars(select(ReviewFinding).where(ReviewFinding.review_version_id == review.id)
                          .order_by(ReviewFinding.created_at, ReviewFinding.id)).all()
    by_element: dict[UUID, list[dict[str, Any]]] = {}
    for finding in findings:
        value = _finding_json(db, review, finding)
        for target in db.scalars(select(ReviewFindingTarget).where(ReviewFindingTarget.review_finding_id == finding.id,
            ReviewFindingTarget.check_element_id.is_not(None))).all():
            if target.check_element_id is not None:
                by_element.setdefault(target.check_element_id, []).append(value)
    result_elements = []
    for element in elements:
        criterion = db.get(Criterion, element.criterion_id)
        requirements = db.scalars(select(Requirement).join(ElementRequirementLink,
            ElementRequirementLink.requirement_id == Requirement.id).where(
            ElementRequirementLink.check_element_id == element.id)).all()
        decision, check = decisions.get(element.id), checks.get(element.id)
        ev = [link.evidence_item_id for link in _active_decision_evidence(db, decision.id)] if decision else []
        analysis = None
        if check:
            coverage = db.scalar(select(CoverageRecord).where(
                CoverageRecord.analysis_run_id == run.id,
                CoverageRecord.check_element_id == element.id)) if run else None
            grounding = db.scalar(select(GroundingEvaluation).where(
                GroundingEvaluation.element_check_id == check.id))
            bindings = db.scalars(select(SupportBinding).where(
                SupportBinding.element_check_id == check.id).order_by(
                SupportBinding.relationship_code, SupportBinding.role,
                SupportBinding.evidence_item_id)).all()
            inspections = db.scalars(select(CoverageInspection).where(
                CoverageInspection.coverage_record_id == coverage.id).order_by(
                CoverageInspection.source_unit_id, CoverageInspection.evidence_item_id)).all() if coverage else []
            analysis = {
                "applicability_status": check.applicability_status,
                "source_coverage_status": check.source_coverage_status,
                "evidence_relationship": check.evidence_relationship,
                "check_status": check.check_status,
                "observations": check.observations_json,
                "interpretation": check.interpretation,
                "reason_codes": check.reason_codes_json,
                "support_bindings": [{
                    "relationship_code": row.relationship_code, "role": row.role,
                    "observation_codes": row.observation_codes_json,
                    "evidence_item_id": str(row.evidence_item_id), "quotes": row.quote_spans_json,
                } for row in bindings],
                "grounding": {
                    "mechanical_status": grounding.mechanical_status,
                    "semantic_status": grounding.semantic_status,
                    "evaluation": grounding.evaluation_json,
                    "limitations": grounding.limitation_codes_json,
                } if grounding else None,
                "coverage": {
                    "retrieval_status": coverage.retrieval_status,
                    "compatible_count": coverage.compatible_count,
                    "inspected_count": coverage.inspected_count,
                    "unresolved_count": coverage.unresolved_count,
                    "sufficient_for_positive": coverage.sufficient_for_positive,
                    "exhaustive_for_negative": coverage.exhaustive_for_negative,
                    "inventory": coverage.inventory_json,
                    "retrieval": coverage.retrieval_json,
                    "limitations": coverage.limitations_json,
                    "inspections": [{
                        "source_unit_id": str(row.source_unit_id),
                        "evidence_item_id": str(row.evidence_item_id) if row.evidence_item_id else None,
                        "state": row.state, "method": row.method,
                        "reason_code": row.reason_code, "detail": row.detail_json,
                    } for row in inspections],
                } if coverage else None,
            }
        result_elements.append({"id": str(element.element_id), "check_element_id": str(element.id),
            "code": element.element_code,
            "criterion": {"id": str(criterion.id), "code": criterion.code, "title": criterion.title} if criterion else None,
            "requirements": [{"id": str(x.id), "code": x.code, "text": x.text, "mandatory": x.mandatory} for x in requirements],
            "decision_question": element.decision_question, "grounding_mode": element.grounding_mode,
            "target_kind": element.target_kind, "payload": element.payload_json,
            "decision": {"review_status": decision.review_status, "resolution": decision.resolution,
                "applicability_override": decision.applicability_override, "reason": decision.reason,
                "evidence_ids": [str(x) for x in ev]} if decision else {"review_status": "pending", "resolution": None},
            "analysis": analysis,
            "findings": by_element.get(element.id, [])})
    annotation_rows = db.scalars(select(ReviewAnnotation).where(ReviewAnnotation.review_version_id == review.id)
                                  .order_by(ReviewAnnotation.created_at, ReviewAnnotation.id)).all()
    annotations = []
    for annotation in annotation_rows:
        current = db.get(ReviewAnnotationRevision, annotation.current_revision_id) if annotation.current_revision_id else None
        revisions = db.scalars(select(ReviewAnnotationRevision).where(ReviewAnnotationRevision.annotation_id == annotation.id)
                                .order_by(ReviewAnnotationRevision.revision_no)).all()
        annotations.append({"id": str(annotation.id), "kind": annotation.kind,
            "current_revision_id": str(current.id) if current else None,
            "current": _annotation_json(db, current) if current else None,
            "history": [_annotation_json(db, row) for row in revisions]})
    source_files = db.execute(select(FileAsset, SubmissionVersionFile.display_order).join(
        SubmissionVersionFile, SubmissionVersionFile.file_asset_id == FileAsset.id).where(
        SubmissionVersionFile.submission_version_id == sv.id).order_by(SubmissionVersionFile.display_order)).all()
    return {"id": str(review.id), "submission_version_id": str(sv.id), "submission_version": sv.version_no,
        "assignment_version_id": str(av.id), "assignment_version": av.version_no,
        "analysis_run": {"id": str(run.id), "status": run.status,
            "completion_kind": run.completion_kind, "config_fingerprint": run.config_fingerprint,
            "provider_mode": run.provider_mode, "data_origin": run.data_origin,
            "label": "SIMULASI SINTETIS • data sintetis" if run.provider_mode == "mock" or run.data_origin == "synthetic" else "analysis run",
            "manifest_snapshot": run.manifest_snapshot_json, "usage": run.usage_snapshot_json,
            "stages": [{"stage": row.stage_name, "status": row.status,
                "output_refs": row.output_refs_json, "limitations": row.limitation_codes_json}
                for row in db.scalars(select(AnalysisRunStage).where(
                    AnalysisRunStage.analysis_run_id == run.id).order_by(
                    AnalysisRunStage.created_at, AnalysisRunStage.id)).all()] if run else [],
            "routing_decisions": [{"check_element_id": str(row.check_element_id) if row.check_element_id else None,
                "task": row.task, "action": row.action, "selected_role": row.selected_role,
                "reason_code": row.reason_code, "features": row.feature_snapshot_json,
                "guards": row.guard_snapshot_json, "outcome": row.outcome}
                for row in db.scalars(select(RoutingDecision).where(
                    RoutingDecision.analysis_run_id == run.id).order_by(
                    RoutingDecision.created_at, RoutingDecision.id)).all()] if run else [],
            "limitations": run.limitations_json} if run else None,
        "state": review.state, "revision": review.revision, "content_hash": review.content_hash,
        "lecturer_notes": review.lecturer_notes, "limitations": review.limitations_json,
        "source_files": [{"id": str(asset.id), "name": asset.original_name, "mime": asset.mime,
            "size_bytes": asset.size_bytes, "sha256": asset.sha256, "preflight": asset.preflight_json,
            "display_order": order} for asset, order in source_files],
        "elements": result_elements, "findings": [_finding_json(db, review, row) for row in findings],
        "annotations": annotations}


@router.get("/reviews/{review_id}/evidence")
def list_review_evidence(review_id: UUID, identity: Annotated[Identity, Depends(require_password_change_completed)],
                         db: Annotated[DbSession, Depends(get_db)],
                         cursor: Annotated[int, Query(ge=0)] = 0,
                         limit: Annotated[int, Query(ge=1, le=100)] = 50) -> dict[str, Any]:
    review = _review(db, review_id)
    _access(db, review, identity)
    rows = db.execute(select(EvidenceItem, FileAsset).join(FileAsset, FileAsset.id == EvidenceItem.file_asset_id)
        .where(EvidenceItem.submission_version_id == review.submission_version_id)
        .order_by(EvidenceItem.created_at, EvidenceItem.id).offset(cursor).limit(limit + 1)).all()
    has_more = len(rows) > limit
    result = []
    for item, asset in rows[:limit]:
        unit = db.get(SourceUnit, item.source_unit_id)
        if (unit is None or asset.validation_state != "validated" or item.source_sha256 != asset.sha256
            or item.anchor_json != unit.anchor_json or not _verify_source_anchor(unit.anchor_json, asset)):
            continue
        decision_links = db.scalars(select(ReviewElementDecisionEvidence.review_element_decision_id).join(
            ReviewElementDecision, ReviewElementDecision.id == ReviewElementDecisionEvidence.review_element_decision_id)
            .where(ReviewElementDecision.review_version_id == review.id,
                   ReviewElementDecisionEvidence.evidence_item_id == item.id)).all()
        findings = db.scalars(select(ReviewFinding.id).join(EvidenceLink,
            EvidenceLink.review_finding_id == ReviewFinding.id).where(
            ReviewFinding.review_version_id == review.id, EvidenceLink.evidence_item_id == item.id)).all()
        result.append({"id": str(item.id), "type": item.type, "source_unit_id": str(unit.id),
            "file_id": str(asset.id), "file_name": asset.original_name, "mime": asset.mime,
            "source_sha256": item.source_sha256, "anchor": item.anchor_json,
            "content": item.content_original_json, "parse_status": item.parse_status,
            "limitations": item.limitations_json,
            "selected_for_element_decisions": bool(decision_links),
            "linked_finding_ids": sorted({str(value) for value in findings}),
            "original_url": f"/api/v1/files/{asset.id}/content"})
    return {"items": result, "next_cursor": cursor + limit if has_more else None}


def _annotation_json(db: DbSession, revision: ReviewAnnotationRevision) -> dict[str, Any]:
    targets = db.scalars(select(ReviewAnnotationTarget).where(ReviewAnnotationTarget.annotation_revision_id == revision.id)
                         .order_by(ReviewAnnotationTarget.id)).all()
    return {"id": str(revision.id), "revision_no": revision.revision_no, "operation": revision.operation,
        "file_id": str(revision.file_asset_id), "source_sha256": revision.source_sha256,
        "anchor": revision.anchor_json,
        "original_evidence_id": str(revision.original_evidence_id) if revision.original_evidence_id else None,
        "corrected_evidence_id": str(revision.corrected_evidence_id) if revision.corrected_evidence_id else None,
        "transcription": revision.transcription, "reason": revision.reason,
        "targets": [{"check_element_id": str(row.check_element_id) if row.check_element_id else None,
            "review_finding_id": str(row.review_finding_id) if row.review_finding_id else None,
            "relationship_code": row.relationship_code, "role": row.role} for row in targets],
        "author_id": str(revision.author_id), "created_at": revision.created_at.isoformat()}


@router.post("/submission-versions/{submission_version_id}/reviews", status_code=201)
def create_review(submission_version_id: UUID, body: CreateReview, request: Request,
                  identity: Annotated[Identity, Depends(require_verified_active_identity)],
                  db: Annotated[DbSession, Depends(get_db)],
                  idem: Annotated[str, Header(alias="Idempotency-Key")]) -> dict[str, Any]:
    route = f"/submission-versions/{submission_version_id}/reviews"
    replay = replay_or_lock(db, identity.user.id, "POST", route, idem, body.model_dump(mode="json"))
    if replay is not None:
        return replay
    sv = db.get(SubmissionVersion, submission_version_id)
    submission = db.get(Submission, sv.submission_id) if sv else None
    assignment = db.get(Assignment, submission.assignment_id) if submission else None
    av = db.get(AssignmentVersion, sv.assignment_version_id) if sv else None
    if not sv or not submission or not assignment or not av:
        raise _hidden()
    role = db.scalar(select(Enrollment.role).where(Enrollment.course_id == assignment.course_id,
        Enrollment.user_id == identity.user.id, Enrollment.active.is_(True)))
    if role != "lecturer" or not db.scalar(select(User.id).where(User.id == identity.user.id, User.active.is_(True))) \
        or not db.scalar(select(Course.id).where(Course.id == assignment.course_id, Course.active.is_(True))):
        raise _hidden()
    spec = _approved_spec(db, sv, av)
    run = db.get(AnalysisRun, body.analysis_run_id) if body.analysis_run_id else None
    candidates: list[FindingCandidate] = []
    if body.analysis_run_id:
        if not run or run.submission_version_id != sv.id or run.specification_version_id != spec.id or run.status in {"queued", "running"}:
            raise _err("analysis run must be terminal and match this source/specification", 409)
        candidates = list(db.scalars(select(FindingCandidate).where(FindingCandidate.analysis_run_id == run.id)).all())
    elements = db.scalars(select(CheckElement).where(CheckElement.specification_version_id == spec.id)).all()
    if not elements:
        raise _err("approved specification contains no review elements", 409)
    number = (db.scalar(select(func.max(ReviewVersion.version_no)).where(ReviewVersion.submission_version_id == sv.id)) or 0) + 1
    review = ReviewVersion(submission_version_id=sv.id, analysis_run_id=run.id if run else None,
        version_no=number, state="draft", content_hash="0" * 64, lecturer_notes="", limitations_json=[], revision=1)
    db.add(review)
    db.flush()
    for element in elements:
        db.add(ReviewElementDecision(review_version_id=review.id, check_element_id=element.id, review_status="pending"))
    criterion_ids = set(db.scalars(select(Criterion.id).where(
        Criterion.criterion_set_version_id == spec.criterion_set_version_id)).all())
    element_ids = {row.id for row in elements}
    requirement_ids = set(db.scalars(select(Requirement.id).where(
        Requirement.criterion_set_version_id == spec.criterion_set_version_id)).all())
    for candidate in candidates:
        if candidate.primary_criterion_id not in criterion_ids:
            raise _err("candidate criterion is outside this specification")
        finding = ReviewFinding(review_version_id=review.id, candidate_id=candidate.id, origin="ai_review",
            action="pending", primary_category=candidate.primary_category,
            primary_criterion_id=candidate.primary_criterion_id, claim=candidate.claim, revision=1)
        db.add(finding)
        db.flush()
        for model, attr in ((FindingElementLink, "check_element_id"), (FindingRequirementLink, "requirement_id"),
                            (FindingCriterionLink, "criterion_id")):
            rows = db.scalars(select(model).where(model.finding_candidate_id == candidate.id)).all()
            for row in rows:
                target_id = getattr(row, attr)
                if target_id not in {"check_element_id": element_ids,
                                     "requirement_id": requirement_ids,
                                     "criterion_id": criterion_ids}[attr]:
                    raise _err("candidate target is outside this approved specification")
                db.add(ReviewFindingTarget(review_finding_id=finding.id, **{attr: getattr(row, attr)}))
        links = db.scalars(select(FindingEvidenceLink).where(FindingEvidenceLink.finding_candidate_id == candidate.id)).all()
        _evidence(db, sv, [link.evidence_item_id for link in links])
        for link in links:
            item = db.get(EvidenceItem, link.evidence_item_id)
            if not item or item.submission_version_id != sv.id:
                raise _err("candidate evidence is outside this final source")
            db.add(EvidenceLink(evidence_item_id=item.id, review_finding_id=finding.id))
        db.flush()
        _decision_event(db, finding, [row.evidence_item_id for row in links], identity)
    db.flush()
    review.content_hash = _review_hash(db, review)
    _audit(db, identity, request, "review.created", review.id, None, review.revision)
    result = _review_json(db, review)
    store_response(db, identity.user.id, "POST", route, idem, result)
    db.commit()
    return result


@router.get("/submission-versions/{submission_version_id}/reviews")
def list_reviews(submission_version_id: UUID, identity: Annotated[Identity, Depends(require_password_change_completed)],
                 db: Annotated[DbSession, Depends(get_db)]) -> dict[str, Any]:
    sv = db.get(SubmissionVersion, submission_version_id)
    submission = db.get(Submission, sv.submission_id) if sv else None
    assignment = db.get(Assignment, submission.assignment_id) if submission else None
    if not sv or not assignment or not db.scalar(select(Course.id).where(
        Course.id == assignment.course_id, Course.active.is_(True))) or not db.scalar(select(User.id).where(
        User.id == identity.user.id, User.active.is_(True))) or db.scalar(select(Enrollment.id).where(Enrollment.course_id == assignment.course_id,
        Enrollment.user_id == identity.user.id, Enrollment.role == "lecturer", Enrollment.active.is_(True))) is None:
        raise _hidden()
    rows = db.scalars(select(ReviewVersion).where(ReviewVersion.submission_version_id == sv.id)
                      .order_by(ReviewVersion.version_no.desc())).all()
    return {"items": [{"id": str(row.id), "version_no": row.version_no, "state": row.state,
        "revision": row.revision, "content_hash": row.content_hash,
        "analysis_run_id": str(row.analysis_run_id) if row.analysis_run_id else None,
        "created_at": row.created_at.isoformat()} for row in rows]}


@router.get("/reviews/{review_id}")
def get_review(review_id: UUID, identity: Annotated[Identity, Depends(require_password_change_completed)],
               db: Annotated[DbSession, Depends(get_db)]) -> dict[str, Any]:
    row = _review(db, review_id)
    _access(db, row, identity)
    return _review_json(db, row)


@router.put("/reviews/{review_id}/element-decisions/{element_id}")
def put_element_decision(review_id: UUID, element_id: UUID, body: ElementDecisionBody, request: Request,
                         identity: Annotated[Identity, Depends(require_verified_active_identity)],
                         db: Annotated[DbSession, Depends(get_db)],
                         idem: Annotated[str, Header(alias="Idempotency-Key")]) -> dict[str, Any]:
    route = f"/reviews/{review_id}/element-decisions/{element_id}"
    replay = replay_or_lock(db, identity.user.id, "PUT", route, idem, body.model_dump(mode="json"))
    if replay is not None:
        return replay
    review = _review(db, review_id, True)
    source, _, _, av = _access(db, review, identity)
    if body.expected_revision != review.revision:
        raise _err("review revision is stale", 409)
    spec = _approved_spec(db, source, av)
    element = db.scalar(select(CheckElement).where(CheckElement.specification_version_id == spec.id,
        CheckElement.element_id == element_id))
    if element is None:
        raise _hidden()
    evidence = _evidence(db, source, body.evidence_ids, review)
    if body.applicability_override and (not body.reason or not body.reason.strip() or not evidence):
        raise _err("applicability override requires reason and source")
    if body.applicability_override == "not_applicable" and body.resolution != "not_applicable":
        raise _err("not_applicable override must resolve the element as not_applicable")
    if body.applicability_override == "unresolved" and body.resolution != "unable_to_conclude":
        raise _err("unresolved applicability must be recorded as unable_to_conclude")
    decision = db.scalar(select(ReviewElementDecision).where(ReviewElementDecision.review_version_id == review.id,
        ReviewElementDecision.check_element_id == element.id).with_for_update())
    if decision is None:
        raise _err("review decision is missing", 409)
    old = review.revision
    decision.review_status, decision.resolution = body.review_status, body.resolution
    decision.applicability_override = body.applicability_override
    decision.reason = body.reason.strip() if body.reason else None
    decision.decided_by, decision.decided_at = identity.user.id, datetime.now(UTC)
    decision.revision += 1
    db.flush()
    db.query(ReviewElementDecisionEvidence).filter(ReviewElementDecisionEvidence.review_element_decision_id == decision.id).delete(synchronize_session=False)
    for item in evidence:
        manual = db.scalar(select(ManualEvidence).where(ManualEvidence.evidence_item_id == item.id))
        db.add(ReviewElementDecisionEvidence(review_element_decision_id=decision.id,
            evidence_item_id=item.id, annotation_revision_id=manual.annotation_revision_id if manual else None))
    db.add(ReviewElementDecisionEvent(review_version_id=review.id, check_element_id=element.id,
        revision_no=decision.revision, payload_json={"status": decision.review_status, "resolution": decision.resolution,
        "override": decision.applicability_override, "reason": decision.reason,
        "evidence_ids": [str(item.id) for item in evidence]}, actor_id=identity.user.id))
    old, new = _touch(db, review)
    _audit(db, identity, request, "review.element_decision", review.id, old, new)
    result = {"review_id": str(review.id), "element_id": str(element.element_id),
        "revision": review.revision, "content_hash": review.content_hash}
    store_response(db, identity.user.id, "PUT", route, idem, result)
    db.commit()
    return result


@router.put("/reviews/{review_id}/candidate-decisions/{candidate_id}")
def put_candidate_decision(review_id: UUID, candidate_id: UUID, body: FindingBody, request: Request,
                           identity: Annotated[Identity, Depends(require_verified_active_identity)],
                           db: Annotated[DbSession, Depends(get_db)],
                           idem: Annotated[str, Header(alias="Idempotency-Key")]) -> dict[str, Any]:
    route = f"/reviews/{review_id}/candidate-decisions/{candidate_id}"
    replay = replay_or_lock(db, identity.user.id, "PUT", route, idem, body.model_dump(mode="json"))
    if replay is not None:
        return replay
    review = _review(db, review_id, True)
    source, _, assignment, av = _access(db, review, identity)
    if body.expected_revision != review.revision:
        raise _err("review revision is stale", 409)
    spec = _approved_spec(db, source, av)
    finding = db.scalar(select(ReviewFinding).where(ReviewFinding.review_version_id == review.id,
        ReviewFinding.candidate_id == candidate_id).with_for_update())
    candidate = db.get(FindingCandidate, candidate_id)
    run = db.get(AnalysisRun, candidate.analysis_run_id) if candidate else None
    if not finding or not run or run.id != review.analysis_run_id or run.submission_version_id != source.id or run.specification_version_id != spec.id:
        raise _hidden()
    _apply_decision(db, review, source, assignment, finding, body, identity)
    old, new = _touch(db, review)
    _audit(db, identity, request, "review.candidate_decision", review.id, old, new)
    result = {"review_id": str(review.id), "finding": _finding_json(db, review, finding),
        "revision": review.revision, "content_hash": review.content_hash}
    store_response(db, identity.user.id, "PUT", route, idem, result)
    db.commit()
    return result


@router.post("/reviews/{review_id}/manual-findings", status_code=201)
def create_manual_finding(review_id: UUID, body: ManualFindingBody, request: Request,
                          identity: Annotated[Identity, Depends(require_verified_active_identity)],
                          db: Annotated[DbSession, Depends(get_db)],
                          idem: Annotated[str, Header(alias="Idempotency-Key")]) -> dict[str, Any]:
    route = f"/reviews/{review_id}/manual-findings"
    replay = replay_or_lock(db, identity.user.id, "POST", route, idem, body.model_dump(mode="json"))
    if replay is not None:
        return replay
    review = _review(db, review_id, True)
    source, _, assignment, av = _access(db, review, identity)
    if body.expected_revision != review.revision:
        raise _err("review revision is stale", 409)
    _approved_spec(db, source, av)
    finding = ReviewFinding(review_version_id=review.id, origin="lecturer", action="pending",
        primary_category=body.primary_category, primary_criterion_id=body.primary_criterion_id,
        claim=body.claim.strip(), revision=1)
    db.add(finding)
    db.flush()
    _apply_decision(db, review, source, assignment, finding, body, identity, manual=True, initial=True)
    old, new = _touch(db, review)
    _audit(db, identity, request, "review.manual_finding", review.id, old, new)
    result = {"review_id": str(review.id), "finding": _finding_json(db, review, finding),
        "revision": review.revision, "content_hash": review.content_hash}
    store_response(db, identity.user.id, "POST", route, idem, result)
    db.commit()
    return result


@router.patch("/reviews/{review_id}/manual-findings/{finding_id}")
def patch_manual_finding(review_id: UUID, finding_id: UUID, body: ManualFindingPatchBody, request: Request,
                         identity: Annotated[Identity, Depends(require_verified_active_identity)],
                         db: Annotated[DbSession, Depends(get_db)],
                         idem: Annotated[str, Header(alias="Idempotency-Key")]) -> dict[str, Any]:
    route = f"/reviews/{review_id}/manual-findings/{finding_id}"
    replay = replay_or_lock(db, identity.user.id, "PATCH", route, idem, body.model_dump(mode="json"))
    if replay is not None:
        return replay
    review = _review(db, review_id, True)
    source, _, assignment, _av = _access(db, review, identity)
    if body.expected_revision != review.revision:
        raise _err("review revision is stale", 409)
    finding = db.scalar(select(ReviewFinding).where(ReviewFinding.id == finding_id,
        ReviewFinding.review_version_id == review.id, ReviewFinding.origin == "lecturer").with_for_update())
    if not finding:
        raise _hidden()
    _apply_decision(db, review, source, assignment, finding, body, identity, manual=True)
    old, new = _touch(db, review)
    _audit(db, identity, request, "review.manual_finding_changed", review.id, old, new)
    result = {"review_id": str(review.id), "finding": _finding_json(db, review, finding),
        "revision": review.revision, "content_hash": review.content_hash}
    store_response(db, identity.user.id, "PATCH", route, idem, result)
    db.commit()
    return result


@router.put("/reviews/{review_id}/notes")
def put_notes(review_id: UUID, body: NotesBody, request: Request,
              identity: Annotated[Identity, Depends(require_verified_active_identity)],
              db: Annotated[DbSession, Depends(get_db)],
              idem: Annotated[str, Header(alias="Idempotency-Key")]) -> dict[str, Any]:
    route = f"/reviews/{review_id}/notes"
    replay = replay_or_lock(db, identity.user.id, "PUT", route, idem, body.model_dump(mode="json"))
    if replay is not None:
        return replay
    review = _review(db, review_id, True)
    _access(db, review, identity)
    if body.expected_revision != review.revision:
        raise _err("review revision is stale", 409)
    if any(not item.strip() or len(item) > 2000 for item in body.limitations):
        raise _err("review limitations must contain 1-2,000 characters")
    review.lecturer_notes = body.lecturer_notes
    review.limitations_json = list(dict.fromkeys(item.strip() for item in body.limitations))
    old, new = _touch(db, review)
    _audit(db, identity, request, "review.notes_changed", review.id, old, new)
    result = {"review_id": str(review.id), "revision": new, "content_hash": review.content_hash,
        "lecturer_notes": review.lecturer_notes, "limitations": review.limitations_json}
    store_response(db, identity.user.id, "PUT", route, idem, result)
    db.commit()
    return result


def _complete_errors(db: DbSession, review: ReviewVersion) -> list[str]:
    errors = []
    decisions = db.scalars(select(ReviewElementDecision).where(ReviewElementDecision.review_version_id == review.id)).all()
    for row in decisions:
        element = db.get(CheckElement, row.check_element_id)
        label = element.element_code if element else str(row.check_element_id)
        if row.review_status != "reviewed" or row.resolution is None:
            errors.append(f"pending_element:{label}")
        if row.resolution == "unable_to_conclude" and not (row.reason or review.limitations_json):
            errors.append(f"limitation_required:{label}")
        if row.applicability_override is not None:
            evidence = _active_decision_evidence(db, row.id)[:1]
            if not row.reason or not evidence:
                errors.append(f"applicability_override_needs_reason_and_source:{label}")
    findings = db.scalars(select(ReviewFinding).where(ReviewFinding.review_version_id == review.id)).all()
    for finding in findings:
        if finding.action == "pending":
            errors.append(f"pending_candidate:{finding.id}")
        if finding.action == "defer" and not finding.reason:
            errors.append(f"defer_limitation_required:{finding.id}")
        if finding.action in {"accept", "edit"}:
            target = db.scalar(select(func.count()).select_from(ReviewFindingTarget).where(ReviewFindingTarget.review_finding_id == finding.id)) or 0
            active_finding_links = _active_links(db, review, finding.id)
            if not target or (not active_finding_links and not finding.coverage_attestation_json) or not finding.claim.strip():
                errors.append(f"confirmed_finding_support_incomplete:{finding.id}")
            if finding.primary_category == "numerical_inconsistency":
                check = db.get(NumericCheck, finding.numeric_check_id) if finding.numeric_check_id else None
                if not check or check.review_version_id != review.id or check.inconsistent is not True:
                    errors.append(f"numeric_finding_unverified:{finding.id}")
    for decision in decisions:
        if decision.resolution == "confirmed_issue":
            ok = any(f.action in {"accept", "edit"} and db.scalar(select(func.count()).select_from(ReviewFindingTarget)
                .where(ReviewFindingTarget.review_finding_id == f.id, ReviewFindingTarget.check_element_id == decision.check_element_id))
                for f in findings)
            if not ok:
                errors.append(f"confirmed_issue_needs_finding:{decision.check_element_id}")
    if not decisions:
        errors.append("no_review_elements")
    return errors


@router.post("/reviews/{review_id}/complete")
def complete_review(review_id: UUID, body: RevisionBody, request: Request,
                    identity: Annotated[Identity, Depends(require_verified_active_identity)],
                    db: Annotated[DbSession, Depends(get_db)],
                    idem: Annotated[str, Header(alias="Idempotency-Key")]) -> dict[str, Any]:
    route = f"/reviews/{review_id}/complete"
    replay = replay_or_lock(db, identity.user.id, "POST", route, idem, body.model_dump(mode="json"))
    if replay is not None:
        return replay
    review = _review(db, review_id, True)
    _access(db, review, identity)
    if body.expected_revision != review.revision:
        raise _err("review revision is stale", 409)
    errors = _complete_errors(db, review)
    if errors:
        raise HTTPException(status_code=422, detail={"message": "review is incomplete", "pending": errors})
    old = review.revision
    review.revision += 1
    review.state, review.completed_by, review.completed_at = "complete", identity.user.id, datetime.now(UTC)
    db.flush()
    review.content_hash = _review_hash(db, review)
    _audit(db, identity, request, "review.completed", review.id, old, review.revision)
    result = {"review_id": str(review.id), "state": review.state, "revision": review.revision,
        "content_hash": review.content_hash, "completed_at": review.completed_at.isoformat()}
    store_response(db, identity.user.id, "POST", route, idem, result)
    db.commit()
    return result


def _asset_for_review(db: DbSession, review: ReviewVersion, file_id: UUID, digest: str) -> tuple[SubmissionVersion, FileAsset]:
    sv = db.get(SubmissionVersion, review.submission_version_id)
    if sv is None:
        raise _hidden()
    link = db.get(SubmissionVersionFile, (sv.id, file_id)) if sv else None
    asset = db.get(FileAsset, file_id) if link else None
    if not asset or asset.validation_state != "validated" or asset.quarantined_at or asset.sha256 != digest:
        raise _hidden()
    return sv, asset


def _manual_artifact(db: DbSession, sv: SubmissionVersion, asset: FileAsset) -> ParseArtifact:
    # Serialize first-artifact creation/order without locking the immutable source row, which the API role cannot update.
    lock_key = int.from_bytes(hashlib.sha256(sv.id.bytes).digest()[:8], "big") & ((1 << 63) - 1)
    db.execute(select(func.pg_advisory_xact_lock(lock_key)))
    row = db.scalar(select(ParseArtifact).where(ParseArtifact.file_asset_id == asset.id,
        ParseArtifact.submission_version_id == sv.id).order_by(ParseArtifact.created_at.desc()).limit(1))
    if row:
        return row
    row = ParseArtifact(file_asset_id=asset.id, submission_version_id=sv.id,
        fingerprint=hashlib.sha256(f"manual:{asset.id}:{asset.sha256}".encode()).hexdigest(),
        owner_scope_hash=hashlib.sha256(f"submission:{sv.id}:{asset.sha256}".encode()).hexdigest(),
        status="partially_parsed", limitations_json=[{"code": "manual_source_anchor",
        "message": "SourceUnit dipilih dosen dan bounds diverifikasi pada original."}])
    db.add(row)
    db.flush()
    return row


def _manual_item(db: DbSession, review: ReviewVersion, sv: SubmissionVersion, asset: FileAsset,
                 anchor: dict[str, Any], transcription: str | None, identity: Identity,
                 original_id: UUID | None, reason: str | None) -> EvidenceItem:
    if not _verify_source_anchor(anchor, asset):
        raise _err("manual anchor is outside verified original bounds")
    if anchor.get("format") == "file" and (anchor.get("source_sha256") != asset.sha256 or not anchor.get("locator_note")):
        raise _err("whole-file anchor requires matching source hash and locator_note")
    original = db.get(EvidenceItem, original_id) if original_id else None
    if original and (original.submission_version_id != sv.id or original.file_asset_id != asset.id or original.source_sha256 != asset.sha256):
        raise _err("transcription original must belong to the same final source")
    artifact = _manual_artifact(db, sv, asset)
    source_order = (db.scalar(select(func.max(SourceUnit.source_order)).where(
        SourceUnit.parse_artifact_id == artifact.id)) or 0) + 1
    unit = SourceUnit(parse_artifact_id=artifact.id,
        locator_key=hashlib.sha256(f"{review.id}:{review.revision}:{uuid4()}:{canonical_hash(anchor)}".encode()).hexdigest(),
        unit_type="lecturer_manual", source_order=source_order,
        anchor_json=anchor, parse_status="parsed" if asset.preflight_json.get("parser_profile") == "native_ooxml_v1" else "partially_parsed",
        limitations_json=[{"code": "lecturer_located", "message": "Lokasi dipilih dosen; original tetap tidak diubah."}])
    db.add(unit)
    db.flush()
    text = transcription or ""
    item = EvidenceItem(source_unit_id=unit.id, submission_version_id=sv.id,
        file_asset_id=asset.id, source_sha256=asset.sha256,
        type="lecturer_transcription" if transcription is not None else "paragraph",
        anchor_json=anchor,
        content_original_json={"text": text, "origin": "lecturer_transcription"} if transcription is not None else {"manual_selection": True},
        content_normalized_json={"text": text} if transcription is not None else {},
        parse_status=unit.parse_status, limitations_json=unit.limitations_json,
        parser_manifest_id=artifact.parser_manifest_id)
    db.add(item)
    db.flush()
    return item


def _annotation_targets(db: DbSession, review: ReviewVersion, element_ids: list[UUID],
                        finding_id: UUID | None, revision_id: UUID,
                        relationship: str | None, role: str | None) -> ReviewFinding | None:
    _, _, _, av = _source(db, review)
    spec = db.get(SpecificationVersion, av.specification_version_id) if av.specification_version_id else None
    elements = db.scalars(select(CheckElement).where(CheckElement.specification_version_id == spec.id,
        CheckElement.element_id.in_(element_ids))).all() if spec and element_ids else []
    if len(elements) != len(set(element_ids)):
        raise _err("annotation element target is outside this review")
    finding = db.get(ReviewFinding, finding_id) if finding_id else None
    if finding and finding.review_version_id != review.id:
        raise _hidden()
    if not elements and not finding:
        raise _err("annotation must target an element or finding")
    if relationship or role:
        if not relationship or not role or not elements:
            raise _err("relationship_code and role must target a specification element")
        if not any(
            isinstance(item, dict)
            and item.get("relationship_code") == relationship
            and isinstance(item.get("predicate"), str)
            and role in REQUIRED_ROLES.get(item["predicate"], set())
            for element in elements
            for item in element.payload_json.get("relationships", [])
        ):
            raise _err("annotation relationship role is not declared by the approved specification")
    for element in elements:
        db.add(ReviewAnnotationTarget(annotation_revision_id=revision_id, check_element_id=element.id,
            relationship_code=relationship, role=role))
    if finding:
        db.add(ReviewAnnotationTarget(annotation_revision_id=revision_id, review_finding_id=finding.id,
            relationship_code=relationship, role=role))
    return finding


@router.post("/reviews/{review_id}/manual-evidence", status_code=201)
def create_manual_evidence(review_id: UUID, body: ManualEvidenceBody, request: Request,
                           identity: Annotated[Identity, Depends(require_verified_active_identity)],
                           db: Annotated[DbSession, Depends(get_db)],
                           idem: Annotated[str, Header(alias="Idempotency-Key")]) -> dict[str, Any]:
    route = f"/reviews/{review_id}/manual-evidence"
    replay = replay_or_lock(db, identity.user.id, "POST", route, idem, body.model_dump(mode="json"))
    if replay is not None:
        return replay
    review = _review(db, review_id, True)
    sv, _, _, _ = _access(db, review, identity)
    if body.expected_revision != review.revision:
        raise _err("review revision is stale", 409)
    if body.transcription is not None and (not body.transcription.strip() or not body.reason or not body.reason.strip()):
        raise _err("transcription requires a reason")
    if body.original_evidence_id and body.transcription is None:
        raise _err("original evidence applies only to a transcription correction")
    sv, asset = _asset_for_review(db, review, body.file_id, body.source_sha256)
    kind = "transcription" if body.transcription is not None else "span_selection"
    item = _manual_item(db, review, sv, asset, body.anchor, body.transcription, identity,
        body.original_evidence_id, body.reason)
    annotation = ReviewAnnotation(review_version_id=review.id, kind=kind, created_by=identity.user.id, revision=1)
    db.add(annotation)
    db.flush()
    revision = ReviewAnnotationRevision(annotation_id=annotation.id, revision_no=1, operation="upsert",
        file_asset_id=asset.id, source_sha256=asset.sha256, anchor_json=body.anchor,
        original_evidence_id=body.original_evidence_id, corrected_evidence_id=item.id,
        transcription=body.transcription, reason=body.reason, author_id=identity.user.id)
    db.add(revision)
    db.flush()
    annotation.current_revision_id = revision.id
    target_finding = _annotation_targets(db, review, body.element_ids, body.review_finding_id,
        revision.id, body.relationship_code, body.role) if (body.element_ids or body.review_finding_id) else None
    db.add(ManualEvidence(evidence_item_id=item.id, review_version_id=review.id,
        annotation_revision_id=revision.id, created_by=identity.user.id,
        origin="lecturer_transcription" if body.transcription is not None else "lecturer_selected",
        source_anchor_json=body.anchor, transcription=body.transcription, reason=body.reason))
    if target_finding:
        db.add(EvidenceLink(evidence_item_id=item.id, review_finding_id=target_finding.id, annotation_revision_id=revision.id))
    old, new = _touch(db, review)
    _audit(db, identity, request, "review.manual_evidence", review.id, old, new)
    result = {"review_id": str(review.id), "evidence_id": str(item.id), "source_unit_id": str(item.source_unit_id),
        "annotation_id": str(annotation.id), "annotation_revision_id": str(revision.id),
        "source_sha256": asset.sha256, "anchor": body.anchor, "revision": new, "content_hash": review.content_hash}
    store_response(db, identity.user.id, "POST", route, idem, result)
    db.commit()
    return result


@router.post("/reviews/{review_id}/annotations", status_code=201)
def create_annotation(review_id: UUID, body: AnnotationBody, request: Request,
                      identity: Annotated[Identity, Depends(require_verified_active_identity)],
                      db: Annotated[DbSession, Depends(get_db)],
                      idem: Annotated[str, Header(alias="Idempotency-Key")]) -> dict[str, Any]:
    route = f"/reviews/{review_id}/annotations"
    replay = replay_or_lock(db, identity.user.id, "POST", route, idem, body.model_dump(mode="json"))
    if replay is not None:
        return replay
    review = _review(db, review_id, True)
    sv, _, _, _ = _access(db, review, identity)
    if body.expected_revision != review.revision:
        raise _err("review revision is stale", 409)
    item: EvidenceItem
    if body.kind == "evidence_binding":
        evidence_id = body.evidence_item_id or body.original_evidence_id
        item = _evidence(db, sv, [evidence_id] if evidence_id else [], review)[0]
        asset = db.get(FileAsset, item.file_asset_id)
        if asset is None:
            raise _hidden()
        anchor = item.anchor_json
    else:
        if body.file_id is None or body.source_sha256 is None or body.anchor is None:
            raise _err("span/transcription annotation needs final file, hash, and anchor")
        if body.kind == "transcription" and (not body.transcription or not body.transcription.strip() or not body.reason or not body.reason.strip()):
            raise _err("transcription annotation requires text and a reason")
        sv, asset = _asset_for_review(db, review, body.file_id, body.source_sha256)
        item = _manual_item(db, review, sv, asset, body.anchor,
            body.transcription if body.kind == "transcription" else None,
            identity, body.original_evidence_id, body.reason)
        anchor = body.anchor
    annotation = ReviewAnnotation(review_version_id=review.id, kind=body.kind,
        created_by=identity.user.id, revision=1)
    db.add(annotation)
    db.flush()
    revision = ReviewAnnotationRevision(annotation_id=annotation.id, revision_no=1,
        operation="upsert", file_asset_id=asset.id, source_sha256=asset.sha256,
        anchor_json=anchor,
        original_evidence_id=body.original_evidence_id or (item.id if body.kind == "evidence_binding" else None),
        corrected_evidence_id=item.id if body.kind != "evidence_binding" else None,
        transcription=body.transcription if body.kind == "transcription" else None,
        reason=body.reason, author_id=identity.user.id)
    db.add(revision)
    db.flush()
    annotation.current_revision_id = revision.id
    finding = _annotation_targets(db, review, body.element_ids, body.review_finding_id,
        revision.id, body.relationship_code, body.role) if (body.element_ids or body.review_finding_id) else None
    if body.kind != "evidence_binding":
        db.add(ManualEvidence(evidence_item_id=item.id, review_version_id=review.id,
            annotation_revision_id=revision.id, created_by=identity.user.id,
            origin="lecturer_transcription" if body.kind == "transcription" else "lecturer_selected",
            source_anchor_json=anchor,
            transcription=body.transcription if body.kind == "transcription" else None,
            reason=body.reason))
    if finding:
        db.add(EvidenceLink(evidence_item_id=item.id, review_finding_id=finding.id,
            annotation_revision_id=revision.id))
    old, new = _touch(db, review)
    _audit(db, identity, request, "review.annotation_created", review.id, old, new)
    result = {"review_id": str(review.id), "annotation_id": str(annotation.id),
        "annotation_revision_id": str(revision.id), "revision_no": revision.revision_no,
        "evidence_id": str(item.id), "review_revision": new, "content_hash": review.content_hash}
    store_response(db, identity.user.id, "POST", route, idem, result)
    db.commit()
    return result


@router.post("/reviews/{review_id}/annotations/{annotation_id}/revisions")
def create_annotation_revision(review_id: UUID, annotation_id: UUID, body: AnnotationRevisionBody,
                               request: Request, identity: Annotated[Identity, Depends(require_verified_active_identity)],
                               db: Annotated[DbSession, Depends(get_db)],
                               idem: Annotated[str, Header(alias="Idempotency-Key")]) -> dict[str, Any]:
    route = f"/reviews/{review_id}/annotations/{annotation_id}/revisions"
    replay = replay_or_lock(db, identity.user.id, "POST", route, idem, body.model_dump(mode="json"))
    if replay is not None:
        return replay
    review = _review(db, review_id, True)
    sv, _, _, _ = _access(db, review, identity)
    if body.expected_revision != review.revision:
        raise _err("review revision is stale", 409)
    annotation = db.scalar(select(ReviewAnnotation).where(ReviewAnnotation.id == annotation_id,
        ReviewAnnotation.review_version_id == review.id).with_for_update())
    if annotation is None:
        raise _hidden()
    prior = db.get(ReviewAnnotationRevision, annotation.current_revision_id) if annotation.current_revision_id else None
    if prior is None:
        raise _err("annotation current revision is missing", 409)
    reason = body.reason.strip() if body.reason else None
    if body.operation == "remove" and not reason:
        raise _err("removing an annotation requires a reason")
    if body.operation == "upsert" and annotation.kind in {"span_selection", "transcription"} and not reason:
        raise _err("location/transcription correction requires a reason")
    item: EvidenceItem | None = None
    if body.operation == "remove":
        file_id, digest, anchor = prior.file_asset_id, prior.source_sha256, prior.anchor_json
        original_id, corrected_id, transcription = prior.original_evidence_id, prior.corrected_evidence_id, prior.transcription
    elif annotation.kind == "evidence_binding":
        evidence_id = body.evidence_item_id or body.original_evidence_id
        item = _evidence(db, sv, [evidence_id] if evidence_id else [], review)[0]
        asset = db.get(FileAsset, item.file_asset_id)
        if asset is None:
            raise _hidden()
        file_id, digest, anchor = asset.id, asset.sha256, item.anchor_json
        original_id, corrected_id, transcription = item.id, None, None
    else:
        if body.file_id is None or body.source_sha256 is None or body.anchor is None:
            raise _err("upsert revision needs final source, hash, and anchor")
        sv, asset = _asset_for_review(db, review, body.file_id, body.source_sha256)
        if not _verify_source_anchor(body.anchor, asset):
            raise _err("manual anchor is outside verified original bounds")
        transcription = body.transcription if annotation.kind == "transcription" else None
        if annotation.kind == "transcription" and (not transcription or not transcription.strip()):
            raise _err("transcription revision requires text")
        original_id = body.original_evidence_id or prior.original_evidence_id
        item = _manual_item(db, review, sv, asset, body.anchor, transcription,
            identity, original_id, reason)
        file_id, digest, anchor, corrected_id = asset.id, asset.sha256, body.anchor, item.id
    revision = ReviewAnnotationRevision(annotation_id=annotation.id,
        revision_no=prior.revision_no + 1, previous_revision_id=prior.id,
        operation=body.operation, file_asset_id=file_id, source_sha256=digest,
        anchor_json=anchor, original_evidence_id=original_id, corrected_evidence_id=corrected_id,
        transcription=transcription, reason=reason, author_id=identity.user.id)
    db.add(revision)
    db.flush()
    annotation.current_revision_id = revision.id
    annotation.revision += 1
    if body.operation == "remove":
        targets = db.scalars(select(ReviewAnnotationTarget).where(
            ReviewAnnotationTarget.annotation_revision_id == prior.id)).all()
        for target in targets:
            db.add(ReviewAnnotationTarget(annotation_revision_id=revision.id,
                check_element_id=target.check_element_id, review_finding_id=target.review_finding_id,
                relationship_code=target.relationship_code, role=target.role))
    else:
        finding = _annotation_targets(db, review, body.element_ids, body.review_finding_id,
            revision.id, body.relationship_code, body.role) if (body.element_ids or body.review_finding_id) else None
        if annotation.kind != "evidence_binding" and item:
            db.add(ManualEvidence(evidence_item_id=item.id, review_version_id=review.id,
                annotation_revision_id=revision.id, created_by=identity.user.id,
                origin="lecturer_transcription" if annotation.kind == "transcription" else "lecturer_selected",
                source_anchor_json=anchor, transcription=transcription, reason=reason))
        if finding and item:
            db.add(EvidenceLink(evidence_item_id=item.id, review_finding_id=finding.id,
                annotation_revision_id=revision.id))
    old, new = _touch(db, review)
    _audit(db, identity, request, "review.annotation_revised", review.id, old, new)
    result = {"review_id": str(review.id), "annotation_id": str(annotation.id),
        "annotation_revision_id": str(revision.id), "revision_no": revision.revision_no,
        "operation": revision.operation, "review_revision": new, "content_hash": review.content_hash}
    store_response(db, identity.user.id, "POST", route, idem, result)
    db.commit()
    return result


@router.get("/reviews/{review_id}/annotations/{annotation_id}/revisions")
def list_annotation_revisions(review_id: UUID, annotation_id: UUID,
                              identity: Annotated[Identity, Depends(require_password_change_completed)],
                              db: Annotated[DbSession, Depends(get_db)]) -> dict[str, Any]:
    review = _review(db, review_id)
    _access(db, review, identity)
    annotation = db.scalar(select(ReviewAnnotation).where(ReviewAnnotation.id == annotation_id,
        ReviewAnnotation.review_version_id == review.id))
    if annotation is None:
        raise _hidden()
    rows = db.scalars(select(ReviewAnnotationRevision).where(
        ReviewAnnotationRevision.annotation_id == annotation.id)
        .order_by(ReviewAnnotationRevision.revision_no)).all()
    return {"items": [_annotation_json(db, row) for row in rows]}


@router.get("/reviews/{review_id}/source-links")
def source_links(review_id: UUID, identity: Annotated[Identity, Depends(require_password_change_completed)],
                 db: Annotated[DbSession, Depends(get_db)], evidence_id: UUID | None = None,
                 source_unit_id: UUID | None = None) -> dict[str, Any]:
    review = _review(db, review_id)
    _access(db, review, identity)
    if (evidence_id is None) == (source_unit_id is None):
        raise _err("provide exactly one evidence_id or source_unit_id")
    q = select(EvidenceItem).where(EvidenceItem.submission_version_id == review.submission_version_id)
    if evidence_id:
        q = q.where(EvidenceItem.id == evidence_id)
    else:
        q = q.where(EvidenceItem.source_unit_id == source_unit_id)
    items = db.scalars(q).all()
    if not items:
        raise _hidden()
    links = []
    for item in items:
        for link in db.scalars(select(EvidenceLink).where(EvidenceLink.evidence_item_id == item.id,
            EvidenceLink.review_finding_id.is_not(None))).all():
            finding = db.get(ReviewFinding, link.review_finding_id)
            if not finding or finding.review_version_id != review.id:
                continue
            if link.annotation_revision_id:
                revision = db.get(ReviewAnnotationRevision, link.annotation_revision_id)
                annotation = db.get(ReviewAnnotation, revision.annotation_id) if revision else None
                if not revision or not annotation or annotation.current_revision_id != revision.id or revision.operation != "upsert":
                    continue
            links.append({"evidence_id": str(item.id), "review_finding_id": str(finding.id),
                "action": finding.action, "claim": finding.claim})
    return {"review_id": str(review.id), "evidence": [{"id": str(item.id), "source_unit_id": str(item.source_unit_id),
        "file_id": str(item.file_asset_id), "source_sha256": item.source_sha256,
        "anchor": item.anchor_json, "content": item.content_original_json} for item in items], "links": links}


def _confirmed_findings(db: DbSession, review: ReviewVersion) -> list[ReviewFinding]:
    return list(db.scalars(select(ReviewFinding).where(ReviewFinding.review_version_id == review.id,
        ReviewFinding.action.in_(["accept", "edit"])).order_by(ReviewFinding.id)).all())


def _feedback_parts(db: DbSession, review: ReviewVersion, body: FeedbackBody | FeedbackUpdateBody
                    ) -> tuple[str, list[dict[str, Any]], list[str], dict[str, str]]:
    findings = _confirmed_findings(db, review)
    by_id = {row.id: row for row in findings}
    included_source = body.included_review_finding_ids
    included = list(dict.fromkeys([row.id for row in findings] if included_source is None else included_source))
    if any(item not in by_id for item in included):
        raise _err("only accepted/edited confirmed findings can be included")
    included_text = [str(item) for item in included]
    exclusions = {key: value.strip() for key, value in body.exclusion_reasons.items()}
    for row in findings:
        if row.id not in included and not exclusions.get(str(row.id), "").strip():
            raise _err("each excluded confirmed finding needs a lecturer reason")
    if any(key not in {str(row.id) for row in findings} or not value or len(value) > 2000
           for key, value in exclusions.items()):
        raise _err("exclusion reason must refer to a confirmed finding")
    mode = body.mode if isinstance(body, FeedbackBody) else "manual"
    blocks = list(body.blocks or [])
    text = body.body_markdown
    if mode == "template":
        blocks = []
        for finding_id in included:
            finding = by_id[finding_id]
            criterion = db.get(Criterion, finding.primary_criterion_id)
            evidence_ids = [str(row.evidence_item_id) for row in _active_links(db, review, finding.id)]
            blocks.append({"criterion_id": str(finding.primary_criterion_id),
                "review_finding_ids": [str(finding.id)], "evidence_ids": evidence_ids,
                "message": f"Temuan yang dikonfirmasi pada {criterion.title if criterion else 'kriteria tugas'}: {finding.claim}",
                "next_action": "Periksa kembali bukti yang ditautkan terhadap tuntutan tugas.",
                "limitation": None})
        text = ("Dalam cakupan yang telah ditelaah, tidak ada masalah yang dikonfirmasi."
                if not blocks else "\n\n".join(
                    block["message"] + "\n\nTindakan berikutnya: " + block["next_action"] for block in blocks))
        if review.limitations_json:
            text += "\n\nBatas pemeriksaan: " + " ".join(review.limitations_json)
    if text is None:
        raise _err("feedback body is required")
    text = text.strip()
    if (len(text) > 20000 or re.search(r"<\s*/?\s*[A-Za-z][^>]*>", text)
        or re.search(r"!\[[^]]*\]\(", text) or re.search(r"\[[^]]+\]\(", text)
        or re.search(r"(?:https?://|www\.)", text, re.IGNORECASE)):
        raise _err("feedback is limited to 20,000 characters and cannot contain raw HTML, links, or remote images")
    if not included and not text.startswith("Dalam cakupan yang telah ditelaah, tidak ada masalah yang dikonfirmasi."):
        raise _err("feedback without confirmed findings must use the neutral master template")
    if not included and mode != "template":
        raise _err("feedback without confirmed findings must use the neutral template")
    if included and mode == "manual" and not blocks:
        raise _err("manual feedback requires structured blocks")
    covered: set[str] = set()
    for block in blocks:
        if not isinstance(block, dict):
            raise _err("feedback block must be an object")
        finding_ids = block.get("review_finding_ids")
        evidence_ids = block.get("evidence_ids", [])
        if (not isinstance(finding_ids, list) or not finding_ids or not isinstance(evidence_ids, list)
            or not isinstance(block.get("message"), str) or not block["message"].strip()):
            raise _err("feedback block needs confirmed finding refs, evidence refs, and message")
        criterion_id = str(block.get("criterion_id", ""))
        for raw_id in finding_ids:
            try:
                finding_id = UUID(str(raw_id))
            except ValueError as exc:
                raise _err("feedback finding ID is invalid") from exc
            included_finding = by_id.get(finding_id)
            if included_finding is None or str(included_finding.primary_criterion_id) != criterion_id or str(finding_id) not in included_text:
                raise _err("feedback block must refer to an included finding and its criterion")
            active_evidence = {str(row.evidence_item_id) for row in _active_links(db, review, finding_id)}
            if any(str(item) not in active_evidence for item in evidence_ids):
                raise _err("feedback block cites evidence not active for its finding")
            covered.add(str(finding_id))
        if len(block["message"]) > 20000 or len(str(block.get("next_action", ""))) > 2000:
            raise _err("feedback block exceeds field limits")
    if covered != set(included_text):
        raise _err("each included finding must appear in a feedback block")
    rejected = db.scalars(select(ReviewFinding.claim).where(ReviewFinding.review_version_id == review.id,
        ReviewFinding.action.in_(["reject", "defer"]))).all()
    lowered = text.casefold()
    if any(claim.strip() and claim.casefold() in lowered for claim in rejected):
        raise _err("rejected/deferred candidate claim cannot be copied into feedback")
    for finding in findings:
        if finding.id not in included and finding.claim.strip() and finding.claim.casefold() in lowered:
            raise _err("excluded confirmed finding cannot be copied into feedback")
    for block in blocks:
        block_copy = " ".join(str(block.get(field, "")) for field in ("message", "next_action", "limitation")).casefold()
        if any(claim.strip() and claim.casefold() in block_copy for claim in rejected):
            raise _err("rejected/deferred candidate claim cannot be copied into feedback blocks")
        if any(finding.id not in included and finding.claim.strip() and finding.claim.casefold() in block_copy
               for finding in findings):
            raise _err("excluded confirmed finding cannot be copied into feedback blocks")
    return text, blocks, included_text, exclusions


def _generate_model_feedback(
    db: DbSession,
    review: ReviewVersion,
    draft: FeedbackDraft,
    job: Job,
    included: list[str],
    exclusions: dict[str, str],
) -> tuple[str | None, list[dict[str, Any]] | None, str | None]:
    """Generate one scoped draft; unavailable, over-budget, and invalid outputs abstain."""
    if not included:
        return None, None, "no_confirmed_findings"
    if review.analysis_run_id is None:
        return None, None, "analysis_run_required"
    run = db.get(AnalysisRun, review.analysis_run_id)
    release = (
        db.get(ReleaseManifest, run.release_manifest_id)
        if run and run.release_manifest_id
        else None
    )
    if run is None or release is None or release.state not in {"active", "verified", "retired"}:
        return None, None, "release_snapshot_unavailable"
    model_manifest = db.get(MethodManifest, release.model_manifest_id)
    if model_manifest is None or model_manifest.kind != "model":
        return None, None, "model_manifest_unavailable"
    provider_mode = str(release.manifest_payload.get("provider_mode", "off"))
    if provider_mode not in {"mock", "live", "off"}:
        return None, None, "provider_disabled"
    if model_manifest.content_json.get("provider_mode") != provider_mode:
        return None, None, "provider_manifest_mismatch"

    findings = {row.id: row for row in _confirmed_findings(db, review)}
    selected_rows = [findings[UUID(raw_id)] for raw_id in included]
    provider_findings: list[dict[str, Any]] = []
    for finding in selected_rows:
        criterion = db.get(Criterion, finding.primary_criterion_id)
        if criterion is None:
            return None, None, "official_criterion_unavailable"
        evidence_rows: list[dict[str, Any]] = []
        evidence_ids: list[str] = []
        for link in _active_links(db, review, finding.id):
            item = db.get(EvidenceItem, link.evidence_item_id)
            if item is None:
                continue
            content = (
                item.content_original_json if isinstance(item.content_original_json, dict) else {}
            )
            evidence_ids.append(str(item.id))
            evidence_rows.append(
                {
                    "evidence_id": str(item.id),
                    "anchor": item.anchor_json,
                    "text": str(content.get("text", ""))[:3000],
                }
            )
        provider_findings.append(
            {
                "review_finding_id": str(finding.id),
                "criterion_id": str(finding.primary_criterion_id),
                "criterion_title": criterion.title,
                "criterion_descriptor": criterion.descriptor,
                "claim": finding.claim,
                "evidence_ids": evidence_ids,
                "evidence": evidence_rows,
            }
        )
    payload: dict[str, Any] = {
        "task_type": "confirmed_feedback_draft",
        "schema_version": 1,
        "json_schema": FEEDBACK_DRAFT_JSON_SCHEMA,
        "official_fields": {
            "review_content_hash": review.content_hash,
            "lecturer_notes": review.lecturer_notes,
            "limitations": list(review.limitations_json),
        },
        "confirmed_findings": provider_findings,
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    input_tokens = (len(serialized.encode("utf-8")) + 3) // 4
    if input_tokens > 12_000:
        return None, None, "feedback_input_budget_exceeded"

    settings = get_settings()
    budget = min(Decimal("0.03"), settings.feedback_budget_usd)
    call_id = uuid4()
    context = ProviderContext(
        call_id=call_id,
        job_id=job.id,
        manifest_id=model_manifest.id,
        policy_id=release.routing_policy_manifest_id,
        scope_ids=[review.id],
        max_input_tokens=12_000,
        max_output_tokens=3_000,
    )
    provider: Any | None = None
    if provider_mode == "mock":
        provider = MockProvider()
    elif provider_mode == "live" and settings.openai_api_key is not None:
        provider = OpenAIResponsesProvider(
            settings.openai_api_key.get_secret_value(),
            {
                "efficient": settings.model_efficient or "",
                "complex": settings.model_complex or "",
                "draft_feedback": settings.model_efficient or "",
            },
        )
    route = RoutingDecision(
        analysis_run_id=run.id,
        check_element_id=None,
        task="draft_feedback",
        action="call" if provider is not None else "abstain",
        selected_role="efficient" if provider is not None else None,
        reason_code="confirmed_feedback_draft",
        feature_snapshot_json={
            "confirmed_findings": len(included),
            "new_retrieval": False,
            "rejected_candidates_included": False,
        },
        guard_snapshot_json={
            "review_complete": review.state == "complete",
            "selected_confirmed_findings_only": True,
            "source_scope_verified": True,
            "paid_call_guard": "blocked_before_network",
        },
        remaining_limits_json={
            "calls": 2,
            "input_tokens": max(0, 12_000 - input_tokens),
            "output_tokens": 3_000,
            "budget_usd": str(budget),
        },
        policy_manifest_id=release.routing_policy_manifest_id,
        outcome="running" if provider is not None else "provider_configuration_unavailable",
        job_id=job.id,
        batching_manifest_id=release.batching_policy_manifest_id,
        timing_refs_json={"baseline_batch_size": 1},
    )
    db.add(route)
    db.flush()

    status = "provider_configuration_unavailable"
    result = None
    failure_code: str | None = None
    output_text: str | None = None
    output_blocks: list[dict[str, Any]] | None = None
    try:
        if provider is None:
            failure_code = status
        else:
            result = provider.draft_feedback(context, payload)
            if (
                result.call_id != context.call_id
                or result.job_id != context.job_id
                or result.manifest_id != context.manifest_id
                or result.policy_id != context.policy_id
                or result.scope_ids != context.scope_ids
            ):
                status, failure_code = "provider_scope_invalid", "provider_scope_invalid"
            elif result.status not in {"mocked", "completed"}:
                status, failure_code = str(result.status)[:32], str(result.status)[:80]
            elif (
                int(result.usage.get("input_tokens", input_tokens)) > 12_000
                or int(result.usage.get("output_tokens", 0)) > 3_000
            ):
                status, failure_code = "provider_usage_over_limit", "provider_usage_over_limit"
            else:
                typed = FeedbackDraftPayload.model_validate_json(
                    json.dumps(result.payload, ensure_ascii=False)
                )
                returned_ids = [item.review_finding_id for item in typed.blocks]
                if len(returned_ids) != len(set(returned_ids)) or set(returned_ids) != {
                    UUID(raw_id) for raw_id in included
                }:
                    raise ValueError("feedback_finding_set_invalid")
                output_blocks = []
                for block in typed.blocks:
                    finding = findings[block.review_finding_id]
                    allowed_evidence = {
                        row.evidence_item_id for row in _active_links(db, review, finding.id)
                    }
                    if (
                        block.criterion_id != finding.primary_criterion_id
                        or not set(block.evidence_ids) <= allowed_evidence
                    ):
                        raise ValueError("feedback_grounding_invalid")
                    output_blocks.append(
                        {
                            "criterion_id": str(block.criterion_id),
                            "review_finding_ids": [str(block.review_finding_id)],
                            "evidence_ids": [str(item) for item in block.evidence_ids],
                            "message": block.message,
                            "next_action": block.next_action,
                            "limitation": block.limitation,
                        }
                    )
                output_text = "\n\n".join(
                    f"{block['message']}\n\nTindakan berikutnya: {block['next_action']}"
                    for block in output_blocks
                )
                if review.limitations_json:
                    output_text += "\n\nBatas pemeriksaan: " + " ".join(review.limitations_json)
                _feedback_parts(
                    db,
                    review,
                    FeedbackBody(
                        mode="manual",
                        body_markdown=output_text,
                        blocks=output_blocks,
                        included_review_finding_ids=[UUID(raw_id) for raw_id in included],
                        exclusion_reasons=exclusions,
                        expected_revision=review.revision,
                    ),
                )
                status = "completed"
    except PaidCallBlocked:
        status, failure_code = (
            "paid_call_guard_blocked_before_network",
            "paid_call_guard_blocked_before_network",
        )
    except Exception:
        status, failure_code = "provider_response_invalid", "provider_response_invalid"

    usage = {
        "input_tokens": max(0, int(result.usage.get("input_tokens", input_tokens)))
        if result
        else input_tokens,
        "output_tokens": max(0, int(result.usage.get("output_tokens", 0))) if result else 0,
    }
    db.add(
        ProviderCallRecord(
            analysis_run_id=run.id,
            routing_decision_id=route.id,
            logical_call_id=call_id,
            attempt_no=1,
            task="draft_feedback",
            provider_mode=provider_mode,
            status=status,
            request_manifest_json={
                "payload_sha256": canonical_hash(payload),
                "input_token_estimate": input_tokens,
            },
            response_manifest_json={
                "payload_sha256": canonical_hash(result.payload) if result else None,
                "simulated": bool(result and result.simulated),
                "fixture_id": result.fixture_id if result else None,
            },
            usage_json=usage,
            cost_usd=Decimal("0") if result and result.simulated else None,
        )
    )
    job.status = "ready_for_review"
    job.stage = "draft_feedback"
    job.lease_until = None
    job.completion_kind = (
        "feedback_draft_completed" if status == "completed" else "fallback_template"
    )
    job.last_error_code = failure_code
    route.outcome = "completed" if status == "completed" else (failure_code or status)
    return (
        (output_text, output_blocks, None)
        if status == "completed"
        else (None, None, failure_code or status)
    )




def _feedback_hash(mode: str, source_hash: str, body: str, blocks: list[dict[str, Any]],
                   included: list[str], exclusions: dict[str, str]) -> str:
    return canonical_hash({"generation_mode": mode, "source_review_hash": source_hash,
        "body_markdown": body, "blocks": blocks,
        "included_review_finding_ids": sorted(included), "exclusion_reasons": exclusions})


def _feedback_json(draft: FeedbackDraft, review: ReviewVersion) -> dict[str, Any]:
    return {"id": str(draft.id), "review_version_id": str(review.id), "sequence_no": draft.sequence_no,
        "generation_mode": draft.generation_mode, "source_review_hash": draft.source_review_hash,
        "body_markdown": draft.body_markdown, "blocks": draft.blocks_json,
        "included_review_finding_ids": draft.included_review_finding_ids_json,
        "exclusion_reasons": draft.exclusion_reasons_json, "content_hash": draft.content_hash,
        "generation_status": draft.generation_status,
        "generation_failure_code": draft.generation_failure_code,
        "generation_job_id": str(draft.generation_job_id) if draft.generation_job_id else None,
        "state": draft.state, "revision": draft.revision,
        "approved_by": str(draft.approved_by) if draft.approved_by else None,
        "approved_at": draft.approved_at.isoformat() if draft.approved_at else None,
        "stale": review.state != "complete" or review.content_hash != draft.source_review_hash}


@router.post("/reviews/{review_id}/feedback-drafts", status_code=201)
def create_feedback_draft(review_id: UUID, body: FeedbackBody, request: Request,
                          identity: Annotated[Identity, Depends(require_verified_active_identity)],
                          db: Annotated[DbSession, Depends(get_db)],
                          idem: Annotated[str, Header(alias="Idempotency-Key")],
                          response: Response) -> dict[str, Any]:
    route = f"/reviews/{review_id}/feedback-drafts"
    replay = replay_or_lock(db, identity.user.id, "POST", route, idem, body.model_dump(mode="json"))
    if replay is not None:
        if replay.get("generation_job_id"):
            response.status_code = 202
        return replay
    review = _review(db, review_id, True)
    _access(db, review, identity)
    if body.expected_revision != review.revision:
        raise _err("review revision is stale", 409)
    if review.state != "complete" or _complete_errors(db, review):
        raise _err("review must be complete before preparing feedback", 409)
    if body.mode == "model":
        if body.body_markdown is not None or body.blocks:
            raise _err("model feedback accepts selected confirmed findings only")
        fallback_body = FeedbackBody(mode="template", body_markdown=None, blocks=[],
            included_review_finding_ids=body.included_review_finding_ids,
            exclusion_reasons=body.exclusion_reasons, expected_revision=body.expected_revision)
        fallback_text, fallback_blocks, included, exclusions = _feedback_parts(db, review, fallback_body)
        text, blocks = fallback_text, fallback_blocks
        mode, generation_status = "model", "queued"
    else:
        text, blocks, included, exclusions = _feedback_parts(db, review, body)
        mode, generation_status = body.mode, "not_requested"
    seq = (db.scalar(select(func.max(FeedbackDraft.sequence_no)).where(
        FeedbackDraft.review_version_id == review.id)) or 0) + 1
    draft = FeedbackDraft(review_version_id=review.id, sequence_no=seq,
        generation_mode=mode, source_review_hash=review.content_hash, blocks_json=blocks,
        body_markdown=text, included_review_finding_ids_json=included,
        exclusion_reasons_json=exclusions,
        content_hash=_feedback_hash(mode, review.content_hash, text, blocks, included, exclusions),
        state="draft", revision=1, generation_status=generation_status)
    db.add(draft)
    db.flush()
    if body.mode == "model":
        run = db.get(AnalysisRun, review.analysis_run_id) if review.analysis_run_id else None
        release = db.get(ReleaseManifest, run.release_manifest_id) if run and run.release_manifest_id else None
        model_manifest = db.get(MethodManifest, release.model_manifest_id) if release else None
        if included and run and release and model_manifest and model_manifest.kind == "model":
            settings = get_settings()
            limits = {
                "max_calls": 2,
                "max_active_seconds": 60,
                "max_text_input_tokens": 12_000,
                "max_text_output_tokens": 3_000,
                "budget_usd": str(min(Decimal("0.03"), settings.feedback_budget_usd)),
            }
            fingerprint = canonical_hash({
                "draft_id": str(draft.id),
                "release_hash": release.config_hash,
                "review_hash": review.content_hash,
                "included": included,
                "exclusions": exclusions,
                "limits": limits,
            })
            job = Job(
                type="feedback_draft",
                subject_id=draft.id,
                scope_json={
                    "review_version_id": str(review.id),
                    "analysis_run_id": str(run.id),
                    "release_manifest_id": str(release.id),
                    "model_manifest_id": str(model_manifest.id),
                },
                input_fingerprint=fingerprint,
                status="queued",
                stage="draft_feedback",
                lease_fence=0,
                job_limits_json=limits,
            )
            db.add(job)
            db.flush()
            stage_hash = hashlib.sha256(f"{fingerprint}:draft_feedback".encode("ascii")).hexdigest()
            db.add(JobStage(
                job_id=job.id,
                stage_name="draft_feedback",
                input_hash=stage_hash,
                status="queued",
                output_refs_json={},
                attempt_count=0,
            ))
            draft.generation_job_id = job.id
            draft.model_manifest_id = model_manifest.id
            db.add(OutboxEvent(
                event_type="job.execute",
                subject_id=job.id,
                payload_json={"job_id": str(job.id)},
                next_attempt_at=datetime.now(UTC),
            ))
            response.status_code = 202
        else:
            mode, text, blocks = "template", fallback_text, fallback_blocks
            draft.generation_mode = mode
            draft.generation_status = "fallback"
            draft.generation_failure_code = "model_scope_unavailable" if included else "no_confirmed_findings"
            draft.body_markdown = text
            draft.blocks_json = blocks
            draft.content_hash = _feedback_hash(mode, review.content_hash, text, blocks, included, exclusions)
    for block in blocks:
        for evidence_id in block.get("evidence_ids", []):
            db.add(EvidenceLink(evidence_item_id=UUID(str(evidence_id)), feedback_draft_id=draft.id))
    result = _feedback_json(draft, review)
    _audit(db, identity, request, "feedback.draft_created", review.id, None, review.revision)
    store_response(db, identity.user.id, "POST", route, idem, result)
    db.commit()
    return result


@router.get("/feedback-drafts/{draft_id}")
def get_feedback_draft(draft_id: UUID, identity: Annotated[Identity, Depends(require_password_change_completed)],
                       db: Annotated[DbSession, Depends(get_db)]) -> dict[str, Any]:
    draft = db.get(FeedbackDraft, draft_id)
    if draft is None:
        raise _hidden()
    review = _review(db, draft.review_version_id)
    _access(db, review, identity)
    return _feedback_json(draft, review)


@router.put("/feedback-drafts/{draft_id}")
def update_feedback_draft(draft_id: UUID, body: FeedbackUpdateBody, request: Request,
                          identity: Annotated[Identity, Depends(require_verified_active_identity)],
                          db: Annotated[DbSession, Depends(get_db)],
                          idem: Annotated[str, Header(alias="Idempotency-Key")]) -> dict[str, Any]:
    route = f"/feedback-drafts/{draft_id}"
    replay = replay_or_lock(db, identity.user.id, "PUT", route, idem, body.model_dump(mode="json"))
    if replay is not None:
        return replay
    draft = db.scalar(select(FeedbackDraft).where(FeedbackDraft.id == draft_id).with_for_update())
    if draft is None:
        raise _hidden()
    review = _review(db, draft.review_version_id, True)
    _access(db, review, identity)
    if draft.state == "approved":
        raise _err("approved feedback is immutable; create a new draft", 409)
    if draft.generation_status in {"queued", "running"}:
        raise _err("feedback generation must finish before editing the draft", 409)
    if body.expected_revision != draft.revision:
        raise _err("feedback draft revision is stale", 409)
    if review.state != "complete" or review.content_hash != draft.source_review_hash:
        raise _err("feedback source review is stale", 409)
    text, blocks, included, exclusions = _feedback_parts(db, review, body)
    draft.body_markdown, draft.blocks_json = text, blocks
    draft.generation_mode = "manual"
    draft.included_review_finding_ids_json, draft.exclusion_reasons_json = included, exclusions
    draft.content_hash = _feedback_hash(draft.generation_mode, draft.source_review_hash,
        text, blocks, included, exclusions)
    draft.revision += 1
    db.flush()
    for block in blocks:
        for evidence_id in block.get("evidence_ids", []):
            db.add(EvidenceLink(evidence_item_id=UUID(str(evidence_id)), feedback_draft_id=draft.id))
    result = _feedback_json(draft, review)
    _audit(db, identity, request, "feedback.draft_edited", review.id, draft.revision - 1, draft.revision)
    store_response(db, identity.user.id, "PUT", route, idem, result)
    db.commit()
    return result


@router.post("/feedback-drafts/{draft_id}/approve")
def approve_feedback_draft(draft_id: UUID, body: FeedbackApproveBody, request: Request,
                           identity: Annotated[Identity, Depends(require_verified_active_identity)],
                           db: Annotated[DbSession, Depends(get_db)],
                           idem: Annotated[str, Header(alias="Idempotency-Key")]) -> dict[str, Any]:
    route = f"/feedback-drafts/{draft_id}/approve"
    replay = replay_or_lock(db, identity.user.id, "POST", route, idem, body.model_dump(mode="json"))
    if replay is not None:
        return replay
    draft = db.scalar(select(FeedbackDraft).where(FeedbackDraft.id == draft_id).with_for_update())
    if draft is None:
        raise _hidden()
    review = _review(db, draft.review_version_id, True)
    _access(db, review, identity)
    if draft.state != "draft" or draft.revision != body.expected_revision:
        raise _err("feedback draft revision is stale or already approved", 409)
    if draft.generation_status in {"queued", "running"}:
        raise _err("feedback generation must finish before approval", 409)
    if review.state != "complete" or review.revision != body.expected_review_revision or review.content_hash != draft.source_review_hash:
        raise _err("feedback approval is stale against the review", 409)
    expected_hash = _feedback_hash(draft.generation_mode, draft.source_review_hash,
        draft.body_markdown, draft.blocks_json, draft.included_review_finding_ids_json,
        draft.exclusion_reasons_json)
    if expected_hash != draft.content_hash:
        raise _err("feedback content hash mismatch", 409)
    draft.state, draft.approved_by, draft.approved_at = "approved", identity.user.id, datetime.now(UTC)
    draft.revision += 1
    db.flush()
    result = _feedback_json(draft, review)
    _audit(db, identity, request, "feedback.approved", review.id, body.expected_review_revision, review.revision)
    store_response(db, identity.user.id, "POST", route, idem, result)
    db.commit()
    return result


def _pub_events(db: DbSession, publication_id: UUID) -> tuple[PublicationEvent | None, PublicationEvent | None]:
    rows = db.scalars(select(PublicationEvent).where(PublicationEvent.publication_id == publication_id)
        .order_by(PublicationEvent.event_at, PublicationEvent.id)).all()
    return next((row for row in rows if row.event == "released"), None), rows[-1] if rows else None


def _publication_snapshot(db: DbSession, review: ReviewVersion, source: SubmissionVersion,
                          submission: Submission, assignment: Assignment, av: AssignmentVersion,
                          draft: FeedbackDraft, identity: Identity) -> dict[str, Any]:
    if draft.approved_by is None or draft.approved_at is None:
        raise _err("feedback approval metadata is missing", 409)
    ids = [UUID(item) for item in draft.included_review_finding_ids_json]
    findings = db.scalars(select(ReviewFinding).where(ReviewFinding.review_version_id == review.id,
        ReviewFinding.id.in_(ids), ReviewFinding.action.in_(["accept", "edit"])).order_by(ReviewFinding.id)).all() if ids else []
    if {item.id for item in findings} != set(ids):
        raise _err("feedback references a finding that is no longer confirmed", 409)
    ann_by_evidence: dict[UUID, dict[str, Any]] = {}
    for annotation in db.scalars(select(ReviewAnnotation).where(ReviewAnnotation.review_version_id == review.id)).all():
        rev = db.get(ReviewAnnotationRevision, annotation.current_revision_id) if annotation.current_revision_id else None
        if rev and rev.operation == "upsert" and (rev.corrected_evidence_id or rev.original_evidence_id):
            evidence_id = rev.corrected_evidence_id or rev.original_evidence_id
            if evidence_id is not None:
                ann_by_evidence[evidence_id] = {"annotation_id": str(annotation.id), "revision_id": str(rev.id),
                    "revision_no": rev.revision_no, "kind": annotation.kind, "anchor": rev.anchor_json,
                    "transcription": rev.transcription, "source_sha256": rev.source_sha256}
    packed = []
    for finding in findings:
        criterion = db.get(Criterion, finding.primary_criterion_id)
        evidence = []
        for link in _active_links(db, review, finding.id):
            item = db.get(EvidenceItem, link.evidence_item_id)
            asset = db.get(FileAsset, item.file_asset_id) if item else None
            if not item or not asset or item.source_sha256 != asset.sha256:
                raise _err("publication evidence source hash verification failed", 409)
            evidence.append({"id": str(item.id), "file_id": str(asset.id), "file_name": asset.original_name,
                "source_sha256": asset.sha256, "anchor": item.anchor_json,
                "content": item.content_original_json, "annotation": ann_by_evidence.get(item.id)})
        targets = db.scalars(select(ReviewFindingTarget).where(ReviewFindingTarget.review_finding_id == finding.id)
            .order_by(ReviewFindingTarget.id)).all()
        numeric = db.get(NumericCheck, finding.numeric_check_id) if finding.numeric_check_id else None
        packed.append({"id": str(finding.id), "category": finding.primary_category,
            "criterion_id": str(finding.primary_criterion_id), "criterion_title": criterion.title if criterion else "Kriteria",
            "claim": finding.claim, "targets": [_target_value(db, row) for row in targets], "evidence": evidence,
            "numeric_check": {"policy_version_id": str(numeric.numeric_policy_version_id),
                "operation": numeric.operation, "normalized_context": numeric.normalized_context_json,
                "operands": numeric.operands_snapshot_json, "result": numeric.result_decimal_text,
                "difference": numeric.difference_text, "allowed_difference": numeric.allowed_difference_text,
                "inconsistent": numeric.inconsistent} if numeric else None})
    return {"schema_version": 1,
        "source": {"submission_id": str(submission.id), "submission_version_id": str(source.id),
            "version_no": source.version_no, "manifest_hash": source.manifest_hash,
            "finalized_at": source.finalized_at.isoformat()},
        "assignment": {"id": str(assignment.id), "title": assignment.title,
            "version_id": str(av.id), "version_no": av.version_no,
            "instruction_hash": av.instruction_hash,
            "specification_version_id": str(av.specification_version_id)},
        "review": {"id": str(review.id), "version_no": review.version_no,
            "revision": review.revision, "content_hash": review.content_hash},
        "feedback": {"draft_id": str(draft.id), "content_hash": draft.content_hash,
            "generation_mode": draft.generation_mode, "body_markdown": draft.body_markdown,
            "blocks": draft.blocks_json},
        "confirmed_findings": packed, "limitations": list(review.limitations_json),
        "approved_by": str(draft.approved_by), "approved_at": draft.approved_at.isoformat(),
        "published_by": str(identity.user.id), "published_at": datetime.now(UTC).isoformat()}


@router.post("/reviews/{review_id}/publish", status_code=201)
def publish_review(review_id: UUID, body: PublishBody, request: Request,
                   identity: Annotated[Identity, Depends(require_verified_active_identity)],
                   db: Annotated[DbSession, Depends(get_db)],
                   idem: Annotated[str, Header(alias="Idempotency-Key")]) -> dict[str, Any]:
    route = f"/reviews/{review_id}/publish"
    replay = replay_or_lock(db, identity.user.id, "POST", route, idem, body.model_dump(mode="json"))
    if replay is not None:
        return replay
    review = _review(db, review_id, True)
    source, submission, assignment, av = _access(db, review, identity)
    locked_submission = db.scalar(select(Submission).where(Submission.id == submission.id)
        .with_for_update().execution_options(populate_existing=True))
    if locked_submission is None:
        raise _hidden()
    submission = locked_submission
    draft = db.scalar(select(FeedbackDraft).where(FeedbackDraft.id == body.feedback_draft_id,
        FeedbackDraft.review_version_id == review.id).with_for_update())
    if not draft:
        raise _hidden()
    if review.revision != body.expected_revision or draft.revision != body.expected_feedback_revision:
        raise _err("review or feedback revision is stale", 409)
    if submission.active_final_version_id != source.id:
        raise HTTPException(status_code=409, detail={"code": "SOURCE_VERSION_CHANGED",
            "message": "A newer final submission version must be reviewed before publication."})
    if review.state != "complete" or _complete_errors(db, review):
        raise _err("review is incomplete", 409)
    if draft.state != "approved" or draft.source_review_hash != review.content_hash:
        raise _err("approved feedback draft is stale", 409)
    snapshot = _publication_snapshot(db, review, source, submission, assignment, av, draft, identity)
    seq = (db.scalar(select(func.max(Publication.sequence_no)).where(Publication.submission_id == submission.id)) or 0) + 1
    now = datetime.now(UTC)
    publication = Publication(submission_id=submission.id, submission_version_id=source.id,
        review_version_id=review.id, feedback_draft_id=draft.id, sequence_no=seq,
        snapshot_json=snapshot, snapshot_hash=canonical_hash(snapshot), approved_by=draft.approved_by,
        approved_at=draft.approved_at, published_by=identity.user.id, published_at=now)
    db.add(publication)
    db.flush()
    submission.latest_published_id = publication.id
    db.add(PublicationEvent(publication_id=publication.id, event="released", actor_id=identity.user.id, event_at=now))
    for finding in snapshot["confirmed_findings"]:
        for item in finding["evidence"]:
            db.add(EvidenceLink(evidence_item_id=UUID(item["id"]), publication_id=publication.id))
    db.add(OutboxEvent(event_type="publication.released", subject_id=publication.id,
        payload_json={"publication_id": str(publication.id), "submission_version_id": str(source.id)}))
    _audit(db, identity, request, "publication.released", review.id, review.revision, review.revision)
    result = {"id": str(publication.id), "submission_id": str(submission.id),
        "submission_version_id": str(source.id), "sequence_no": publication.sequence_no,
        "snapshot_hash": publication.snapshot_hash, "status": "released",
        "published_at": publication.published_at.isoformat()}
    store_response(db, identity.user.id, "POST", route, idem, result)
    db.commit()
    return result


@router.post("/publications/{publication_id}/withdraw")
def withdraw_publication(publication_id: UUID, body: WithdrawBody, request: Request,
                         identity: Annotated[Identity, Depends(require_verified_active_identity)],
                         db: Annotated[DbSession, Depends(get_db)],
                         idem: Annotated[str, Header(alias="Idempotency-Key")]) -> dict[str, Any]:
    route = f"/publications/{publication_id}/withdraw"
    replay = replay_or_lock(db, identity.user.id, "POST", route, idem, body.model_dump(mode="json"))
    if replay is not None:
        return replay
    # Publication snapshots are immutable and API has no UPDATE privilege on that table.
    publication = db.get(Publication, publication_id)
    if not publication:
        raise _hidden()
    review = _review(db, publication.review_version_id, True)
    _access(db, review, identity)
    released, latest = _pub_events(db, publication.id)
    if released is None or (latest is not None and latest.event == "withdrawn"):
        raise _err("publication is already withdrawn or not released", 409)
    withdrawn_at = datetime.now(UTC)
    event = PublicationEvent(publication_id=publication.id, event="withdrawn",
        actor_id=identity.user.id, reason=body.reason.strip(), event_at=withdrawn_at)
    db.add(event)
    _audit(db, identity, request, "publication.withdrawn", review.id, review.revision, review.revision)
    result = {"publication_id": str(publication.id), "status": "withdrawn",
        "message": "Hasil ditarik untuk koreksi", "withdrawn_at": withdrawn_at.isoformat()}
    store_response(db, identity.user.id, "POST", route, idem, result)
    db.commit()
    return result


def _student_access(db: DbSession, identity: Identity, publication: Publication) -> tuple[Submission, SubmissionVersion]:
    source = db.get(SubmissionVersion, publication.submission_version_id)
    submission = db.get(Submission, publication.submission_id)
    assignment = db.get(Assignment, submission.assignment_id) if submission else None
    if not source or not submission or not assignment:
        raise _hidden()
    if (db.scalar(select(Course.id).where(Course.id == assignment.course_id, Course.active.is_(True))) is None
        or db.scalar(select(User.id).where(User.id == identity.user.id, User.active.is_(True))) is None
        or db.scalar(select(Enrollment.id).where(Enrollment.course_id == assignment.course_id,
            Enrollment.user_id == identity.user.id, Enrollment.role == "student", Enrollment.active.is_(True))) is None):
        raise _hidden()
    allowed = submission.individual_user_id == identity.user.id
    if submission.group_id is not None:
        allowed = db.scalar(select(SubmissionVersionMember.user_id).where(
            SubmissionVersionMember.submission_version_id == source.id,
            SubmissionVersionMember.user_id == identity.user.id)) is not None
    if not allowed:
        raise _hidden()
    return submission, source


def _student_snapshot(db: DbSession, publication: Publication) -> dict[str, Any]:
    released, latest = _pub_events(db, publication.id)
    source = db.get(SubmissionVersion, publication.submission_version_id)
    if released is None:
        return {"publication_id": str(publication.id), "status": "not_published"}
    if latest is None or latest.event == "withdrawn":
        return {"publication_id": str(publication.id), "status": "withdrawn",
            "message": "Hasil ditarik untuk koreksi",
            "submission_version": source.version_no if source else None,
            "withdrawn_at": latest.event_at.isoformat() if latest else None}
    snap = publication.snapshot_json
    safe_findings = []
    for finding in snap["confirmed_findings"]:
        safe_evidence = []
        for item in finding["evidence"]:
            content = item.get("content") if isinstance(item.get("content"), dict) else {}
            safe_content = {key: content[key] for key in (
                "text", "value", "unit", "row", "column", "row_label", "column_label",
                "formula", "number_format", "merged_range", "headers", "table_text", "origin",
            ) if key in content}
            annotation = item.get("annotation")
            safe_evidence.append({"file_name": item["file_name"], "source_sha256": item["source_sha256"],
                "anchor": item["anchor"], "content": safe_content,
                "annotation": ({"kind": annotation.get("kind"), "anchor": annotation.get("anchor"),
                    "transcription": annotation.get("transcription"), "source_sha256": annotation.get("source_sha256")}
                    if isinstance(annotation, dict) else None)})
        safe_findings.append({"category": finding["category"], "criterion_title": finding["criterion_title"],
            "claim": finding["claim"], "evidence": safe_evidence})
    return {"publication_id": str(publication.id), "status": "released",
        "submission_version": source.version_no if source else None,
        "assignment_version": snap["assignment"]["version_no"], "published_at": publication.published_at.isoformat(),
        "confirmed_findings": safe_findings,
        "feedback_markdown": snap["feedback"]["body_markdown"], "limitations": snap["limitations"],
        "feedback_blocks": [{"message": item.get("message", ""),
            "next_action": item.get("next_action"), "limitation": item.get("limitation")}
            for item in snap["feedback"].get("blocks", [])],
        "source_labels": sorted({item["file_name"] for finding in safe_findings for item in finding["evidence"]}),
        "source": {"submission_version": source.version_no if source else None,
            "finalized_at": snap["source"]["finalized_at"]}}


@router.get("/submissions/{submission_id}/published-result")
def published_result(submission_id: UUID, publication_id: UUID | None = None,
                     identity: Annotated[Identity, Depends(require_password_change_completed)] = None,  # type: ignore[assignment]
                     db: Annotated[DbSession, Depends(get_db)] = None) -> dict[str, Any]:  # type: ignore[assignment]
    submission = db.get(Submission, submission_id)
    if not submission:
        raise _hidden()
    selected = publication_id or submission.latest_published_id
    if selected is None:
        # Owners still get a safe state with no inference from parser/job status.
        assignment = db.get(Assignment, submission.assignment_id)
        source = db.get(SubmissionVersion, submission.active_final_version_id) if submission.active_final_version_id else None
        is_student = bool(assignment
            and db.scalar(select(Course.id).where(Course.id == assignment.course_id, Course.active.is_(True)))
            and db.scalar(select(User.id).where(User.id == identity.user.id, User.active.is_(True)))
            and db.scalar(select(Enrollment.id).where(Enrollment.course_id == assignment.course_id,
                Enrollment.user_id == identity.user.id, Enrollment.role == "student", Enrollment.active.is_(True))))
        allowed = submission.individual_user_id == identity.user.id
        if submission.group_id is not None and source is not None:
            allowed = db.scalar(select(SubmissionVersionMember.user_id).where(
                SubmissionVersionMember.submission_version_id == source.id,
                SubmissionVersionMember.user_id == identity.user.id)) is not None
        if not is_student or not allowed:
            raise _hidden()
        return {"submission_id": str(submission.id), "status": "not_published", "message": "Feedback belum diterbitkan"}
    publication = db.get(Publication, selected)
    if not publication or publication.submission_id != submission.id:
        raise _hidden()
    _student_access(db, identity, publication)
    return _student_snapshot(db, publication)


@router.get("/submissions/{submission_id}/publications")
def list_publications(submission_id: UUID, identity: Annotated[Identity, Depends(require_password_change_completed)],
                      db: Annotated[DbSession, Depends(get_db)]) -> dict[str, Any]:
    submission = db.get(Submission, submission_id)
    if not submission:
        raise _hidden()
    assignment = db.get(Assignment, submission.assignment_id)
    staff = bool(assignment and db.scalar(select(Enrollment.id).where(
        Enrollment.course_id == assignment.course_id, Enrollment.user_id == identity.user.id,
        Enrollment.role == "lecturer", Enrollment.active.is_(True))))
    if not staff:
        # An available publication may prove student ownership; revoked membership still blocks it.
        rows = db.scalars(select(Publication).where(Publication.submission_id == submission.id)
                          .order_by(Publication.sequence_no.desc())).all()
        if not rows:
            raise _hidden()
        _student_access(db, identity, rows[0])
        items = []
        for row in rows:
            source_version = db.get(SubmissionVersion, row.submission_version_id)
            if source_version is None:
                raise _hidden()
            items.append({"publication_id": str(row.id), "sequence_no": row.sequence_no,
                "submission_version": source_version.version_no,
                "status": _student_snapshot(db, row)["status"], "published_at": row.published_at.isoformat()})
        return {"items": items}
    rows = db.scalars(select(Publication).where(Publication.submission_id == submission.id)
                      .order_by(Publication.sequence_no.desc())).all()
    items = []
    for row in rows:
        _released, latest = _pub_events(db, row.id)
        items.append({"publication_id": str(row.id), "sequence_no": row.sequence_no,
            "submission_version_id": str(row.submission_version_id), "snapshot_hash": row.snapshot_hash,
            "status": latest.event if latest else "unknown",
            "published_at": row.published_at.isoformat()})
    return {"items": items}


@router.get("/publications/{publication_id}")
def get_publication(publication_id: UUID, identity: Annotated[Identity, Depends(require_password_change_completed)],
                    db: Annotated[DbSession, Depends(get_db)]) -> dict[str, Any]:
    publication = db.get(Publication, publication_id)
    if not publication:
        raise _hidden()
    review = _review(db, publication.review_version_id)
    try:
        _access(db, review, identity)
    except HTTPException:
        _student_access(db, identity, publication)
        return _student_snapshot(db, publication)
    _released, latest = _pub_events(db, publication.id)
    return {"publication_id": str(publication.id), "status": latest.event if latest else "unknown",
        "snapshot_hash": publication.snapshot_hash, "snapshot": publication.snapshot_json,
        "published_at": publication.published_at.isoformat()}


@router.get("/publications/{publication_id}/export")
def export_publication(publication_id: UUID, format: Literal["json", "md"] = Query(...),
                       identity: Annotated[Identity, Depends(require_password_change_completed)] = None,  # type: ignore[assignment]
                       db: Annotated[DbSession, Depends(get_db)] = None) -> Response:  # type: ignore[assignment]
    publication = db.get(Publication, publication_id)
    if not publication:
        raise _hidden()
    review = _review(db, publication.review_version_id)
    try:
        _access(db, review, identity)
        payload = _student_snapshot(db, publication)
    except HTTPException:
        _student_access(db, identity, publication)
        payload = _student_snapshot(db, publication)
    if payload.get("status") != "released":
        raise _err("only released publications can be exported", 409)
    if format == "json":
        import json
        content, media = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), "application/json; charset=utf-8"
        suffix = "json"
    else:
        lines = [f"# Hasil tugas versi {payload['submission_version']}", "",
            f"Versi tugas: {payload['assignment_version']}", "",
            str(payload["feedback_markdown"]), ""]
        if payload["limitations"]:
            lines.extend(["## Batas pemeriksaan", *[f"- {item}" for item in payload["limitations"]], ""])
        if payload["confirmed_findings"]:
            lines.extend(["## Temuan yang dikonfirmasi", *[
                f"- **{item['criterion_title']}** ({item['category']}): {item['claim']}"
                for item in payload["confirmed_findings"]], ""])
        content, media, suffix = "\n".join(lines), "text/markdown; charset=utf-8", "md"
    return Response(content=content, media_type=media,
        headers={"Content-Disposition": f'attachment; filename="align-publication-{publication.sequence_no}.{suffix}"',
                 "Cache-Control": "private, no-store"})
