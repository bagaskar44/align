"""Offline B7 compiler and executor robustness protocol validation.

This runner checks release binding, data splits, reviewer metadata, compiler
structure and DAG ordering. It does not claim semantic equivalence or run an
AI/provider executor; those remain explicit report limitations.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from align_domain.assignment_contracts import SpecificationPayload, compile_lint
from align_domain.executor import resolve_applicability, topological_order


class ProtocolError(ValueError):
    """Raised when a robustness protocol is unsafe or not reproducible."""


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _environment_metadata() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[4]
    memory_bytes: int | None = None
    try:
        memory_line = next(
            line for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines()
            if line.startswith("MemTotal:")
        )
        memory_bytes = int(memory_line.split()[1]) * 1024
    except (OSError, StopIteration, ValueError, IndexError):
        try:
            memory_bytes = int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
        except (OSError, ValueError, TypeError):
            pass
    try:
        api_version = importlib.metadata.version("align-api")
    except importlib.metadata.PackageNotFoundError:
        api_version = "workspace-source"
    return {
        "python": sys.version.split()[0],
        "align_api": api_version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "host_or_vm_memory_bytes": memory_bytes,
        "uv_lock_sha256": hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest()
        if (root / "uv.lock").is_file() else None,
        "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ProtocolError(f"{label} must be a lowercase SHA-256")
    return value


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"cannot read {label} JSON") from exc
    if not isinstance(payload, dict):
        raise ProtocolError(f"{label} must be a JSON object")
    return payload


def _specification(payload: Any, label: str) -> SpecificationPayload:
    try:
        return SpecificationPayload.model_validate_json(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        )
    except (ValidationError, TypeError, ValueError) as exc:
        raise ProtocolError(f"{label} has an invalid specification or DAG") from exc


def _obligation_signature(spec: SpecificationPayload) -> list[dict[str, Any]]:
    """Canonical structural obligations; this is not a semantic-equivalence score."""
    return sorted(
        [
            {
                "obligation_key": element.obligation_key,
                "target_kind": element.target_kind,
                "applicability": element.applicability.model_dump(mode="json"),
                "accepted_evidence_types": sorted(element.accepted_evidence_types),
                "derivation_refs": sorted(
                    [item.model_dump(mode="json") for item in element.derivation_refs],
                    key=lambda item: json.dumps(item, sort_keys=True),
                ),
                "relationships": sorted(
                    [item.model_dump(mode="json") for item in element.relationships],
                    key=lambda item: json.dumps(item, sort_keys=True),
                ),
                "dependencies": sorted(str(item) for item in element.dependencies),
            }
            for element in spec.elements
        ],
        key=lambda item: item["obligation_key"],
    )


def _run_applicability(spec: SpecificationPayload) -> list[dict[str, str | None]]:
    results: list[dict[str, str | None]] = []
    for element in spec.elements:
        status, reason = resolve_applicability(element, {})
        results.append({"element_id": str(element.element_id), "status": status, "reason_code": reason})
    return results


def validate_protocol(
    protocol: dict[str, Any], dataset: dict[str, Any], release: dict[str, Any]
) -> dict[str, Any]:
    if protocol.get("schema_version") != 1 or dataset.get("schema_version") != 1:
        raise ProtocolError("protocol and dataset manifest must use schema_version 1")
    if not protocol.get("protocol_id") or not protocol.get("transformation_id"):
        raise ProtocolError("protocol_id and transformation_id are required")
    method = protocol.get("method")
    if method not in {"compiler", "executor"}:
        raise ProtocolError("method must be compiler or executor")
    partition = protocol.get("partition")
    if partition not in {"development", "validation"}:
        raise ProtocolError("robustness runs are limited to development or validation; frozen test cannot be tuned")
    data_mode = dataset.get("data_mode")
    if data_mode not in {"synthetic", "institutional"}:
        raise ProtocolError("dataset data_mode must be synthetic or institutional")
    if data_mode == "institutional" and dataset.get("authorization_ref") is None:
        raise ProtocolError("institutional dataset requires an authorization_ref")
    if protocol.get("release_id") != release.get("id"):
        raise ProtocolError("protocol release_id does not match the requested immutable release")
    if protocol.get("release_config_hash") != release.get("config_hash"):
        raise ProtocolError("protocol release_config_hash does not match the registered release")
    exact_hashes = protocol.get("exact_manifest_hashes")
    release_hashes = release.get("method_manifest_sha256")
    if not isinstance(exact_hashes, dict) or exact_hashes != release_hashes:
        raise ProtocolError("exact_manifest_hashes do not match the immutable release")
    for kind, digest in exact_hashes.items():
        _sha256(digest, f"manifest {kind}")
    if protocol.get("seed") is None or type(protocol.get("seed")) is not int:
        raise ProtocolError("protocol seed must be an integer")
    repeats = protocol.get("repeat_count")
    if type(repeats) is not int or not 1 <= repeats <= 20:
        raise ProtocolError("repeat_count must be an integer from 1 to 20")
    scenario_fields = (
        "executor_repeat_scenarios",
        "format_variation_scenarios",
        "source_corruption_scenarios",
        "repeat_dependence_scenarios",
    )
    declared_scenarios: dict[str, list[dict[str, Any]]] = {}
    for field in scenario_fields:
        scenarios = protocol.get(field, [])
        if not isinstance(scenarios, list) or any(not isinstance(item, dict) for item in scenarios):
            raise ProtocolError(f"{field} must be a list of scenario objects")
        declared_scenarios[field] = scenarios

    samples = dataset.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ProtocolError("dataset manifest needs samples")
    sample_by_id: dict[str, dict[str, Any]] = {}
    group_partitions: dict[str, str] = {}
    for sample in samples:
        if not isinstance(sample, dict) or not isinstance(sample.get("case_id"), str):
            raise ProtocolError("dataset samples need unique case_id values")
        case_id = sample["case_id"]
        if case_id in sample_by_id:
            raise ProtocolError("dataset case_id values must be unique")
        _sha256(sample.get("source_sha256"), f"dataset source hash for {case_id}")
        sample_partition = sample.get("partition")
        if sample_partition not in {"development", "validation", "frozen_test"}:
            raise ProtocolError("dataset sample has an unknown partition")
        groups = sample.get("group_keys", [])
        if not isinstance(groups, list) or any(not isinstance(group, str) or not group for group in groups):
            raise ProtocolError("group_keys must be nonblank strings")
        for group in groups:
            previous = group_partitions.setdefault(group, sample_partition)
            if previous != sample_partition:
                raise ProtocolError("dataset group leakage crosses partitions")
        sample_by_id[case_id] = sample
    dataset_hash = _canonical_hash(dataset)
    if protocol.get("dataset_manifest_sha256") != dataset_hash:
        raise ProtocolError("dataset_manifest_sha256 does not match the exact dataset manifest")

    cases = protocol.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ProtocolError("protocol needs at least one case")
    case_by_id: dict[str, dict[str, Any]] = {}
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("case_id"), str):
            raise ProtocolError("protocol cases need case_id")
        case_id = case["case_id"]
        if case_id in case_by_id:
            raise ProtocolError("protocol case_id values must be unique")
        sample = sample_by_id.get(case_id)
        if sample is None:
            raise ProtocolError(f"protocol case {case_id} is absent from the dataset manifest")
        if case.get("partition") != sample["partition"] or case.get("partition") != partition:
            raise ProtocolError("case partition does not match both the manifest and selected run partition")
        if case.get("source_sha256") != sample["source_sha256"]:
            raise ProtocolError("case source hash does not match the dataset manifest")
        equivalence = case.get("equivalence")
        if not isinstance(equivalence, dict) or equivalence.get("status") not in {"baseline", "equivalent", "not_equivalent"}:
            raise ProtocolError("human equivalence status must be resolved before evaluation")
        if equivalence["status"] != "baseline":
            reviewer = equivalence.get("reviewer")
            if not isinstance(reviewer, dict) or reviewer.get("kind") not in {"human", "fixture"}:
                raise ProtocolError("equivalence reviewer must be an identified human or labelled synthetic fixture")
            if not reviewer.get("actor_id") or not reviewer.get("decision_ref"):
                raise ProtocolError("equivalence reviewer actor_id and decision_ref are required")
            if reviewer["kind"] == "fixture" and data_mode != "synthetic":
                raise ProtocolError("fixture reviewers are allowed only for a synthetic software gate")
        case_by_id[case_id] = case
    for _case_id, case in case_by_id.items():
        parent_id = case.get("parent_case_id")
        if parent_id is not None:
            parent_sample = sample_by_id.get(parent_id)
            if parent_sample is None or parent_sample["partition"] != case["partition"]:
                raise ProtocolError("variant parent is missing or crosses a partition")
            if parent_id not in case_by_id:
                raise ProtocolError("variant parent must have a protocol case")

    compiled: dict[str, dict[str, Any]] = {}
    signatures: dict[str, list[dict[str, Any]]] = {}
    topological_orders: dict[str, list[str]] = {}
    applicability_results: dict[str, list[dict[str, str | None]]] = {}
    applicability_repeats: dict[str, dict[str, Any]] = {}
    for case_id, case in case_by_id.items():
        spec = _specification(case.get("specification"), f"case {case_id}")
        spec_hash = _canonical_hash(spec.model_dump(mode="json"))
        lint = compile_lint(
            spec,
            revision=1,
            spec_hash=spec_hash,
            package_hashes={"source": str(case["source_sha256"])},
        )
        signatures[case_id] = _obligation_signature(spec)
        expected_invariant = case.get("expected_invariant")
        if not isinstance(expected_invariant, dict) or expected_invariant.get("obligations") != signatures[case_id]:
            raise ProtocolError(f"case {case_id} expected_invariant.obligations does not match the typed specification")
        compiled[case_id] = {
            "specification_sha256": spec_hash,
            "lint_initial_status": lint["initial_status"],
            "lint_issue_codes": sorted(item["rule_code"] for item in lint["issues"]),
            "obligation_count": len(spec.elements),
        }
        ordered = topological_order(spec)
        topological_orders[case_id] = [str(item.element_id) for item in ordered]
        applicability_results[case_id] = _run_applicability(spec)
        repeated_results = [_run_applicability(spec) for _ in range(repeats)]
        applicability_repeats[case_id] = {
            "runs": repeats,
            "stable_output": all(item == repeated_results[0] for item in repeated_results[1:]),
            "scope": "deterministic applicability rule replay only",
        }
        if method == "executor":
            provided_order = case.get("element_order")
            expected_ids = {str(item.element_id) for item in spec.elements}
            if not isinstance(provided_order, list) or len(provided_order) != len(expected_ids) or set(provided_order) != expected_ids:
                raise ProtocolError(f"executor case {case_id} element_order must contain each specification element exactly once")
            position = {element_id: index for index, element_id in enumerate(provided_order)}
            by_id = {str(item.element_id): item for item in spec.elements}
            if any(position[str(parent)] >= position[element_id] for element_id, item in by_id.items() for parent in item.dependencies):
                raise ProtocolError(f"executor case {case_id} element_order violates dependency DAG")

    results: list[dict[str, Any]] = []
    for case_id, case in case_by_id.items():
        parent_id = case.get("parent_case_id")
        equivalence_status = case["equivalence"]["status"]
        equivalent = equivalence_status == "equivalent"
        if equivalence_status == "baseline":
            if parent_id is not None:
                raise ProtocolError("baseline case cannot have a parent")
            results.append({"case_id": case_id, "parent_case_id": None,
                "case_class": "baseline", "compiler": compiled[case_id],
                "deterministic_applicability": applicability_results[case_id],
                "executor_runtime": "not_run"})
            continue
        if parent_id is None:
            raise ProtocolError("equivalent and negative fidelity cases require a parent case")
        if equivalent:
            structurally_equal = signatures[case_id] == signatures[str(parent_id)]
            results.append({
                "case_id": case_id,
                "parent_case_id": parent_id,
                "case_class": "reviewer-labelled-equivalent",
                "reviewer_kind": case["equivalence"]["reviewer"]["kind"],
                "structural_obligations_match": structurally_equal,
                "expected_invariant_match": case["expected_invariant"]["obligations"] == signatures[case_id],
                "parent_compiler": compiled[str(parent_id)],
                "variant_compiler": compiled[case_id],
                "order_invariant": topological_orders[case_id] == topological_orders[str(parent_id)],
                "permutation_invariant": applicability_results[case_id] == applicability_results[str(parent_id)],
                "deterministic_applicability": applicability_results[case_id],
                "repeat_stability": applicability_repeats[case_id],
                "executor_runtime": "not_run",
            })
        else:
            changed = sorted({item["obligation_key"] for item in signatures[case_id]} ^ {item["obligation_key"] for item in signatures[str(parent_id)]})
            changed_keys = case.get("expected_changed_obligation_keys")
            if not isinstance(changed_keys, list) or not changed_keys or any(
                not isinstance(item, str) or not item for item in changed_keys
            ):
                raise ProtocolError("negative fidelity case must state expected_changed_obligation_keys")
            declared = set(changed_keys)
            if not declared <= set(changed):
                raise ProtocolError(f"negative fidelity case {case_id} does not change its declared obligation keys")
            results.append({
                "case_id": case_id,
                "parent_case_id": parent_id,
                "case_class": "reviewer-labelled-negative-fidelity",
                "reviewer_kind": case["equivalence"]["reviewer"]["kind"],
                "expected_changed_obligation_keys": sorted(declared),
                "observed_changed_obligation_keys": changed,
                "fidelity_change_present": True,
                "parent_compiler": compiled[str(parent_id)] if parent_id else None,
                "variant_compiler": compiled[case_id],
                "executor_runtime": "not_run",
            })

    status = "incomplete" if repeats < 3 or method == "executor" else "software_gate_only"
    robustness_dimensions = {
        "compiler_fidelity": {"status": "checked", "case_count": len(results)},
        "executor_variation": {
            "status": "declared_not_run" if declared_scenarios["executor_repeat_scenarios"] else "not_run",
            "declared_scenario_count": len(declared_scenarios["executor_repeat_scenarios"]),
            "runtime_cases": 0,
        },
        "format_variation": {
            "status": "declared_not_run" if declared_scenarios["format_variation_scenarios"] else "not_run",
            "declared_scenario_count": len(declared_scenarios["format_variation_scenarios"]),
            "runtime_cases": 0,
        },
        "source_corruption": {
            "status": "declared_not_run" if declared_scenarios["source_corruption_scenarios"] else "not_run",
            "declared_scenario_count": len(declared_scenarios["source_corruption_scenarios"]),
            "runtime_cases": 0,
        },
        "repeat_dependence": {
            "status": "deterministic_applicability_only",
            "deterministic_rule_repeats": repeats,
            "declared_scenario_count": len(declared_scenarios["repeat_dependence_scenarios"]),
            "stochastic_runtime_repeats": 0,
        },
    }
    return {
        "schema_version": 1,
        "report_type": "align_robustness_protocol_report",
        "protocol_id": protocol["protocol_id"],
        "transformation_id": protocol["transformation_id"],
        "method": method,
        "partition": partition,
        "data_mode": data_mode,
        "data_origin": "synthetic_fixture" if data_mode == "synthetic" else "institutional_protocol",
        "provider_mode": "not_used",
        "provider_calls": 0,
        "provider_actual_cost_usd": "0.00",
        "infrastructure_cost": {"amount": None, "currency": None, "status": "not_metered"},
        "environment": _environment_metadata(),
        "evidence_class": "synthetic_fixture_software_gate" if data_mode == "synthetic" else "institutional_protocol_only",
        "status": status,
        "release_id": release["id"],
        "release_config_hash": release["config_hash"],
        "exact_manifest_hashes": exact_hashes,
        "dataset_manifest_sha256": dataset_hash,
        "protocol_sha256": _canonical_hash(protocol),
        "seed": protocol["seed"],
        "repeat_count": repeats,
        "cases": results,
        "robustness_dimensions": robustness_dimensions,
        "limitations": [
            "Structural obligation comparison is not a human semantic-equivalence verdict.",
            "No provider call, evidence-level executor decision, stochastic stability, cost, or research metric was run.",
            "No source-corruption or file-format variation scenario was executed by this CLI run.",
            "Repeat count covers deterministic applicability replay only, not executor or source-level repeat dependence.",
            "Fixture reviewer labels are synthetic test metadata, not human study adjudication.",
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# ALIGN robustness protocol report",
        "",
        f"- Status: `{report['status']}`",
        f"- Method: `{report['method']}`",
        f"- Partition: `{report['partition']}`",
        f"- Evidence class: `{report['evidence_class']}`",
        f"- Provider/cost: `{report['provider_mode']}` / `{report['provider_actual_cost_usd']} USD`; infrastructure cost `{report['infrastructure_cost']['status']}`",
        f"- Environment: `{report['environment']['platform']}` / `{report['environment']['machine']}` / Python `{report['environment']['python']}` / {report['environment']['cpu_count']} CPUs",
        f"- Protocol: `{report['protocol_id']}` / `{report['transformation_id']}`",
        f"- Release: `{report['release_id']}` (`{report['release_config_hash']}`)",
        f"- Dataset SHA-256: `{report['dataset_manifest_sha256']}`",
        f"- Repeats: {report['repeat_count']}",
        "",
        "## Cases",
        "",
        "| Case | Class | Structural obligations | Executor runtime |",
        "|---|---|---|---|",
    ]
    for case in report["cases"]:
        structural = case.get("structural_obligations_match", case.get("fidelity_change_present", "not compared"))
        lines.append(f"| `{case['case_id']}` | {case['case_class']} | {structural} | {case['executor_runtime']} |")
    lines.extend([
        "",
        "## Robustness dimensions",
        "",
        "| Dimension | Status | Evidence / count |",
        "|---|---|---|",
    ])
    for name, dimension in report["robustness_dimensions"].items():
        details = ", ".join(f"{key}={value}" for key, value in dimension.items() if key != "status") or "none"
        lines.append(f"| `{name}` | `{dimension['status']}` | {details} |")
    lines.extend(["", "## Limitations", "", *[f"- {item}" for item in report["limitations"]], ""])
    return "\n".join(lines)


def evaluate_robustness_files(
    protocol_path: Path, dataset_path: Path, output_path: Path, release: dict[str, Any]
) -> dict[str, Any]:
    if output_path.resolve() in {protocol_path.resolve(), dataset_path.resolve()}:
        raise ProtocolError("output path must not overwrite protocol or dataset inputs")
    protocol = _load_json(protocol_path, "protocol")
    dataset = _load_json(dataset_path, "dataset manifest")
    report = validate_protocol(protocol, dataset, release)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n" if output_path.suffix.lower() == ".json" else render_markdown(report)
    output_path.write_text(rendered, encoding="utf-8")
    return report


def parse_release_id(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise ProtocolError("release must be a registered UUID") from exc

