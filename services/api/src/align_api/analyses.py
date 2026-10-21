from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BeforeValidator
from sqlalchemy import select, text
from sqlalchemy.orm import Session as DbSession

from align_api.auth import (
    Identity,
    StrictRequest,
    require_password_change_completed,
    require_verified_active_identity,
)
from align_api.db import get_db
from align_api.idempotency import replay_or_lock, store_response
from align_api.models import (
    AnalysisResultEvidence,
    AnalysisRun,
    AnalysisRunStage,
    Assignment,
    AssignmentVersion,
    CheckElement,
    Course,
    CoverageInspection,
    CoverageRecord,
    ElementCheck,
    Enrollment,
    FindingCandidate,
    FindingElementLink,
    FindingEvidenceLink,
    FindingRequirementLink,
    GroundingEvaluation,
    Job,
    MethodManifest,
    NumericPolicyVersion,
    OutboxEvent,
    ParseArtifact,
    ProviderCallRecord,
    ReleaseManifest,
    RoutingDecision,
    SpecificationVersion,
    Submission,
    SubmissionVersion,
    SubmissionVersionFile,
    SupportBinding,
)
from align_domain.assignment_contracts import SpecificationPayload
from align_domain.executor import analysis_fingerprint

router = APIRouter(tags=["analysis"])


