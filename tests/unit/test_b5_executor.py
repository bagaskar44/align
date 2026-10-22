from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from align_api.models import (
    AnalysisRun,
    ProviderCallRecord,
    RoutingDecision,
    SubmissionVersion,
)
from align_domain.assignment_contracts import SpecificationPayload, TaskFact, canonical_hash
from align_domain.executor import (
    ObservationBundle,
    SemanticVerdict,
    analysis_fingerprint,
    bm25_rank,
    candidate_category,
    chunk_text,
    cosine_similarity,
    dedup_key,
    evaluate_registered_rule,
    reciprocal_rank_fusion,
    resolve_applicability,
    semantic_support_is_acceptable,
    token_budget_guard,
    topological_order,
    validate_batch_response,
    validate_observation_bundle,
    verified_task_fact_values,
)
from align_domain.providers import (
    OpenAIResponsesProvider,
    PaidCallBlocked,
    ProviderContext,
    ProviderResult,
)
from align_worker.executor import _invoke_embedding


def _relationship(predicate: str = "supports") -> SimpleNamespace:
    return SimpleNamespace(relationship_code="r_support", predicate=predicate)


def _element(*, mode: str = "linked_relationship", predicate: str = "supports") -> SimpleNamespace:
    return SimpleNamespace(
        relationships=[_relationship(predicate)] if mode == "linked_relationship" else [],
        grounding_contract=SimpleNamespace(mode=mode),
    )


def _bundle(evidence_id: UUID, *, roles: list[str] | None = None) -> ObservationBundle:
    bindings = [
        {
            "relationship_code": "r_support",
            "role": role,
            "observation_codes": ["o_claim"],
            "evidence_ids": [str(evidence_id)],
            "quotes": [{"evidence_id": str(evidence_id), "start": 0, "end": 19,
                        "text": "hasil dilaporkan 12"}],
        }
        for role in (roles or ["claim", "support", "relation"])
    ]
    return ObservationBundle.model_validate_json(
        __import__("json").dumps(
            {
                "observations": [
                    {"observation_code": "o_claim", "text": "Hasil dilaporkan", "evidence_ids": [str(evidence_id)]}
                ],
                "support_bindings": bindings,
                "relationship_observations": [{"relationship_code": "r_support", "text": "Hasil 12 mendukung klaim."}],
                "proposed_evidence_relationship": "supported",
                "schema_version": 1,
                "limitations": [],
                "proposed_reason_codes": [],
            }
        )
    )


def test_applicability_is_closed_typed_and_unknown_facts_remain_unresolved() -> None:
    rule = {
        "op": "all",
        "children": [
            {"op": "eq", "fact": "assignment.mode", "value": "group"},
            {"op": "gte", "fact": "task.team_size", "value": "2"},
        ],
    }
    assert evaluate_registered_rule(rule, {"assignment.mode": "group"}) is None
    assert evaluate_registered_rule(rule, {"assignment.mode": "individual"}) is False
    condition = SimpleNamespace(mode="fact_rule", rule=rule)
    assert resolve_applicability(SimpleNamespace(applicability=condition), {"assignment.mode": "group"}) == (
        "unresolved",
        "applicability_fact_unverified",
    )
    assert resolve_applicability(
        SimpleNamespace(applicability=SimpleNamespace(mode="always", rule=None)), {}
    ) == ("applicable", None)
    assert resolve_applicability(
        SimpleNamespace(applicability=SimpleNamespace(mode="lecturer_required", rule=None)), {}
    ) == ("unresolved", "lecturer_confirmation_required")


