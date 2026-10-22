from __future__ import annotations

import pytest
from scripts.parser_capacity_preflight import check_capacity
from scripts.release_guard import classify_active_releases

TARGET = {
    "version_label": "align-b5-synthetic-baseline-v2",
    "provider_mode": "mock",
    "data_mode": "synthetic",
    "capabilities_available": [
        "manual_auth",
        "mock_provider_schema",
        "b5_executor",
        "deterministic_applicability_dag",
        "scoped_hybrid_retrieval",
        "typed_grounding",
        "semantic_batch1_validator",
        "real_adapter_configurable",
        "review_analysis_integration",
    ],
}


def test_release_guard_accepts_empty_database_and_known_default_b0() -> None:
    assert classify_active_releases([], TARGET)["action"] == "register_activate"
    b0 = {
        "id": "b0-id",
        "version_label": "align-b0-synthetic-baseline-v1",
        "config_hash": "a" * 64,
        "provider_mode": "mock",
        "data_mode": "synthetic",
        "capabilities_available": ["manual_auth", "mock_provider_schema"],
    }
    result = classify_active_releases([b0], TARGET)
    assert result["action"] == "register_activate"
    assert result["active_release_id"] == "b0-id"


def test_release_guard_verifies_target_identity_without_reactivation() -> None:
    active = {**TARGET, "id": "b5-id", "config_hash": "b" * 64}
    assert classify_active_releases([active], TARGET)["action"] == "verify_target"


@pytest.mark.parametrize(
    "active",
    [
        {
            "version_label": "custom-mock",
            "provider_mode": "mock",
            "data_mode": "synthetic",
            "capabilities_available": ["manual_auth", "mock_provider_schema"],
        },
        {
            "version_label": "align-b0-synthetic-baseline-v1",
            "provider_mode": "live",
            "data_mode": "synthetic",
            "capabilities_available": ["manual_auth", "mock_provider_schema"],
        },
        {
            "version_label": "align-b0-synthetic-baseline-v1",
            "provider_mode": "mock",
            "data_mode": "synthetic",
            "capabilities_available": ["manual_auth", "mock_provider_schema", "b5_executor"],
        },
        {**TARGET, "version_label": "other-b5-release", "config_hash": "c" * 64},
    ],
)
def test_release_guard_preserves_custom_live_and_unrecognized_releases(active: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="deployment stopped before promotion"):
        classify_active_releases([active], TARGET)


def test_release_guard_rejects_ambiguous_multiple_active_releases() -> None:
    with pytest.raises(ValueError, match="expected one active release"):
        classify_active_releases([{}, {}], TARGET)


def test_development_parser_preflight_requires_four_gib_plus_one_gib_headroom() -> None:
    rejected = check_capacity(5 * 1024**3 - 1, 4 * 1024**3, 1024**3)
    accepted = check_capacity(5 * 1024**3, 4 * 1024**3, 1024**3)
    assert rejected["accepted"] is False
    assert accepted["accepted"] is True
    assert accepted["container_memory_limit_bytes"] == 5 * 1024**3
