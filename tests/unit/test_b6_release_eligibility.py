from __future__ import annotations

import json
from pathlib import Path

import pytest

from align_api.cli import (
    _requires_policy_validation_report,
    require_release_validation_report,
    sha256_json,
    validate_live_model_release,
    validate_live_synthetic_smoke_manifest,
    validate_policy_validation_report,
    validate_release_data_mode,
)
from align_api.models import PolicyValidationReport

ROOT = Path(__file__).parents[2]


def _baseline() -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    path = ROOT / "config" / "release.baseline.json"
    release = json.loads(path.read_text(encoding="utf-8"))
    manifests = {
        key: json.loads((path.parent / value).read_text(encoding="utf-8"))
        for key, value in release["manifests"].items()
    }
    return release, manifests


def _report(release: dict[str, object], manifests: dict[str, dict[str, object]]) -> dict[str, object]:
    core = sha256_json({
        "methods": {kind: sha256_json(value) for kind, value in sorted(manifests.items())},
        "grounding_registry_version": release["grounding_registry_version"],
        "lint_registry_version": release["lint_registry_version"],
    })
    report: dict[str, object] = {
        "schema_version": 2,
        "status": "eligible",
        "provider_mode": "mock",
        "data_mode": "synthetic",
        "data_origin": "synthetic",
        "allowed_data_modes": ["synthetic"],
        "candidate_core_hash": core,
        "baseline_core_hash": "a" * 64,
        "dataset_sha256": "b" * 64,
        "protocol_sha256": "c" * 64,
        "codebook_sha256": "d" * 64,
        "split_sha256": "e" * 64,
        "sample_count": 120,
        "scope": {"task_families": ["numeric"], "categories": ["numeric_inconsistency"], "modalities": ["spreadsheet"]},
        "compatibility": {
            "method_manifest_sha256": {kind: sha256_json(value) for kind, value in sorted(manifests.items())},
            "parser_resource_sha256": manifests["parser_resource"]["resource_manifest_sha256"],
            "grounding_registry_version": release["grounding_registry_version"],
            "lint_registry_version": release["lint_registry_version"],
        },
        "support_by_category": {"numeric_inconsistency": {
            "gold_positive_applicable_assessable": 25,
            "applicable_assessable_units": 40,
            "baseline_predicted_findings": 25,
            "candidate_predicted_findings": 25,
            "independent_assignments": 5,
        }},
        "metrics_by_category": {"numeric_inconsistency": {
            "baseline": {"tp": 20, "fp": 5, "fn": 5, "evidence_supported_findings": 25, "decisions": 30, "gold_unassessable_violations": 1},
            "candidate": {"tp": 22, "fp": 3, "fn": 3, "evidence_supported_findings": 25, "decisions": 32, "gold_unassessable_violations": 1},
        }},
        "confidence_intervals_by_category": {"numeric_inconsistency": {
            "baseline": {
                "precision": ["0.75", "0.85"],
                "evidence_support_rate": ["0.95", "1.0"],
                "end_to_end_recall": ["0.75", "0.85"],
                "selective_decision_coverage": ["0.70", "0.80"],
            },
            "candidate": {
                "precision": ["0.83", "0.93"],
                "evidence_support_rate": ["0.95", "1.0"],
                "end_to_end_recall": ["0.83", "0.93"],
                "selective_decision_coverage": ["0.75", "0.85"],
            },
        }},
        "actual_cost_usd_per_bundle": {"baseline": "0.20", "candidate": "0.17"},
        "pending_liability_count": 0,
        "repeats": {"count": 3, "task_families": ["numeric"], "subset_preselected": True, "critical_invariant_violations": 0},
        "critical_invariant_violations": [],
        "ledger_refs": ["sha256:" + "f" * 64],
        "evaluation_kind": "empirical",
    }
    report["report_sha256"] = sha256_json(report)
    return report


def test_release_pins_resource_and_rates_without_claiming_them_verified() -> None:
    release, manifests = _baseline()
    assert "parser_resource" in manifests
    assert "rates" in manifests
    assert manifests["rates"]["status"] == "unverified"
    assert manifests["rates"]["rates"] is None
    resource = ROOT / "config" / manifests["parser_resource"]["resource_manifest"]
    assert resource.is_file()
    import hashlib

    assert hashlib.sha256(resource.read_bytes()).hexdigest() == manifests["parser_resource"]["resource_manifest_sha256"]
    assert manifests["batching"]["maximum_elements"] == 1
    assert manifests["routing"]["semantic_batch_size"] == 1
    core = sha256_json({
        "methods": {kind: sha256_json(value) for kind, value in sorted(manifests.items())},
        "grounding_registry_version": release["grounding_registry_version"],
        "lint_registry_version": release["lint_registry_version"],
    })
    require_release_validation_report(
        release_payload=release,
        routing=manifests["routing"],
        batching=manifests["batching"],
        candidate_core_hash=core,
        report=None,
    )


