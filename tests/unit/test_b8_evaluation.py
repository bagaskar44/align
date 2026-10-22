from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from align_api.evaluation import (
    EvaluationError,
    _expected_policy_manifest_hashes,
    _method_payloads,
    _policy_manifest,
    evaluate_metrics,
    evaluate_policy_protocol,
)


def _gold(
    obligation: str,
    label: str | None,
    *,
    assessability: str = "assessable",
    applicability: str = "applicable",
) -> dict[str, Any]:
    return {
        "obligation_key": obligation,
        "issue_category": "numerical_inconsistency",
        "numeric_context": None,
        "applicability": applicability,
        "gold_assessability": assessability,
        "issue_label": label,
        "compatible_source_units": 3,
        "inspected_source_units": 2,
        "independent_rater_count": 2,
        "adjudication_ref": "synthetic-fixture:gold-review",
    }


def _prediction(obligation: str, decision: str, *, suffix: str = "1") -> dict[str, Any]:
    return {
        "prediction_id": f"prediction-{obligation}-{suffix}",
        "obligation_key": obligation,
        "issue_category": "numerical_inconsistency",
        "numeric_context": None,
        "decision": decision,
        "anchors": [True] if decision == "finding" else [],
        "evidence_support": "supported" if decision == "finding" else "unrated",
    }


def _case(
    *, gold: list[dict[str, Any]] | None = None, predictions: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "case_id": "case-1",
        "bundle_version": "bundle-version-1",
        "assignment_id": "assignment-1",
        "course_id": "course-1",
        "partition": "validation",
        "modality": "pdf",
        "source_sha256": "a" * 64,
        "gold": gold if gold is not None else [_gold("positive", "issue_present")],
        "predictions": predictions if predictions is not None else [_prediction("positive", "finding")],
        "failure_events": [],
        "documents": [{
            "accepted": True,
            "source_units_total": 3,
            "source_units_complete": 3,
        }],
    }


def _dataset(cases: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "dataset_id": "fixture-b8-metrics-v1",
        "data_mode": "synthetic",
        "provider_mode": "mock",
        "partition": "validation",
        "codebook_sha256": "b" * 64,
        "split_sha256": "c" * 64,
        "cases": cases,
    }


def test_master_three_unit_example_keeps_abstention_in_recall_denominator() -> None:
    case = _case(
        gold=[
            _gold("positive-a", "issue_present"),
            _gold("positive-b", "issue_present"),
            _gold("negative", "issue_absent"),
        ],
        predictions=[
            _prediction("positive-a", "finding"),
            _prediction("positive-b", "abstain"),
            _prediction("negative", "no_issue"),
        ],
    )
    report = evaluate_metrics(_dataset([case]), bootstrap_resamples=100, seed=20261018)
    category = report["per_category"]["numerical_inconsistency"]

    assert category["counts"]["tp"] == 1
    assert category["counts"]["fn"] == 1
    assert category["counts"]["tn"] == 1
    assert category["counts"]["fp"] == 0
    assert category["metrics"]["precision"] == 1
    assert category["metrics"]["end_to_end_recall"] == 0.5
    assert category["metrics"]["selective_decision_coverage"] == pytest.approx(2 / 3)
    assert category["metrics"]["selective_risk"] == 0
    assert report["status"] == "software_gate_only"
    assert report["confidence_intervals"]["per_category"]["numerical_inconsistency"]["precision"]["status"] == "descriptive_unstable"


def test_zero_denominators_are_na_and_duplicate_findings_do_not_earn_extra_tp() -> None:
    case = _case(
        gold=[_gold("negative", "issue_absent")],
        predictions=[
            _prediction("negative", "finding", suffix="1"),
            _prediction("negative", "finding", suffix="2"),
        ],
    )
    report = evaluate_metrics(_dataset([case]), bootstrap_resamples=25)
    category = report["per_category"]["numerical_inconsistency"]
    assert category["counts"]["tp"] == 0
    assert category["counts"]["fp"] == 2
    assert category["counts"]["duplicate_findings"] == 1
    assert category["metrics"]["end_to_end_recall"] is None
    assert category["metrics"]["selective_risk"] == 1

    no_findings = _case(gold=[_gold("negative", "issue_absent")], predictions=[])
    empty_report = evaluate_metrics(_dataset([no_findings]), bootstrap_resamples=10)
    empty_category = empty_report["per_category"]["numerical_inconsistency"]
    assert empty_category["metrics"]["precision"] is None
    assert empty_category["metrics"]["end_to_end_recall"] is None
    assert empty_category["metrics"]["f1"] is None
    assert empty_category["metrics"]["selective_risk"] is None


def test_cost_is_only_reported_as_complete_for_end_to_end_ledgers() -> None:
    case = _case()
    case["approved_feedback_count"] = 2
    case["usage"] = {
        "billing_status": "settled",
        "actual_cost_usd": "0.40",
        "cost_scope": "end_to_end",
        "ledger_sha256": "e" * 64,
    }
    report = evaluate_metrics(_dataset([case]), bootstrap_resamples=5)
    cost = report["cost"]
    assert cost["settled_cost_usd"] == "0.40"
    assert cost["settled_cost_per_bundle_usd"] == "0.40"
    assert cost["settled_cost_per_correctly_supported_finding_usd"] == "0.40"
    assert cost["settled_cost_per_approved_feedback_usd"] == "0.20"

    case["usage"]["cost_scope"] = "provider_only"
    incomplete = evaluate_metrics(_dataset([case]), bootstrap_resamples=5)["cost"]
    assert incomplete["settled_cost_usd"] is None
    assert incomplete["partial_settled_cost_usd"] == "0.40"
    assert incomplete["bundles_without_end_to_end_cost_scope"] == 1


