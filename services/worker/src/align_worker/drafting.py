"""Fenced, source-bound B2 drafting assistance; results remain lecturer proposals."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid5

from sqlalchemy import select

from align_api.config import get_settings
from align_api.idempotency import canonical_hash
from align_api.models import (
    AssignmentVersion,
    Criterion,
    CriterionOutcomeLink,
    CriterionRequirementLink,
    CriterionSetVersion,
    Job,
    JobStage,
    LearningOutcome,
    MethodManifest,
    Requirement,
)
from align_api.paid_calls import parse_manifest_id
from align_domain.drafting import DraftAssistanceOutput, DraftSourceReference
from align_domain.providers import (
    MockProvider,
    OpenAIResponsesProvider,
    PaidCallBlocked,
    ProviderContext,
)
from align_worker.database import SessionFactory
from align_worker.finance import WorkerPaidCallLedger

MAX_INPUT_TOKENS = 20_000
MAX_OUTPUT_TOKENS = 4_000


def _strict_schema() -> dict[str, Any]:
    source_ref = {
        "type": "object",
        "additionalProperties": False,
        "required": ["source_kind", "source_id", "source_hash", "quote"],
        "properties": {
            "source_kind": {"type": "string", "enum": ["instruction", "criterion", "requirement"]},
            "source_id": {"type": "string", "format": "uuid"},
            "source_hash": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
            "quote": {"type": "string", "minLength": 1, "maxLength": 2000},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "status", "suggestions", "limitations"],
        "properties": {
            "schema_version": {"type": "integer", "enum": [1]},
            "status": {"type": "string", "enum": ["suggestions", "abstained"]},
            "suggestions": {
                "type": "array",
                "maxItems": 100,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["proposal_kind", "title", "description", "source_refs"],
                    "properties": {
                        "proposal_kind": {"type": "string", "enum": ["criterion", "requirement", "spec_element"]},
                        "title": {"type": "string", "minLength": 1, "maxLength": 240},
                        "description": {"type": "string", "minLength": 1, "maxLength": 2000},
                        "source_refs": {"type": "array", "minItems": 1, "maxItems": 20, "items": source_ref},
                    },
                },
            },
            "limitations": {"type": "array", "maxItems": 100, "items": {"type": "string"}},
        },
    }


def _source_ref(
    kind: Literal["instruction", "criterion", "requirement"],
    source_id: UUID,
    digest: str,
    quote: str,
) -> dict[str, str]:
    return DraftSourceReference(
        source_kind=kind, source_id=source_id, source_hash=digest, quote=quote[:2000]
    ).model_dump(mode="json")


def _collect_source(db: Any, job: Job) -> tuple[dict[str, Any], list[dict[str, str]]]:
    scope = job.scope_json
    version_id = UUID(str(scope["assignment_version_id"]))
    target_id = UUID(str(scope["target_id"]))
    version = db.get(AssignmentVersion, version_id)
    criterion_set = db.get(CriterionSetVersion, target_id)
    if (
        version is None
        or version.assignment_id != UUID(str(scope["assignment_id"]))
        or criterion_set is None
        or criterion_set.assignment_id != version.assignment_id
        or criterion_set.instruction_hash != version.instruction_hash
        or criterion_set.revision != scope.get("target_revision")
        or criterion_set.content_hash != scope.get("target_content_hash")
    ):
        raise ValueError("draft_source_snapshot_mismatch")
    allowed: list[dict[str, str]] = []
    instruction_refs: list[dict[str, Any]] = []
    offset = 0
    for line in version.instruction_original.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        leading = len(content) - len(content.lstrip())
        quote = content.strip()
        start, end = offset + leading, offset + leading + len(quote)
        if quote:
            ref = _source_ref("instruction", version.id, version.instruction_hash, quote)
            allowed.append(ref)
            instruction_refs.append({"span": {"start": start, "end": end}, "quote": quote, "source_ref": ref})
        offset += len(line)
    if job.type == "criteria_draft":
        if version.state != "draft" or criterion_set.state != "draft":
            raise ValueError("criteria_draft_is_no_longer_editable")
        source = {
            "task_type": "criteria_draft",
            "instruction": version.instruction_original,
            "instruction_hash": version.instruction_hash,
            "exact_instruction_excerpts": instruction_refs,
            "existing_criteria": [],
            "rubric_note": "Rubrik diparse melalui job terpisah; saran ini tidak menambah bukti rubrik.",
        }
    else:
        if version.state != "draft" or criterion_set.state != "approved":
            raise ValueError("specification_requires_approved_criteria_and_editable_version")
        criteria = db.scalars(
            select(Criterion).where(Criterion.criterion_set_version_id == criterion_set.id)
            .order_by(Criterion.display_order, Criterion.id)
        ).all()
        criterion_rows: list[dict[str, Any]] = []
        for criterion in criteria:
            quote = criterion.descriptor.strip()
            criterion_ref = None
            if quote:
                criterion_ref = _source_ref("criterion", criterion.id, canonical_hash(criterion.descriptor), quote)
                allowed.append(criterion_ref)
            requirements = db.scalars(
                select(Requirement)
                .join(CriterionRequirementLink, CriterionRequirementLink.requirement_id == Requirement.id)
                .where(CriterionRequirementLink.criterion_id == criterion.id)
                .order_by(Requirement.code, Requirement.id)
            ).all()
            requirement_rows = []
            for requirement in requirements:
                req_ref = _source_ref("requirement", requirement.id, canonical_hash(requirement.text), requirement.text)
                allowed.append(req_ref)
                requirement_rows.append({
                    "id": str(requirement.id), "code": requirement.code,
                    "text": requirement.text, "mandatory": requirement.mandatory,
                    "source_ref": req_ref,
                })
            outcomes = db.scalars(
                select(LearningOutcome)
                .join(CriterionOutcomeLink, CriterionOutcomeLink.learning_outcome_id == LearningOutcome.id)
                .where(CriterionOutcomeLink.criterion_id == criterion.id)
                .order_by(LearningOutcome.code, LearningOutcome.id)
            ).all()
            criterion_rows.append({
                "id": str(criterion.id), "code": criterion.code,
                "title": criterion.title, "descriptor": criterion.descriptor,
                "source_ref": criterion_ref,
                "requirements": requirement_rows,
                "learning_outcomes": [{"id": str(item.id), "code": item.code, "description": item.description} for item in outcomes],
            })
        source = {
            "task_type": "specification_draft",
            "instruction": version.instruction_original,
            "instruction_hash": version.instruction_hash,
            "exact_instruction_excerpts": instruction_refs,
            "approved_criteria": criterion_rows,
            "rule": "Suggest check questions only. The lecturer must build/validate grounding, applicability and operators in the schema editor.",
        }
    payload_bytes = len(json.dumps(source, ensure_ascii=False).encode("utf-8"))
    if payload_bytes > MAX_INPUT_TOKENS * 4:
        raise ValueError("draft_input_processing_limit")
    return source, allowed


def _provider(settings: Any, mode: str) -> Any | None:
    if mode == "mock":
        return MockProvider()
    if mode != "live" or not settings.openai_api_key:
        return None
    model = settings.model_efficient or ""
    return OpenAIResponsesProvider(
        settings.openai_api_key.get_secret_value(),
        {"efficient": model, "extract_fields": model},
        paid_call_ledger=WorkerPaidCallLedger(enabled=settings.paid_calls_enabled),
    )


def _attempt(job: Job, source: dict[str, Any], allowed_refs: list[dict[str, str]], settings: Any) -> dict[str, Any]:
    mode = str(job.scope_json.get("provider_mode", "off"))
    try:
        manifest_id = UUID(str(job.scope_json["model_manifest_id"]))
        policy_id = UUID(str(job.scope_json["routing_manifest_id"]))
    except (KeyError, ValueError, TypeError):
        return {"status": "provider_manifest_invalid", "provider_mode": mode, "paid_calls": 0}
    input_tokens = max(1, (len(json.dumps(source, ensure_ascii=False).encode("utf-8")) + 3) // 4)
    if input_tokens > MAX_INPUT_TOKENS:
        return {"status": "processing_limit", "provider_mode": mode, "paid_calls": 0}
    payload = {**source, "json_schema": _strict_schema()}
    billing_input_tokens = (len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + 3) // 4 + 256
    if billing_input_tokens > MAX_INPUT_TOKENS:
        return {"status": "processing_limit", "provider_mode": mode, "paid_calls": 0}
    call_id = uuid5(job.id, f"{job.input_fingerprint}:1")
    call = ProviderContext(
        call_id=call_id,
        job_id=job.id,
        manifest_id=manifest_id,
        policy_id=policy_id,
        scope_ids=[UUID(str(job.scope_json["assignment_version_id"])), UUID(str(job.scope_json["target_id"]))],
        max_input_tokens=MAX_INPUT_TOKENS,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        paid_release_manifest_id=parse_manifest_id(job.scope_json.get("paid_release_manifest_id")),
        rate_manifest_id=parse_manifest_id(job.scope_json.get("rate_manifest_id")),
        logical_call_id=call_id,
        paid_calls_enabled=bool(job.scope_json.get("paid_calls_enabled") and getattr(settings, "paid_calls_enabled", False)),
        billing_bounds={
            "input_tokens": min(MAX_INPUT_TOKENS, billing_input_tokens),
            "output_tokens": MAX_OUTPUT_TOKENS,
            "embedding_tokens": 0,
            "vision_calls": 0,
            "image_count": 0,
            "active_ms": min(60_000, getattr(settings, "job_max_active_seconds", 600) * 1000),
        },
    )
    provider = _provider(settings, mode)
    if provider is None:
        return {"status": "provider_disabled", "provider_mode": mode, "paid_calls": 0}
    input_hash = canonical_hash(payload)
    try:
        result = provider.extract_fields(call, payload)
    except PaidCallBlocked:
        return {
            "status": "paid_call_guard_blocked_before_network",
            "provider_mode": mode,
            "simulated": False,
            "input_sha256": input_hash,
            "input_token_estimate": input_tokens,
            "paid_calls": 0,
        }
    except Exception:
        return {"status": "provider_error", "provider_mode": mode, "input_sha256": input_hash, "paid_calls": 0}
    simulated = mode == "mock"
    usage = {
        "input_tokens": max(0, int(result.usage.get("input_tokens", input_tokens) or input_tokens)),
        "output_tokens": max(0, int(result.usage.get("output_tokens", 0) or 0)),
    }
    if (
        result.call_id != call.call_id
        or result.job_id != call.job_id
        or result.manifest_id != call.manifest_id
        or result.policy_id != call.policy_id
        or result.scope_ids != call.scope_ids
        or result.limits != {"max_input_tokens": MAX_INPUT_TOKENS, "max_output_tokens": MAX_OUTPUT_TOKENS}
        or result.simulated != simulated
        or result.status != ("mocked" if simulated else "completed")
        or usage["input_tokens"] > MAX_INPUT_TOKENS
        or usage["output_tokens"] > MAX_OUTPUT_TOKENS
    ):
        return {"status": "provider_response_invalid", "provider_mode": mode, "simulated": simulated, "paid_calls": 0}
    try:
        parsed = DraftAssistanceOutput.model_validate(result.payload)
        if simulated and (parsed.status != "abstained" or parsed.suggestions):
            raise ValueError("mock_drafting_must_abstain")
        allowed = {canonical_hash(item) for item in allowed_refs}
        if not allowed:
            raise ValueError("draft_source_registry_missing")
        if any(
            canonical_hash(ref.model_dump(mode="json")) not in allowed
            for suggestion in parsed.suggestions
            for ref in suggestion.source_refs
        ):
            raise ValueError("draft_source_reference_invalid")
    except Exception:
        return {"status": "provider_response_invalid", "provider_mode": mode, "simulated": simulated, "paid_calls": 0}
    return {
        "status": "mock_abstention" if simulated else parsed.status,
        "provider_mode": mode,
        "simulated": simulated,
        "label": "SIMULASI • data sintetis" if simulated else None,
        "fixture_id": result.fixture_id,
        "input_sha256": input_hash,
        "output_sha256": canonical_hash(result.payload),
        "usage": usage,
        "paid_calls": 0,
        "suggestions": [
            {**item.model_dump(mode="json"), "status": "needs_lecturer_decision"}
            for item in parsed.suggestions
        ],
        "limitations": parsed.limitations,
    }


def execute_draft_job(job_id: UUID, expected_fence: int, expected_fingerprint: str) -> dict[str, Any]:
    settings = get_settings()
    with SessionFactory() as db:
        job = db.get(Job, job_id)
        if job is None:
            return {"status": "missing"}
        if job.lease_fence != expected_fence or job.input_fingerprint != expected_fingerprint:
            return {"status": "stale", "fenced": True}
        try:
            source, allowed_refs = _collect_source(db, job)
            model_manifest = db.get(MethodManifest, UUID(str(job.scope_json["model_manifest_id"])))
            routing_manifest = db.get(MethodManifest, UUID(str(job.scope_json["routing_manifest_id"])))
            if (
                model_manifest is None
                or model_manifest.kind != "model"
                or model_manifest.sha256 != job.scope_json.get("model_manifest_sha256")
                or routing_manifest is None
                or routing_manifest.kind != "routing"
                or routing_manifest.sha256 != job.scope_json.get("routing_manifest_sha256")
            ):
                raise ValueError("draft_provider_manifest_mismatch")
            output = _attempt(job, source, allowed_refs, settings)
        except (KeyError, TypeError, ValueError):
            output = {"status": "manual_fallback", "provider_mode": job.scope_json.get("provider_mode", "off"),
                      "paid_calls": 0, "limitations": ["draft_source_snapshot_unavailable"]}
    with SessionFactory.begin() as db:
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if job is None:
            return {"status": "missing"}
        if job.lease_fence != expected_fence or job.input_fingerprint != expected_fingerprint:
            return {"status": "stale", "fenced": True}
        stage = db.scalar(
            select(JobStage).where(JobStage.job_id == job.id, JobStage.stage_name == job.stage)
            .with_for_update()
        )
        if stage is None:
            job.status = "failed"
            job.last_error_code = "DRAFT_STAGE_MISSING"
            job.lease_until = None
            return {"status": "failed", "reason": "DRAFT_STAGE_MISSING"}
        stage.status = "succeeded"
        stage.output_refs_json = output
        stage.finished_at = datetime.now(UTC)
        job.status = "ready_for_review"
        job.completion_kind = "manual_fallback" if output.get("status") in {
            "paid_call_guard_blocked_before_network", "provider_disabled", "provider_error", "manual_fallback",
            "processing_limit", "provider_response_invalid",
        } else "completed_with_limits"
        job.last_error_code = output.get("status") if job.completion_kind == "manual_fallback" else None
        job.lease_until = None
    return {"status": "ready_for_review", "generation_status": output.get("status")}