def test_applicability_uses_only_version_bound_lecturer_verified_task_facts() -> None:
    assignment_version_id = str(uuid4())
    specification_version_id = str(uuid4())
    specification_hash = "a" * 64
    definitions = {
        "submission_variant": TaskFact(
            key="submission_variant", value_type="string", allowed_values=["original", "alternate"]
        )
    }
    rule = {"op": "eq", "fact": "task.submission_variant", "value": "original"}

    def snapshot(value: str | None, *, verified: str = "lecturer_confirmed") -> dict[str, Any]:
        facts: dict[str, Any] = {}
        if value is not None:
            facts["submission_variant"] = {
                "value": value,
                "origin": "Confirmed against the synthetic task instructions.",
                "evidence_ids": [],
                "verification": verified,
                "verified_by": str(uuid4()),
                "verified_at": "2026-10-22T00:00:00+00:00",
                "assignment_version_id": assignment_version_id,
                "specification_version_id": specification_version_id,
                "specification_hash": specification_hash,
            }
        return {
            "schema_version": 1,
            "assignment_version_id": assignment_version_id,
            "specification_version_id": specification_version_id,
            "specification_hash": specification_hash,
            "facts": facts,
            "content_hash": canonical_hash(facts),
        }

    true_values, true_provenance = verified_task_fact_values(
        snapshot("original"), assignment_version_id=assignment_version_id,
        specification_version_id=specification_version_id, specification_hash=specification_hash,
        definitions=definitions,
    )
    assert resolve_applicability(SimpleNamespace(applicability=SimpleNamespace(mode="fact_rule", rule=rule)), true_values) == ("applicable", None)
    assert true_provenance["submission_variant"]["verification"] == "lecturer_confirmed"

    false_values, _ = verified_task_fact_values(
        snapshot("alternate"), assignment_version_id=assignment_version_id,
        specification_version_id=specification_version_id, specification_hash=specification_hash,
        definitions=definitions,
    )
    assert resolve_applicability(SimpleNamespace(applicability=SimpleNamespace(mode="fact_rule", rule=rule)), false_values) == (
        "not_applicable", "verified_fact_rule_false"
    )

    unknown_values, _ = verified_task_fact_values(
        snapshot(None), assignment_version_id=assignment_version_id,
        specification_version_id=specification_version_id, specification_hash=specification_hash,
        definitions=definitions,
    )
    assert resolve_applicability(SimpleNamespace(applicability=SimpleNamespace(mode="fact_rule", rule=rule)), unknown_values) == (
        "unresolved", "applicability_fact_unverified"
    )
    proposed_values, _ = verified_task_fact_values(
        snapshot("original", verified="proposed"), assignment_version_id=assignment_version_id,
        specification_version_id=specification_version_id, specification_hash=specification_hash,
        definitions=definitions,
    )
    assert proposed_values == {}

    wrong_version_values, _ = verified_task_fact_values(
        snapshot("original"), assignment_version_id=str(uuid4()),
        specification_version_id=specification_version_id, specification_hash=specification_hash,
        definitions=definitions,
    )
    assert wrong_version_values == {}
    stale_spec_values, _ = verified_task_fact_values(
        snapshot("original"), assignment_version_id=assignment_version_id,
        specification_version_id=specification_version_id, specification_hash="b" * 64,
        definitions=definitions,
    )
    assert stale_spec_values == {}
    wrong_specification_values, _ = verified_task_fact_values(
        snapshot("original"), assignment_version_id=assignment_version_id,
        specification_version_id=str(uuid4()), specification_hash=specification_hash,
        definitions=definitions,
    )
    assert wrong_specification_values == {}


def test_dag_is_stable_and_dependencies_are_ordered_first() -> None:
    parent = SimpleNamespace(element_id=UUID(int=2), element_code="B", dependencies=[])
    first = SimpleNamespace(element_id=UUID(int=1), element_code="A", dependencies=[])
    child = SimpleNamespace(element_id=UUID(int=3), element_code="C", dependencies=[parent.element_id])
    spec = SimpleNamespace(elements=[child, parent, first])
    assert [item.element_code for item in topological_order(cast(SpecificationPayload, spec))] == ["A", "B", "C"]


def test_tokenizer_chunks_preserve_source_text_and_exact_overlap() -> None:
    source = "Kata café dan angka 1,25. " * 70
    chunks = chunk_text(source, size=20, overlap=5)
    assert len(chunks) > 1
    assert chunks[0]["token_start"] == 0
    assert chunks[1]["token_start"] == chunks[0]["token_end"] - 5
    assert chunks[0]["text"] in source
    assert "café" in chunks[0]["text"].casefold()
    with pytest.raises(ValueError, match="chunk limits"):
        chunk_text(source, size=10, overlap=10)


def test_bm25_has_no_stemming_and_zero_match_returns_empty_for_expansion() -> None:
    docs = [
        {"id": "a", "text": "kegiatan berkegiatan kegiatan", "order": 0},
        {"id": "b", "text": "berkegiatan", "order": 1},
    ]
    ranked = bm25_rank("kegiatan", docs)
    assert ranked and ranked[0][0] == "a"
    assert bm25_rank("tidak ada di dokumen", docs) == []