def test_unassessable_decision_is_reported_as_a_separate_violation() -> None:
    case = _case(
        gold=[_gold("unreadable", None, assessability="unassessable")],
        predictions=[_prediction("unreadable", "no_issue")],
    )
    report = evaluate_metrics(_dataset([case]), bootstrap_resamples=10)
    values = report["per_category"]["numerical_inconsistency"]
    assert values["counts"]["gold_unassessable"] == 1
    assert values["counts"]["gold_unassessable_violations"] == 1
    assert values["metrics"]["gold_unassessable_decision_violation"] == 1
    assert values["metrics"]["selective_decision_coverage"] is None


def test_failure_and_modality_breakdowns_keep_failure_classes_separate() -> None:
    case = _case(predictions=[_prediction("positive", "abstain")])
    case["failure_events"] = [
        {"failure_id": "acquisition-1", "failure_type": "acquisition"},
        {"failure_id": "interpretation-1", "failure_type": "interpretation"},
        {"failure_id": "unsupported-1", "failure_type": "unsupported"},
    ]
    report = evaluate_metrics(_dataset([case]), bootstrap_resamples=10)
    assert report["failure_breakdown"]["event_counts"] == {
        "acquisition": 1,
        "interpretation": 1,
        "unsupported": 1,
        "abstention": 0,
    }
    assert report["failure_breakdown"]["prediction_outcome_counts"] == {
        "abstention_decisions": 1,
        "unsupported_findings": 0,
    }
    assert report["failure_breakdown"]["modality_counts"]["pdf"]["cases"] == 1


def test_partition_and_parent_leakage_are_rejected() -> None:
    case = _case()
    dataset = _dataset([case])
    dataset["partition_inventory"] = [
        {"case_id": "parent-dev", "partition": "development", "group_keys": ["student-1"]},
        {"case_id": "case-1", "partition": "validation", "group_keys": ["student-1"]},
    ]
    with pytest.raises(EvaluationError, match="group leakage"):
        evaluate_metrics(dataset, bootstrap_resamples=5)

    case["parent_case_id"] = "missing-parent"
    with pytest.raises(EvaluationError, match="parent"):
        evaluate_metrics(_dataset([case]), bootstrap_resamples=5)


def test_policy_report_remains_incomplete_without_validation_support_and_actual_ledgers(
    tmp_path: Path,
) -> None:
    case = _case()
    case["task_family"] = "numeric_consistency"
    case["predictions_by_arm"] = {
        "baseline": case.pop("predictions"),
        "candidate": [_prediction("positive", "finding")],
    }
    case.pop("documents")
    case["usage_by_arm"] = {
        "baseline": {"billing_status": "unknown", "actual_cost_usd": None},
        "candidate": {"billing_status": "unknown", "actual_cost_usd": None},
    }
    dataset = _dataset([case])
    dataset.update({
        "protocol_sha256": "d" * 64,
        "scope": {
            "task_families": ["numeric_consistency"],
            "categories": ["numerical_inconsistency"],
            "modalities": ["pdf"],
        },
        "repeats": {"count": 1, "subset_preselected": False, "task_families": []},
    })
    methods, _release = _method_payloads(Path(__file__).resolve().parents[2])
    baseline_path, baseline_policy = _policy_manifest(
        Path(__file__).resolve().parents[2], "routing-baseline-v2"
    )
    assert baseline_path == "config/routing.baseline.json"
    dataset["paired_manifest_sha256_by_arm"] = {
        arm: _expected_policy_manifest_hashes(methods, "routing", baseline_policy)
        for arm in ("baseline", "candidate")
    }

    report = evaluate_policy_protocol(
        dataset,
        candidate_id="routing-baseline-v2",
        baseline_id="routing-baseline-v2",
        root=Path(__file__).resolve().parents[2],
        bootstrap_resamples=10,
    )

    assert report["status"] == "incomplete"
    assert report["evaluation_kind"] == "software_gate"
    assert report["pending_liability_count"] == 2
    assert any("support" in reason for reason in report["eligibility_reasons"])
    assert any("ledger" in reason for reason in report["eligibility_reasons"])
    assert any("repeats" in reason for reason in report["eligibility_reasons"])
    assert any("human evidence rating" in reason for reason in report["eligibility_reasons"])
    assert report["repeats"]["count"] == 0
    assert report["compatibility"]["paired_manifest_compatible"] is True
    assert report["evidence_rating_complete"] is False


def test_policy_rejects_a_pair_that_changes_a_non_tested_model_manifest() -> None:
    root = Path(__file__).resolve().parents[2]
    methods, _release = _method_payloads(root)
    _path, baseline_policy = _policy_manifest(root, "routing-baseline-v2")
    binding = _expected_policy_manifest_hashes(methods, "routing", baseline_policy)
    altered = dict(binding)
    altered["model"] = "f" * 64
    case = _case()
    case["predictions_by_arm"] = {"baseline": case.pop("predictions"), "candidate": [_prediction("positive", "finding")]}
    case.pop("documents")
    case["usage_by_arm"] = {"baseline": {"billing_status": "unknown"}, "candidate": {"billing_status": "unknown"}}
    dataset = _dataset([case])
    dataset.update({
        "protocol_sha256": "d" * 64,
        "scope": {"task_families": ["numeric_consistency"], "categories": ["numerical_inconsistency"], "modalities": ["pdf"]},
        "repeats": {},
        "paired_manifest_sha256_by_arm": {"baseline": binding, "candidate": altered},
    })
    with pytest.raises(EvaluationError, match="immutable evaluated manifests"):
        evaluate_policy_protocol(
            dataset,
            candidate_id="routing-baseline-v2",
            baseline_id="routing-baseline-v2",
            root=root,
            bootstrap_resamples=5,
        )
