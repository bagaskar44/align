"""B5 deterministic analysis pipeline over one immutable submission snapshot."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import unicodedata
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid5

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from align_api.config import get_settings
from align_api.models import (
    AnalysisResultEvidence,
    AnalysisRun,
    AnalysisRunStage,
    Assignment,
    AssignmentVersion,
    CheckElement,
    ChunkEvidenceLink,
    Course,
    CoverageInspection,
    CoverageRecord,
    Criterion,
    ElementCheck,
    ElementRequirementLink,
    EvidenceItem,
    FileAsset,
    FindingCandidate,
    FindingCoverageLink,
    FindingCriterionLink,
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
    Requirement,
    RetrievalChunk,
    RoutingCallLink,
    RoutingDecision,
    SemanticBatchMember,
    SourceUnit,
    SpecificationVersion,
    Submission,
    SubmissionVersion,
    SubmissionVersionFile,
)
from align_api.models import (
    SupportBinding as EvidenceSupportBinding,
)
from align_domain.assignment_contracts import SpecificationPayload, canonical_hash
from align_domain.executor import (
    ObservationBundle,
    SemanticClaimAssessment,
    SemanticVerdict,
    analysis_fingerprint,
    bm25_rank,
    candidate_category,
    chunk_text,
    dedup_key,
    reciprocal_rank_fusion,
    resolve_applicability,
    semantic_support_is_acceptable,
    token_budget_guard,
    topological_order,
    validate_batch_response,
    validate_observation_bundle,
)
from align_domain.providers import (
    OBSERVATION_BUNDLE_JSON_SCHEMA,
    SEMANTIC_CLAIM_JSON_SCHEMA,
    MockProvider,
    OpenAIResponsesProvider,
    PaidCallBlocked,
    ProviderContext,
)
from align_worker.database import SessionFactory

ANALYSIS_NAMESPACE = UUID("93db6ec9-fc68-4d66-b82d-e706d195a227")
TOKENIZER_MANIFEST = "unicode-wordpunct-nfkc-v1"
MOCK_EMBEDDING_MANIFEST = "synthetic-hash-embedding-1536-v1"
STAGES = (
    "validate_manifest",
    "parse",
    "build_evidence",
    "index",
    "execute_checks",
    "verify",
    "ready_for_review",
)


def _flatten(value: Any, prefix: str = "") -> str:
    """Preserve table/header/unit labels while producing quoteable source text."""
    if isinstance(value, str):
        return f"{prefix}: {value}" if prefix else value
    if isinstance(value, (int, float, bool)):
        return f"{prefix}: {value}" if prefix else str(value)
    if isinstance(value, list):
        return "\n".join(_flatten(item, prefix) for item in value if item is not None)
    if isinstance(value, dict):
        rows: list[str] = []
        for key in sorted(value):
            if str(key).lower() in {"image_bytes", "base64", "raw_data"}:
                continue
            rows.append(_flatten(value[key], f"{prefix} {key}".strip()))
        return "\n".join(item for item in rows if item)
    return ""


def _literal_text(value: Any) -> str:
    """Extract exact literal text for B5 quote code-point spans, without adding labels."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        direct = value.get("text")
        if isinstance(direct, str):
            return direct
        return "\n".join(part for item in value.values() if (part := _literal_text(item)))
    if isinstance(value, list):
        return "\n".join(part for item in value if (part := _literal_text(item)))
    return ""