def test_candidate_release_requires_exact_eligible_report_and_core_hash() -> None:
    release, manifests = _baseline()
    candidate = {**release, "provider_mode": "mock", "data_mode": "synthetic"}
    report_payload = _report(candidate, manifests)
    validate_policy_validation_report(report_payload, candidate, manifests)
    report = PolicyValidationReport(
        report_hash=sha256_json(report_payload),
        status="eligible",
        data_mode="synthetic",
        candidate_core_hash=str(report_payload["candidate_core_hash"]),
        baseline_core_hash=str(report_payload["baseline_core_hash"]),
        report_json=report_payload,
    )
    candidate_batching = {**manifests["batching"], "maximum_elements": 3}
    require_release_validation_report(
        release_payload=candidate,
        routing=manifests["routing"],
        batching=candidate_batching,
        candidate_core_hash=str(report_payload["candidate_core_hash"]),
        report=report,
    )
    with pytest.raises(SystemExit, match="eligible immutable"):
        require_release_validation_report(
            release_payload=candidate,
            routing=manifests["routing"],
            batching=candidate_batching,
            candidate_core_hash=str(report_payload["candidate_core_hash"]),
            report=None,
        )
    with pytest.raises(SystemExit, match="core hash"):
        require_release_validation_report(
            release_payload=candidate,
            routing=manifests["routing"],
            batching=candidate_batching,
            candidate_core_hash="0" * 64,
            report=report,
        )


def test_only_mock_or_live_synthetic_baseline_smoke_skips_research_report() -> None:
    release, manifests = _baseline()
    live_routing = {**manifests["routing"], "allow_live": True}
    batching = manifests["batching"]
    live_model = {"capability_smoke": True}
    smoke_release = {
        **release,
        "provider_mode": "live",
        "data_mode": "synthetic",
        "activation_checks": {"capability_smoke": True, "empirical_verification": False},
    }

    assert not _requires_policy_validation_report(live_routing, batching)
    require_release_validation_report(
        release_payload=smoke_release,
        routing=live_routing,
        batching=batching,
        candidate_core_hash="0" * 64,
        report=None,
        model=live_model,
    )
    with pytest.raises(SystemExit, match="eligible immutable"):
        require_release_validation_report(
            release_payload={**smoke_release, "activation_checks": {}},
            routing=live_routing,
            batching=batching,
            candidate_core_hash="0" * 64,
            report=None,
            model=live_model,
        )
    with pytest.raises(SystemExit, match="eligible immutable"):
        require_release_validation_report(
            release_payload={**release, "provider_mode": "live", "data_mode": "institutional"},
            routing=live_routing,
            batching=batching,
            candidate_core_hash="0" * 64,
            report=None,
        )


def test_only_development_synthetic_demo_can_register_live_synthetic_release() -> None:
    settings = type("Settings", (), {
        "app_env": "development", "data_mode": "synthetic", "app_demo_mode": True,
    })()
    validate_release_data_mode("live", "synthetic", settings)

    for invalid in (
        type("Settings", (), {
            "app_env": "production", "data_mode": "synthetic", "app_demo_mode": True,
        })(),
        type("Settings", (), {
            "app_env": "development", "data_mode": "institutional", "app_demo_mode": True,
        })(),
        type("Settings", (), {
            "app_env": "development", "data_mode": "synthetic", "app_demo_mode": False,
        })(),
    ):
        with pytest.raises(SystemExit, match="explicit development demo stack"):
            validate_release_data_mode("live", "synthetic", invalid)


def test_live_synthetic_smoke_cannot_enable_policy_optimizations() -> None:
    release, manifests = _baseline()
    live_routing = {**manifests["routing"], "allow_live": True}
    optimized_routing = {
        **live_routing,
        "semantic_routes": {"interpret_evidence": "efficient", "verify_claim": "complex"},
    }
    assert _requires_policy_validation_report(optimized_routing, manifests["batching"])
    with pytest.raises(SystemExit, match="eligible immutable"):
        require_release_validation_report(
            release_payload={**release, "provider_mode": "live", "data_mode": "synthetic"},
            routing=optimized_routing,
            batching=manifests["batching"],
            candidate_core_hash="0" * 64,
            report=None,
        )
    validate_live_synthetic_smoke_manifest(
        live_routing,
        manifests["batching"],
        {"baseline_batch_size": 1, "capability_smoke": True, "empirical_verification": False},
        {"capability_smoke": True},
    )
    with pytest.raises(SystemExit, match="baseline-only"):
        validate_live_synthetic_smoke_manifest(
            optimized_routing,
            manifests["batching"],
            {"baseline_batch_size": 1, "capability_smoke": True, "empirical_verification": False},
            {"capability_smoke": True},
        )
    with pytest.raises(SystemExit, match="baseline-only"):
        validate_live_synthetic_smoke_manifest(
            live_routing,
            manifests["batching"],
            {"baseline_batch_size": 1, "capability_smoke": True, "empirical_verification": True},
            {"capability_smoke": True},
        )


