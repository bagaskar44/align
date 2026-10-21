from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from align_domain.assignment_contracts import (
    CheckElement,
    NumericPolicyPayload,
    SpecificationPayload,
    canonical_hash,
    compile_lint,
    lint_effective_status,
    validate_spec_against_sources,
)

INSTRUCTION_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CRITERION_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
ELEMENT_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")


def element(
    *, element_id: UUID = ELEMENT_ID, dependencies: list[str] | None = None
) -> dict[str, object]:
    return {
        "element_id": str(element_id),
        "element_code": f"EL-{str(element_id)[:8]}",
        "criterion_id": str(CRITERION_ID),
        "requirement_ids": [],
        "obligation_key": f"obligation.{str(element_id)[:8]}",
        "target_kind": "descriptor",
        "decision_question": "Apakah sumber menjelaskan metode yang dipilih?",
        "derivation_refs": [
            {
                "source_kind": "instruction",
                "source_id": str(INSTRUCTION_ID),
                "span": {"start": 1, "end": 2},
            }
        ],
        "expected_evidence": "Penjelasan metode pada sumber tugas.",
        "grounding_contract": {
            "schema_version": 1,
            "mode": "anchored_content",
            "absence_support": "approved_coverage_policy",
        },
        "accepted_evidence_types": ["paragraph", "table"],
        "relationships": [],
        "applicability": {"mode": "always"},
        "source_hints": ["metode"],
        "operators": [
            "locate_evidence",
            "expand_context",
            "inspect_source_coverage",
            "check_coverage",
            "request_human_review",
        ],
        "dependencies": dependencies or [],
        "coverage_policy": {
            "positive_strategy": "sufficient_support",
            "negative_strategy": "exhaustive_compatible_source_units",
            "inspect_embedded_visuals": True,
            "unresolved_compatible_unit_blocks_negative": True,
            "preserve_table_context": True,
        },
        "abstention_conditions": ["unreadable_relevant_region"],
        "numeric_policy_version_id": None,
        "human_judgment_boundary": "Penilaian mutu menyeluruh tetap menjadi keputusan dosen.",
    }