def test_cosine_requires_exact_finite_1536_dimension() -> None:
    left = [1.0] + [0.0] * 1535
    same = left.copy()
    orthogonal = [0.0, 1.0] + [0.0] * 1534
    assert cosine_similarity(left, same) == pytest.approx(1.0)
    assert cosine_similarity(left, orthogonal) == pytest.approx(0.0)
    assert cosine_similarity([0.0] * 1536, left) is None
    with pytest.raises(ValueError, match="1536"):
        cosine_similarity([1.0], left)
    with pytest.raises(ValueError, match="finite"):
        cosine_similarity([float("nan")] + [0.0] * 1535, same)


def test_rrf_is_deterministic_and_empty_top_k_has_no_false_result() -> None:
    assert reciprocal_rank_fusion(["b", "a"], ["a", "c"], order={"a": (0,), "b": (1,), "c": (2,)}) == [
        "a",
        "b",
        "c",
    ]
    assert reciprocal_rank_fusion([], []) == []


def test_quote_and_anchor_are_mechanical_only_until_semantic_verdict() -> None:
    evidence_id = uuid4()
    element = _element()
    bundle = _bundle(evidence_id)
    mechanical = validate_observation_bundle(
        bundle,
        element=element,
        evidence_text={evidence_id: "hasil dilaporkan 12 pada periode yang sama."},
    )
    assert mechanical.valid
    assert not semantic_support_is_acceptable(None, mechanical)
    provider_verdict = SemanticVerdict(
        status="supported",
        claim="Nilai 12 menjawab klaim yang diminta.",
        evidence_item_ids=[evidence_id],
        explanation="Konteks dan relasi makna konsisten.",
        verifier="provider",
    )
    assert semantic_support_is_acceptable(provider_verdict, mechanical)
    unverified = provider_verdict.model_copy(update={"verifier": "human"})
    assert not semantic_support_is_acceptable(unverified, mechanical)
    absence_verdict = SemanticVerdict(
        status="supported", claim="absent", evidence_item_ids=[], explanation="scanned", verifier="provider"
    )
    assert not semantic_support_is_acceptable(absence_verdict, mechanical, absence_claim=True)
    assert semantic_support_is_acceptable(
        absence_verdict, mechanical, absence_claim=True, coverage_exhaustive=True
    )


