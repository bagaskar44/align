"""Offline, denominator-explicit metrics and routing/batching policy evaluation.

The module consumes immutable JSON envelopes. It never reads product tables,
calls a provider, or makes gold labels available to the runtime executor.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import platform
import random
import sys
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


class EvaluationError(ValueError):
    """An offline evaluation input is incomplete or internally inconsistent."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _environment_metadata(root: Path | None = None) -> dict[str, Any]:
    workspace = root or Path(__file__).resolve().parents[4]
    versions: dict[str, str] = {}
    for distribution in ("align-api", "align-domain"):
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = "workspace-source"
    memory_bytes: int | None = None
    try:
        memory_bytes = int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
    except (OSError, ValueError, TypeError):
        pass
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "host_memory_bytes": memory_bytes,
        "uv_lock_sha256": file_sha256(workspace / "uv.lock") if (workspace / "uv.lock").is_file() else None,
        "evaluator_source_sha256": file_sha256(Path(__file__)),
        "software_versions": versions,
        "provider_compute_cost": "not_metered",
    }


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationError(f"cannot read {label} JSON") from exc
    if not isinstance(value, dict):
        raise EvaluationError(f"{label} must be a JSON object")
    return value


def _sha(value: Any, field: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise EvaluationError(f"{field} must be a lowercase SHA-256")
    return value


def _nonnegative_int(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        raise EvaluationError(f"{field} must be a non-negative integer")
    return value


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _identity(record: dict[str, Any], bundle_version: str) -> tuple[str, str, str, str]:
    obligation = record.get("obligation_key")
    category = record.get("issue_category")
    numeric = record.get("numeric_context")
    if not isinstance(obligation, str) or not obligation.strip():
        raise EvaluationError("every gold and prediction needs an obligation_key")
    if not isinstance(category, str) or not category.strip():
        raise EvaluationError("every gold and prediction needs an issue_category")
    if numeric is not None and not isinstance(numeric, dict):
        raise EvaluationError("numeric_context must be an object or null")
    return bundle_version, obligation, category, canonical_json(numeric)


def _validate_dataset(dataset: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    if dataset.get("schema_version") != 1:
        raise EvaluationError("dataset manifest schema_version must be 1")
    mode = dataset.get("data_mode")
    if mode not in {"synthetic", "institutional"}:
        raise EvaluationError("data_mode must be synthetic or institutional")
    partition = dataset.get("partition")
    if partition not in {"pilot", "development", "validation", "frozen_test"}:
        raise EvaluationError("dataset partition is unknown")
    if mode == "institutional" and not isinstance(dataset.get("authorization_ref"), str):
        raise EvaluationError("institutional data requires authorization_ref")
    provider_mode = dataset.get("provider_mode", "mock" if mode == "synthetic" else "not_recorded")
    if provider_mode not in {"mock", "live", "not_recorded"}:
        raise EvaluationError("provider_mode must be mock, live, or not_recorded")
    if mode == "institutional" and provider_mode == "mock":
        raise EvaluationError("mock provider evidence cannot be labeled institutional")
    _sha(dataset.get("codebook_sha256"), "codebook_sha256")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise EvaluationError("dataset manifest needs at least one case")
    case_ids: set[str] = set()
    bundle_versions: set[str] = set()
    assignment_courses: dict[str, str] = {}
    group_partitions: dict[str, str] = {}
    partition = dataset["partition"]
    for case in cases:
        if not isinstance(case, dict):
            raise EvaluationError("cases must be objects")
        for field in ("case_id", "bundle_version", "assignment_id", "course_id", "modality"):
            if not isinstance(case.get(field), str) or not case[field].strip():
                raise EvaluationError(f"case {field} is required")
        if case["case_id"] in case_ids:
            raise EvaluationError("case_id values must be unique")
        case_ids.add(case["case_id"])
        if case["bundle_version"] in bundle_versions:
            raise EvaluationError("each case must represent one unique immutable bundle_version")
        bundle_versions.add(case["bundle_version"])
        previous_course = assignment_courses.setdefault(case["assignment_id"], case["course_id"])
        if previous_course != case["course_id"]:
            raise EvaluationError("one assignment_id cannot refer to multiple courses")
        if case.get("partition", partition) != partition:
            raise EvaluationError("case partition does not match the selected dataset partition")
        group_keys = case.get("group_keys", [])
        if not isinstance(group_keys, list) or any(not isinstance(item, str) or not item for item in group_keys):
            raise EvaluationError("group_keys must be nonblank strings")
        for group_key in group_keys:
            previous_partition = group_partitions.setdefault(group_key, partition)
            if previous_partition != partition:
                raise EvaluationError("group membership crosses dataset partitions")
        _sha(case.get("source_sha256"), f"case {case['case_id']} source_sha256")
        gold = case.get("gold")
        predictions = case.get("predictions")
        if not isinstance(gold, list) or not isinstance(predictions, list):
            raise EvaluationError("each case needs gold and predictions arrays")
        seen_gold: set[tuple[str, str, str, str]] = set()
        for item in gold:
            if not isinstance(item, dict):
                raise EvaluationError("gold records must be objects")
            key = _identity(item, case["bundle_version"])
            if key in seen_gold:
                raise EvaluationError("gold target/category/numeric identity must be unique per bundle")
            seen_gold.add(key)
            applicability = item.get("applicability")
            assessability = item.get("gold_assessability")
            label = item.get("issue_label")
            if applicability not in {"applicable", "not_applicable", "unresolved"}:
                raise EvaluationError("gold applicability is invalid")
            if assessability not in {"assessable", "unassessable"}:
                raise EvaluationError("gold_assessability is invalid")
            if applicability == "applicable" and assessability == "assessable":
                if label not in {"issue_present", "issue_absent"}:
                    raise EvaluationError("assessable applicable gold requires an issue_label")
            elif label is not None:
                raise EvaluationError("issue_label must be null for non-applicable or unassessable gold")
            for field in ("compatible_source_units", "inspected_source_units"):
                if field in item:
                    _nonnegative_int(item[field], f"gold {field}")
            if item.get("inspected_source_units", 0) > item.get("compatible_source_units", 0):
                raise EvaluationError("inspected source units cannot exceed compatible units")
        seen_predictions: set[str] = set()
        for item in predictions:
            if not isinstance(item, dict):
                raise EvaluationError("predictions must be objects")
            prediction_id = item.get("prediction_id")
            if not isinstance(prediction_id, str) or not prediction_id.strip() or prediction_id in seen_predictions:
                raise EvaluationError("prediction_id values must be nonblank and unique within a case")
            seen_predictions.add(prediction_id)
            _identity(item, case["bundle_version"])
            if item.get("decision") not in {"finding", "no_issue", "abstain"}:
                raise EvaluationError("prediction decision is invalid")
            support = item.get("evidence_support", "unrated")
            if support not in {"supported", "unsupported", "unrated"}:
                raise EvaluationError("evidence_support must be supported, unsupported, or unrated")
            anchors = item.get("anchors", [])
            if not isinstance(anchors, list) or any(type(anchor) is not bool for anchor in anchors):
                raise EvaluationError("anchors must be a list of explicit boolean validity results")
        usage = case.get("usage")
        if usage is not None:
            if not isinstance(usage, dict) or usage.get("billing_status") not in {"settled", "pending", "unknown"}:
                raise EvaluationError("usage billing_status must be settled, pending, or unknown")
            amount = usage.get("actual_cost_usd")
            if amount is not None:
                try:
                    decimal_amount = Decimal(str(amount))
                except InvalidOperation as exc:
                    raise EvaluationError("actual_cost_usd is not a decimal amount") from exc
                if not decimal_amount.is_finite() or decimal_amount < 0:
                    raise EvaluationError("actual_cost_usd must be finite and non-negative")
            if usage.get("billing_status") == "settled":
                _sha(usage.get("ledger_sha256"), "settled usage ledger_sha256")
        documents = case.get("documents", [])
        if not isinstance(documents, list):
            raise EvaluationError("documents must be an array")
        for document in documents:
            if not isinstance(document, dict) or type(document.get("accepted")) is not bool:
                raise EvaluationError("document entries require an accepted boolean")
            total = _nonnegative_int(document.get("source_units_total"), "document source_units_total")
            complete = _nonnegative_int(document.get("source_units_complete"), "document source_units_complete")
            if complete > total:
                raise EvaluationError("complete source units cannot exceed inventoried source units")
        if "approved_feedback_count" in case:
            _nonnegative_int(case["approved_feedback_count"], "approved_feedback_count")
        failures = case.get("failure_events")
        if not isinstance(failures, list):
            raise EvaluationError("failure_events must explicitly record an empty list or classified events")
        seen_failures: set[str] = set()
        for failure in failures:
            if not isinstance(failure, dict):
                raise EvaluationError("failure_events entries must be objects")
            failure_id = failure.get("failure_id")
            if not isinstance(failure_id, str) or not failure_id.strip() or failure_id in seen_failures:
                raise EvaluationError("failure_id values must be nonblank and unique within a case")
            seen_failures.add(failure_id)
            if failure.get("failure_type") not in {"acquisition", "interpretation", "unsupported", "abstention"}:
                raise EvaluationError("failure_type must be acquisition, interpretation, unsupported, or abstention")
    all_case_ids = {case["case_id"] for case in cases}
    for case in cases:
        parent_case_id = case.get("parent_case_id")
        if parent_case_id is not None and parent_case_id not in all_case_ids:
            raise EvaluationError("derived variant parent must be present in the same partition manifest")
    partition_inventory = dataset.get("partition_inventory", [])
    if not isinstance(partition_inventory, list):
        raise EvaluationError("partition_inventory must be an array")
    inventory_groups: dict[str, str] = {}
    inventory_cases: dict[str, str] = {}
    for item in partition_inventory:
        if not isinstance(item, dict) or not isinstance(item.get("case_id"), str):
            raise EvaluationError("partition_inventory records need case_id")
        item_partition = item.get("partition")
        if item_partition not in {"pilot", "development", "validation", "frozen_test"}:
            raise EvaluationError("partition_inventory record has an unknown partition")
        previous_partition = inventory_cases.setdefault(item["case_id"], item_partition)
        if previous_partition != item_partition:
            raise EvaluationError("one case_id cannot appear in more than one partition")
        groups = item.get("group_keys", [])
        if not isinstance(groups, list):
            raise EvaluationError("partition inventory group_keys must be an array")
        for group_key in groups:
            if not isinstance(group_key, str) or not group_key:
                raise EvaluationError("partition inventory group_keys must be nonblank strings")
            prior = inventory_groups.setdefault(group_key, item_partition)
            if prior != item_partition:
                raise EvaluationError("partition inventory contains group leakage")
    for item in partition_inventory:
        parent = item.get("parent_case_id")
        if parent is not None:
            parent_partition = inventory_cases.get(str(parent))
            if parent_partition is None:
                raise EvaluationError("derived variant parent is missing from partition inventory")
            if parent_partition != item["partition"]:
                raise EvaluationError("derived variant parent crosses partitions")
    return cases, mode


def _empty_counts() -> dict[str, int]:
    return {
        "tp": 0,
        "fp": 0,
        "fn": 0,
        "tn": 0,
        "decisions": 0,
        "wrong_decisions": 0,
        "gold_units": 0,
        "gold_positive": 0,
        "gold_unassessable": 0,
        "gold_unassessable_violations": 0,
        "duplicate_findings": 0,
        "evidence_supported_findings": 0,
        "evidence_rated_findings": 0,
        "anchors_valid": 0,
        "anchors_total": 0,
        "false_findings": 0,
        "compatible_source_units": 0,
        "inspected_source_units": 0,
    }


def _case_category_counts(case: dict[str, Any], category: str) -> dict[str, int]:
    counts = _empty_counts()
    bundle_version = case["bundle_version"]
    gold_by_identity: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for gold in case["gold"]:
        if gold["issue_category"] != category:
            continue
        key = _identity(gold, bundle_version)
        gold_by_identity[key] = gold
        counts["compatible_source_units"] += gold.get("compatible_source_units", 0)
        counts["inspected_source_units"] += gold.get("inspected_source_units", 0)
        if gold["applicability"] != "applicable" or gold["gold_assessability"] != "assessable":
            counts["gold_unassessable"] += 1
        else:
            counts["gold_units"] += 1
            if gold["issue_label"] == "issue_present":
                counts["gold_positive"] += 1
    predictions_by_identity: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    orphan_findings: list[dict[str, Any]] = []
    for prediction in case["predictions"]:
        if prediction["issue_category"] != category:
            continue
        key = _identity(prediction, bundle_version)
        if key not in gold_by_identity:
            if prediction["decision"] == "finding":
                orphan_findings.append(prediction)
            continue
        predictions_by_identity[key].append(prediction)

    for key, gold in gold_by_identity.items():
        predictions = predictions_by_identity.get(key, [])
        findings = [item for item in predictions if item["decision"] == "finding"]
        decisions = [item for item in predictions if item["decision"] in {"finding", "no_issue"}]
        if gold["applicability"] != "applicable" or gold["gold_assessability"] != "assessable":
            if decisions:
                counts["gold_unassessable_violations"] += 1
            continue
        label = gold["issue_label"]
        if decisions:
            counts["decisions"] += 1
        if label == "issue_present":
            if findings:
                counts["tp"] += 1
                counts["duplicate_findings"] += max(0, len(findings) - 1)
                counts["fp"] += max(0, len(findings) - 1)
                counts["wrong_decisions"] += int(any(item["decision"] == "no_issue" for item in decisions))
            else:
                counts["fn"] += 1
                counts["wrong_decisions"] += int(any(item["decision"] == "no_issue" for item in decisions))
        elif label == "issue_absent":
            counts["tn"] += int(any(item["decision"] == "no_issue" for item in decisions))
            counts["fp"] += len(findings)
            counts["duplicate_findings"] += max(0, len(findings) - 1)
            counts["wrong_decisions"] += int(bool(findings))
        for finding in findings:
            support = finding.get("evidence_support", "unrated")
            if support != "unrated":
                counts["evidence_rated_findings"] += 1
                counts["evidence_supported_findings"] += int(support == "supported")
            for anchor in finding.get("anchors", []):
                counts["anchors_total"] += 1
                counts["anchors_valid"] += int(anchor)
    for prediction in orphan_findings:
        counts["fp"] += 1
        counts["wrong_decisions"] += 1
        counts["false_findings"] += 1
        support = prediction.get("evidence_support", "unrated")
        if support != "unrated":
            counts["evidence_rated_findings"] += 1
            counts["evidence_supported_findings"] += int(support == "supported")
        for anchor in prediction.get("anchors", []):
            counts["anchors_total"] += 1
            counts["anchors_valid"] += int(anchor)
    counts["false_findings"] = counts["fp"]
    return counts


def _metrics(counts: dict[str, int]) -> dict[str, float | None]:
    precision = _ratio(counts["tp"], counts["tp"] + counts["fp"])
    recall = _ratio(counts["tp"], counts["tp"] + counts["fn"])
    f1 = None if precision is None or recall is None or precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return {
        "precision": precision,
        "end_to_end_recall": recall,
        "f1": f1,
        "selective_decision_coverage": _ratio(counts["decisions"], counts["gold_units"]),
        "selective_risk": _ratio(counts["wrong_decisions"], counts["decisions"]),
        "gold_unassessable_decision_violation": _ratio(
            counts["gold_unassessable_violations"], counts["gold_unassessable"]
        ),
        "anchor_validity": _ratio(counts["anchors_valid"], counts["anchors_total"]),
        "evidence_support_rate": _ratio(
            counts["evidence_supported_findings"], counts["evidence_rated_findings"]
        ),
        "source_inspection_coverage": _ratio(
            counts["inspected_source_units"], counts["compatible_source_units"]
        ),
    }


def _failure_counts(cases: list[dict[str, Any]]) -> dict[str, Any]:
    failure_types = ("acquisition", "interpretation", "unsupported", "abstention")
    event_counts = {failure_type: 0 for failure_type in failure_types}
    outcome_counts = {"abstention_decisions": 0, "unsupported_findings": 0}
    modality_counts: dict[str, dict[str, int]] = {}
    support_counts = {"supported": 0, "unsupported": 0, "unrated": 0}
    for case in cases:
        modality = case["modality"]
        modality_counts.setdefault(modality, {"cases": 0, "gold_units": 0, "predicted_findings": 0})
        modality_counts[modality]["cases"] += 1
        modality_counts[modality]["gold_units"] += sum(
            item["applicability"] == "applicable" and item["gold_assessability"] == "assessable"
            for item in case["gold"]
        )
        modality_counts[modality]["predicted_findings"] += sum(
            item["decision"] == "finding" for item in case["predictions"]
        )
        for failure in case["failure_events"]:
            event_counts[failure["failure_type"]] += 1
        for prediction in case["predictions"]:
            if prediction["decision"] == "abstain":
                outcome_counts["abstention_decisions"] += 1
            if prediction["decision"] == "finding":
                support_counts[prediction.get("evidence_support", "unrated")] += 1
                if prediction.get("evidence_support") == "unsupported":
                    outcome_counts["unsupported_findings"] += 1
    return {
        "classification": "event_log_and_prediction_outcomes_reported_separately_to_avoid_double_counting",
        "event_counts": event_counts,
        "prediction_outcome_counts": outcome_counts,
        "modality_counts": modality_counts,
        "finding_support_counts": support_counts,
    }


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = (len(ordered) - 1) * quantile
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    fraction = index - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _bootstrap_intervals(
    cases: list[dict[str, Any]],
    categories: list[str],
    resamples: int,
    seed: int,
) -> tuple[dict[str, Any], int]:
    if type(resamples) is not int or not 1 <= resamples <= 100_000:
        raise EvaluationError("bootstrap_resamples must be between 1 and 100000")
    if type(seed) is not int:
        raise EvaluationError("bootstrap seed must be an integer")
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for case in cases:
        grouped[case["course_id"]][case["assignment_id"]].append(case)
    assignment_count = sum(len(assignments) for assignments in grouped.values())
    rng = random.Random(seed)  # noqa: S311 -- deterministic bootstrap resampling, not security randomness
    metric_names = (
        "precision",
        "end_to_end_recall",
        "selective_decision_coverage",
        "selective_risk",
        "evidence_support_rate",
        "gold_unassessable_decision_violation",
        "anchor_validity",
        "source_inspection_coverage",
    )
    values: dict[str, dict[str, list[float]]] = {
        category: {name: [] for name in metric_names} for category in categories
    }
    zero_denominator: dict[str, dict[str, int]] = {
        category: {name: 0 for name in metric_names} for category in categories
    }
    per_case = {
        case["case_id"]: {category: _case_category_counts(case, category) for category in categories}
        for case in cases
    }
    for _ in range(resamples):
        sampled_cases: list[dict[str, Any]] = []
        for assignments in grouped.values():
            assignment_ids = list(assignments)
            for sampled_id in rng.choices(assignment_ids, k=len(assignment_ids)):
                sampled_cases.extend(assignments[sampled_id])
        for category in categories:
            total = _empty_counts()
            for case in sampled_cases:
                current = per_case[case["case_id"]][category]
                for name in total:
                    total[name] += current[name]
            metrics = _metrics(total)
            for name in metric_names:
                value = metrics[name]
                if value is None:
                    zero_denominator[category][name] += 1
                else:
                    values[category][name].append(value)
    intervals: dict[str, Any] = {}
    for category in categories:
        intervals[category] = {}
        for name in metric_names:
            available = values[category][name]
            intervals[category][name] = {
                "lower": _percentile(available, 0.025) if available else None,
                "upper": _percentile(available, 0.975) if available else None,
                "valid_resamples": len(available),
                "zero_denominator_resamples": zero_denominator[category][name],
                "method": "assignment_cluster_bootstrap_stratified_by_course",
                "status": "descriptive_unstable" if assignment_count < 5 else "descriptive",
            }
    return intervals, assignment_count


def _aggregate(case_counts: list[dict[str, int]]) -> dict[str, int]:
    total = _empty_counts()
    for current in case_counts:
        for field in total:
            total[field] += current[field]
    return total


def evaluate_metrics(
    dataset: dict[str, Any], *, bootstrap_resamples: int = 10_000, seed: int = 20261018
) -> dict[str, Any]:
    cases, data_mode = _validate_dataset(dataset)
    categories = sorted({
        item["issue_category"]
        for case in cases
        for item in (*case["gold"], *case["predictions"])
    })
    if not categories:
        raise EvaluationError("dataset has no gold issue categories")
    per_category: dict[str, Any] = {}
    for category in categories:
        counts = _aggregate([_case_category_counts(case, category) for case in cases])
        per_category[category] = {"counts": counts, "metrics": _metrics(counts)}
    all_counts = _aggregate(
        [
            _aggregate([_case_category_counts(case, category) for category in categories])
            for case in cases
        ]
    )
    intervals, assignment_count = _bootstrap_intervals(cases, categories, bootstrap_resamples, seed)
    accepted_documents = [
        document
        for case in cases
        for document in case.get("documents", [])
        if document["accepted"]
    ]
    covered_documents = sum(
        document["source_units_total"] > 0
        and document["source_units_complete"] == document["source_units_total"]
        for document in accepted_documents
    )
    findings_per_bundle = sum(
        item["decision"] == "finding"
        for case in cases
        for item in case["predictions"]
    )
    supported_findings = sum(
        item["decision"] == "finding" and item.get("evidence_support") == "supported"
        for case in cases
        for item in case["predictions"]
    )
    approved_feedback_count = sum(case.get("approved_feedback_count", 0) for case in cases)
    false_findings = sum(value["counts"]["false_findings"] for value in per_category.values())
    actual_costs: list[Decimal] = []
    pending_liabilities = 0
    missing_cost_cases = 0
    non_end_to_end_cost_cases = 0
    for case in cases:
        usage = case.get("usage")
        if usage is None:
            missing_cost_cases += 1
        elif usage["billing_status"] in {"pending", "unknown"}:
            pending_liabilities += 1
        elif usage.get("actual_cost_usd") is not None:
            actual_costs.append(Decimal(str(usage["actual_cost_usd"])))
            if usage.get("cost_scope") != "end_to_end":
                non_end_to_end_cost_cases += 1
        else:
            missing_cost_cases += 1
    bundle_count = len({case["bundle_version"] for case in cases})
    full_cost_complete = (
        not pending_liabilities
        and not missing_cost_cases
        and not non_end_to_end_cost_cases
        and len(actual_costs) == len(cases)
    )
    full_settled_cost = sum(actual_costs, Decimal("0")) if full_cost_complete else None
    human_adjudication_complete = all(
        type(item.get("independent_rater_count")) is int
        and item["independent_rater_count"] >= 2
        and isinstance(item.get("adjudication_ref"), str)
        and item["adjudication_ref"].strip()
        for case in cases
        for item in case["gold"]
        if item["applicability"] == "applicable" and item["gold_assessability"] == "assessable"
    )
    limitations: list[str] = []
    if data_mode == "synthetic":
        status = "software_gate_only"
        evaluation_kind = "software_example"
        limitations.append("Synthetic fixture metrics verify denominator and report logic only; they are not research results.")
    else:
        evaluation_kind = "empirical_descriptive"
        status = "descriptive_empirical" if human_adjudication_complete else "incomplete"
        if not human_adjudication_complete:
            limitations.append("Two-rater adjudication evidence is incomplete for one or more assessable gold units.")
    if dataset.get("partition") == "frozen_test":
        limitations.append("Frozen-test output is evaluation-only and must not be used for tuning.")
    if assignment_count < 5:
        limitations.append("Fewer than five independent assignments: cluster intervals are descriptive and unstable.")
    if pending_liabilities:
        limitations.append("Pending or unknown usage remains a liability and is not treated as zero cost.")
    if missing_cost_cases:
        limitations.append("Full actual cost is unavailable for one or more bundles.")
    if non_end_to_end_cost_cases:
        limitations.append("One or more cost records do not cover the complete end-to-end execution scope.")
    if not accepted_documents:
        limitations.append("Operational document coverage is NA because no accepted-file inventory was supplied.")
    report: dict[str, Any] = {
        "schema_version": 1,
        "report_type": "align_offline_metrics_report",
        "dataset_id": dataset.get("dataset_id", "not_named"),
        "dataset_sha256": canonical_sha256(dataset),
        "codebook_sha256": dataset["codebook_sha256"],
        "split_sha256": _sha(dataset.get("split_sha256"), "split_sha256", optional=True),
        "partition": dataset["partition"],
        "data_mode": data_mode,
        "data_origin": data_mode,
        "provider_mode": dataset.get("provider_mode", "mock" if data_mode == "synthetic" else "not_recorded"),
        "environment": _environment_metadata(),
        "evaluation_kind": evaluation_kind,
        "status": status,
        "tuning_allowed": dataset["partition"] in {"development", "validation"},
        "sample_count": len(cases),
        "bundle_count": len({case["bundle_version"] for case in cases}),
        "independent_assignment_count": assignment_count,
        "all_categories": {"counts": all_counts, "metrics": _metrics(all_counts)},
        "per_category": per_category,
        "operational_document_coverage": {
            "numerator": covered_documents,
            "denominator": len(accepted_documents),
            "estimate": _ratio(covered_documents, len(accepted_documents)),
        },
        "false_findings_per_bundle": _ratio(false_findings, len({case["bundle_version"] for case in cases})),
        "finding_count": findings_per_bundle,
        "correctly_supported_finding_count": supported_findings,
        "failure_breakdown": _failure_counts(cases),
        "cost": {
            "settled_cost_usd": str(full_settled_cost) if full_settled_cost is not None else None,
            "partial_settled_cost_usd": str(sum(actual_costs, Decimal("0"))) if actual_costs else None,
            "end_to_end_complete": full_cost_complete,
            "settled_cost_per_bundle_usd": (
                str(full_settled_cost / bundle_count) if full_settled_cost is not None and bundle_count else None
            ),
            "settled_cost_per_correctly_supported_finding_usd": (
                str(full_settled_cost / supported_findings)
                if full_settled_cost is not None and supported_findings else None
            ),
            "approved_feedback_count": approved_feedback_count,
            "settled_cost_per_approved_feedback_usd": (
                str(full_settled_cost / approved_feedback_count)
                if full_settled_cost is not None and approved_feedback_count else None
            ),
            "bundles_with_settled_cost": len(actual_costs),
            "pending_liability_count": pending_liabilities,
            "bundles_missing_cost": missing_cost_cases,
            "bundles_without_end_to_end_cost_scope": non_end_to_end_cost_cases,
        },
        "confidence_intervals": {
            "method": "assignment_cluster_bootstrap_stratified_by_course",
            "resamples": bootstrap_resamples,
            "seed": seed,
            "per_category": intervals,
        },
        "limitations": limitations,
    }
    return report


def _policy_manifest(root: Path, manifest_id: str) -> tuple[str, dict[str, Any]]:
    config_root = (root / "config").resolve()
    found: list[tuple[str, dict[str, Any]]] = []
    for path in config_root.rglob("*.json"):
        payload = _load_json(path, "config manifest")
        if manifest_id in {payload.get("manifest_id"), payload.get("policy_id")}:
            found.append((path.relative_to(root).as_posix(), payload))
    if len(found) != 1:
        raise EvaluationError(f"manifest id must resolve uniquely under config/: {manifest_id}")
    return found[0]


def _method_payloads(root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    release = _load_json(root / "config/release.baseline.json", "baseline release")
    raw_manifests = release.get("manifests")
    if not isinstance(raw_manifests, dict):
        raise EvaluationError("baseline release must list method manifests")
    payloads: dict[str, dict[str, Any]] = {}
    for kind, relative in raw_manifests.items():
        if not isinstance(relative, str):
            raise EvaluationError("release manifest paths must be strings")
        path = (root / "config" / relative).resolve()
        if config_root := (root / "config").resolve():
            if config_root not in path.parents:
                raise EvaluationError("release manifest path escapes config/")
        payloads[kind] = _load_json(path, f"{kind} baseline manifest")
    return payloads, release


def _policy_core_hash(methods: dict[str, dict[str, Any]], release: dict[str, Any]) -> str:
    return canonical_sha256({
        "methods": {kind: canonical_sha256(methods[kind]) for kind in sorted(methods)},
        "grounding_registry_version": release.get("grounding_registry_version"),
        "lint_registry_version": release.get("lint_registry_version"),
    })


def _expected_policy_manifest_hashes(
    methods: dict[str, dict[str, Any]],
    policy_kind: str,
    policy_manifest: dict[str, Any],
) -> dict[str, str]:
    payloads = {**methods, policy_kind: policy_manifest}
    return {kind: canonical_sha256(payload) for kind, payload in sorted(payloads.items())}


def _validate_paired_manifest_bindings(
    dataset: dict[str, Any],
    *,
    methods: dict[str, dict[str, Any]],
    policy_kind: str,
    baseline_manifest: dict[str, Any],
    candidate_manifest: dict[str, Any],
) -> bool:
    bindings = dataset.get("paired_manifest_sha256_by_arm")
    if not isinstance(bindings, dict) or set(bindings) != {"baseline", "candidate"}:
        return False
    expected = {
        "baseline": _expected_policy_manifest_hashes(methods, policy_kind, baseline_manifest),
        "candidate": _expected_policy_manifest_hashes(methods, policy_kind, candidate_manifest),
    }
    for arm in ("baseline", "candidate"):
        actual = bindings[arm]
        if not isinstance(actual, dict) or set(actual) != set(expected[arm]):
            return False
        for kind, digest in actual.items():
            _sha(digest, f"{arm} paired manifest {kind}")
        if actual != expected[arm]:
            raise EvaluationError(f"{arm} run manifests do not match the immutable evaluated manifests")
    for kind in expected["baseline"]:
        if kind != policy_kind and expected["baseline"][kind] != expected["candidate"][kind]:
            raise EvaluationError(f"paired arms changed non-tested {kind} manifest")
    return True


def _policy_finding_ratings_complete(dataset: dict[str, Any]) -> bool:
    for case in dataset["cases"]:
        predictions_by_arm = case.get("predictions_by_arm")
        if not isinstance(predictions_by_arm, dict):
            return False
        for arm in ("baseline", "candidate"):
            predictions = predictions_by_arm.get(arm)
            if not isinstance(predictions, list):
                return False
            for prediction in predictions:
                if prediction.get("decision") != "finding":
                    continue
                rating = prediction.get("evidence_rating")
                if (
                    prediction.get("evidence_support") not in {"supported", "unsupported"}
                    or not isinstance(rating, dict)
                    or not isinstance(rating.get("rater_id"), str)
                    or not rating["rater_id"].strip()
                    or not isinstance(rating.get("competence_ref"), str)
                    or not rating["competence_ref"].strip()
                    or rating.get("codebook_sha256") != dataset.get("codebook_sha256")
                    or not isinstance(rating.get("rating_ref"), str)
                    or not rating["rating_ref"].strip()
                ):
                    return False
    return True


def _policy_metrics_dataset(
    dataset: dict[str, Any], arm: str
) -> dict[str, Any]:
    cases = []
    for original in dataset["cases"]:
        by_arm = original.get("predictions_by_arm")
        if not isinstance(by_arm, dict) or not isinstance(by_arm.get(arm), list):
            raise EvaluationError(f"case {original.get('case_id')} has no {arm} predictions")
        case = {key: value for key, value in original.items() if key not in {
            "predictions_by_arm", "usage_by_arm", "critical_invariant_violations_by_arm"
        }}
        case["predictions"] = by_arm[arm]
        usage = original.get("usage_by_arm", {})
        case["usage"] = usage.get(arm) if isinstance(usage, dict) else None
        cases.append(case)
    return {**dataset, "cases": cases}


def _metrics_by_category(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for category, values in report["per_category"].items():
        counts = values["counts"]
        metrics = values["metrics"]
        output[category] = {
            "tp": counts["tp"],
            "fp": counts["fp"],
            "fn": counts["fn"],
            "evidence_supported_findings": counts["evidence_supported_findings"],
            "decisions": counts["decisions"],
            "gold_unassessable_violations": counts["gold_unassessable_violations"],
            "precision": metrics["precision"],
            "evidence_support_rate": metrics["evidence_support_rate"],
            "end_to_end_recall": metrics["end_to_end_recall"],
            "selective_decision_coverage": metrics["selective_decision_coverage"],
        }
    return output


def _policy_intervals(metrics_report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    converted: dict[str, dict[str, Any]] = {}
    for category, values in metrics_report["confidence_intervals"]["per_category"].items():
        converted[category] = {}
        for source, target in (
            ("precision", "precision"),
            ("end_to_end_recall", "end_to_end_recall"),
            ("selective_decision_coverage", "selective_decision_coverage"),
            ("evidence_support_rate", "evidence_support_rate"),
        ):
            interval = values[source]
            converted[category][target] = {
                "lower": interval["lower"],
                "upper": interval["upper"],
                "valid_resamples": interval["valid_resamples"],
                "zero_denominator_resamples": interval["zero_denominator_resamples"],
                "status": interval["status"],
            }
    return converted


def evaluate_policy_protocol(
    dataset: dict[str, Any],
    *,
    candidate_id: str,
    baseline_id: str,
    root: Path,
    bootstrap_resamples: int = 10_000,
    seed: int = 20261018,
) -> dict[str, Any]:
    cases, data_mode = _validate_dataset({
        **dataset,
        "cases": [
            {
                **case,
                "predictions": case.get("predictions_by_arm", {}).get("candidate", []),
                "usage": case.get("usage_by_arm", {}).get("candidate"),
            }
            for case in dataset.get("cases", [])
        ],
    })
    if dataset.get("partition") != "validation":
        raise EvaluationError("policy selection/promotion evaluation must use the validation partition")
    methods, release = _method_payloads(root)
    baseline_path, baseline_manifest = _policy_manifest(root, baseline_id)
    candidate_path, candidate_manifest = _policy_manifest(root, candidate_id)
    policy_kind = "routing" if "semantic_routes" in baseline_manifest else "batching" if "maximum_elements" in baseline_manifest else None
    candidate_kind = "routing" if "semantic_routes" in candidate_manifest else "batching" if "maximum_elements" in candidate_manifest else None
    if policy_kind is None or candidate_kind != policy_kind:
        raise EvaluationError("baseline and candidate must resolve to the same routing or batching policy kind")
    base_methods = dict(methods)
    base_methods[policy_kind] = baseline_manifest
    candidate_methods = dict(methods)
    candidate_methods[policy_kind] = candidate_manifest
    paired_manifest_compatible = _validate_paired_manifest_bindings(
        dataset,
        methods=methods,
        policy_kind=policy_kind,
        baseline_manifest=baseline_manifest,
        candidate_manifest=candidate_manifest,
    )
    evidence_rating_complete = _policy_finding_ratings_complete(dataset)
    baseline_metrics = evaluate_metrics(
        _policy_metrics_dataset(dataset, "baseline"),
        bootstrap_resamples=bootstrap_resamples,
        seed=seed,
    )
    candidate_metrics = evaluate_metrics(
        _policy_metrics_dataset(dataset, "candidate"),
        bootstrap_resamples=bootstrap_resamples,
        seed=seed,
    )
    categories = sorted({item["issue_category"] for case in cases for item in case["gold"]})
    scope = dataset.get("scope")
    if not isinstance(scope, dict):
        scope = {
            "task_families": sorted({str(case.get("task_family", "unspecified")) for case in cases}),
            "categories": categories,
            "modalities": sorted({str(case["modality"]) for case in cases}),
        }
    expected_scope = {
        "task_families": sorted({str(case.get("task_family", "unspecified")) for case in cases}),
        "categories": categories,
        "modalities": sorted({str(case["modality"]) for case in cases}),
    }
    if any(
        not isinstance(scope.get(field), list)
        or scope[field] != expected_scope[field]
        for field in expected_scope
    ):
        raise EvaluationError("policy scope task families, categories, and modalities must match the paired validation set")
    support: dict[str, Any] = {}
    metrics: dict[str, Any] = {}
    intervals: dict[str, Any] = {}
    sufficient = True
    gate_failures: list[str] = []
    for category in categories:
        baseline_values = baseline_metrics["per_category"][category]
        candidate_values = candidate_metrics["per_category"][category]
        category_assignments = {
            case["assignment_id"]
            for case in cases
            if any(item["issue_category"] == category for item in case["gold"])
        }
        support[category] = {
            "gold_positive_applicable_assessable": baseline_values["counts"]["gold_positive"],
            "applicable_assessable_units": baseline_values["counts"]["gold_units"],
            "baseline_predicted_findings": (
                baseline_values["counts"]["tp"] + baseline_values["counts"]["fp"]
            ),
            "candidate_predicted_findings": (
                candidate_values["counts"]["tp"] + candidate_values["counts"]["fp"]
            ),
            "independent_assignments": len(category_assignments),
        }
        metrics[category] = {
            "baseline": _metrics_by_category(baseline_metrics)[category],
            "candidate": _metrics_by_category(candidate_metrics)[category],
        }
        intervals[category] = {
            "baseline": _policy_intervals(baseline_metrics)[category],
            "candidate": _policy_intervals(candidate_metrics)[category],
        }
        counts = support[category]
        if (
            counts["gold_positive_applicable_assessable"] < 20
            or counts["baseline_predicted_findings"] < 20
            or counts["candidate_predicted_findings"] < 20
            or counts["independent_assignments"] < 5
        ):
            sufficient = False
    repeat_protocol = dataset.get("repeats", {})
    if not isinstance(repeat_protocol, dict):
        repeat_protocol = {}
    pending_liability_count = 0
    missing_usage_count = 0
    cost_by_arm: dict[str, Decimal | None] = {"baseline": Decimal("0"), "candidate": Decimal("0")}
    cost_complete = True
    ledger_refs: set[str] = set()
    for case in cases:
        usage_by_arm = case.get("usage_by_arm", {})
        for arm in ("baseline", "candidate"):
            usage = usage_by_arm.get(arm) if isinstance(usage_by_arm, dict) else None
            if not isinstance(usage, dict) or usage.get("billing_status") != "settled":
                cost_complete = False
                pending_liability_count += int(isinstance(usage, dict) and usage.get("billing_status") in {"pending", "unknown"})
                missing_usage_count += int(not isinstance(usage, dict))
                cost_by_arm[arm] = None
                continue
            _sha(usage.get("ledger_sha256"), f"{arm} usage ledger_sha256")
            ledger_refs.add(f"sha256:{usage['ledger_sha256']}")
            if cost_by_arm[arm] is not None:
                try:
                    amount = Decimal(str(usage.get("actual_cost_usd")))
                except InvalidOperation as exc:
                    raise EvaluationError("settled usage requires actual_cost_usd") from exc
                if not amount.is_finite() or amount < 0:
                    raise EvaluationError("settled actual costs must be finite non-negative decimals")
                cost_by_arm[arm] = (cost_by_arm[arm] or Decimal("0")) + amount
            if usage.get("cost_scope") != "end_to_end":
                cost_complete = False
    bundle_count = len({case["bundle_version"] for case in cases})
    if cost_complete and bundle_count:
        cost_by_arm = {arm: amount / bundle_count if amount is not None else None for arm, amount in cost_by_arm.items()}
    else:
        cost_by_arm = {"baseline": None, "candidate": None}
    critical_violations = sorted({
        str(item)
        for case in cases
        for arm_items in case.get("critical_invariant_violations_by_arm", {}).values()
        if isinstance(arm_items, list)
        for item in arm_items
    })
    explicit_support_failure = False
    for category in categories:
        baseline = metrics[category]["baseline"]
        candidate = metrics[category]["candidate"]
        values = (
            ("candidate precision", candidate["precision"], max(0.85, baseline["precision"] or 0.0), ">="),
            ("candidate evidence support", candidate["evidence_support_rate"], baseline["evidence_support_rate"], ">="),
            ("candidate recall", candidate["end_to_end_recall"], max(0.70, (baseline["end_to_end_recall"] or 0.0) - 0.02), ">="),
            ("candidate coverage", candidate["selective_decision_coverage"], (baseline["selective_decision_coverage"] or 0.0) - 0.02, ">="),
        )
        for label, actual, minimum, _operator in values:
            if actual is not None and minimum is not None and actual < minimum:
                gate_failures.append(f"{category}: {label} fails its predeclared minimum")
        if candidate["gold_unassessable_violations"] > baseline["gold_unassessable_violations"]:
            gate_failures.append(f"{category}: gold-unassessable decision violations increased")
        for arm in ("baseline", "candidate"):
            evidence = metrics[category][arm]
            if evidence["evidence_support_rate"] is None or evidence["precision"] is None:
                explicit_support_failure = True
    repeat_runs = repeat_protocol.get("runs", [])
    repeat_case_ids = repeat_protocol.get("preselected_case_ids", [])
    repeat_task_families = repeat_protocol.get("task_families", [])
    if not isinstance(repeat_task_families, list) or any(
        not isinstance(item, str) or not item for item in repeat_task_families
    ):
        repeat_task_families = []
    repeat_runs_valid = isinstance(repeat_runs, list) and len(repeat_runs) == 3
    repeat_ids_valid = (
        isinstance(repeat_case_ids, list)
        and bool(repeat_case_ids)
        and all(isinstance(item, str) and item for item in repeat_case_ids)
        and len(set(repeat_case_ids)) == len(repeat_case_ids)
        and set(repeat_case_ids).issubset({case["case_id"] for case in cases})
    )
    if repeat_runs_valid:
        repeat_ids: set[str] = set()
        repeat_clean = True
        for run in repeat_runs:
            if not isinstance(run, dict):
                repeat_runs_valid = False
                break
            run_id = run.get("run_id")
            if not isinstance(run_id, str) or not run_id or run_id in repeat_ids:
                repeat_runs_valid = False
                break
            repeat_ids.add(run_id)
            if run.get("case_ids") != repeat_case_ids:
                repeat_runs_valid = False
            if run.get("task_families") != repeat_task_families:
                repeat_runs_valid = False
            try:
                _sha(run.get("outputs_sha256"), "repeat outputs_sha256")
                _sha(run.get("subset_sha256"), "repeat subset_sha256")
            except EvaluationError:
                repeat_runs_valid = False
            violations = run.get("critical_invariant_violations")
            if not isinstance(violations, list) or violations:
                repeat_clean = False
    else:
        repeat_clean = False
    repeats_valid = (
        repeat_runs_valid
        and repeat_ids_valid
        and repeat_protocol.get("subset_preselected") is True
        and type(repeat_protocol.get("count")) is int
        and repeat_protocol["count"] == 3
        and isinstance(repeat_task_families, list)
        and set(repeat_task_families) == set(scope["task_families"])
        and repeat_clean
    )
    institutional_evidence = (
        data_mode == "institutional"
        and dataset.get("authorization_ref")
        and dataset.get("provider_mode") == "live"
        and dataset.get("partition") == "validation"
        and dataset.get("gold_adjudication_status") == "two_rater_adjudicated"
        and all(
            type(item.get("independent_rater_count")) is int
            and item["independent_rater_count"] >= 2
            and isinstance(item.get("adjudication_ref"), str)
            and item["adjudication_ref"].strip()
            for case in cases
            for item in case["gold"]
            if item["applicability"] == "applicable" and item["gold_assessability"] == "assessable"
        )
    )
    if critical_violations or (gate_failures and sufficient):
        status = "ineligible"
    elif (
        not sufficient
        or not cost_complete
        or not repeats_valid
        or critical_violations
        or explicit_support_failure
        or not institutional_evidence
        or not paired_manifest_compatible
        or not evidence_rating_complete
    ):
        status = "incomplete"
    elif cost_by_arm["baseline"] is None or cost_by_arm["candidate"] is None:
        status = "incomplete"
    elif cost_by_arm["baseline"] <= 0 or cost_by_arm["candidate"] > cost_by_arm["baseline"] * Decimal("0.90"):
        status = "ineligible"
        gate_failures.append("candidate actual mean cost does not meet the 10% reduction threshold")
    else:
        status = "eligible"
    reasons = list(gate_failures)
    if not sufficient:
        reasons.append("validation support is below the per-category minimum")
    if not cost_complete:
        reasons.append("settled end-to-end ledger cost is missing, out of scope, or liability remains pending/unknown")
    if missing_usage_count:
        reasons.append("one or more paired executions have no usage record")
    if not repeats_valid:
        reasons.append("three preselected task-family repeats with clean critical invariants are not evidenced")
    if critical_violations:
        reasons.append("one or more critical invariant violations are present")
    if not institutional_evidence:
        reasons.append("authorized live institutional validation with two-rater adjudication is unavailable")
    if not paired_manifest_compatible:
        reasons.append("paired run manifest hashes are missing or unverified")
    if not evidence_rating_complete:
        reasons.append("one or more findings lack a codebook-bound competent human evidence rating")
    for category in categories:
        for arm in ("baseline", "candidate"):
            if metrics[category][arm]["evidence_support_rate"] is None:
                reasons.append(f"{category}/{arm}: evidence support has no human-rated denominator")
    manifest_hashes = {kind: canonical_sha256(value) for kind, value in sorted(candidate_methods.items())}
    candidate_core_hash = _policy_core_hash(candidate_methods, release)
    baseline_core_hash = _policy_core_hash(base_methods, release)
    report: dict[str, Any] = {
        "schema_version": 2,
        "report_type": "align_policy_validation_report",
        "status": status,
        "provider_mode": dataset.get("provider_mode", "not_recorded"),
        "environment": _environment_metadata(root),
        "data_mode": data_mode,
        "data_origin": data_mode,
        "candidate_id": candidate_id,
        "baseline_id": baseline_id,
        "candidate_manifest_path": candidate_path,
        "baseline_manifest_path": baseline_path,
        "candidate_core_hash": candidate_core_hash,
        "baseline_core_hash": baseline_core_hash,
        "dataset_sha256": canonical_sha256(dataset),
        "protocol_sha256": _sha(dataset.get("protocol_sha256"), "protocol_sha256"),
        "codebook_sha256": dataset["codebook_sha256"],
        "split_sha256": _sha(dataset.get("split_sha256"), "split_sha256"),
        "sample_count": len(cases),
        "scope": scope,
        "compatibility": {
            "method_manifest_sha256": manifest_hashes,
            "parser_resource_sha256": candidate_methods.get("parser_resource", {}).get("resource_manifest_sha256"),
            "grounding_registry_version": release.get("grounding_registry_version"),
            "lint_registry_version": release.get("lint_registry_version"),
            "paired_manifest_compatible": paired_manifest_compatible,
        },
        "support_by_category": support,
        "metrics_by_category": metrics,
        "confidence_intervals_by_category": intervals,
        "actual_cost_usd_per_bundle": {
            arm: str(value) if value is not None else None for arm, value in cost_by_arm.items()
        },
        "pending_liability_count": pending_liability_count,
        "missing_usage_record_count": missing_usage_count,
        "evidence_rating_complete": evidence_rating_complete,
        "repeats": {
            "count": len(repeat_runs) if isinstance(repeat_runs, list) else 0,
            "subset_preselected": repeat_protocol.get("subset_preselected", False),
            "task_families": repeat_protocol.get("task_families", []),
            "preselected_case_ids": repeat_case_ids if isinstance(repeat_case_ids, list) else [],
            "runs": repeat_runs if isinstance(repeat_runs, list) else [],
            "critical_invariant_violations": len(critical_violations),
        },
        "critical_invariant_violations": critical_violations,
        "ledger_refs": sorted(ledger_refs),
        "allowed_data_modes": ["synthetic"] if data_mode == "synthetic" else ["institutional"],
        "evaluation_kind": "empirical" if institutional_evidence else "software_gate",
        "bootstrap": {
            "method": "assignment_cluster_bootstrap_stratified_by_course",
            "resamples": bootstrap_resamples,
            "seed": seed,
            "independent_assignment_count": len({case["assignment_id"] for case in cases}),
        },
        "eligibility_reasons": sorted(set(reasons)),
    }
    report["report_sha256"] = canonical_sha256(report)
    return report


def write_reports(report: dict[str, Any], output: Path, *, markdown_lines: list[str] | None = None) -> tuple[Path, Path]:
    json_path = output if output.suffix.lower() == ".json" else output.with_suffix(".json")
    markdown_path = json_path.with_suffix(".md")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    lines = markdown_lines or [
        f"# {report.get('report_type', 'ALIGN offline report')}",
        "",
        f"- Status: **{report.get('status')}**",
        f"- Mode/data origin: `{report.get('provider_mode')}` / `{report.get('data_origin')}`",
        f"- Dataset SHA-256: `{report.get('dataset_sha256')}`",
        f"- Report SHA-256: `{report.get('report_sha256', canonical_sha256(report))}`",
        "",
        "## Limitations",
        "",
        *[f"- {item}" for item in report.get("limitations", report.get("eligibility_reasons", []))],
    ]
    json_path.write_text(body, encoding="utf-8")
    markdown_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return json_path, markdown_path


def evaluate_metrics_file(
    dataset_path: Path,
    output: Path,
    *,
    bootstrap_resamples: int = 10_000,
    seed: int = 20261018,
) -> dict[str, Any]:
    dataset = _load_json(dataset_path, "metrics dataset")
    report = evaluate_metrics(dataset, bootstrap_resamples=bootstrap_resamples, seed=seed)
    report["dataset_file_sha256"] = file_sha256(dataset_path)
    report["report_sha256"] = canonical_sha256({key: value for key, value in report.items() if key != "report_sha256"})
    write_reports(report, output)
    return report


def evaluate_policy_file(
    dataset_path: Path,
    output: Path,
    *,
    candidate_id: str,
    baseline_id: str,
    root: Path,
    bootstrap_resamples: int = 10_000,
    seed: int = 20261018,
    ledger_verified: bool = False,
) -> dict[str, Any]:
    dataset = _load_json(dataset_path, "policy evaluation dataset")
    report = evaluate_policy_protocol(
        dataset,
        candidate_id=candidate_id,
        baseline_id=baseline_id,
        root=root,
        bootstrap_resamples=bootstrap_resamples,
        seed=seed,
    )
    if report["status"] == "eligible" and not ledger_verified:
        report["status"] = "incomplete"
        report["eligibility_reasons"] = sorted({
            *report["eligibility_reasons"],
            "application usage ledger references were not verified against settled database entries",
        })
    report["dataset_file_sha256"] = file_sha256(dataset_path)
    report["report_sha256"] = canonical_sha256({key: value for key, value in report.items() if key != "report_sha256"})
    lines = [
        "# ALIGN Policy Validation Report",
        "",
        f"- Status: **{report['status']}**",
        f"- Provider/data: `{report['provider_mode']}` / `{report['data_mode']}`",
        f"- Candidate/baseline: `{candidate_id}` / `{baseline_id}`",
        f"- Dataset SHA-256: `{report['dataset_sha256']}`",
        f"- Candidate/baseline core SHA-256: `{report['candidate_core_hash']}` / `{report['baseline_core_hash']}`",
        "",
        "## Per-category gates",
        "",
        "| Category | Baseline precision / recall / coverage / ESR | Candidate precision / recall / coverage / ESR | Support |",
        "|---|---|---|---|",
    ]
    for category, arms in report["metrics_by_category"].items():
        baseline = arms["baseline"]
        candidate = arms["candidate"]
        supported = report["support_by_category"][category]
        lines.append(
            f"| {category} | {baseline['precision']} / {baseline['end_to_end_recall']} / "
            f"{baseline['selective_decision_coverage']} / {baseline['evidence_support_rate']} | "
            f"{candidate['precision']} / {candidate['end_to_end_recall']} / "
            f"{candidate['selective_decision_coverage']} / {candidate['evidence_support_rate']} | "
            f"positives={supported['gold_positive_applicable_assessable']}, "
            f"assignments={supported['independent_assignments']} |"
        )
    lines.extend(["", "## Eligibility limits", "", *[f"- {reason}" for reason in report["eligibility_reasons"]]])
    write_reports(report, output, markdown_lines=lines)
    return report