def spec_payload(elements: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "schema_version": 2,
        "elements": elements or [element()],
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


def parse_spec(payload: dict[str, object]) -> SpecificationPayload:
    return SpecificationPayload.model_validate_json(json.dumps(payload))


def test_unicode_derivation_spans_use_code_points_and_source_binding() -> None:
    parsed = parse_spec(spec_payload())
    instruction = "A😀B"
    validate_spec_against_sources(
        parsed,
        criteria={CRITERION_ID: "descriptor"},
        requirements={},
        instruction_id=INSTRUCTION_ID,
        instruction=instruction,
        numeric_policy_ids=set(),
    )
    ref = parsed.elements[0].derivation_refs[0]
    assert instruction[ref.span.start : ref.span.end] == "😀"
    assert ref.span.end == 2  # UTF-16 would end at 3 for this character.


def test_unknown_registry_values_extra_contract_fields_and_disabled_guardrails_are_rejected() -> (
    None
):
    bad_operator = element()
    bad_operator["operators"] = ["execute_model_code"]
    with pytest.raises(ValidationError):
        parse_spec(spec_payload([bad_operator]))

    bad_registry = spec_payload()
    bad_registry["predicate_registry_version"] = "invented-v2"
    with pytest.raises(ValidationError):
        parse_spec(bad_registry)

    bad_guard = element()
    bad_guard["coverage_policy"] = {
        "positive_strategy": "sufficient_support",
        "negative_strategy": "exhaustive_compatible_source_units",
        "inspect_embedded_visuals": False,
        "unresolved_compatible_unit_blocks_negative": True,
        "preserve_table_context": True,
    }
    with pytest.raises(ValidationError):
        parse_spec(spec_payload([bad_guard]))


def test_dag_cycle_and_unregistered_task_fact_are_rejected() -> None:
    first_id, second_id = uuid4(), uuid4()
    first = element(element_id=first_id, dependencies=[str(second_id)])
    second = element(element_id=second_id, dependencies=[str(first_id)])
    with pytest.raises(ValidationError):
        parse_spec(spec_payload([first, second]))

    with_unknown = element()
    with_unknown["applicability"] = {
        "mode": "fact_rule",
        "rule": {"op": "eq", "fact": "task.unregistered", "value": True},
    }
    with pytest.raises(ValidationError):
        parse_spec(spec_payload([with_unknown]))


def test_numeric_policy_requires_explicit_tolerance_and_approved_linear_units() -> None:
    exact = {
        "metric_definition": "Besaran yang sama pada konteks yang sama.",
        "required_context_fields": ["metric", "entity", "definition", "unit"],
        "number_locale": "id-ID",
        "canonical_unit": "IDR",
        "allowed_conversions": [
            {"kind": "linear", "from": "IDR_million", "to": "IDR", "factor": "1000000"}
        ],
        "comparison_mode": "pairwise",
        "absolute_tolerance": "0",
        "relative_tolerance": "0",
        "rounding": {"mode": "none", "quantum": None},
        "operand_roles": {"left": "angka laporan", "right": "angka tabel"},
    }
    assert NumericPolicyPayload.model_validate_json(json.dumps(exact)).relative_tolerance == "0"

    for field, value in (
        ("absolute_tolerance", None),
        ("relative_tolerance", "1.01"),
        ("canonical_unit", "USD"),
    ):
        invalid = dict(exact)
        invalid[field] = value
        with pytest.raises(ValidationError):
            NumericPolicyPayload.model_validate_json(json.dumps(invalid))

    wrong_factor = dict(exact)
    wrong_factor["allowed_conversions"] = [
        {"kind": "linear", "from": "IDR_million", "to": "IDR", "factor": "1000"}
    ]
    with pytest.raises(ValidationError):
        NumericPolicyPayload.model_validate_json(json.dumps(wrong_factor))


def test_canonical_spec_hash_and_review_resolution_binding() -> None:
    payload = spec_payload()
    assert canonical_hash(payload) == canonical_hash(json.loads(json.dumps(payload)))
    parsed = parse_spec(payload)
    report = compile_lint(
        parsed, revision=4, spec_hash="a" * 64, package_hashes={"instruction": "b" * 64}
    )
    assert report["initial_status"] == "passed"
    assert lint_effective_status(report, [], current_revision=4, current_hash="a" * 64) == "passed"
    assert lint_effective_status(report, [], current_revision=5, current_hash="a" * 64) == "stale"

    review_element = element()
    review_element["decision_question"] = "Apakah dokumen menjelaskan metode jika tersedia?"
    reviewed = compile_lint(
        parse_spec(spec_payload([review_element])),
        revision=1,
        spec_hash="c" * 64,
        package_hashes={},
    )
    assert reviewed["initial_status"] == "needs_review"
    issue_code = reviewed["issues"][0]["issue_code"]
    assert (
        lint_effective_status(
            reviewed, [{"issue_code": issue_code}], current_revision=1, current_hash="c" * 64
        )
        == "passed"
    )


def test_required_requirement_coverage_is_blocking_and_linked_roles_are_registry_driven() -> None:
    parsed = parse_spec(spec_payload())
    linked = element()
    linked["grounding_contract"] = {
        "schema_version": 1,
        "mode": "linked_relationship",
        "absence_support": "approved_coverage_policy",
    }
    linked["relationships"] = [
        {
            "relationship_code": "scaling_applies",
            "predicate": "applies",
            "subject": "StandardScaler",
            "object": "fitur",
            "qualifiers": {"basis": None, "period": None, "scenario": None, "metric": None},
        }
    ]
    assert CheckElement.model_validate_json(json.dumps(linked)).required_support_roles == {
        "scaling_applies": ["application", "method", "relation"]
    }
    report = compile_lint(
        parsed,
        revision=1,
        spec_hash="d" * 64,
        package_hashes={},
        required_requirement_ids={str(uuid4())},
    )
    assert report["initial_status"] == "failed"
    assert report["issues"][0]["rule_code"] == "unsupported_requirement"
    assert lint_effective_status(report, [], current_revision=1, current_hash="d" * 64) == "failed"


def test_numeric_policy_operand_roles_are_closed_by_comparison_mode() -> None:
    payload = {
        "metric_definition": "Besaran yang sama.",
        "required_context_fields": ["metric", "entity", "definition", "unit"],
        "number_locale": "id-ID",
        "canonical_unit": "IDR",
        "allowed_conversions": [],
        "comparison_mode": "sum_against_reported",
        "absolute_tolerance": "0",
        "relative_tolerance": "0",
        "rounding": {"mode": "none", "quantum": None},
        "operand_roles": {"left": "nilai A", "right": "nilai B"},
    }
    with pytest.raises(ValidationError):
        NumericPolicyPayload.model_validate_json(json.dumps(payload))
