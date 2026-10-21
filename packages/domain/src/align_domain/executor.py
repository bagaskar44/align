"""Deterministic, scope-aware primitives used by the B5 executor.

This module deliberately does not infer semantic support. Mechanical source
validation and an explicit semantic verdict are separate inputs.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, StrictStr

from .assignment_contracts import SpecificationPayload, canonical_hash
from .contracts import StrictModel


class Observation(StrictModel):
    observation_code: StrictStr = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]{0,79}$")
    text: StrictStr = Field(min_length=1, max_length=2000)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)


class QuoteSpan(StrictModel):
    evidence_id: UUID
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: StrictStr = Field(min_length=1, max_length=1000)


class SupportBinding(StrictModel):
    relationship_code: StrictStr | None = Field(max_length=64)
    role: StrictStr = Field(min_length=1, max_length=64)
    observation_codes: list[StrictStr] = Field(min_length=1, max_length=20)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)
    quotes: list[QuoteSpan] = Field(max_length=20)


class RelationshipObservation(StrictModel):
    relationship_code: StrictStr = Field(min_length=1, max_length=64)
    text: StrictStr = Field(min_length=1, max_length=2000)


class ObservationBundle(StrictModel):
    schema_version: Literal[1]
    observations: list[Observation] = Field(max_length=100)
    support_bindings: list[SupportBinding] = Field(max_length=300)
    relationship_observations: list[RelationshipObservation] = Field(max_length=100)
    proposed_evidence_relationship: Literal[
        "supported", "partial", "not_found", "conflicting", "unclear"
    ]
    limitations: list[StrictStr] = Field(max_length=100)
    proposed_reason_codes: list[StrictStr] = Field(max_length=100)


class MechanicalGrounding(StrictModel):
    valid: bool
    reason_codes: list[StrictStr]
    evidence_item_ids: list[UUID]
    relationship_roles: dict[str, list[str]]


class SemanticVerdict(StrictModel):
    status: Literal["supported", "unsupported", "insufficient"]
    claim: StrictStr = Field(min_length=1, max_length=2000)
    evidence_item_ids: list[UUID] = Field(max_length=20)
    explanation: StrictStr = Field(min_length=1, max_length=2000)
    verifier: Literal["provider", "human", "deterministic_rule"]


class SemanticClaimAssessment(StrictModel):
    status: Literal["supported", "unsupported", "insufficient"]
    claim: StrictStr = Field(min_length=1, max_length=2000)
    evidence_ids: list[UUID] = Field(max_length=20)
    explanation: StrictStr = Field(min_length=1, max_length=2000)


class FeedbackDraftBlock(StrictModel):
    review_finding_id: UUID
    criterion_id: UUID
    evidence_ids: list[UUID] = Field(max_length=100)
    message: StrictStr = Field(min_length=1, max_length=4000)
    next_action: StrictStr = Field(min_length=1, max_length=2000)
    limitation: StrictStr | None = Field(default=None, max_length=2000)


class FeedbackDraftPayload(StrictModel):
    schema_version: Literal[1]
    blocks: list[FeedbackDraftBlock] = Field(max_length=100)


def topological_order(spec: SpecificationPayload) -> list[Any]:
    """Stable Kahn sort; ties use element_code then immutable element ID."""
    by_id = {element.element_id: element for element in spec.elements}
    incoming = {element.element_id: set(element.dependencies) for element in spec.elements}
    result: list[Any] = []
    while incoming:
        ready = sorted(
            (element_id for element_id, parents in incoming.items() if not parents),
            key=lambda item: (by_id[item].element_code, str(item)),
        )
        if not ready:
            raise ValueError("dependency graph contains a cycle")
        for element_id in ready:
            result.append(by_id[element_id])
            incoming.pop(element_id)
            for parents in incoming.values():
                parents.discard(element_id)
    return result


def evaluate_registered_rule(rule: dict[str, Any], facts: dict[str, Any]) -> bool | None:
    """Evaluate only the closed B2 rule vocabulary; missing values stay unknown."""
    op = rule["op"]
    if op in {"all", "any"}:
        values = [evaluate_registered_rule(child, facts) for child in rule["children"]]
        if op == "all":
            if False in values:
                return False
            return True if all(value is True for value in values) else None
        if True in values:
            return True
        return False if all(value is False for value in values) else None
    if op == "not":
        value = evaluate_registered_rule(rule["child"], facts)
        return None if value is None else not value
    fact_name = rule["fact"]
    if fact_name not in facts:
        return None
    actual = facts[fact_name]
    if op == "contains":
        return rule["value"] in actual if isinstance(actual, (list, tuple, set)) else None
    expected_values = rule.get("values", [rule.get("value")])
    if op in {"eq", "in"}:
        return actual in expected_values
    try:
        left, right = Decimal(str(actual)), Decimal(str(expected_values[0]))
    except (InvalidOperation, ValueError):
        return None
    return {
        "gt": left > right,
        "gte": left >= right,
        "lt": left < right,
        "lte": left <= right,
    }[op]


def resolve_applicability(element: Any, facts: dict[str, Any]) -> tuple[str, str | None]:
    applicability = element.applicability
    if applicability.mode == "always":
        return "applicable", None
    if applicability.mode == "lecturer_required":
        return "unresolved", "lecturer_confirmation_required"
    result = evaluate_registered_rule(applicability.rule, facts)
    if result is None:
        return "unresolved", "applicability_fact_unverified"
    return ("applicable", None) if result else ("not_applicable", "verified_fact_rule_false")


def retrieval_fingerprint(payload: dict[str, Any]) -> str:
    return canonical_hash(payload)


_TOKEN_RE = re.compile(r"[^\W_]+|[^\s]", re.UNICODE)


def tokenize(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return [token for token in _TOKEN_RE.findall(normalized) if token.strip()]


def chunk_text(text: str, *, size: int = 600, overlap: int = 80) -> list[dict[str, Any]]:
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("invalid chunk limits")
    normalized = unicodedata.normalize("NFKC", text)
    matches = list(_TOKEN_RE.finditer(normalized))
    if not matches:
        return []
    result: list[dict[str, Any]] = []
    step = size - overlap
    for start in range(0, len(matches), step):
        part = matches[start : start + size]
        if not part:
            break
        result.append(
            {
                "chunk_no": len(result),
                "token_start": start,
                "token_end": start + len(part),
                "text": normalized[part[0].start() : part[-1].end()],
            }
        )
        if start + size >= len(matches):
            break
    return result


def bm25_rank(query: str, documents: list[dict[str, Any]]) -> list[tuple[str, float]]:
    """Small deterministic Okapi BM25 implementation, no stemming/stopword removal."""
    query_tokens = list(dict.fromkeys(tokenize(query)))
    tokenized = {str(doc["id"]): tokenize(str(doc["text"])) for doc in documents}
    if not tokenized:
        return []
    n = len(tokenized)
    lengths = {key: len(tokens) for key, tokens in tokenized.items()}
    avg_len = sum(lengths.values()) / n or 1.0
    dfs: Counter[str] = Counter()
    for tokens in tokenized.values():
        dfs.update(set(tokens))
    scores: list[tuple[str, float]] = []
    k1, b = 1.2, 0.75
    for document in documents:
        key = str(document["id"])
        term_freq = Counter(tokenized[key])
        score = 0.0
        for term in query_tokens:
            df = dfs[term]
            idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
            tf = term_freq[term]
            score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * lengths[key] / avg_len))
        scores.append((key, score))
    order = {str(doc["id"]): doc.get("order", "") for doc in documents}
    return sorted(
        (item for item in scores if item[1] > 0),
        key=lambda item: (-item[1], order.get(item[0], ""), item[0]),
    )


def cosine_similarity(left: list[float], right: list[float]) -> float | None:
    if len(left) != 1536 or len(right) != 1536:
        raise ValueError("embedding dimensions must be exactly 1536")
    if any(not math.isfinite(value) for value in (*left, *right)):
        raise ValueError("embedding must be finite")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return None
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


def reciprocal_rank_fusion(
    *rankings: list[str], k: int = 60, order: dict[str, tuple[Any, ...]] | None = None
) -> list[str]:
    if k != 60:
        raise ValueError("B5 baseline RRF constant is 60")
    scores: defaultdict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, item_id in enumerate(ranking, 1):
            scores[item_id] += 1.0 / (k + rank)
    tie_order = order or {}
    return sorted(scores, key=lambda item: (-scores[item], tie_order.get(item, ()), item))


def validate_observation_bundle(
    bundle: ObservationBundle,
    *,
    element: Any,
    evidence_text: dict[UUID, str],
) -> MechanicalGrounding:
    """Validate membership, quote spans and grounding shape without judging meaning."""
    reasons: list[str] = []
    observations = {item.observation_code: item for item in bundle.observations}
    if len(observations) != len(bundle.observations):
        reasons.append("duplicate_observation_code")
    all_ids: set[UUID] = set()
    for observation in bundle.observations:
        if any(item_id not in evidence_text for item_id in observation.evidence_ids):
            reasons.append("foreign_or_missing_evidence_id")
        all_ids.update(observation.evidence_ids)
    relationships = {item.relationship_code: item for item in element.relationships}
    role_map: dict[str, set[str]] = defaultdict(set)
    anchored_bindings = 0
    anchored_observation_codes: set[str] = set()
    relationship_observations = {
        item.relationship_code: item for item in bundle.relationship_observations
    }
    if len(relationship_observations) != len(bundle.relationship_observations):
        reasons.append("duplicate_relationship_observation")

    def validate_quotes(binding: SupportBinding) -> None:
        for quote in binding.quotes:
            source = evidence_text.get(quote.evidence_id)
            if quote.evidence_id not in binding.evidence_ids or source is None:
                reasons.append("quote_evidence_not_bound")
            elif quote.end <= quote.start or quote.end > len(source):
                reasons.append("quote_span_out_of_bounds")
            elif source[quote.start : quote.end] != quote.text:
                reasons.append("quote_span_text_mismatch")

    for binding in bundle.support_bindings:
        if element.grounding_contract.mode == "anchored_content":
            anchored_bindings += 1
            if binding.relationship_code is not None or binding.role != "content":
                reasons.append("anchored_content_binding_invalid")
            if any(code not in observations for code in binding.observation_codes):
                reasons.append("foreign_observation_code")
            anchored_observation_codes.update(binding.observation_codes)
            observed_evidence = {
                item_id
                for code in binding.observation_codes
                if code in observations
                for item_id in observations[code].evidence_ids
            }
            if not observed_evidence.intersection(binding.evidence_ids):
                reasons.append("observation_not_bound_to_content_source")
            if any(item_id not in evidence_text for item_id in binding.evidence_ids):
                reasons.append("foreign_or_missing_evidence_id")
            validate_quotes(binding)
            all_ids.update(binding.evidence_ids)
            continue
        relationship_code = binding.relationship_code
        if relationship_code is None:
            reasons.append("foreign_relationship_code")
            continue
        relationship = relationships.get(relationship_code)
        if relationship is None:
            reasons.append("foreign_relationship_code")
            continue
        if any(code not in observations for code in binding.observation_codes):
            reasons.append("foreign_observation_code")
        observed_evidence = {
            item_id
            for code in binding.observation_codes
            if code in observations
            for item_id in observations[code].evidence_ids
        }
        if not observed_evidence.intersection(binding.evidence_ids):
            reasons.append("observation_not_bound_to_relationship_source")
        if any(item_id not in evidence_text for item_id in binding.evidence_ids):
            reasons.append("foreign_or_missing_evidence_id")
        allowed_roles = {
            "covers": {"content", "relation"},
            "compares": {"left", "right", "basis", "relation"},
            "supports": {"claim", "support", "relation"},
            "applies": {"method", "application", "relation"},
            "traces_to": {"artifact", "trace", "relation"},
            "reports_same_quantity": {"left", "right", "relation"},
        }[relationship.predicate]
        if binding.role not in allowed_roles:
            reasons.append("relationship_role_invalid")
        validate_quotes(binding)
        role_map[relationship_code].add(binding.role)
        all_ids.update(binding.evidence_ids)
    if (
        element.grounding_contract.mode == "anchored_content"
        and bundle.observations
        and not anchored_bindings
    ):
        reasons.append("anchored_content_support_binding_missing")
    if element.grounding_contract.mode == "anchored_content" and not set(observations) <= anchored_observation_codes:
        reasons.append("anchored_observation_binding_missing")
    if bundle.proposed_evidence_relationship == "supported":
        for code, relationship in relationships.items():
            required = {
                "covers": {"content", "relation"},
                "compares": {"left", "right", "basis", "relation"},
                "supports": {"claim", "support", "relation"},
                "applies": {"method", "application", "relation"},
                "traces_to": {"artifact", "trace", "relation"},
                "reports_same_quantity": {"left", "right", "relation"},
            }[relationship.predicate]
            if not required <= role_map[code]:
                reasons.append("required_relationship_role_missing")
            if code not in relationship_observations:
                reasons.append("relationship_observation_missing")
    if set(relationship_observations) - set(relationships):
        reasons.append("foreign_relationship_observation")
    for code, relation_observation in relationship_observations.items():
        if code in relationships and not relation_observation.text.strip():
            reasons.append("relationship_observation_empty")
    return MechanicalGrounding(
        valid=not reasons,
        reason_codes=sorted(set(reasons)),
        evidence_item_ids=sorted(all_ids, key=str),
        relationship_roles={key: sorted(value) for key, value in role_map.items()},
    )


def semantic_support_is_acceptable(
    verdict: SemanticVerdict | None,
    mechanical: MechanicalGrounding,
    *,
    absence_claim: bool = False,
    coverage_exhaustive: bool = False,
) -> bool:
    """A source quote/anchor alone never establishes semantic support."""
    return bool(
        verdict is not None
        and verdict.status == "supported"
        and verdict.verifier == "provider"
        and mechanical.valid
        and set(verdict.evidence_item_ids) <= set(mechanical.evidence_item_ids)
        and (
            bool(verdict.evidence_item_ids)
            or (absence_claim and coverage_exhaustive)
        )
    )


def dedup_key(
    *,
    category: str,
    criterion_id: UUID,
    obligation_key: str,
    issue_signature: str,
    numeric_signature: str | None = None,
) -> str:
    """Exact obligation identity; claim wording similarity is intentionally ignored."""
    return hashlib.sha256(
        canonical_hash(
            {
                "category": category,
                "criterion_id": str(criterion_id),
                "obligation_key": obligation_key,
                "issue_signature": issue_signature,
                "numeric_signature": numeric_signature,
            }
        ).encode()
    ).hexdigest()


def candidate_category(
    *,
    applicability: str,
    grounding_mode: str,
    semantic_verdict: SemanticVerdict | None,
    mechanical: MechanicalGrounding,
    coverage_exhaustive: bool,
    coverage_sufficient_for_positive: bool,
    mandatory_requirement: bool = False,
    absence_claim: bool = False,
    descriptor_gap_claim: bool = False,
    numeric_engine_record: dict[str, Any] | None = None,
) -> str | None:
    """Only the three allowed finding categories can cross their specific gates."""
    if applicability != "applicable" or grounding_mode == "human_only":
        return None
    if (
        semantic_verdict is None
        or semantic_verdict.status != "supported"
        or semantic_verdict.verifier != "provider"
        or not mechanical.valid
    ):
        return None
    if absence_claim:
        if not coverage_exhaustive:
            return None
        if mandatory_requirement:
            return "missing_requirement"
        return "rubric_coverage_gap" if descriptor_gap_claim else None
    if descriptor_gap_claim:
        return (
            "rubric_coverage_gap"
            if coverage_exhaustive and coverage_sufficient_for_positive
            else None
        )
    if grounding_mode == "comparable_numeric":
        if (
            numeric_engine_record is not None
            and numeric_engine_record.get("verified") is True
            and numeric_engine_record.get("inconsistent") is True
        ):
            return "numerical_inconsistency"
    return None


def validate_batch_response(
    expected_element_ids: list[UUID], entries: list[dict[str, Any]]
) -> dict[UUID, dict[str, Any]]:
    """Per-entry batch validator. Foreign/duplicate/omitted IDs never gain coverage."""
    if len(expected_element_ids) != len(set(expected_element_ids)):
        raise ValueError("expected batch element IDs must be unique")
    expected = set(expected_element_ids)
    grouped: defaultdict[UUID, list[dict[str, Any]]] = defaultdict(list)
    foreign: list[dict[str, Any]] = []
    for entry in entries:
        raw_id = entry.get("check_element_id")
        try:
            element_id = UUID(str(raw_id))
        except (ValueError, TypeError, AttributeError):
            foreign.append({"status": "foreign", "entry": entry})
            continue
        if element_id not in expected:
            foreign.append({"status": "foreign", "check_element_id": str(element_id)})
        else:
            grouped[element_id].append(entry)
    result: dict[UUID, dict[str, Any]] = {}
    for element_id in expected_element_ids:
        matches = grouped.get(element_id, [])
        if not matches:
            result[element_id] = {"status": "omitted", "payload": None}
        elif len(matches) != 1:
            result[element_id] = {"status": "duplicate", "payload": None}
        else:
            result[element_id] = {"status": "valid", "payload": matches[0].get("payload")}
    if foreign:
        result[UUID(int=0)] = {"status": "foreign", "entries": foreign}
    return result


def token_budget_guard(
    *,
    input_tokens: int,
    output_tokens: int,
    calls: int,
    vision_calls: int,
    elapsed_seconds: int,
    cost_usd: Decimal,
) -> str | None:
    caps = (
        (calls, 16, "call_cap"),
        (vision_calls, 4, "vision_call_cap"),
        (input_tokens, 40_000, "input_token_cap"),
        (output_tokens, 8_000, "output_token_cap"),
        (elapsed_seconds, 600, "active_time_cap"),
        (cost_usd, Decimal("0.20"), "cost_cap"),
    )
    return next((code for value, limit, code in caps if value > limit), None)


def analysis_fingerprint(snapshot: dict[str, Any]) -> str:
    """Fingerprint every immutable release/source/config input supplied by caller."""
    required = {
        "submission_manifest_hash",
        "specification_hash",
        "assignment_version_id",
        "parser",
        "model",
        "retrieval",
        "rules",
        "prompt",
        "routing",
        "batching",
        "grounding_registry",
        "lint_registry",
        "numeric_policy_hashes",
    }
    if not required <= snapshot.keys():
        raise ValueError("analysis fingerprint snapshot is incomplete")
    return canonical_hash(snapshot)