def _uuid(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            return value
    return value


UUIDInput = Annotated[UUID, BeforeValidator(_uuid)]


class StartAnalysis(StrictRequest):
    release_manifest_id: UUIDInput


def _hidden() -> HTTPException:
    return HTTPException(status_code=404, detail="analysis resource not found")


def _scope(
    db: DbSession, source: SubmissionVersion, identity: Identity
) -> tuple[Submission, Assignment, AssignmentVersion, Course]:
    submission = db.get(Submission, source.submission_id)
    assignment = db.get(Assignment, submission.assignment_id) if submission else None
    version = db.get(AssignmentVersion, source.assignment_version_id)
    course = db.get(Course, assignment.course_id) if assignment else None
    if submission is None or assignment is None or version is None or course is None:
        raise _hidden()
    role = db.scalar(
        select(Enrollment.role).where(
            Enrollment.course_id == assignment.course_id,
            Enrollment.user_id == identity.user.id,
            Enrollment.active.is_(True),
        )
    )
    if role != "lecturer" or not course.active or not identity.user.active:
        raise _hidden()
    return submission, assignment, version, course


def _fingerprint_snapshot(
    db: DbSession,
    source: SubmissionVersion,
    version: AssignmentVersion,
    spec: SpecificationVersion,
    release: ReleaseManifest,
) -> dict[str, Any]:
    method_rows = {
        "parser": db.get(MethodManifest, release.parser_manifest_id),
        "model": db.get(MethodManifest, release.model_manifest_id),
        "retrieval": db.get(MethodManifest, release.retrieval_manifest_id),
        "rules": db.get(MethodManifest, release.rules_manifest_id),
        "prompt": db.get(MethodManifest, release.prompt_manifest_id),
        "routing": db.get(MethodManifest, release.routing_policy_manifest_id),
        "batching": db.get(MethodManifest, release.batching_policy_manifest_id),
    }
    policies = db.scalars(
        select(NumericPolicyVersion)
        .where(
            NumericPolicyVersion.assignment_id == version.assignment_id,
            NumericPolicyVersion.state == "approved",
        )
        .order_by(NumericPolicyVersion.id)
    ).all()
    parse_artifacts = db.scalars(
        select(ParseArtifact)
        .where(ParseArtifact.submission_version_id == source.id)
        .order_by(ParseArtifact.file_asset_id, ParseArtifact.id)
    ).all()
    parse_snapshot = []
    for artifact in parse_artifacts:
        parser_manifest = (
            db.get(MethodManifest, artifact.parser_manifest_id)
            if artifact.parser_manifest_id
            else None
        )
        resource_manifest = (
            db.get(MethodManifest, artifact.parser_resource_manifest_id)
            if artifact.parser_resource_manifest_id
            else None
        )
        parse_snapshot.append(
            {
                "artifact_id": str(artifact.id),
                "file_asset_id": str(artifact.file_asset_id),
                "fingerprint": artifact.fingerprint,
                "status": artifact.status,
                "parser_manifest": parser_manifest.sha256 if parser_manifest else None,
                "resource_manifest": resource_manifest.sha256 if resource_manifest else None,
                "limitations": artifact.limitations_json,
            }
        )
    return {
        "submission_manifest_hash": source.manifest_hash,
        "specification_hash": spec.content_hash,
        "assignment_version_id": str(version.id),
        "parser": method_rows["parser"].sha256 if method_rows["parser"] else "missing",
        "model": method_rows["model"].sha256 if method_rows["model"] else "missing",
        "retrieval": method_rows["retrieval"].sha256 if method_rows["retrieval"] else "missing",
        "rules": method_rows["rules"].sha256 if method_rows["rules"] else "missing",
        "prompt": method_rows["prompt"].sha256 if method_rows["prompt"] else "missing",
        "routing": method_rows["routing"].sha256 if method_rows["routing"] else "missing",
        "batching": method_rows["batching"].sha256 if method_rows["batching"] else "missing",
        "grounding_registry": release.grounding_registry_version,
        "lint_registry": release.lint_registry_version,
        "numeric_policy_hashes": [policy.content_hash for policy in policies],
        "submission_parser_artifacts": parse_snapshot,
        "release_hash": release.config_hash,
    }


def _analysis_access(
    db: DbSession, run: AnalysisRun, identity: Identity
) -> tuple[SubmissionVersion, Submission, Assignment, AssignmentVersion]:
    source = db.get(SubmissionVersion, run.submission_version_id)
    if source is None:
        raise _hidden()
    submission, assignment, version, _ = _scope(db, source, identity)
    if run.specification_version_id != version.specification_version_id:
        raise _hidden()
    return source, submission, assignment, version


@router.get("/runtime/releases")
def runtime_releases(
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
) -> dict[str, Any]:
    lecturer_exists = db.scalar(
        select(Enrollment.id).where(
            Enrollment.user_id == identity.user.id,
            Enrollment.role == "lecturer",
            Enrollment.active.is_(True),
        )
    )
    if not lecturer_exists or not identity.user.active:
        raise _hidden()
    rows = db.scalars(
        select(ReleaseManifest)
        .where(ReleaseManifest.state.in_(["active", "verified"]))
        .order_by(ReleaseManifest.version_label, ReleaseManifest.config_hash)
    ).all()
    rows = [
        row for row in rows
        if "b5_executor" in row.manifest_payload.get("capabilities_available", [])
    ]
    return {
        "releases": [
            {
                "id": str(row.id),
                "version_label": row.version_label,
                "state": row.state,
                "config_hash": row.config_hash,
                "provider_mode": row.manifest_payload.get("provider_mode", "off"),
                "data_mode": row.manifest_payload.get("data_mode", "synthetic"),
                "capabilities_available": row.manifest_payload.get("capabilities_available", []),
                "capabilities_unavailable": row.manifest_payload.get(
                    "capabilities_unavailable", []
                ),
                "paid_calls": "blocked_before_network",
            }
            for row in rows
        ]
    }


@router.post("/submission-versions/{submission_version_id}/analysis-runs", status_code=202)
def start_analysis(
    submission_version_id: UUID,
    body: StartAnalysis,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    idem: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, Any]:
    source = db.get(SubmissionVersion, submission_version_id)
    if source is None:
        raise _hidden()
    submission, assignment, version, _ = _scope(db, source, identity)
    if submission.active_final_version_id != source.id:
        raise HTTPException(
            status_code=409, detail="analysis requires the current immutable final submission"
        )
    final_file_ids = set(
        db.scalars(
            select(SubmissionVersionFile.file_asset_id).where(
                SubmissionVersionFile.submission_version_id == source.id
            )
        ).all()
    )
    parser_artifacts = db.scalars(
        select(ParseArtifact).where(ParseArtifact.submission_version_id == source.id)
    ).all()
    if (
        not final_file_ids
        or {artifact.file_asset_id for artifact in parser_artifacts} != final_file_ids
        or any(artifact.status in {"queued", "running"} for artifact in parser_artifacts)
    ):
        raise HTTPException(
            status_code=409,
            detail="submission parser checkpoints must finish before analysis",
        )
    if version.state != "published" or version.specification_version_id is None:
        raise HTTPException(status_code=409, detail="published schema-2 specification required")
    spec = db.get(SpecificationVersion, version.specification_version_id)
    if (
        spec is None
        or spec.state != "approved"
        or spec.schema_version != 2
        or spec.assignment_id != assignment.id
        or spec.instruction_hash != version.instruction_hash
        or spec.context_version_id != version.context_version_id
    ):
        raise HTTPException(
            status_code=409, detail="approved schema-2 specification snapshot is unavailable"
        )
    try:
        payload = SpecificationPayload.model_validate_json(
            json.dumps(spec.payload_json, ensure_ascii=False)
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail="approved specification snapshot is invalid"
        ) from exc
    if not payload.elements:
        raise HTTPException(
            status_code=409, detail="approved specification contains no check elements"
        )
    release = db.get(ReleaseManifest, body.release_manifest_id)
    if release is None or release.state not in {"active", "verified"}:
        raise HTTPException(status_code=409, detail="usable immutable release manifest required")
    provider_mode = release.manifest_payload.get("provider_mode")
    data_mode = release.manifest_payload.get("data_mode")
    if provider_mode not in {"mock", "off", "live"} or data_mode not in {
        "synthetic",
        "institutional",
    }:
        raise HTTPException(status_code=409, detail="release provider or data mode is invalid")
    available = set(release.manifest_payload.get("capabilities_available", []))
    if not {"b5_executor", "scoped_hybrid_retrieval", "typed_grounding"}.issubset(available):
        raise HTTPException(status_code=409, detail="release manifest does not include verified B5 capabilities")
    snapshot = _fingerprint_snapshot(db, source, version, spec, release)
    fingerprint = analysis_fingerprint(snapshot)
    route = f"/submission-versions/{source.id}/analysis-runs"
    replay = replay_or_lock(db, identity.user.id, "POST", route, idem, body.model_dump(mode="json"))
    if replay is not None:
        return replay
    advisory = int(fingerprint[:16], 16)
    if advisory >= 2**63:
        advisory -= 2**64
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": advisory})
    run = db.scalar(
        select(AnalysisRun)
        .where(
            AnalysisRun.submission_version_id == source.id,
            AnalysisRun.config_fingerprint == fingerprint,
        )
        .with_for_update()
    )
    if run is None:
        run = AnalysisRun(
            submission_version_id=source.id,
            specification_version_id=spec.id,
            release_manifest_id=release.id,
            config_fingerprint=fingerprint,
            status="queued",
            provider_mode=str(provider_mode),
            data_origin="synthetic" if data_mode == "synthetic" else "production",
            limitations_json=[],
            manifest_snapshot_json=snapshot,
            stage_snapshot_json={},
            usage_snapshot_json={},
            completion_kind=None,
        )
        db.add(run)
        db.flush()
        settings_limits = {
            "max_calls": 16,
            "max_vision_calls": 4,
            "max_text_input_tokens": 40_000,
            "max_text_output_tokens": 8_000,
            "max_embedding_tokens": 80_000,
            "max_active_seconds": 600,
            "budget_usd": "0.20",
            "batch_size": 1,
        }
        job = Job(
            type="analysis",
            subject_id=run.id,
            scope_json={
                "course_id": str(assignment.course_id),
                "assignment_version_id": str(version.id),
                "submission_version_id": str(source.id),
            },
            input_fingerprint=fingerprint,
            status="queued",
            stage="validate_manifest",
            lease_fence=0,
            job_limits_json=settings_limits,
        )
        db.add(job)
        db.flush()
        run.job_id = job.id
        db.add(
            OutboxEvent(
                event_type="job.analysis_run",
                subject_id=job.id,
                payload_json={"analysis_run_id": str(run.id)},
                next_attempt_at=datetime.now(UTC),
            )
        )
    result = {
        "analysis_run_id": str(run.id),
        "job_id": str(run.job_id) if run.job_id else None,
        "status": run.status,
        "config_fingerprint": fingerprint,
        "provider_mode": run.provider_mode,
        "label": "SIMULASI • data sintetis"
        if run.provider_mode == "mock" or run.data_origin == "synthetic"
        else "analysis run",
        "duplicate": run.status != "queued",
    }
    store_response(db, identity.user.id, "POST", route, idem, result)
    db.commit()
    return result


@router.get("/analysis-runs/{analysis_run_id}")
def get_analysis(
    analysis_run_id: UUID,
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
) -> dict[str, Any]:
    run = db.get(AnalysisRun, analysis_run_id)
    if run is None:
        raise _hidden()
    source, _, _, _ = _analysis_access(db, run, identity)
    checks = db.scalars(
        select(ElementCheck)
        .where(ElementCheck.analysis_run_id == run.id)
        .order_by(ElementCheck.check_element_id)
    ).all()
    elements = db.scalars(
        select(CheckElement)
        .where(CheckElement.specification_version_id == run.specification_version_id)
        .order_by(CheckElement.element_code, CheckElement.id)
    ).all()
    check_by_element = {row.check_element_id: row for row in checks}
    check_json = []
    for element in elements:
        check = check_by_element.get(element.id)
        coverage = db.scalar(
            select(CoverageRecord).where(
                CoverageRecord.analysis_run_id == run.id,
                CoverageRecord.check_element_id == element.id,
            )
        )
        grounding = None
        if check is not None:
            grounding = db.scalar(
                select(GroundingEvaluation).where(
                    GroundingEvaluation.analysis_run_id == run.id,
                    GroundingEvaluation.element_check_id == check.id,
                )
            )
        evidence_ids = (
            db.scalars(
                select(AnalysisResultEvidence.evidence_item_id)
                .where(AnalysisResultEvidence.element_check_id == check.id)
                .order_by(AnalysisResultEvidence.evidence_item_id)
            ).all()
            if check
            else []
        )
        support_bindings = (
            db.scalars(
                select(SupportBinding)
                .where(SupportBinding.element_check_id == check.id)
                .order_by(
                    SupportBinding.relationship_code,
                    SupportBinding.role,
                    SupportBinding.evidence_item_id,
                )
            ).all()
            if check
            else []
        )
        check_json.append(
            {
                "check_element_id": str(element.id),
                "element_id": str(element.element_id),
                "element_code": element.element_code,
                "decision_question": element.decision_question,
                "grounding_mode": element.grounding_mode,
                "applicability_status": check.applicability_status if check else "unresolved",
                "source_coverage_status": check.source_coverage_status if check else "unresolved",
                "evidence_relationship": check.evidence_relationship if check else "unclear",
                "check_status": check.check_status if check else "unassessable",
                "observations": check.observations_json if check else [],
                "interpretation": check.interpretation if check else None,
                "reason_codes": check.reason_codes_json if check else ["check_pending"],
                "evidence_ids": [str(value) for value in evidence_ids],
                "support_bindings": [
                    {
                        "relationship_code": item.relationship_code,
                        "role": item.role,
                        "observation_codes": item.observation_codes_json,
                        "evidence_item_id": str(item.evidence_item_id),
                        "quotes": item.quote_spans_json,
                    }
                    for item in support_bindings
                ],
                "grounding": {
                    "mechanical_status": grounding.mechanical_status,
                    "semantic_status": grounding.semantic_status,
                    "evaluation": grounding.evaluation_json,
                    "limitations": grounding.limitation_codes_json,
                }
                if grounding
                else None,
                "coverage": None
                if coverage is None
                else {
                    "retrieval_status": coverage.retrieval_status,
                    "compatible_count": coverage.compatible_count,
                    "inspected_count": coverage.inspected_count,
                    "unresolved_count": coverage.unresolved_count,
                    "sufficient_for_positive": coverage.sufficient_for_positive,
                    "exhaustive_for_negative": coverage.exhaustive_for_negative,
                    "inventory": coverage.inventory_json,
                    "retrieval": coverage.retrieval_json,
                    "limitations": coverage.limitations_json,
                    "inspections": [
                        {
                            "source_unit_id": str(item.source_unit_id),
                            "evidence_item_id": str(item.evidence_item_id)
                            if item.evidence_item_id
                            else None,
                            "state": item.state,
                            "method": item.method,
                            "reason_code": item.reason_code,
                            "detail": item.detail_json,
                        }
                        for item in db.scalars(
                            select(CoverageInspection)
                            .where(CoverageInspection.coverage_record_id == coverage.id)
                            .order_by(
                                CoverageInspection.source_unit_id,
                                CoverageInspection.evidence_item_id,
                            )
                        ).all()
                    ],
                },
            }
        )
    candidates = db.scalars(
        select(FindingCandidate)
        .where(FindingCandidate.analysis_run_id == run.id)
        .order_by(FindingCandidate.created_at, FindingCandidate.id)
    ).all()
    candidate_json = []
    for candidate in candidates:
        candidate_json.append(
            {
                "id": str(candidate.id),
                "category": candidate.primary_category,
                "criterion_id": str(candidate.primary_criterion_id),
                "obligation_key": candidate.obligation_key,
                "issue_signature": candidate.issue_signature,
                "claim": candidate.claim,
                "technical_validation": candidate.technical_validation,
                "semantic_support": candidate.semantic_support,
                "limitations": candidate.limitations_json,
                "element_ids": [
                    str(item)
                    for item in db.scalars(
                        select(FindingElementLink.check_element_id).where(
                            FindingElementLink.finding_candidate_id == candidate.id
                        )
                    ).all()
                ],
                "requirement_ids": [
                    str(item)
                    for item in db.scalars(
                        select(FindingRequirementLink.requirement_id).where(
                            FindingRequirementLink.finding_candidate_id == candidate.id
                        )
                    ).all()
                ],
                "evidence_ids": [
                    str(item)
                    for item in db.scalars(
                        select(FindingEvidenceLink.evidence_item_id).where(
                            FindingEvidenceLink.finding_candidate_id == candidate.id
                        )
                    ).all()
                ],
            }
        )
    stages = db.scalars(
        select(AnalysisRunStage)
        .where(AnalysisRunStage.analysis_run_id == run.id)
        .order_by(AnalysisRunStage.created_at, AnalysisRunStage.id)
    ).all()
    routing = db.scalars(
        select(RoutingDecision)
        .where(RoutingDecision.analysis_run_id == run.id)
        .order_by(RoutingDecision.created_at, RoutingDecision.id)
    ).all()
    calls = db.scalars(
        select(ProviderCallRecord)
        .where(ProviderCallRecord.analysis_run_id == run.id)
        .order_by(ProviderCallRecord.created_at, ProviderCallRecord.id)
    ).all()
    return {
        "id": str(run.id),
        "submission_version_id": str(source.id),
        "specification_version_id": str(run.specification_version_id),
        "release_manifest_id": str(run.release_manifest_id) if run.release_manifest_id else None,
        "config_fingerprint": run.config_fingerprint,
        "status": run.status,
        "completion_kind": run.completion_kind,
        "provider_mode": run.provider_mode,
        "data_origin": run.data_origin,
        "label": "SIMULASI • data sintetis"
        if run.provider_mode == "mock" or run.data_origin == "synthetic"
        else "analysis run",
        "job_id": str(run.job_id) if run.job_id else None,
        "manifest_snapshot": run.manifest_snapshot_json,
        "stage_snapshot": run.stage_snapshot_json,
        "usage": run.usage_snapshot_json,
        "limitations": run.limitations_json,
        "stages": [
            {
                "stage": item.stage_name,
                "status": item.status,
                "input_hash": item.input_hash,
                "output_refs": item.output_refs_json,
                "limitations": item.limitation_codes_json,
            }
            for item in stages
        ],
        "elements": check_json,
        "finding_candidates": candidate_json,
        "routing_decisions": [
            {
                "check_element_id": str(item.check_element_id) if item.check_element_id else None,
                "task": item.task,
                "action": item.action,
                "selected_role": item.selected_role,
                "reason_code": item.reason_code,
                "features": item.feature_snapshot_json,
                "guards": item.guard_snapshot_json,
                "remaining_limits": item.remaining_limits_json,
                "outcome": item.outcome,
            }
            for item in routing
        ],
        "provider_calls": [
            {
                "id": str(item.id),
                "task": item.task,
                "provider_mode": item.provider_mode,
                "status": item.status,
                "request": item.request_manifest_json,
                "response": item.response_manifest_json,
                "usage": item.usage_json,
                "cost_usd": str(item.cost_usd) if item.cost_usd is not None else None,
            }
            for item in calls
        ],
    }