def _synthetic_embedding(value: str) -> list[float]:
    """Untrained, lexical hash vector used only for explicitly synthetic mock runs."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    tokens = re.findall(r"[^\W_]+|[^\s]", normalized, re.UNICODE)
    vector = [0.0] * 1536
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % len(vector)
        vector[index] += 1.0 if digest[4] & 1 else -1.0
    norm = math.sqrt(sum(item * item for item in vector))
    return [item / norm for item in vector] if norm else vector


def _vector_literal(vector: list[float]) -> str:
    if len(vector) != 1536 or any(not math.isfinite(item) for item in vector):
        raise ValueError("embedding must be finite and exactly 1536 dimensions")
    return "[" + ",".join(f"{item:.9g}" for item in vector) + "]"


def _vector_rank(
    db: Session,
    *,
    scope: dict[str, UUID],
    query_vector: list[float],
    embedding_manifest: str,
) -> list[str]:
    # Exact cosine over fully qualified scope predicates; deliberately no ANN index.
    rows = db.execute(
        text("""
        SELECT c.id::text AS chunk_id
        FROM evidence_embeddings e
        JOIN retrieval_chunks c ON c.id=e.chunk_id
          AND c.submission_version_id=e.submission_version_id
          AND c.course_id=e.course_id AND c.assignment_id=e.assignment_id
          AND c.submission_id=e.owner_submission_id
        JOIN evidence_items i ON i.id=c.evidence_item_id
          AND i.submission_version_id=e.submission_version_id
          AND i.source_sha256=c.source_sha256
        WHERE e.submission_version_id=:submission_version_id
          AND e.course_id=:course_id AND e.assignment_id=:assignment_id
          AND e.owner_submission_id=:submission_id
          AND e.embedding_manifest=:embedding_manifest
        ORDER BY e.embedding <=> CAST(:query_vector AS vector),
          c.source_order,c.evidence_item_id,c.chunk_no,c.id
        LIMIT 20
    """),
        {
            **{key: str(value) for key, value in scope.items()},
            "embedding_manifest": embedding_manifest,
            "query_vector": _vector_literal(query_vector),
        },
    ).all()
    return [str(row.chunk_id) for row in rows]


def _stage(
    db: Session,
    run_id: UUID,
    name: str,
    fingerprint: str,
    output: dict[str, Any],
    limitations: list[str] | None = None,
) -> None:
    row = db.scalar(
        select(AnalysisRunStage)
        .where(
            AnalysisRunStage.analysis_run_id == run_id,
            AnalysisRunStage.stage_name == name,
            AnalysisRunStage.input_hash == fingerprint,
        )
        .with_for_update()
    )
    now = datetime.now(UTC)
    if row is None:
        row = AnalysisRunStage(
            analysis_run_id=run_id,
            stage_name=name,
            input_hash=fingerprint,
            status="succeeded",
            output_refs_json=output,
            limitation_codes_json=limitations or [],
            started_at=now,
            finished_at=now,
        )
        db.add(row)
    else:
        row.status = "succeeded"
        row.output_refs_json = output
        row.limitation_codes_json = limitations or []
        row.finished_at = now


def _load_source(
    db: Session, source: SubmissionVersion
) -> tuple[Submission, Assignment, AssignmentVersion, Course]:
    submission = db.get(Submission, source.submission_id)
    assignment = db.get(Assignment, submission.assignment_id) if submission else None
    version = db.get(AssignmentVersion, source.assignment_version_id)
    course = db.get(Course, assignment.course_id) if assignment else None
    if not submission or not assignment or not version or not course:
        raise ValueError("analysis source scope is incomplete")
    return submission, assignment, version, course


def _verify_snapshot(
    db: Session, run: AnalysisRun
) -> tuple[
    SubmissionVersion,
    Submission,
    Assignment,
    AssignmentVersion,
    SpecificationVersion,
    ReleaseManifest,
]:
    source = db.get(SubmissionVersion, run.submission_version_id)
    spec = db.get(SpecificationVersion, run.specification_version_id)
    release = db.get(ReleaseManifest, run.release_manifest_id) if run.release_manifest_id else None
    if not source or not spec or not release:
        raise ValueError("analysis_run_snapshot_missing")
    submission, assignment, version, _ = _load_source(db, source)
    if submission.active_final_version_id != source.id:
        raise ValueError("submission_version_is_no_longer_current_final")
    if (
        version.specification_version_id != spec.id
        or spec.state != "approved"
        or spec.schema_version != 2
        or spec.assignment_id != assignment.id
        or spec.instruction_hash != version.instruction_hash
        or spec.context_version_id != version.context_version_id
    ):
        raise ValueError("approved_specification_snapshot_mismatch")
    spec_payload = SpecificationPayload.model_validate_json(
        json.dumps(spec.payload_json, ensure_ascii=False)
    )
    if release.state not in {"active", "verified", "retired"}:
        raise ValueError("release_manifest_not_usable")
    if release.manifest_payload.get("provider_mode") not in {"mock", "off", "live"}:
        raise ValueError("release_provider_mode_invalid")
    if not {"b5_executor", "scoped_hybrid_retrieval", "typed_grounding"}.issubset(
        set(release.manifest_payload.get("capabilities_available", []))
    ):
        raise ValueError("release_manifest_missing_b5_capabilities")
    source_files = db.execute(
        select(SubmissionVersionFile, FileAsset)
        .join(FileAsset, FileAsset.id == SubmissionVersionFile.file_asset_id)
        .where(SubmissionVersionFile.submission_version_id == source.id)
        .order_by(SubmissionVersionFile.display_order, SubmissionVersionFile.file_asset_id)
    ).all()
    manifest_files = source.manifest_json.get("files", [])
    actual = [
        {"file_asset_id": str(link.file_asset_id), "sha256": asset.sha256}
        for link, asset in source_files
    ]
    actual.sort(key=lambda item: item["file_asset_id"])
    expected = [
        {
            "file_asset_id": str(item.get("file_id", item.get("file_asset_id"))),
            "sha256": item.get("sha256"),
        }
        for item in manifest_files
        if isinstance(item, dict)
    ]
    if expected != actual:
        raise ValueError("final_source_manifest_integrity_failed")
    if source.manifest_hash != canonical_hash(manifest_files):
        raise ValueError("final_source_manifest_hash_mismatch")
    if not spec_payload.elements:
        raise ValueError("approved_specification_has_no_elements")
    return source, submission, assignment, version, spec, release


def _provider_mode(release: ReleaseManifest) -> str:
    return str(release.manifest_payload.get("provider_mode", "off"))


def _method(db: Session, method_id: UUID | None) -> MethodManifest | None:
    return db.get(MethodManifest, method_id) if method_id else None


def _fingerprint_snapshot(
    db: Session,
    source: SubmissionVersion,
    version: AssignmentVersion,
    spec: SpecificationVersion,
    release: ReleaseManifest,
) -> dict[str, Any]:
    methods = {
        "parser": _method(db, release.parser_manifest_id),
        "model": _method(db, release.model_manifest_id),
        "retrieval": _method(db, release.retrieval_manifest_id),
        "rules": _method(db, release.rules_manifest_id),
        "prompt": _method(db, release.prompt_manifest_id),
        "routing": _method(db, release.routing_policy_manifest_id),
        "batching": _method(db, release.batching_policy_manifest_id),
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
        parser_manifest = _method(db, artifact.parser_manifest_id)
        resource_manifest = _method(db, artifact.parser_resource_manifest_id)
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
        "parser": methods["parser"].sha256 if methods["parser"] else "missing",
        "model": methods["model"].sha256 if methods["model"] else "missing",
        "retrieval": methods["retrieval"].sha256 if methods["retrieval"] else "missing",
        "rules": methods["rules"].sha256 if methods["rules"] else "missing",
        "prompt": methods["prompt"].sha256 if methods["prompt"] else "missing",
        "routing": methods["routing"].sha256 if methods["routing"] else "missing",
        "batching": methods["batching"].sha256 if methods["batching"] else "missing",
        "grounding_registry": release.grounding_registry_version,
        "lint_registry": release.lint_registry_version,
        "numeric_policy_hashes": [item.content_hash for item in policies],
        "submission_parser_artifacts": parse_snapshot,
        "release_hash": release.config_hash,
    }


def _compatibility(
    unit: SourceUnit, evidence: EvidenceItem | None, accepted: set[str], has_children: set[UUID]
) -> tuple[bool, str | None]:
    if unit.unit_type in {"root", "container"} and unit.id in has_children:
        return False, "structural_parent_has_child_units"
    if evidence is not None:
        if evidence.type in accepted:
            return True, None
        return False, "evidence_type_not_accepted"
    if unit.unit_type in {"image", "diagram", "picture", "drawing"}:
        return (
            (True, None)
            if accepted & {"diagram", "image_text"}
            else (False, "visual_type_not_accepted")
        )
    if unit.unit_type in {"table", "sheet", "worksheet", "spreadsheet"}:
        return (
            (True, None)
            if accepted & {"table", "spreadsheet_cell", "spreadsheet_range"}
            else (False, "table_type_not_accepted")
        )
    if unit.unit_type in {"root", "container", "unsupported"}:
        return True, "unit_type_unresolved"
    return (
        (True, None)
        if accepted & {"paragraph", "table", "spreadsheet_cell", "spreadsheet_range"}
        else (False, "unit_type_not_accepted")
    )


def _dependency_coverage_inventory(
    element: Any,
    all_units: list[tuple[SourceUnit, ParseArtifact, EvidenceItem | None]],
) -> list[dict[str, Any]]:
    """Retain each source unit even when DAG dependency prevents inspection."""
    child_ids = {unit.parent_id for unit, _, _ in all_units if unit.parent_id}
    grouped: dict[UUID, dict[str, Any]] = {}
    accepted = set(element.accepted_evidence_types)
    for unit, artifact, evidence in all_units:
        compatible, reason = _compatibility(unit, evidence, accepted, child_ids)
        parser_incomplete = unit.parse_status in {
            "partially_parsed", "unreadable", "unsupported", "failed"
        } or artifact.status in {
            "partially_parsed", "unreadable", "unsupported", "failed", "blocked"
        }
        item = grouped.setdefault(unit.id, {
            "source_unit_id": str(unit.id),
            "evidence_item_ids": [],
            "compatible": False,
            "unresolved": False,
            "reason": reason,
            "parse_status": unit.parse_status,
            "artifact_status": artifact.status,
            "limitations": [*unit.limitations_json, *artifact.limitations_json],
        })
        item["compatible"] = bool(item["compatible"] or compatible)
        item["unresolved"] = bool(item["unresolved"] or compatible and (parser_incomplete or evidence is None))
        if evidence is not None:
            item["evidence_item_ids"].append(str(evidence.id))
            item["limitations"].extend(evidence.limitations_json)
    inventory = []
    for unit_id in sorted(grouped, key=lambda value: (next(
        unit.source_order for unit, _, _ in all_units if unit.id == value
    ), str(value))):
        item = grouped[unit_id]
        state = (
            "excluded" if not item["compatible"]
            else "unresolved" if item["unresolved"]
            else "compatible"
        )
        inventory.append({
            "source_unit_id": item["source_unit_id"],
            "evidence_ids": item["evidence_item_ids"],
            "state": state,
            "reason": "dependency_unresolved" if item["compatible"] else item["reason"],
            "parser_state": item["parse_status"],
            "artifact_state": item["artifact_status"],
            "limitations": item["limitations"],
        })
    return inventory


def _persist_element(
    db: Session,
    *,
    run: AnalysisRun,
    element_row: CheckElement,
    element: Any,
    source: SubmissionVersion,
    submission: Submission,
    assignment: Assignment,
    version: AssignmentVersion,
    course: Course,
    evidence_rows: list[tuple[EvidenceItem, SourceUnit, ParseArtifact]],
    all_units: list[tuple[SourceUnit, ParseArtifact, EvidenceItem | None]],
    prior_checks: dict[UUID, ElementCheck],
    provider: Any | None,
    mode: str,
    limits: dict[str, Any],
    counters: dict[str, Any],
    routing_manifest_id: UUID,
    batching_manifest_id: UUID,
    embedding_manifest: str,
    embedding_index_ready: bool,
    model_manifest_id: UUID,
    embedding_model: str,
) -> None:
    existing_check = prior_checks.get(element_row.id)
    if existing_check is not None:
        return
    file_types: set[str] = set()
    for file_link in db.scalars(
        select(SubmissionVersionFile).where(
            SubmissionVersionFile.submission_version_id == source.id
        )
    ).all():
        asset = db.get(FileAsset, file_link.file_asset_id)
        if asset is not None:
            file_types.add(Path(asset.original_name).suffix.lower().lstrip("."))
    facts: dict[str, Any] = {
        "assignment.mode": version.mode,
        "submission.file_types": sorted(file_types),
    }
    applicability, applicability_reason = resolve_applicability(element, facts)
    accepted = set(element.accepted_evidence_types)
    child_ids = {unit.parent_id for unit, _, _ in all_units if unit.parent_id}
    inventory: list[dict[str, Any]] = []
    compatible_rows: list[
        tuple[EvidenceItem | None, SourceUnit, ParseArtifact, bool, str | None]
    ] = []
    grouped_units: dict[UUID, dict[str, Any]] = {}
    for unit, artifact, item in all_units:
        compatible, exclusion_reason = _compatibility(unit, item, accepted, child_ids)
        is_unresolved = unit.parse_status in {
            "partially_parsed",
            "unreadable",
            "unsupported",
            "failed",
        } or artifact.status in {
            "partially_parsed",
            "unreadable",
            "unsupported",
            "failed",
            "blocked",
        }
        compatible_rows.append((item, unit, artifact, compatible, exclusion_reason))
        entry = grouped_units.setdefault(
            unit.id,
            {
                "unit": unit,
                "artifact": artifact,
                "compatible": False,
                "unresolved": is_unresolved or item is None,
                "reason": exclusion_reason,
                "evidence_ids": [],
                "evidence_types": [],
                "limitations": [*unit.limitations_json, *artifact.limitations_json],
            },
        )
        entry["compatible"] = bool(entry["compatible"] or compatible)
        if item is not None:
            entry["unresolved"] = bool(entry["unresolved"] or not compatible and exclusion_reason == "unit_type_unresolved")
            entry["evidence_ids"].append(item.id)
            entry["evidence_types"].append(item.type)
            entry["limitations"].extend(item.limitations_json)
    for unit_id in sorted(grouped_units, key=lambda item: (grouped_units[item]["unit"].source_order, str(item))):
        entry = grouped_units[unit_id]
        unit = entry["unit"]
        artifact = entry["artifact"]
        compatible = bool(entry["compatible"])
        is_unresolved = bool(entry["unresolved"])
        inventory.append(
            {
                "source_unit_id": str(unit.id),
                "evidence_item_ids": [str(item) for item in entry["evidence_ids"]],
                "locator": unit.anchor_json,
                "unit_type": unit.unit_type,
                "evidence_types": entry["evidence_types"],
                "parse_status": unit.parse_status,
                "artifact_status": artifact.status,
                "state": "unresolved" if compatible and is_unresolved else "compatible" if compatible else "excluded",
                "reason": entry["reason"] if not compatible else ("parse_or_artifact_incomplete" if is_unresolved else None),
                "limitations": entry["limitations"],
            }
        )
    compatible_items = {
        item.id: item for item, _, _, compatible, _ in compatible_rows if item and compatible
    }
    required = db.scalars(
        select(Requirement)
        .join(ElementRequirementLink, ElementRequirementLink.requirement_id == Requirement.id)
        .where(ElementRequirementLink.check_element_id == element_row.id)
        .order_by(Requirement.code, Requirement.id)
    ).all()
    criterion = db.get(Criterion, element_row.criterion_id)
    query_parts = [
        element.decision_question,
        element.expected_evidence,
        *(element.source_hints or []),
        *(item.text for item in required),
        criterion.descriptor if criterion else "",
    ]
    primary_query = " ".join(part for part in query_parts if part)
    query_variants = [
        primary_query,
        element.expected_evidence,
        criterion.descriptor if criterion else primary_query,
    ]
    chunks = db.scalars(
        select(RetrievalChunk)
        .where(
            RetrievalChunk.submission_version_id == source.id,
            RetrievalChunk.submission_id == submission.id,
            RetrievalChunk.course_id == course.id,
            RetrievalChunk.assignment_id == assignment.id,
        )
        .order_by(
            RetrievalChunk.source_order, RetrievalChunk.evidence_item_id, RetrievalChunk.chunk_no
        )
    ).all()
    chunk_docs = [
        {
            "id": str(chunk.id),
            "text": chunk.chunk_text,
            "order": (chunk.source_order, str(chunk.evidence_item_id), chunk.chunk_no),
        }
        for chunk in chunks
        if chunk.evidence_item_id in compatible_items
    ]
    by_chunk = {str(chunk.id): chunk for chunk in chunks}
    rankings: list[list[str]] = []
    for query in query_variants[:3]:
        lexical = [key for key, _ in bm25_rank(query, chunk_docs)[:20]]
        if lexical:
            rankings.append(lexical)
    vector_rank: list[str] = []
    embedding_failure: str | None = None
    if mode == "mock" and run.data_origin == "synthetic" and chunks:
        vector_rank = _vector_rank(
            db,
            scope={
                "submission_version_id": source.id,
                "course_id": course.id,
                "assignment_id": assignment.id,
                "submission_id": submission.id,
            },
            query_vector=_synthetic_embedding(primary_query),
            embedding_manifest=MOCK_EMBEDDING_MANIFEST,
        )
        vector_rank = [
            chunk_id
            for chunk_id in vector_rank
            if chunk_id in by_chunk and by_chunk[chunk_id].evidence_item_id in compatible_items
        ]
        if vector_rank:
            rankings.append(vector_rank)
    elif mode == "live" and embedding_index_ready and chunks:
        query_route = RoutingDecision(
            analysis_run_id=run.id,
            check_element_id=element_row.id,
            task="embed",
            action="call",
            selected_role="embedding",
            reason_code="query_embedding_required",
            feature_snapshot_json={"text_count": 1, "embedding_index_ready": True},
            guard_snapshot_json={"source_scope_verified": True, "paid_call_guard": "blocked_before_network"},
            remaining_limits_json={"embedding_tokens": max(0, limits["max_embedding_tokens"] - counters["embedding_tokens"])},
            policy_manifest_id=routing_manifest_id,
            outcome="queued",
            job_id=run.job_id,
            batching_manifest_id=batching_manifest_id,
        )
        db.add(query_route)
        db.flush()
        vectors, embed_status = _invoke_embedding(
            db,
            provider=provider,
            texts=[primary_query],
            route=query_route,
            run=run,
            source=source,
            model_manifest_id=model_manifest_id,
            policy_manifest_id=routing_manifest_id,
            limits=limits,
            counters=counters,
            model=embedding_model,
        )
        if vectors:
            vector_rank = _vector_rank(
                db,
                scope={
                    "submission_version_id": source.id,
                    "course_id": course.id,
                    "assignment_id": assignment.id,
                    "submission_id": submission.id,
                },
                query_vector=vectors[0],
                embedding_manifest=embedding_manifest,
            )
            vector_rank = [
                chunk_id
                for chunk_id in vector_rank
                if chunk_id in by_chunk and by_chunk[chunk_id].evidence_item_id in compatible_items
            ]
            if vector_rank:
                rankings.append(vector_rank)
        else:
            embedding_failure = embed_status
    order: dict[str, tuple[Any, ...]] = {str(doc["id"]): tuple(doc["order"]) for doc in chunk_docs}
    fused = reciprocal_rank_fusion(*rankings, order=order)[:20] if rankings else []
    empty_top_k = not fused
    # Empty top-k triggers a compatible-source expansion. It does not create an absence claim.
    if empty_top_k:
        fused = [str(chunk.id) for chunk in chunks if chunk.evidence_item_id in compatible_items]
    sent_chunk_ids = fused[:6]
    sent_evidence_ids = sorted(
        {by_chunk[item_id].evidence_item_id for item_id in sent_chunk_ids if item_id in by_chunk},
        key=str,
    )
    unresolved_count = sum(
        1 for entry in grouped_units.values() if entry["compatible"] and entry["unresolved"]
    )
    compatible_count = sum(1 for entry in grouped_units.values() if entry["compatible"])
    inspected_count = sum(
        1
        for entry in grouped_units.values()
        if entry["compatible"]
        and not entry["unresolved"]
        and entry["unit"].parse_status == "parsed"
        and entry["artifact"].status == "parsed"
    )
    full_source_texts = {
        item_id: _literal_text(item.content_original_json)
        for item_id, item in compatible_items.items()
    }
    negative_scan_payload: dict[str, Any] = {
        "task_type": "semantic_scan",
        "source_version_id": str(source.id),
        "obligation": element.decision_question,
        "official_expected_evidence": element.expected_evidence,
        "sources": [
            {"evidence_id": str(item_id), "anchor": compatible_items[item_id].anchor_json,
             "text": full_source_texts[item_id]}
            for item_id in sorted(compatible_items, key=str)
        ],
    }
    negative_scan_possible = (
        unresolved_count == 0
        and inspected_count == compatible_count
        and all(full_source_texts.values())
        and counters["input_tokens"] + _estimated_payload_tokens(negative_scan_payload)
        <= limits["max_text_input_tokens"]
    )
    exhaustive = False  # Set true only after a successful, bounded semantic scan.
    retrieval_status = (
        "hybrid"
        if rankings and vector_rank
        else "bm25_fallback"
        if rankings
        else "empty_top_k_expanded"
        if empty_top_k
        else "unavailable"
    )
    coverage_limitations: list[dict[str, Any]] = []
    if unresolved_count:
        coverage_limitations.append(
            {"code": "incomplete_source_inspection", "count": unresolved_count}
        )
    if empty_top_k:
        coverage_limitations.append(
            {"code": "empty_top_k_expanded", "compatible_units_scanned": compatible_count}
        )
    if mode == "mock":
        coverage_limitations.append(
            {"code": "synthetic_hash_embedding", "semantic_quality": "not_empirically_verified"}
        )
    if embedding_failure:
        coverage_limitations.append({"code": embedding_failure})
    coverage = CoverageRecord(
        analysis_run_id=run.id,
        check_element_id=element_row.id,
        retrieval_status=retrieval_status,
        compatible_count=compatible_count,
        inspected_count=inspected_count,
        unresolved_count=unresolved_count,
        element_check_id=None,
        sufficient_for_positive=bool(sent_evidence_ids),
        exhaustive_for_negative=exhaustive,
        inventory_json={
            "units": inventory,
        "denominator": len(grouped_units),
            "compatible": compatible_count,
            "inspected": inspected_count,
            "unresolved": unresolved_count,
        },
        retrieval_json={
            "queries": query_variants[:3],
            "query_count": min(3, len(query_variants)),
            "reformulations": 2,
            "bm25_top20": rankings[0] if rankings else [],
            "vector_top20": vector_rank[:20],
            "rrf_k": 60,
            "fused_top20": fused[:20],
            "sent_chunk_ids": sent_chunk_ids,
            "sent_evidence_ids": [str(i) for i in sent_evidence_ids],
            "source_scan_possible": negative_scan_possible,
            "inspected_evidence_ids": [],
            "scope": {
                "course_id": str(course.id),
                "assignment_id": str(assignment.id),
                "submission_id": str(submission.id),
                "submission_version_id": str(source.id),
            },
            "embedding_manifest": embedding_manifest if vector_rank else None,
            "tokenizer_manifest": TOKENIZER_MANIFEST,
        },
        limitations_json=coverage_limitations,
    )
    db.add(coverage)
    db.flush()
    if applicability == "not_applicable":
        check_status = None
        coverage_status = "sufficient_for_check"
        relationship_status = "not_found"
        reasons = [applicability_reason or "not_applicable"]
        interpretation = "Applicability rule evaluated false from a registered verified fact."
    elif applicability == "unresolved":
        check_status = "unassessable"
        coverage_status = "unresolved"
        relationship_status = "unclear"
        reasons = [applicability_reason or "applicability_unresolved"]
        interpretation = "Applicability is unresolved; no academic decision was inferred."
    elif element.grounding_contract.mode == "human_only":
        check_status = "unassessable"
        coverage_status = "unreadable" if unresolved_count else "sufficient_for_check"
        relationship_status = "unclear"
        reasons = ["human_judgment_required"]
        interpretation = "This element is reserved for lecturer judgment."
    else:
        check_status = "unassessable"
        coverage_status = "unreadable" if unresolved_count else "partial"
        relationship_status = "unclear"
        reasons = ["semantic_verification_unavailable"]
        if unresolved_count:
            reasons.append("incomplete_source_inspection")
        interpretation = "No provider-verified semantic conclusion is available."
    check = ElementCheck(
        analysis_run_id=run.id,
        check_element_id=element_row.id,
        applicability_status=applicability,
        source_coverage_status=coverage_status,
        evidence_relationship=relationship_status,
        check_status=check_status,
        observations_json=[],
        interpretation=interpretation,
        reason_codes_json=sorted(set(reasons)),
    )
    db.add(check)
    db.flush()
    coverage.element_check_id = check.id
    for entry in grouped_units.values():
        unit = entry["unit"]
        artifact = entry["artifact"]
        compatible = bool(entry["compatible"])
        inspection_status = "unresolved" if compatible else "excluded"
        state = inspection_status
        db.add(
            CoverageInspection(
                coverage_record_id=coverage.id,
                source_unit_id=unit.id,
                evidence_item_id=entry["evidence_ids"][0] if entry["evidence_ids"] else None,
                state=state,
                method="structural",
                inspection_status=inspection_status,
                reason_code=("parse_or_artifact_incomplete" if entry["unresolved"] and compatible else entry["reason"]),
                detail_json={
                    "unit_type": unit.unit_type,
                    "parse_status": unit.parse_status,
                    "artifact_status": artifact.status,
                    "evidence_types": entry["evidence_types"],
                    "evidence_ids": [str(item) for item in entry["evidence_ids"]],
                },
                observation_json={"evidence_ids": [str(item) for item in entry["evidence_ids"]]},
            )
        )
    for evidence_id in sent_evidence_ids:
        db.add(AnalysisResultEvidence(element_check_id=check.id, evidence_item_id=evidence_id))
    initial_mechanical = "not_applicable" if applicability == "not_applicable" else "invalid"
    sem_status = "human_required" if element.grounding_contract.mode == "human_only" and applicability == "applicable" else "not_run"
    grounding = GroundingEvaluation(
        analysis_run_id=run.id,
        element_check_id=check.id,
        coverage_record_id=coverage.id,
        mechanical_status=initial_mechanical,
        semantic_status=sem_status,
        evaluation_json={"state": sem_status, "quote_match_is_not_support": True},
        limitation_codes_json=list(reasons),
        contract_schema_version=1,
        mode=element.grounding_contract.mode,
        status="human_required" if sem_status == "human_required" else "not_run",
        decision_basis=None,
        mechanical_validation_json={
            "membership": None,
            "anchors": None,
            "quote_matches": None,
            "required_roles": None,
            "coverage_gate": None,
            "numeric_gate": None,
        },
        semantic_support="not_run",
        validator_manifest_ids_json=[str(routing_manifest_id)],
    )
    db.add(grounding)
    role = (
        "complex"
        if element.grounding_contract.mode in {"anchored_content", "linked_relationship"}
        else None
    )
    reason_code = (
        "human_only_boundary"
        if sem_status == "human_required"
        else "applicability_unresolved"
        if applicability == "unresolved"
        else "complex_semantic_baseline"
        if role
        else "deterministic_or_manual_boundary"
    )
    route = RoutingDecision(
        analysis_run_id=run.id,
        check_element_id=element_row.id,
        task="interpret_evidence",
        action="call" if mode in {"mock", "live"} and role and applicability == "applicable" else "manual_only",
        selected_role=role,
        reason_code=reason_code,
        feature_snapshot_json={
            "grounding_mode": element.grounding_contract.mode,
            "source_coverage": coverage_status,
            "unresolved_units": unresolved_count,
            "human_judgment_boundary": element.human_judgment_boundary,
        },
        guard_snapshot_json={
            "authorized_submission_version": str(source.id),
            "source_scope_verified": True,
            "paid_call_guard": "blocked_before_network",
        },
        remaining_limits_json={
            "calls": max(0, limits["max_calls"] - counters["calls"]),
            "vision_calls": max(0, limits["max_vision_calls"] - counters["vision_calls"]),
            "input_tokens": max(0, limits["max_text_input_tokens"] - counters["input_tokens"]),
            "output_tokens": max(0, limits["max_text_output_tokens"] - counters["output_tokens"]),
        },
        policy_manifest_id=routing_manifest_id,
        outcome="queued" if mode in {"mock", "live"} and role and applicability == "applicable" else "abstained",
        job_id=run.job_id,
        batching_manifest_id=batching_manifest_id,
        timing_refs_json={"budget_started_monotonic": counters["started"]},
    )
    db.add(route)
    db.flush()
    model_manifest_value = run.manifest_snapshot_json.get("model_manifest_id")
    try:
        model_id = UUID(str(model_manifest_value))
    except (TypeError, ValueError):
        model_id = ANALYSIS_NAMESPACE
    bundle: ObservationBundle | None = None
    mechanical = None
    semantic_verdict: SemanticVerdict | None = None
    semantic_claim: SemanticClaimAssessment | None = None
    result: Any | None = None
    simulated = False
    sem_support_ok = False
    proposed = "unclear"
    full_scan_completed = False
    provider_status = "not_run"

    if route.action == "call":
        safe_payload: dict[str, Any] = {
            "task_type": "interpret_evidence",
            "element": {
                "decision_question": element.decision_question,
                "expected_evidence": element.expected_evidence,
                "grounding_mode": element.grounding_contract.mode,
                "relationships": [item.model_dump(mode="json") for item in element.relationships],
            },
            "official_normative": {
                "criterion_code": criterion.code if criterion else None,
                "criterion_descriptor": criterion.descriptor if criterion else None,
                "requirements": [
                    {"requirement_id": str(item.id), "text": item.text,
                     "mandatory": item.mandatory, "obligation_key": item.obligation_key}
                    for item in required
                ],
            },
            "source_chunks": [
                {"evidence_id": str(by_chunk[item_id].evidence_item_id),
                 "anchor": by_chunk[item_id].anchor_json,
                 "text": by_chunk[item_id].chunk_text}
                for item_id in sent_chunk_ids if item_id in by_chunk
            ],
            "json_schema": OBSERVATION_BUNDLE_JSON_SCHEMA,
        }
        result, provider_status, simulated, _ = _invoke_provider(
            db, provider=provider, method="interpret_evidence", schema_name="ObservationBundle-v1",
            payload=safe_payload, route=route, run=run, source=source, element_row=element_row,
            mode=mode, limits=limits, counters=counters, model_manifest_id=model_id,
        )
        if result is not None:
            try:
                batch_result = validate_batch_response(
                    [element_row.id],
                    [{"check_element_id": str(element_row.id), "payload": result.payload}],
                )
                entry_result = batch_result[element_row.id]
                if entry_result["status"] != "valid":
                    raise ValueError("batch_element_invalid")
                bundle = ObservationBundle.model_validate_json(
                    json.dumps(entry_result["payload"], ensure_ascii=False)
                )
                proposed = bundle.proposed_evidence_relationship
                allowed_evidence_ids = set(sent_evidence_ids)
                checked_text = {
                    evidence_id: full_source_texts[evidence_id]
                    for evidence_id in allowed_evidence_ids if evidence_id in full_source_texts
                }
                mechanical = validate_observation_bundle(
                    bundle, element=element, evidence_text=checked_text
                )
                check.observations_json = [
                    {**item.model_dump(mode="json"),
                     "evidence_ids": [str(value) for value in item.evidence_ids if value in allowed_evidence_ids]}
                    for item in bundle.observations
                ]
                check.evidence_relationship = proposed
                if bundle.observations:
                    check.interpretation = "Observasi sumber dari model; makna belum diverifikasi."
                else:
                    check.interpretation = "Model abstain; tidak ada observasi atau keputusan akademik yang dibuat."
                if not simulated:
                    for binding in bundle.support_bindings:
                        for evidence_id in binding.evidence_ids:
                            if evidence_id not in allowed_evidence_ids:
                                continue
                            db.add(
                                EvidenceSupportBinding(
                                    element_check_id=check.id,
                                    relationship_code=binding.relationship_code,
                                    role=binding.role,
                                    observation_codes_json=binding.observation_codes,
                                    evidence_item_id=evidence_id,
                                    quote_spans_json=[
                                        quote.model_dump(mode="json") for quote in binding.quotes
                                        if quote.evidence_id == evidence_id
                                    ],
                                    validator_refs_json=["grounding-registry-v1", "quote-span-v1"],
                                )
                            )
            except Exception:
                bundle = None
                mechanical = None
                provider_status = "model_response_invalid"
                proposed = "unclear"
        if result is not None and simulated:
            provider_status = "mock_abstention" if bundle and not bundle.observations else "mock_observation_only"
        elif result is None and provider_status == "paid_call_guard_blocked_before_network":
            reasons.append("paid_call_guard_incomplete")
        elif result is None and provider_status not in {"not_run", "limit_prevents_call"}:
            reasons.append("model_response_invalid" if "invalid" in provider_status else provider_status)

    if result is not None and bundle is not None and mechanical is not None and not simulated:
        if applicability == "applicable" and mechanical.valid:
            mandatory_requirements = [item for item in required if item.mandatory]
            if proposed == "supported":
                claim_to_verify = f"Official check is satisfied: {element.decision_question}"
                verify_ids = mechanical.evidence_item_ids
                verify_sources = [
                    {"evidence_id": str(item_id), "anchor": compatible_items[item_id].anchor_json,
                     "text": full_source_texts[item_id]}
                    for item_id in verify_ids if item_id in compatible_items
                ]
                absence_claim = False
            elif proposed in {"not_found", "partial"} and negative_scan_possible:
                if proposed == "partial" and element.target_kind != "descriptor":
                    reasons.append("partial_requirement_not_explicit_absence")
                    claim_to_verify = ""
                elif proposed == "not_found" and element.target_kind == "requirement" and mandatory_requirements:
                    claim_to_verify = f"Mandatory obligation {element.obligation_key} is absent from the complete compatible source set."
                elif element.target_kind == "descriptor" and criterion is not None:
                    claim_to_verify = f"Descriptor obligation {element.obligation_key} has a verified gap in the complete compatible source set."
                else:
                    claim_to_verify = ""
                verify_ids = []
                verify_sources = negative_scan_payload["sources"]
                absence_claim = proposed == "not_found"
            else:
                claim_to_verify = ""
                verify_ids = []
                verify_sources = []
                absence_claim = False
                if proposed in {"not_found", "partial"}:
                    reasons.append("incomplete_source_inspection")
            if claim_to_verify:
                verify_payload: dict[str, Any] = {
                    "task_type": "verify_claim",
                    "claim_to_verify": claim_to_verify,
                    "decision_question": element.decision_question,
                    "grounding_mode": element.grounding_contract.mode,
                    "complete_source_scan": bool(proposed in {"not_found", "partial"}),
                    "sources": verify_sources,
                    "json_schema": SEMANTIC_CLAIM_JSON_SCHEMA,
                }
                verify_route = RoutingDecision(
                    analysis_run_id=run.id, check_element_id=element_row.id,
                    task="verify_claim", action="call", selected_role="complex",
                    reason_code="complex_relationship", feature_snapshot_json={
                        "proposed_relationship": proposed,
                        "coverage_exhaustive": negative_scan_possible,
                        "evidence_count": len(verify_sources),
                    },
                    guard_snapshot_json={"source_scope_verified": True, "paid_call_guard": "blocked_before_network"},
                    remaining_limits_json={key: max(0, value - counters.get(key, 0))
                                           for key, value in limits.items() if key in counters},
                    policy_manifest_id=routing_manifest_id, outcome="queued", job_id=run.job_id,
                    batching_manifest_id=batching_manifest_id,
                )
                db.add(verify_route)
                db.flush()
                if absence_claim and _estimated_payload_tokens(verify_payload) > limits["max_input_tokens_per_call"]:
                    verify_route.action = "retrieve_expand"
                    verify_route.reason_code = "bounded_exhaustive_scan_expanded"
                    verify_route.outcome = "expanded_to_scan_batches"
                    semantic_claim, full_scan_completed, provider_status = _verify_absence_in_batches(
                        db,
                        provider=provider,
                        claim=claim_to_verify,
                        decision_question=element.decision_question,
                        grounding_mode=element.grounding_contract.mode,
                        sources=verify_sources,
                        run=run,
                        source=source,
                        element_row=element_row,
                        mode=mode,
                        limits=limits,
                        counters=counters,
                        model_manifest_id=model_id,
                        routing_manifest_id=routing_manifest_id,
                        batching_manifest_id=batching_manifest_id,
                    )
                    if semantic_claim is not None:
                        semantic_verdict = SemanticVerdict(
                            status=semantic_claim.status,
                            claim=claim_to_verify,
                            evidence_item_ids=semantic_claim.evidence_ids,
                            explanation=semantic_claim.explanation,
                            verifier="provider",
                        )
                        sem_support_ok = semantic_support_is_acceptable(
                            semantic_verdict,
                            mechanical,
                            absence_claim=True,
                            coverage_exhaustive=full_scan_completed,
                        )
                else:
                    verify_result, verify_status, verify_simulated, _ = _invoke_provider(
                        db, provider=provider, method="verify_claim", schema_name="SemanticClaimAssessment-v1",
                        payload=verify_payload, route=verify_route, run=run, source=source,
                        element_row=element_row, mode=mode, limits=limits, counters=counters,
                        model_manifest_id=model_id,
                    )
                    if verify_result is not None and not verify_simulated:
                        try:
                            semantic_claim = SemanticClaimAssessment.model_validate_json(
                                json.dumps(verify_result.payload, ensure_ascii=False)
                            )
                            if semantic_claim.claim != claim_to_verify:
                                raise ValueError("semantic_verifier_changed_server_claim")
                            if not absence_claim and (
                                not semantic_claim.evidence_ids
                                or not set(semantic_claim.evidence_ids) <= set(mechanical.evidence_item_ids)
                            ):
                                raise ValueError("semantic_verifier_evidence_not_grounded")
                            if absence_claim and semantic_claim.status == "supported" and semantic_claim.evidence_ids:
                                raise ValueError("absence_verifier_must_use_coverage_not_quotes")
                            if absence_claim and not {str(item) for item in semantic_claim.evidence_ids} <= set(compatible_items):
                                raise ValueError("absence_verifier_evidence_not_in_complete_source_set")
                            full_scan_completed = bool(
                                proposed in {"not_found", "partial"}
                                and semantic_claim.status != "insufficient"
                            )
                            semantic_verdict = SemanticVerdict(
                                status=semantic_claim.status, claim=claim_to_verify,
                                evidence_item_ids=semantic_claim.evidence_ids,
                                explanation=semantic_claim.explanation, verifier="provider",
                            )
                            sem_support_ok = semantic_support_is_acceptable(
                                semantic_verdict, mechanical, absence_claim=absence_claim,
                                coverage_exhaustive=full_scan_completed,
                            )
                            provider_status = "semantic_verification_completed"
                        except Exception:
                            provider_status = "semantic_response_invalid"
                            reasons.append("model_response_invalid")
                    else:
                        verify_route.outcome = verify_status
                        provider_status = verify_status
                if provider_status == "paid_call_guard_blocked_before_network":
                    reasons.append("paid_call_guard_incomplete")
                elif proposed in {"not_found", "partial"} and not full_scan_completed:
                    reasons.append("incomplete_source_inspection")
            elif proposed in {"not_found", "partial"} and not negative_scan_possible:
                reasons.append("incomplete_source_inspection")

    if bundle is not None and mechanical is not None:
        mechanical_reasons = set(mechanical.reason_codes)
        quote_present = any(binding.quotes for binding in bundle.support_bindings)
        required_roles_applicable = proposed == "supported" and element.grounding_contract.mode == "linked_relationship"
        validation_json = {
            "membership": "valid" if not ({"foreign_or_missing_evidence_id", "foreign_observation_code"} & mechanical_reasons) else "invalid",
            "anchors": "valid" if "foreign_or_missing_evidence_id" not in mechanical_reasons else "invalid",
            "quote_matches": ("valid" if not ({"quote_span_out_of_bounds", "quote_span_text_mismatch", "quote_evidence_not_bound"} & mechanical_reasons) else "invalid") if quote_present else None,
            "required_roles": ("valid" if "required_relationship_role_missing" not in mechanical_reasons else "invalid") if required_roles_applicable else None,
            "coverage_gate": ("valid" if full_scan_completed else "invalid") if proposed in {"not_found", "partial"} else None,
            "numeric_gate": None,
            "reason_codes": mechanical.reason_codes,
        }
        grounding.mechanical_status = "valid" if mechanical.valid else "invalid"
        grounding.mechanical_validation_json = validation_json
        grounding.semantic_status = semantic_verdict.status if semantic_verdict else "not_run"
        grounding.semantic_support = "supported" if sem_support_ok else "insufficient" if semantic_verdict else "not_run"
        grounding.status = "supported" if sem_support_ok else "insufficient"
        grounding.decision_basis = "positive_support" if proposed == "supported" else "coverage_absence" if proposed in {"partial", "not_found"} and full_scan_completed else None
        grounding.evaluation_json = {
            "contract_schema_version": 1,
            "mode": element.grounding_contract.mode,
            "status": grounding.status,
            "decision_basis": grounding.decision_basis,
            "mechanical_validation": validation_json,
            "semantic_support": grounding.semantic_support,
            "semantic_verdict": semantic_verdict.model_dump(mode="json") if semantic_verdict else None,
        }
        grounding.limitation_codes_json = sorted(set(reasons + mechanical.reason_codes))
        if semantic_claim and semantic_verdict:
            check.interpretation = semantic_claim.explanation
        if sem_support_ok and semantic_verdict and semantic_verdict.status == "supported":
            if proposed == "supported" and coverage.sufficient_for_positive:
                check.check_status = "no_issue_detected"
                check.evidence_relationship = "supported"
                check.source_coverage_status = "sufficient_for_check"
                check.reason_codes_json = []
                coverage.sufficient_for_positive = True
            else:
                mandatory_requirements = [item for item in required if item.mandatory]
                category = candidate_category(
                    applicability=applicability,
                    grounding_mode=element.grounding_contract.mode,
                    semantic_verdict=semantic_verdict,
                    mechanical=mechanical,
                    coverage_exhaustive=full_scan_completed,
                    coverage_sufficient_for_positive=bool(sent_evidence_ids),
                    mandatory_requirement=bool(mandatory_requirements),
                    absence_claim=proposed == "not_found",
                    descriptor_gap_claim=element.target_kind == "descriptor" and proposed in {"not_found", "partial"},
                )
                if category and criterion is not None:
                    if category == "missing_requirement":
                        claim = f"Persyaratan wajib belum ditemukan pada seluruh sumber yang dapat diperiksa: {mandatory_requirements[0].text}"
                    else:
                        claim = f"Cakupan bukti belum memenuhi deskripsi rubrik setelah pemeriksaan sumber lengkap: {criterion.descriptor}"
                    candidate = _persist_candidate(
                        db, run=run, check=check, element=element, criterion=criterion,
                        requirements=required, coverage=coverage, category=category,
                        claim=claim, evidence_ids=list(semantic_verdict.evidence_item_ids),
                    )
                    check.check_status = "finding_candidate"
                    check.evidence_relationship = proposed
                    check.source_coverage_status = "sufficient_for_check"
                    check.reason_codes_json = []
                    grounding.status = "supported"
                    grounding.decision_basis = "coverage_absence"
                    grounding.evaluation_json["candidate_id"] = str(candidate.id)
                else:
                    check.check_status = "unassessable"
                    check.reason_codes_json = ["semantic_support_insufficient"]
        else:
            check.check_status = "unassessable"
            check.reason_codes_json = sorted(set(reasons + ["semantic_support_insufficient"]))
    elif applicability == "applicable" and element.grounding_contract.mode != "human_only":
        check.check_status = "unassessable"
        check.reason_codes_json = sorted(set(reasons + ["semantic_verification_unavailable"]))

    if full_scan_completed:
        coverage.exhaustive_for_negative = True
        coverage.inspected_count = compatible_count
        coverage.retrieval_json = {**coverage.retrieval_json, "inspected_evidence_ids": [str(item) for item in sorted(compatible_items, key=str)]}
        for inspection in db.scalars(select(CoverageInspection).where(CoverageInspection.coverage_record_id == coverage.id)).all():
            if inspection.inspection_status != "excluded":
                inspection.inspection_status = "inspected"
                inspection.state = "inspected"
                inspection.method = "semantic_scan"
                inspection.observation_json = {"full_source_scan": True}
    elif sem_support_ok:
        scanned_units = {unit.id for _, unit, _, compatible, _ in compatible_rows if compatible and any(
            item.id in sent_evidence_ids for item, source_unit, _, _, _ in compatible_rows if item and source_unit.id == unit.id
        )}
        coverage.inspected_count = len(scanned_units)
        for inspection in db.scalars(select(CoverageInspection).where(CoverageInspection.coverage_record_id == coverage.id)).all():
            if inspection.source_unit_id in scanned_units:
                inspection.inspection_status = "inspected"
                inspection.state = "inspected"
                inspection.method = "semantic_scan"
    check.reason_codes_json = sorted(set(check.reason_codes_json))
    if provider_status == "paid_call_guard_blocked_before_network":
        route.outcome = provider_status


def _estimated_tokens(query: str, chunk_ids: list[str], by_chunk: dict[str, RetrievalChunk]) -> int:
    raw = (
        query + " " + " ".join(by_chunk[item].chunk_text for item in chunk_ids if item in by_chunk)
    )
    return max(1, math.ceil(len(raw.encode("utf-8")) / 4))


def _estimated_payload_tokens(payload: dict[str, Any]) -> int:
    counted = {key: value for key, value in payload.items() if key != "json_schema"}
    return max(1, math.ceil(len(json.dumps(counted, ensure_ascii=False).encode("utf-8")) / 4))


def _pack_embedding_batches(texts: list[str], model: str, *, token_limit: int = 7000) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []
    for value in texts:
        candidate = [*current, value]
        if _estimated_payload_tokens({"input": candidate, "model": model}) > token_limit:
            if not current:
                return []
            batches.append(current)
            current = [value]
            if _estimated_payload_tokens({"input": current, "model": model}) > token_limit:
                return []
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches


def _pack_negative_scan_batches(
    *, base_payload: dict[str, Any], base_claim: str, token_limit: int = 7000
) -> list[list[dict[str, Any]]]:
    """Partition a full absence scan without dropping units or breaking source objects."""
    sources = base_payload.get("sources")
    if not isinstance(sources, list) or not sources:
        return []
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []

    def payload_for(items: list[dict[str, Any]], index: int) -> dict[str, Any]:
        return {
            "task_type": "verify_claim",
            "claim_to_verify": f"Segment {index}: no compatible source in this segment supports: {base_claim}",
            "complete_source_scan": True,
            "sources": items,
        }

    for source in sources:
        if not isinstance(source, dict):
            return []
        candidate = [*current, source]
        if _estimated_payload_tokens(payload_for(candidate, len(batches) + 1)) > token_limit:
            if not current:
                return []
            batches.append(current)
            current = [source]
            if _estimated_payload_tokens(payload_for(current, len(batches) + 1)) > token_limit:
                return []
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches


def _persist_candidate(
    db: Session,
    *,
    run: AnalysisRun,
    check: ElementCheck,
    element: Any,
    criterion: Criterion,
    requirements: Sequence[Requirement],
    coverage: CoverageRecord,
    category: str,
    claim: str,
    evidence_ids: list[UUID],
) -> FindingCandidate:
    issue_signature = f"{category}:{element.obligation_key}"
    identity = dedup_key(
        category=category,
        criterion_id=criterion.id,
        obligation_key=element.obligation_key,
        issue_signature=issue_signature,
    )
    candidate = db.scalar(
        select(FindingCandidate).where(
            FindingCandidate.analysis_run_id == run.id,
            FindingCandidate.dedup_key == identity,
        )
    )
    if candidate is None:
        candidate = FindingCandidate(
            analysis_run_id=run.id,
            primary_category=category,
            primary_criterion_id=criterion.id,
            obligation_key=element.obligation_key,
            issue_signature=issue_signature,
            dedup_key=identity,
            claim=claim,
            technical_validation="valid",
            semantic_support="supported",
            limitations_json=[],
        )
        db.add(candidate)
        db.flush()

    def ensure_link(model: Any, key: str, value: UUID) -> None:
        if db.get(model, (candidate.id, value)) is None:
            db.add(model(**{key: candidate.id, "check_element_id" if model is FindingElementLink else
                            "requirement_id" if model is FindingRequirementLink else
                            "criterion_id" if model is FindingCriterionLink else
                            "coverage_record_id": value}))

    ensure_link(FindingElementLink, "finding_candidate_id", check.check_element_id)
    ensure_link(FindingCriterionLink, "finding_candidate_id", criterion.id)
    ensure_link(FindingCoverageLink, "finding_candidate_id", coverage.id)
    for requirement in requirements:
        ensure_link(FindingRequirementLink, "finding_candidate_id", requirement.id)
    for evidence_id in evidence_ids:
        if db.get(FindingEvidenceLink, (candidate.id, evidence_id)) is None:
            db.add(FindingEvidenceLink(finding_candidate_id=candidate.id, evidence_item_id=evidence_id))
    return candidate


def _invoke_provider(
    db: Session,
    *,
    provider: Any,
    method: str,
    schema_name: str,
    payload: dict[str, Any],
    route: RoutingDecision,
    run: AnalysisRun,
    source: SubmissionVersion,
    element_row: CheckElement,
    mode: str,
    limits: dict[str, Any],
    counters: dict[str, Any],
    model_manifest_id: UUID,
    call_no: int = 1,
) -> tuple[Any | None, str, bool, int]:
    """One fenced baseline batch1 provider call with typed usage and fail-closed accounting."""
    call_id = uuid5(run.id, f"{element_row.id}:{method}:{call_no}")
    batch_id = uuid5(run.id, f"semantic-batch1:{element_row.id}:{method}:{call_no}")
    input_tokens = _estimated_payload_tokens(payload)
    if input_tokens > limits["max_input_tokens_per_call"]:
        route.action = "abstain"
        route.outcome = "per_call_input_token_cap"
        return None, "per_call_input_token_cap", False, input_tokens
    limit_reason = token_budget_guard(
        input_tokens=counters["input_tokens"] + input_tokens,
        output_tokens=counters["output_tokens"],
        calls=counters["calls"] + 1,
        vision_calls=counters["vision_calls"],
        elapsed_seconds=int(time.monotonic() - counters["started"]),
        cost_usd=counters["cost_usd"],
    )
    if limit_reason:
        route.action = "abstain"
        route.outcome = limit_reason
        return None, limit_reason, False, input_tokens
    context = ProviderContext(
        call_id=call_id,
        job_id=run.job_id or run.id,
        manifest_id=model_manifest_id,
        policy_id=uuid5(ANALYSIS_NAMESPACE, str(route.policy_manifest_id or ANALYSIS_NAMESPACE)),
        scope_ids=[source.id],
        max_input_tokens=max(0, limits["max_text_input_tokens"] - counters["input_tokens"]),
        max_output_tokens=max(0, limits["max_text_output_tokens"] - counters["output_tokens"]),
    )
    counters["calls"] += 1
    counters["input_tokens"] += input_tokens
    result = None
    response_hash = ""
    status = "provider_error"
    simulated = False
    fixture_id = None
    response_usage: dict[str, int] = {"input_tokens": input_tokens, "output_tokens": 0}
    try:
        if provider is None:
            raise PaidCallBlocked("PAID_CALL_GUARD_INCOMPLETE")
        result = getattr(provider, method)(context, payload)
        fixture_id = result.fixture_id
        simulated = result.simulated
        response_hash = canonical_hash(result.payload)
        response_usage = {
            "input_tokens": max(0, int(result.usage.get("input_tokens", input_tokens) or input_tokens)),
            "output_tokens": max(0, int(result.usage.get("output_tokens", 0) or 0)),
        }
        if (
            result.call_id != context.call_id
            or result.job_id != context.job_id
            or result.scope_ids != [source.id]
        ):
            status = "provider_scope_invalid"
            result = None
        elif result.status not in {"mocked", "completed"}:
            status = str(result.status)[:32]
            result = None
        elif (
            response_usage["input_tokens"] > context.max_input_tokens
            or response_usage["output_tokens"] > context.max_output_tokens
        ):
            status = "provider_usage_over_limit"
            result = None
        else:
            status = "completed"
            counters["input_tokens"] += max(0, response_usage["input_tokens"] - input_tokens)
            counters["output_tokens"] += response_usage["output_tokens"]
    except PaidCallBlocked:
        status = "paid_call_guard_blocked_before_network"
        counters["blocked_calls"] += 1
        result = None
    except Exception:
        # Provider errors are typed without storing an exception body or source content.
        status = "provider_error"
        result = None
    db.add(
        ProviderCallRecord(
            analysis_run_id=run.id,
            routing_decision_id=route.id,
            logical_call_id=call_id,
            attempt_no=1,
            task=method,
            provider_mode=mode,
            status=status,
            request_manifest_json={
                "payload_sha256": canonical_hash(payload),
                "input_token_estimate": input_tokens,
                "schema": schema_name,
            },
            response_manifest_json={
                "payload_sha256": response_hash,
                "fixture_id": fixture_id,
                "simulated": simulated,
                "label": "SIMULASI • data sintetis" if simulated else None,
            },
            usage_json={**response_usage, "physical_calls": 1, "paid_calls": 0},
            cost_usd=Decimal("0") if mode == "mock" or status.startswith("paid_call_guard") else None,
        )
    )
    db.add(RoutingCallLink(routing_decision_id=route.id, call_id=call_id))
    entry_status = "valid" if result is not None else "invalid"
    db.add(
        SemanticBatchMember(
            analysis_run_id=run.id,
            batch_call_id=batch_id,
            check_element_id=element_row.id,
            entry_status=entry_status,
            response_hash=response_hash or None,
            detail_json={"batch_size": 1, "call_id": str(call_id), "status": status},
        )
    )
    counters["output_tokens"] += 0
    route.outcome = status
    return result, status, simulated, input_tokens


def _invoke_embedding(
    db: Session,
    *,
    provider: Any | None,
    texts: list[str],
    route: RoutingDecision,
    run: AnalysisRun,
    source: SubmissionVersion,
    model_manifest_id: UUID,
    policy_manifest_id: UUID,
    limits: dict[str, Any],
    counters: dict[str, Any],
    model: str,
) -> tuple[list[list[float]], str]:
    """Run one bounded embedding batch and account it outside the text-call cap."""
    payload: dict[str, Any] = {"input": texts, "model": model}
    input_tokens = _estimated_payload_tokens(payload)
    if counters["embedding_tokens"] + input_tokens > limits["max_embedding_tokens"]:
        route.action = "abstain"
        route.outcome = "embedding_token_cap"
        return [], "embedding_token_cap"
    call_no = counters["embedding_calls"] + 1
    call_id = uuid5(run.id, f"embedding:{route.check_element_id or 'index'}:{call_no}")
    context = ProviderContext(
        call_id=call_id,
        job_id=run.job_id or run.id,
        manifest_id=model_manifest_id,
        policy_id=uuid5(ANALYSIS_NAMESPACE, str(policy_manifest_id)),
        scope_ids=[source.id],
        max_input_tokens=max(0, limits["max_embedding_tokens"] - counters["embedding_tokens"]),
        max_output_tokens=0,
    )
    counters["embedding_calls"] += 1
    counters["embedding_tokens"] += input_tokens
    vectors: list[list[float]] = []
    status = "provider_error"
    response_hash = ""
    fixture_id: str | None = None
    usage = {"input_tokens": input_tokens, "output_tokens": 0}
    try:
        if provider is None:
            raise PaidCallBlocked("PAID_CALL_GUARD_INCOMPLETE")
        result = provider.embed(context, payload)
        fixture_id = result.fixture_id
        usage = {
            "input_tokens": max(0, int(result.usage.get("input_tokens", input_tokens) or input_tokens)),
            "output_tokens": max(0, int(result.usage.get("output_tokens", 0) or 0)),
        }
        raw_vectors = result.payload.get("embeddings")
        if raw_vectors is None and len(texts) == 1:
            raw_vectors = [result.payload.get("embedding")]
        if (
            result.call_id != context.call_id
            or result.job_id != context.job_id
            or result.manifest_id != context.manifest_id
            or result.policy_id != context.policy_id
            or result.scope_ids != [source.id]
            or result.status != "completed"
            or result.simulated
            or result.limits != {"max_input_tokens": context.max_input_tokens, "max_output_tokens": 0}
            or not isinstance(raw_vectors, list)
            or len(raw_vectors) != len(texts)
            or usage["input_tokens"] > context.max_input_tokens
            or usage["output_tokens"] > 0
        ):
            status = "provider_response_invalid"
        else:
            for vector in raw_vectors:
                if (
                    not isinstance(vector, list)
                    or len(vector) != 1536
                    or any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in vector)
                ):
                    status = "provider_embedding_dimension_invalid"
                    vectors = []
                    break
                vectors.append([float(value) for value in vector])
            else:
                status = "completed"
                response_hash = canonical_hash(vectors)
                counters["embedding_tokens"] += max(0, usage["input_tokens"] - input_tokens)
    except PaidCallBlocked:
        status = "paid_call_guard_blocked_before_network"
        counters["blocked_embeddings"] += 1
    except Exception:
        status = "provider_error"
    db.add(
        ProviderCallRecord(
            analysis_run_id=run.id,
            routing_decision_id=route.id,
            logical_call_id=call_id,
            attempt_no=1,
            task="embed",
            provider_mode="live",
            status=status,
            request_manifest_json={
                "payload_sha256": canonical_hash(payload),
                "input_token_estimate": input_tokens,
                "model": model,
                "dimensions": 1536,
            },
            response_manifest_json={
                "payload_sha256": response_hash or None,
                "fixture_id": fixture_id,
                "simulated": False,
            },
            usage_json={**usage, "embedding_tokens": usage["input_tokens"], "physical_calls": 1, "paid_calls": 0},
            cost_usd=None,
        )
    )
    db.add(RoutingCallLink(routing_decision_id=route.id, call_id=call_id))
    route.outcome = status
    return vectors, status


def _verify_absence_in_batches(
    db: Session,
    *,
    provider: Any | None,
    claim: str,
    decision_question: str,
    grounding_mode: str,
    sources: list[dict[str, Any]],
    run: AnalysisRun,
    source: SubmissionVersion,
    element_row: CheckElement,
    mode: str,
    limits: dict[str, Any],
    counters: dict[str, Any],
    model_manifest_id: UUID,
    routing_manifest_id: UUID,
    batching_manifest_id: UUID,
) -> tuple[SemanticClaimAssessment | None, bool, str]:
    base = {"sources": sources}
    batches = _pack_negative_scan_batches(base_payload=base, base_claim=claim)
    if not batches or sum(len(batch) for batch in batches) != len(sources):
        return None, False, "per_call_input_token_cap"
    assessments: list[SemanticClaimAssessment] = []
    all_ids = {str(item.get("evidence_id")) for item in sources}
    for index, batch in enumerate(batches, start=1):
        segment_claim = f"No compatible source in scan segment {index}/{len(batches)} supports: {claim}"
        payload = {
            "task_type": "verify_claim",
            "claim_to_verify": segment_claim,
            "decision_question": decision_question,
            "grounding_mode": grounding_mode,
            "complete_source_scan": True,
            "scan_segment": {"index": index, "count": len(batches)},
            "sources": batch,
            "json_schema": SEMANTIC_CLAIM_JSON_SCHEMA,
        }
        route = RoutingDecision(
            analysis_run_id=run.id,
            check_element_id=element_row.id,
            task="verify_claim",
            action="call",
            selected_role="complex",
            reason_code="bounded_exhaustive_absence_scan",
            feature_snapshot_json={
                "proposed_relationship": "not_found",
                "coverage_exhaustive": False,
                "scan_segment": index,
                "scan_segment_count": len(batches),
                "evidence_count": len(batch),
            },
            guard_snapshot_json={
                "source_scope_verified": True,
                "paid_call_guard": "blocked_before_network",
            },
            remaining_limits_json={
                "calls": max(0, limits["max_calls"] - counters["calls"]),
                "input_tokens": max(0, limits["max_text_input_tokens"] - counters["input_tokens"]),
            },
            policy_manifest_id=routing_manifest_id,
            outcome="queued",
            job_id=run.job_id,
            batching_manifest_id=batching_manifest_id,
        )
        db.add(route)
        db.flush()
        result, status, simulated, _ = _invoke_provider(
            db,
            provider=provider,
            method="verify_claim",
            schema_name="SemanticClaimAssessment-v1",
            payload=payload,
            route=route,
            run=run,
            source=source,
            element_row=element_row,
            mode=mode,
            limits=limits,
            counters=counters,
            model_manifest_id=model_manifest_id,
            call_no=index + 1,
        )
        if result is None or simulated:
            return None, False, status if result is None else "mock_semantic_verification_unavailable"
        try:
            assessment = SemanticClaimAssessment.model_validate_json(
                json.dumps(result.payload, ensure_ascii=False)
            )
            batch_ids = {str(item.get("evidence_id")) for item in batch}
            if assessment.claim != segment_claim or not {str(item) for item in assessment.evidence_ids} <= batch_ids:
                raise ValueError("scan_segment_evidence_or_claim_invalid")
            if assessment.status == "supported" and assessment.evidence_ids:
                raise ValueError("absence_segment_cannot_bind_positive_evidence")
        except Exception:
            return None, False, "semantic_response_invalid"
        assessments.append(assessment)
    statuses = {item.status for item in assessments}
    aggregate_status: Literal["supported", "unsupported", "insufficient"] = (
        "unsupported" if "unsupported" in statuses
        else "insufficient" if "insufficient" in statuses
        else "supported"
    )
    ids = sorted(
        {item_id for item in assessments for item_id in item.evidence_ids if str(item_id) in all_ids},
        key=str,
    )
    explanation = " ".join(item.explanation for item in assessments)[:2000]
    aggregate = SemanticClaimAssessment(
        status=aggregate_status,
        claim=claim,
        evidence_ids=ids,
        explanation=explanation or "Semua segmen sumber kompatibel telah diperiksa.",
    )
    # Exhaustion records completed inspection. A segment that could not be assessed
    # keeps the negative gate closed even when every source object was transmitted.
    exhaustive = aggregate_status != "insufficient"
    return aggregate, exhaustive, "semantic_scan_completed" if exhaustive else "semantic_scan_insufficient"


def execute_analysis_run(analysis_run_id: str) -> dict[str, Any]:
    run_id = UUID(analysis_run_id)
    settings = get_settings()
    with SessionFactory.begin() as db:
        run = db.scalar(select(AnalysisRun).where(AnalysisRun.id == run_id).with_for_update())
        if run is None:
            return {"status": "missing"}
        if run.status in {"partial", "completed", "failed", "blocked"}:
            return {"status": run.status, "analysis_run_id": analysis_run_id, "duplicate": True}
        if run.job_id is None:
            run.status = "blocked"
            run.limitations_json = [{"code": "analysis_job_missing"}]
            return {"status": "blocked"}
        job = db.scalar(select(Job).where(Job.id == run.job_id).with_for_update())
        if job is None:
            run.status = "blocked"
            run.limitations_json = [{"code": "analysis_job_missing"}]
            return {"status": "blocked"}
        job.status, job.stage, job.lease_until = "running", "validate_manifest", datetime.now(UTC)
        job.lease_fence += 1
        run.status = "running"
        run.limitations_json = []
        started = time.monotonic()
        try:
            with db.begin_nested():
                source, submission, assignment, version, spec_row, release = _verify_snapshot(db, run)
                spec = SpecificationPayload.model_validate_json(
                    json.dumps(spec_row.payload_json, ensure_ascii=False)
                )
        except Exception as exc:
            # The snapshot savepoint contains permission/validation failures while
            # leaving the outer transaction available for a safe terminal state.
            failure_code = (
                str(exc)[:120]
                if isinstance(exc, ValueError)
                else "analysis_snapshot_verification_failed"
            )
            run = db.get(AnalysisRun, run_id)
            if run is None:
                return {"status": "missing", "analysis_run_id": analysis_run_id}
            job = db.get(Job, run.job_id) if run.job_id else None
            run.status = "blocked"
            run.limitations_json = [{"code": failure_code}]
            run.completion_kind = "blocked"
            if job is not None:
                job.status, job.stage, job.last_error_code = (
                    "blocked",
                    "validate_manifest",
                    "analysis_manifest_invalid",
                )
            return {"status": "blocked", "analysis_run_id": analysis_run_id}

        _, _, _, course = _load_source(db, source)
        fingerprint_snapshot = _fingerprint_snapshot(db, source, version, spec_row, release)
        if analysis_fingerprint(fingerprint_snapshot) != run.config_fingerprint:
            run.status = "blocked"
            run.limitations_json = [{"code": "analysis_fingerprint_mismatch"}]
            run.completion_kind = "blocked"
            job.status, job.stage, job.last_error_code = (
                "blocked",
                "validate_manifest",
                "analysis_fingerprint_mismatch",
            )
            return {"status": "blocked", "analysis_run_id": analysis_run_id}
        mode = _provider_mode(release)
        run.provider_mode = mode
        _stage(
            db,
            run.id,
            "validate_manifest",
            run.config_fingerprint,
            {
                "source_version_id": str(source.id),
                "specification_version_id": str(spec_row.id),
                "release_manifest_id": str(release.id),
                "fingerprint": run.config_fingerprint,
            },
        )
        job.stage = "parse"
        artifacts = db.scalars(
            select(ParseArtifact)
            .where(ParseArtifact.submission_version_id == source.id)
            .order_by(ParseArtifact.file_asset_id, ParseArtifact.id)
        ).all()
        source_parse_limitations: list[dict[str, Any]] = []
        if not artifacts:
            source_parse_limitations.append({"code": "submission_parse_artifact_missing"})
        for artifact in artifacts:
            if artifact.status != "parsed" or artifact.limitations_json:
                source_parse_limitations.append(
                    {
                        "code": "source_parse_limited",
                        "file_asset_id": str(artifact.file_asset_id),
                        "status": artifact.status,
                        "limitations": artifact.limitations_json,
                    }
                )
        _stage(
            db,
            run.id,
            "parse",
            canonical_hash(
                {
                    "submission": source.manifest_hash,
                    "parser_manifest": fingerprint_snapshot["parser"],
                }
            ),
            {
                "artifact_ids": [str(item.id) for item in artifacts],
                "statuses": [item.status for item in artifacts],
            },
            [item["code"] for item in source_parse_limitations],
        )
        job.stage = "build_evidence"
        all_units: list[tuple[SourceUnit, ParseArtifact, EvidenceItem | None]] = []
        evidence_rows: list[tuple[EvidenceItem, SourceUnit, ParseArtifact]] = []
        for artifact in artifacts:
            units = db.scalars(
                select(SourceUnit)
                .where(SourceUnit.parse_artifact_id == artifact.id)
                .order_by(SourceUnit.source_order, SourceUnit.id)
            ).all()
            for unit in units:
                asset = db.get(FileAsset, artifact.file_asset_id)
                if asset is None:
                    all_units.append((unit, artifact, None))
                    continue
                items = db.scalars(
                    select(EvidenceItem)
                    .where(
                        EvidenceItem.source_unit_id == unit.id,
                        EvidenceItem.submission_version_id == source.id,
                        EvidenceItem.file_asset_id == artifact.file_asset_id,
                        EvidenceItem.source_sha256 == asset.sha256,
                    )
                    .order_by(EvidenceItem.type, EvidenceItem.id)
                ).all()
                if not items:
                    all_units.append((unit, artifact, None))
                for item in items:
                    all_units.append((unit, artifact, item))
                    evidence_rows.append((item, unit, artifact))
        _stage(
            db,
            run.id,
            "build_evidence",
            canonical_hash(
                {
                    "submission": source.manifest_hash,
                    "evidence_ids": [str(item.id) for item, _, _ in evidence_rows],
                    "unit_ids": [str(unit.id) for unit, _, _ in all_units],
                }
            ),
            {
                "inventory_units": len(all_units),
                "evidence_items": len(evidence_rows),
                "failed_or_unreadable_units": sum(
                    1
                    for unit, artifact, _ in all_units
                    if unit.parse_status != "parsed" or artifact.status != "parsed"
                ),
            },
        )
        job.stage = "index"
        settings_limits = {
            "max_calls": settings.job_max_calls,
            "max_vision_calls": settings.job_max_vision_calls,
            "max_text_input_tokens": settings.job_max_text_input_tokens,
            "max_text_output_tokens": settings.job_max_text_output_tokens,
            "max_input_tokens_per_call": 8000,
            "max_embedding_tokens": settings.job_max_embedding_tokens,
            "max_active_seconds": settings.job_max_active_seconds,
        }
        counters: dict[str, Any] = {
            "calls": 0,
            "vision_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "blocked_calls": 0,
            "embedding_calls": 0,
            "embedding_tokens": 0,
            "blocked_embeddings": 0,
            "cost_usd": Decimal("0"),
            "started": started,
        }
        provider: Any | None = None
        if mode == "mock":
            provider = MockProvider()
        elif mode == "live":
            model_map = {
                "efficient": settings.model_efficient or "",
                "complex": settings.model_complex or "",
                "extract_fields": settings.model_efficient or "",
                "interpret_evidence": settings.model_complex or "",
                "verify_claim": settings.model_complex or "",
                "vision": settings.model_vision or "",
                "embedding": settings.embedding_model,
            }
            if settings.openai_api_key:
                provider = OpenAIResponsesProvider(
                    settings.openai_api_key.get_secret_value(), model_map
                )
        index_count = 0
        embedding_count = 0
        indexed_chunks: list[RetrievalChunk] = []
        for evidence, unit, _ in evidence_rows:
            source_text = _flatten(evidence.content_normalized_json)
            for chunk_piece in chunk_text(source_text, size=600, overlap=80):
                chunk_hash = hashlib.sha256(chunk_piece["text"].encode("utf-8")).hexdigest()
                chunk = db.scalar(
                    select(RetrievalChunk).where(
                        RetrievalChunk.submission_version_id == source.id,
                        RetrievalChunk.evidence_item_id == evidence.id,
                        RetrievalChunk.chunk_no == chunk_piece["chunk_no"],
                        RetrievalChunk.chunk_hash == chunk_hash,
                    )
                )
                if chunk is None:
                    chunk = RetrievalChunk(
                        submission_version_id=source.id,
                        submission_id=submission.id,
                        course_id=course.id,
                        assignment_id=assignment.id,
                        evidence_item_id=evidence.id,
                        source_sha256=evidence.source_sha256,
                        chunk_no=chunk_piece["chunk_no"],
                        tokenizer_manifest=TOKENIZER_MANIFEST,
                        token_start=chunk_piece["token_start"],
                        token_end=chunk_piece["token_end"],
                        chunk_text=chunk_piece["text"],
                        chunk_hash=chunk_hash,
                        anchor_json=evidence.anchor_json,
                        source_order=unit.source_order,
                    )
                    db.add(chunk)
                    db.flush()
                    db.add(
                        ChunkEvidenceLink(
                            chunk_id=chunk.id,
                            evidence_item_id=evidence.id,
                            submission_version_id=source.id,
                            relation="source",
                        )
                    )
                    index_count += 1
                indexed_chunks.append(chunk)
                if mode == "mock" and run.data_origin == "synthetic":
                    tokens = len(re.findall(r"[^\W_]+|[^\s]", chunk_piece["text"], re.UNICODE))
                    if counters["embedding_tokens"] + tokens <= settings_limits["max_embedding_tokens"]:
                        vector = _synthetic_embedding(chunk_piece["text"])
                        db.execute(
                            text("""
                            INSERT INTO evidence_embeddings(chunk_id,submission_version_id,course_id,
                              assignment_id,owner_submission_id,embedding_manifest,embedding)
                            VALUES (:chunk_id,:submission_version_id,:course_id,:assignment_id,
                              :owner_submission_id,:embedding_manifest,CAST(:embedding AS vector))
                            ON CONFLICT(chunk_id,embedding_manifest) DO NOTHING
                        """),
                            {
                                "chunk_id": str(chunk.id),
                                "submission_version_id": str(source.id),
                                "course_id": str(course.id),
                                "assignment_id": str(assignment.id),
                                "owner_submission_id": str(submission.id),
                                "embedding_manifest": MOCK_EMBEDDING_MANIFEST,
                                "embedding": _vector_literal(vector),
                            },
                        )
                        counters["embedding_tokens"] += tokens

        embedding_manifest = MOCK_EMBEDDING_MANIFEST
        if mode == "live":
            embedding_manifest = "openai-embedding-1536:" + canonical_hash(
                {"model": settings.embedding_model, "model_manifest": fingerprint_snapshot["model"]}
            )
            text_by_chunk = {chunk.id: chunk.chunk_text for chunk in indexed_chunks}
            existing_vector_ids = set(
                db.scalars(
                    text(
                        "SELECT chunk_id FROM evidence_embeddings WHERE submission_version_id=:version "
                        "AND embedding_manifest=:manifest"
                    ),
                    {"version": str(source.id), "manifest": embedding_manifest},
                ).all()
            )
            missing_chunks = [chunk for chunk in indexed_chunks if chunk.id not in existing_vector_ids]
            if missing_chunks:
                embed_route = RoutingDecision(
                    analysis_run_id=run.id,
                    check_element_id=None,
                    task="embed",
                    action="call",
                    selected_role="embedding",
                    reason_code="source_chunk_embedding_required",
                    feature_snapshot_json={"chunk_count": len(missing_chunks), "dimensions": 1536},
                    guard_snapshot_json={"source_scope_verified": True, "paid_call_guard": "blocked_before_network"},
                    remaining_limits_json={"embedding_tokens": settings_limits["max_embedding_tokens"]},
                    policy_manifest_id=release.routing_policy_manifest_id,
                    outcome="queued",
                    job_id=run.job_id,
                    batching_manifest_id=release.batching_policy_manifest_id,
                )
                db.add(embed_route)
                db.flush()
                batches = _pack_embedding_batches(
                    [text_by_chunk[chunk.id] for chunk in missing_chunks], settings.embedding_model
                )
                if not batches:
                    embed_route.action = "abstain"
                    embed_route.outcome = "embedding_input_exceeds_batch_limit"
                else:
                    offset = 0
                    for batch in batches:
                        vectors, embed_status = _invoke_embedding(
                            db,
                            provider=provider,
                            texts=batch,
                            route=embed_route,
                            run=run,
                            source=source,
                            model_manifest_id=release.model_manifest_id,
                            policy_manifest_id=release.routing_policy_manifest_id,
                            limits=settings_limits,
                            counters=counters,
                            model=settings.embedding_model,
                        )
                        if len(vectors) != len(batch):
                            break
                        for chunk, vector in zip(missing_chunks[offset : offset + len(batch)], vectors, strict=True):
                            db.execute(
                                text("""
                                INSERT INTO evidence_embeddings(chunk_id,submission_version_id,course_id,
                                  assignment_id,owner_submission_id,embedding_manifest,model_manifest_id,
                                  dimension,content_hash,embedding)
                                VALUES (:chunk_id,:submission_version_id,:course_id,:assignment_id,
                                  :owner_submission_id,:embedding_manifest,:model_manifest_id,1536,
                                  :content_hash,CAST(:embedding AS vector))
                                ON CONFLICT(chunk_id,embedding_manifest) DO NOTHING
                                """),
                                {
                                    "chunk_id": str(chunk.id),
                                    "submission_version_id": str(source.id),
                                    "course_id": str(course.id),
                                    "assignment_id": str(assignment.id),
                                    "owner_submission_id": str(submission.id),
                                    "embedding_manifest": embedding_manifest,
                                    "model_manifest_id": str(release.model_manifest_id),
                                    "content_hash": hashlib.sha256(chunk.chunk_text.encode("utf-8")).hexdigest(),
                                    "embedding": _vector_literal(vector),
                                },
                            )
                        offset += len(batch)
                    else:
                        embed_route.outcome = "completed"

        embedding_count = int(
            db.scalar(
                text(
                    "SELECT count(*) FROM evidence_embeddings WHERE submission_version_id=:version "
                    "AND embedding_manifest=:manifest"
                ),
                {"version": str(source.id), "manifest": embedding_manifest},
            )
            or 0
        )
        embedding_index_ready = bool(indexed_chunks) and embedding_count == len(indexed_chunks)
        run.manifest_snapshot_json = {
            **fingerprint_snapshot,
            "release_manifest_id": str(release.id),
            "model_manifest_id": str(release.model_manifest_id),
            "routing_manifest_id": str(release.routing_policy_manifest_id),
            "batching_baseline": 1,
            "semantic_role": "complex",
            "provider_mode": mode,
            "embedding_mode": (
                "synthetic_hash" if mode == "mock" and embedding_count
                else "provider_live" if mode == "live" and embedding_index_ready
                else "unavailable"
            ),
            "embedding_manifest": embedding_manifest if embedding_index_ready else None,
            "embedding_index_ready": embedding_index_ready,
        }
        _stage(
            db,
            run.id,
            "index",
            canonical_hash(
                {
                    "submission": source.manifest_hash,
                    "tokenizer": TOKENIZER_MANIFEST,
                    "retrieval_manifest": fingerprint_snapshot["retrieval"],
                }
            ),
            {
                "chunks_created": index_count,
                "embeddings_created": embedding_count,
                "embedding_tokens": counters["embedding_tokens"],
                "embedding_calls": counters["embedding_calls"],
                "embedding_blocked_calls": counters["blocked_embeddings"],
                "embedding_dimensions": 1536,
                "embedding_index_ready": embedding_index_ready,
                "vector_search": "exact_cosine_with_scope_predicates",
                "approximate_index": False,
            },
        )
        job.stage = "execute_checks"
        element_rows = db.scalars(
            select(CheckElement)
            .where(CheckElement.specification_version_id == spec_row.id)
            .order_by(CheckElement.element_code, CheckElement.id)
        ).all()
        row_by_element_id = {row.element_id: row for row in element_rows}
        checks = {
            item.check_element_id: item
            for item in db.scalars(
                select(ElementCheck).where(ElementCheck.analysis_run_id == run.id)
            ).all()
        }
        _stage(
            db,
            run.id,
            "execute_checks",
            canonical_hash(
                {
                    "specification": spec_row.content_hash,
                    "source": source.manifest_hash,
                    "limits": settings_limits,
                    "mode": mode,
                }
            ),
            {
                "baseline_batch_size": 1,
                "elements": len(element_rows),
                "dags": "topological_stable_order",
            },
        )
        for element in topological_order(spec):
            element_row = row_by_element_id[element.element_id]
            dependencies = [checks.get(row_by_element_id[item].id) for item in element.dependencies]
            if any(
                dep is None
                or dep.applicability_status == "unresolved"
                or dep.check_status == "unassessable"
                for dep in dependencies
            ):
                # The dependency remains explicitly unresolved; independent checks continue.
                prior = checks.get(element_row.id)
                if prior is None:
                    dependency_inventory = _dependency_coverage_inventory(element, all_units)
                    compatible_inventory = [
                        item for item in dependency_inventory if item["state"] != "excluded"
                    ]
                    check = ElementCheck(
                        analysis_run_id=run.id,
                        check_element_id=element_row.id,
                        applicability_status="unresolved",
                        source_coverage_status="unresolved",
                        evidence_relationship="unclear",
                        check_status="unassessable",
                        observations_json=[],
                        interpretation="A required dependency did not resolve.",
                        reason_codes_json=["dependency_unresolved"],
                    )
                    db.add(check)
                    db.flush()
                    checks[element_row.id] = check
                    record = CoverageRecord(
                        element_check_id=check.id,
                        analysis_run_id=run.id,
                        check_element_id=element_row.id,
                        retrieval_status="unavailable",
                        compatible_count=len(compatible_inventory),
                        inspected_count=0,
                        unresolved_count=len(compatible_inventory),
                        sufficient_for_positive=False,
                        exhaustive_for_negative=False,
                        inventory_json={
                            "units": dependency_inventory,
                            "reason": "dependency_unresolved",
                        },
                        retrieval_json={
                            "dependency_element_ids": [str(i) for i in element.dependencies]
                        },
                        limitations_json=[{"code": "dependency_unresolved"}],
                    )
                    db.add(record)
                    db.flush()
                    for dep_item in dependency_inventory:
                        db.add(
                            CoverageInspection(
                                coverage_record_id=record.id,
                                source_unit_id=UUID(dep_item["source_unit_id"]),
                                evidence_item_id=UUID(dep_item["evidence_ids"][0]) if dep_item["evidence_ids"] else None,
                                state="excluded" if dep_item["state"] == "excluded" else "unresolved",
                                method="not_inspected_dependency",
                                reason_code=dep_item["reason"],
                                detail_json={
                                    "parser_state": dep_item["parser_state"],
                                    "artifact_state": dep_item["artifact_state"],
                                    "evidence_item_ids": dep_item["evidence_ids"],
                                    "limitations": dep_item["limitations"],
                                },
                            )
                        )
                    db.add(
                        GroundingEvaluation(
                            analysis_run_id=run.id,
                            element_check_id=check.id,
                            coverage_record_id=record.id,
                            mechanical_status="not_applicable",
                            semantic_status="not_run",
                            evaluation_json={
                                "mechanical": {"state": "not_applicable"},
                                "semantic": {"state": "not_run"},
                            },
                            limitation_codes_json=["dependency_unresolved"],
                        )
                    )
                    route = RoutingDecision(
                        analysis_run_id=run.id,
                        check_element_id=element_row.id,
                        task="interpret_evidence",
                        action="manual_only",
                        selected_role=None,
                        reason_code="dependency_unresolved",
                        feature_snapshot_json={
                            "dependencies": [str(i) for i in element.dependencies]
                        },
                        guard_snapshot_json={"source_scope_verified": True},
                        remaining_limits_json={
                            key: settings_limits[key] for key in settings_limits
                        },
                        policy_manifest_id=release.routing_policy_manifest_id,
                        outcome="abstained",
                    )
                    db.add(route)
                    checks[element_row.id] = check
                continue
            _persist_element(
                db,
                run=run,
                element_row=element_row,
                element=element,
                source=source,
                submission=submission,
                assignment=assignment,
                version=version,
                course=course,
                evidence_rows=evidence_rows,
                all_units=all_units,
                prior_checks=checks,
                provider=provider,
                mode=mode,
                limits=settings_limits,
                counters=counters,
                routing_manifest_id=release.routing_policy_manifest_id,
                batching_manifest_id=release.batching_policy_manifest_id,
                embedding_manifest=embedding_manifest,
                embedding_index_ready=embedding_index_ready,
                model_manifest_id=release.model_manifest_id,
                embedding_model=settings.embedding_model,
            )
            check_row: ElementCheck | None = db.scalar(
                select(ElementCheck).where(
                    ElementCheck.analysis_run_id == run.id,
                    ElementCheck.check_element_id == element_row.id,
                )
            )
            if check_row is not None:
                checks[element_row.id] = check_row
        _stage(
            db,
            run.id,
            "verify",
            canonical_hash(
                {
                    "mechanical_grounding": "typed-membership-and-source-anchor-v1",
                    "semantic_verifier": "provider-required",
                    "absence_gate": "exhaustive-compatible-units-v1",
                }
            ),
            {
                "checks": len(checks),
                "candidates": 0,
                "claim_policy": "quote-match-alone-rejected",
                "semantic_verification": "not_run_for_mock_baseline",
            },
        )
        unresolved = sum(1 for check in checks.values() if check.check_status == "unassessable")
        limitations = [*source_parse_limitations]
        if mode == "live":
            if counters["blocked_calls"]:
                limitations.append(
                    {
                        "code": "paid_call_guard_incomplete",
                        "calls_blocked_before_network": counters["blocked_calls"],
                    }
                )
            if counters["blocked_embeddings"]:
                limitations.append(
                    {
                        "code": "paid_embedding_guard_incomplete",
                        "calls_blocked_before_network": counters["blocked_embeddings"],
                    }
                )
        if mode == "mock":
            limitations.append({"code": "synthetic_mock_semantic_verification_not_empirical"})
        run.status = "partial" if unresolved or limitations else "completed"
        run.completion_kind = "completed_with_limits" if run.status == "partial" else "completed"
        run.limitations_json = limitations
        run.stage_snapshot_json = {stage: "succeeded" for stage in STAGES}
        run.usage_snapshot_json = {
            **{
                key: (str(value) if isinstance(value, Decimal) else value)
                for key, value in counters.items()
                if key != "started"
            },
            "active_seconds": round(time.monotonic() - started, 3),
            "caps": settings_limits,
        }
        _stage(
            db,
            run.id,
            "ready_for_review",
            canonical_hash(
                {
                    "checks": sorted(str(item.check_element_id) for item in checks.values()),
                    "limitations": limitations,
                }
            ),
            {
                "analysis_status": run.status,
                "completion_kind": run.completion_kind,
                "element_checks": len(checks),
                "finding_candidates": 0,
            },
        )
        job.status = "ready_for_review"
        job.stage = "ready_for_review"
        job.completion_kind = run.completion_kind
        job.lease_until = None
        return {
            "status": run.status,
            "analysis_run_id": analysis_run_id,
            "element_checks": len(checks),
            "unassessable": unresolved,
            "finding_candidates": 0,
            "completion_kind": run.completion_kind,
        }


def create_analysis_job(
    db: Session,
    run: AnalysisRun,
    *,
    course_id: UUID,
    assignment_version_id: UUID,
    limits: dict[str, Any],
) -> Job:
    job = Job(
        type="analysis",
        subject_id=run.id,
        scope_json={
            "course_id": str(course_id),
            "assignment_version_id": str(assignment_version_id),
            "submission_version_id": str(run.submission_version_id),
        },
        input_fingerprint=run.config_fingerprint,
        status="queued",
        stage="validate_manifest",
        lease_fence=0,
        job_limits_json=limits,
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
    return job
