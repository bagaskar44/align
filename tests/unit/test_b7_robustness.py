from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from test_b2_assignment_contracts import spec_payload

from align_api.reviews import _markdown_literal
from align_api.robustness import (
    ProtocolError,
    _canonical_hash,
    _obligation_signature,
    validate_protocol,
)
from align_domain.assignment_contracts import SpecificationPayload


def protocol_fixture(*, method: str = "compiler") -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    first = spec_payload()
    second = copy.deepcopy(first)
    second_element = second["elements"][0]
    assert isinstance(second_element, dict)
    second_element["decision_question"] = "Apakah sumber menjelaskan metode yang dipilih?"
    first_spec = SpecificationPayload.model_validate_json(json.dumps(first))
    second_spec = SpecificationPayload.model_validate_json(json.dumps(second))
    source_a = "a" * 64
    source_b = "b" * 64
    dataset: dict[str, Any] = {
        "schema_version": 1,
        "dataset_id": "fixture-b7",
        "data_mode": "synthetic",
        "samples": [
            {"case_id": "base", "source_sha256": source_a, "partition": "development", "group_keys": ["g-1"]},
            {"case_id": "variant", "source_sha256": source_b, "partition": "development", "group_keys": ["g-1"]},
            {"case_id": "validation-held", "source_sha256": "c" * 64, "partition": "validation", "group_keys": ["g-2"]},
        ],
    }
    release = {
        "id": "6b2ab56c-6272-4f14-b943-b90e4305f14d",
        "config_hash": "d" * 64,
        "method_manifest_sha256": {"parser": "e" * 64, "model": "f" * 64},
    }
    protocol: dict[str, Any] = {
        "schema_version": 1,
        "protocol_id": "fixture-b7-protocol",
        "transformation_id": "paraphrase-v1",
        "method": method,
        "partition": "development",
        "release_id": release["id"],
        "release_config_hash": release["config_hash"],
        "exact_manifest_hashes": release["method_manifest_sha256"],
        "dataset_manifest_sha256": _canonical_hash(dataset),
        "seed": 7,
        "repeat_count": 3,
        "cases": [
            {
                "case_id": "base",
                "partition": "development",
                "source_sha256": source_a,
                "equivalence": {"status": "baseline"},
                "specification": first,
                "expected_invariant": {"obligations": _obligation_signature(first_spec)},
                **({"element_order": [str(first_spec.elements[0].element_id)]} if method == "executor" else {}),
            },
            {
                "case_id": "variant",
                "parent_case_id": "base",
                "partition": "development",
                "source_sha256": source_b,
                "equivalence": {"status": "equivalent", "reviewer": {
                    "kind": "fixture", "actor_id": "fixture:rater-1", "decision_ref": "fixture:decision-1",
                }},
                "specification": second,
                "expected_invariant": {"obligations": _obligation_signature(second_spec)},
                **({"element_order": [str(second_spec.elements[0].element_id)]} if method == "executor" else {}),
            },
        ],
    }
    return protocol, dataset, release


def test_compiler_runner_is_exact_release_bound_and_reports_software_only() -> None:
    protocol, dataset, release = protocol_fixture()
    report = validate_protocol(protocol, dataset, release)
    assert report["status"] == "software_gate_only"
    assert report["evidence_class"] == "synthetic_fixture_software_gate"
    assert report["cases"][1]["structural_obligations_match"] is True
    assert report["cases"][1]["reviewer_kind"] == "fixture"
    assert "semantic-equivalence verdict" in report["limitations"][0]


def test_executor_runner_checks_dependency_order_and_marks_runtime_incomplete() -> None:
    protocol, dataset, release = protocol_fixture(method="executor")
    report = validate_protocol(protocol, dataset, release)
    assert report["status"] == "incomplete"
    assert report["cases"][1]["repeat_stability"]["stable_output"] is True
    assert report["cases"][1]["executor_runtime"] == "not_run"


@pytest.mark.parametrize("mutation", ["frozen", "hash", "group", "unreviewed", "bad_dag"])
def test_unsafe_protocols_are_rejected(mutation: str) -> None:
    protocol, dataset, release = protocol_fixture(method="executor" if mutation == "bad_dag" else "compiler")
    if mutation == "frozen":
        protocol["partition"] = "frozen_test"
    elif mutation == "hash":
        protocol["release_config_hash"] = "0" * 64
    elif mutation == "group":
        dataset["samples"][2]["group_keys"] = ["g-1"]
        protocol["dataset_manifest_sha256"] = _canonical_hash(dataset)
    elif mutation == "unreviewed":
        protocol["cases"][1]["equivalence"] = {"status": "equivalent"}
    else:
        protocol["cases"][1]["element_order"] = ["not-an-element"]
    with pytest.raises(ProtocolError):
        validate_protocol(protocol, dataset, release)


def test_negative_fidelity_case_requires_expected_obligation_change() -> None:
    protocol, dataset, release = protocol_fixture()
    variant = protocol["cases"][1]
    variant["equivalence"]["status"] = "not_equivalent"
    variant["expected_changed_obligation_keys"] = ["obligation.not-present"]
    with pytest.raises(ProtocolError, match="does not change"):
        validate_protocol(protocol, dataset, release)


def test_markdown_output_is_supported_by_cli_renderer() -> None:
    from align_api.robustness import render_markdown

    protocol, dataset, release = protocol_fixture()
    report = validate_protocol(protocol, dataset, release)
    content = render_markdown(report)
    assert "synthetic_fixture_software_gate" in content
    assert "stochastic stability" in content


def test_feedback_export_treats_html_and_remote_markdown_links_as_literal_text() -> None:
    rendered = _markdown_literal(
        '<script>alert(1)</script> [link](https://example.invalid/x) '
        '![image](https://example.invalid/y.png)'
    )

    assert "<script>" not in rendered
    assert "![image](" not in rendered
    assert "[link](https://" not in rendered
    assert r"\[link\]\(https\\://example\.invalid/x\)" in rendered