def test_live_release_requires_exact_pinned_role_models_and_rate_coverage() -> None:
    settings = type("Settings", (), {
        "model_efficient": "text-e", "model_complex": "text-c",
        "model_vision": "vision-v", "embedding_model": "embed-x",
    })()
    model = {
        "provider_mode": "live", "paid_calls": True,
        "live_model_ids": {
            "efficient": "text-e", "complex": "text-c",
            "vision": "vision-v", "embedding": "embed-x",
        },
    }
    rates = {"models": {name: {} for name in model["live_model_ids"].values()}}
    validate_live_model_release(model, rates, settings)

    mismatched = {**model, "live_model_ids": {**model["live_model_ids"], "complex": "other"}}
    with pytest.raises(SystemExit, match="pin current role model IDs"):
        validate_live_model_release(mismatched, rates, settings)
    with pytest.raises(SystemExit, match="pin current role model IDs"):
        validate_live_model_release(model, {"models": {"text-e": {}}}, settings)


def test_report_rejects_synthetic_data_claimed_as_institutional() -> None:
    release, manifests = _baseline()
    report = _report(release, manifests)
    report["data_mode"] = "institutional"
    report["data_origin"] = "institutional"
    report["allowed_data_modes"] = ["synthetic"]
    report["institutional_authorization_ref"] = "institutional-approval:123"
    with pytest.raises(SystemExit, match="mock policy reports can only use synthetic data"):
        validate_policy_validation_report(report, release, manifests)


def test_report_rejects_candidate_that_fails_precision_cost_or_exact_compatibility() -> None:
    release, manifests = _baseline()
    report = _report(release, manifests)
    report["metrics_by_category"]["numeric_inconsistency"]["candidate"]["tp"] = 20
    report["metrics_by_category"]["numeric_inconsistency"]["candidate"]["fp"] = 5
    report["metrics_by_category"]["numeric_inconsistency"]["candidate"]["fn"] = 5
    with pytest.raises(SystemExit, match="precision gate"):
        validate_policy_validation_report(report, release, manifests)

    report = _report(release, manifests)
    report["actual_cost_usd_per_bundle"]["candidate"] = "0.19"
    with pytest.raises(SystemExit, match="cost reduction gate"):
        validate_policy_validation_report(report, release, manifests)

    report = _report(release, manifests)
    report["compatibility"]["method_manifest_sha256"]["model"] = "0" * 64
    with pytest.raises(SystemExit, match="compatibility"):
        validate_policy_validation_report(report, release, manifests)


def test_report_rejects_low_support_pending_liability_and_mock_live_scope() -> None:
    release, manifests = _baseline()
    report = _report(release, manifests)
    report["support_by_category"]["numeric_inconsistency"]["gold_positive_applicable_assessable"] = 19
    report["support_by_category"]["numeric_inconsistency"]["baseline_predicted_findings"] = 20
    report["support_by_category"]["numeric_inconsistency"]["candidate_predicted_findings"] = 20
    for arm in ("baseline", "candidate"):
        report["metrics_by_category"]["numeric_inconsistency"][arm].update(
            {"tp": 19, "fp": 1, "fn": 0, "evidence_supported_findings": 20}
        )
    with pytest.raises(SystemExit, match="required per-category evaluation support"):
        validate_policy_validation_report(report, release, manifests)

    report = _report(release, manifests)
    report["pending_liability_count"] = 1
    with pytest.raises(SystemExit, match="settled ledger evidence"):
        validate_policy_validation_report(report, release, manifests)

    report = _report(release, manifests)
    report["allowed_data_modes"] = ["synthetic", "institutional"]
    with pytest.raises(SystemExit, match="allowed data modes"):
        validate_policy_validation_report(report, release, manifests)


def test_report_rejects_wrong_self_hash() -> None:
    release, manifests = _baseline()
    report = _report(release, manifests)
    report["report_sha256"] = "0" * 64
    with pytest.raises(SystemExit, match="report hash"):
        validate_policy_validation_report(report, release, manifests)