def test_live_provider_guard_blocks_before_network(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network must not be reached before the finance guard")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    provider = OpenAIResponsesProvider("synthetic-test-key", {"verify_claim": "synthetic-model"})
    context = ProviderContext(
        call_id=uuid4(), job_id=uuid4(), manifest_id=uuid4(), policy_id=uuid4(),
        scope_ids=[uuid4()], max_input_tokens=100, max_output_tokens=100,
    )
    with pytest.raises(PaidCallBlocked, match="PAID_CALL_GUARD_INCOMPLETE"):
        provider.verify_claim(context, {"json_schema": {"type": "object"}})


def test_executor_embedding_accepts_only_complete_scoped_non_mock_vectors() -> None:
    model_id, policy_id, source_id = uuid4(), uuid4(), uuid4()
    route = SimpleNamespace(id=uuid4(), check_element_id=None, action="call", outcome="queued")
    run = SimpleNamespace(id=uuid4(), job_id=uuid4())
    source = SimpleNamespace(id=source_id)
    records: list[object] = []
    counters: dict[str, int] = {"embedding_tokens": 0, "embedding_calls": 0, "blocked_embeddings": 0}

    class NonNetworkTestEmbeddingProvider:
        def embed(self, call: ProviderContext, payload: dict[str, object]) -> ProviderResult:
            inputs = payload["input"]
            assert isinstance(inputs, list)
            vectors = [[0.25] * 1536 for _ in inputs]
            return ProviderResult(
                call_id=call.call_id,
                job_id=call.job_id,
                manifest_id=call.manifest_id,
                policy_id=call.policy_id,
                scope_ids=call.scope_ids,
                limits={"max_input_tokens": call.max_input_tokens, "max_output_tokens": 0},
                status="completed",
                payload={"embeddings": vectors},
                usage={"input_tokens": 1, "output_tokens": 0},
                simulated=False,
                fixture_id="test:non-network-embedding-stub:v1",
            )

    vectors, status = _invoke_embedding(
        cast(Session, SimpleNamespace(add=records.append)),
        provider=NonNetworkTestEmbeddingProvider(),
        texts=["synthetic evidence chunk", "another synthetic chunk"],
        route=cast(RoutingDecision, route),
        run=cast(AnalysisRun, run),
        source=cast(SubmissionVersion, source),
        model_manifest_id=model_id,
        policy_manifest_id=policy_id,
        limits={"max_embedding_tokens": 5000},
        counters=counters,
        model="synthetic-test-model",
    )

    assert status == "completed" and route.outcome == "completed"
    assert len(vectors) == 2 and all(len(vector) == 1536 for vector in vectors)
    assert counters["embedding_calls"] == 1 and counters["blocked_embeddings"] == 0
    embedding_record = cast(ProviderCallRecord, records[0])
    assert embedding_record.task == "embed"
    assert embedding_record.response_manifest_json["fixture_id"] == "test:non-network-embedding-stub:v1"


def test_executor_embedding_fails_closed_without_provider() -> None:
    route = SimpleNamespace(id=uuid4(), check_element_id=None, action="call", outcome="queued")
    run = SimpleNamespace(id=uuid4(), job_id=uuid4())
    source = SimpleNamespace(id=uuid4())
    records: list[object] = []
    counters: dict[str, int] = {"embedding_tokens": 0, "embedding_calls": 0, "blocked_embeddings": 0}

    vectors, status = _invoke_embedding(
        cast(Session, SimpleNamespace(add=records.append)),
        provider=None,
        texts=["synthetic evidence chunk"],
        route=cast(RoutingDecision, route),
        run=cast(AnalysisRun, run),
        source=cast(SubmissionVersion, source),
        model_manifest_id=uuid4(),
        policy_manifest_id=uuid4(),
        limits={"max_embedding_tokens": 5000},
        counters=counters,
        model="synthetic-test-model",
    )

    assert vectors == [] and status == "paid_call_guard_blocked_before_network"
    assert route.outcome == status
    assert counters["blocked_embeddings"] == 1
    assert cast(ProviderCallRecord, records[0]).status == status


def test_missing_relationship_role_and_irrelevant_quote_do_not_ground_support() -> None:
    evidence_id = uuid4()
    missing_role = _bundle(evidence_id, roles=["claim", "support"])
    result = validate_observation_bundle(
        missing_role,
        element=_element(),
        evidence_text={evidence_id: "hasil dilaporkan 12"},
    )
    assert not result.valid
    assert "required_relationship_role_missing" in result.reason_codes
    irrelevant = _bundle(evidence_id)
    result = validate_observation_bundle(
        irrelevant,
        element=_element(),
        evidence_text={evidence_id: "hasil berbeda pada konteks lain"},
    )
    assert not result.valid
    assert "quote_span_text_mismatch" in result.reason_codes


def test_foreign_evidence_and_unknown_observation_ids_are_rejected() -> None:
    allowed, foreign = uuid4(), uuid4()
    payload = _bundle(allowed).model_dump(mode="json")
    payload["support_bindings"][0]["evidence_ids"] = [str(foreign)]
    result = validate_observation_bundle(
        ObservationBundle.model_validate_json(json.dumps(payload)),
        element=_element(),
        evidence_text={allowed: "hasil dilaporkan 12"},
    )
    assert not result.valid
    assert "foreign_or_missing_evidence_id" in result.reason_codes


def test_anchored_content_requires_explicit_source_binding() -> None:
    evidence_id = uuid4()
    payload = {
        "schema_version": 1,
        "observations": [{"observation_code": "o1", "text": "Ada penjelasan", "evidence_ids": [str(evidence_id)]}],
        "support_bindings": [{"relationship_code": None, "role": "content", "observation_codes": ["o1"],
                              "evidence_ids": [str(evidence_id)], "quotes": [{"evidence_id": str(evidence_id),
                              "start": 4, "end": 21, "text": "penjelasan sumber"}]}],
        "relationship_observations": [], "proposed_evidence_relationship": "supported",
        "limitations": [], "proposed_reason_codes": [],
    }
    result = validate_observation_bundle(
        ObservationBundle.model_validate_json(json.dumps(payload)), element=_element(mode="anchored_content"),
        evidence_text={evidence_id: "Ada penjelasan sumber yang cukup."},
    )
    assert result.valid
    payload["support_bindings"] = []
    result = validate_observation_bundle(
        ObservationBundle.model_validate_json(json.dumps(payload)), element=_element(mode="anchored_content"),
        evidence_text={evidence_id: "Ada penjelasan sumber yang cukup."},
    )
    assert result.valid is False


def test_candidate_categories_have_separate_negative_positive_and_numeric_gates() -> None:
    evidence_id = uuid4()
    mechanical = validate_observation_bundle(
        _bundle(evidence_id), element=_element(), evidence_text={evidence_id: "hasil dilaporkan 12"}
    )
    verdict = SemanticVerdict(status="supported", claim="verifier", evidence_item_ids=[evidence_id],
                              explanation="verified", verifier="provider")
    def category(**overrides: Any) -> str | None:
        parameters: dict[str, Any] = {
            "applicability": "applicable",
            "grounding_mode": "linked_relationship",
            "semantic_verdict": verdict,
            "mechanical": mechanical,
            "coverage_exhaustive": False,
            "coverage_sufficient_for_positive": False,
        }
        parameters.update(overrides)
        return candidate_category(**parameters)

    assert category(mandatory_requirement=True, absence_claim=True) is None
    assert category(coverage_exhaustive=True, mandatory_requirement=True, absence_claim=True) == "missing_requirement"
    assert category(descriptor_gap_claim=True) is None
    assert category(coverage_exhaustive=True, coverage_sufficient_for_positive=True,
                    descriptor_gap_claim=True) == "rubric_coverage_gap"
    assert category(grounding_mode="human_only", coverage_exhaustive=True,
                    descriptor_gap_claim=True) is None
    assert category(grounding_mode="comparable_numeric", coverage_exhaustive=True,
                    numeric_engine_record={"verified": True, "inconsistent": True}) == "numerical_inconsistency"


def test_exact_dedup_identity_merges_same_obligation_but_not_distinct_issue() -> None:
    criterion = uuid4()
    def key(issue_signature: str) -> str:
        return dedup_key(
            category="missing_requirement",
            criterion_id=criterion,
            obligation_key="deliverable.analysis",
            issue_signature=issue_signature,
        )

    assert key("absence:analysis") == key("absence:analysis")
    assert key("absence:analysis") != key("absence:method")


def test_partial_duplicate_and_foreign_batch_entries_are_separated() -> None:
    valid_id, duplicate_id, omitted_id, foreign_id = uuid4(), uuid4(), uuid4(), uuid4()
    result = validate_batch_response(
        [valid_id, duplicate_id, omitted_id],
        [
            {"check_element_id": str(valid_id), "payload": {"x": 1}},
            {"check_element_id": str(duplicate_id), "payload": {"x": 1}},
            {"check_element_id": str(duplicate_id), "payload": {"x": 2}},
            {"check_element_id": str(foreign_id), "payload": {"x": 3}},
        ],
    )
    assert result[valid_id] == {"status": "valid", "payload": {"x": 1}}
    assert result[duplicate_id] == {"status": "duplicate", "payload": None}
    assert result[omitted_id] == {"status": "omitted", "payload": None}
    assert result[UUID(int=0)]["status"] == "foreign"


def test_budget_caps_and_config_change_fingerprint_are_explicit() -> None:
    assert token_budget_guard(input_tokens=40_000, output_tokens=8000, calls=16,
                              vision_calls=4, elapsed_seconds=600, cost_usd=Decimal("0.20")) is None
    assert token_budget_guard(input_tokens=40_001, output_tokens=0, calls=0,
                              vision_calls=0, elapsed_seconds=0, cost_usd=Decimal("0")) == "input_token_cap"
    snapshot = {
        "submission_manifest_hash": "a" * 64, "specification_hash": "b" * 64,
        "assignment_version_id": str(uuid4()), "parser": "p1", "model": "m1",
        "retrieval": "r1", "rules": "r1", "prompt": "p1", "routing": "route1",
        "batching": "batch1", "grounding_registry": "g1", "lint_registry": "l1",
        "numeric_policy_hashes": [],
        "task_facts_hash": "facts1",
    }
    assert analysis_fingerprint(snapshot) != analysis_fingerprint({**snapshot, "model": "m2"})
    assert analysis_fingerprint(snapshot) != analysis_fingerprint({**snapshot, "task_facts_hash": "facts2"})
    with pytest.raises(ValueError, match="incomplete"):
        analysis_fingerprint({"submission_manifest_hash": "x"})
