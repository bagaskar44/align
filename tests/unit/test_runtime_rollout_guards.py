from __future__ import annotations

import builtins
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from scripts import parser_capacity_preflight
from scripts.parser_capacity_preflight import check_capacity
from scripts.release_guard import classify_active_releases, verify_registered_target

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
    plan = classify_active_releases([active], TARGET)
    assert plan["action"] == "verify_target"
    assert verify_registered_target(
        plan, {"release_id": "b5-id", "config_hash": "b" * 64, "state": "active"}
    )["state"] == "active"


@pytest.mark.parametrize(
    "registered",
    [
        {"release_id": "other-id", "config_hash": "b" * 64, "state": "active"},
        {"release_id": "b5-id", "config_hash": "c" * 64, "state": "active"},
        {"release_id": "b5-id", "config_hash": "b" * 64, "state": "retired"},
        {"release_id": "b5-id", "config_hash": "b" * 64, "state": "draft"},
    ],
)
def test_release_guard_rejects_target_id_hash_state_mismatch(registered: dict[str, str]) -> None:
    plan = classify_active_releases([{**TARGET, "id": "b5-id", "config_hash": "b" * 64}], TARGET)
    with pytest.raises(ValueError):
        verify_registered_target(plan, registered)


def test_release_guard_rejects_retired_target_when_registering_from_empty_or_b0() -> None:
    plan = classify_active_releases([], TARGET)
    with pytest.raises(ValueError, match="retired"):
        verify_registered_target(
            plan, {"release_id": "retired-id", "config_hash": "d" * 64, "state": "retired"}
        )


def test_release_guard_cli_shares_registered_target_policy_with_macos() -> None:
    plan = {"action": "verify_target", "active_release_id": "same-id", "active_config_hash": "e" * 64}
    registered = {"release_id": "same-id", "config_hash": "e" * 64, "state": "active"}
    script = Path(__file__).resolve().parents[2] / "scripts" / "release_guard.py"
    result = subprocess.run(  # noqa: S603 -- fixed repository script and synthetic JSON only
        [sys.executable, str(script), "--verify-registered", "--plan-json", json.dumps(plan)],
        input=json.dumps(registered),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["release_id"] == "same-id"


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
    rejected = check_capacity(5 * 1024**3 - 1, 5 * 1024**3, 4 * 1024**3, 1024**3)
    accepted = check_capacity(5 * 1024**3, 5 * 1024**3, 4 * 1024**3, 1024**3)
    assert rejected["accepted"] is False
    assert accepted["accepted"] is True
    assert accepted["container_memory_limit_bytes"] == 5 * 1024**3


def test_development_parser_preflight_requires_docker_total_not_windows_ram() -> None:
    insufficient_docker_vm = check_capacity(6 * 1024**3, 5 * 1024**3 - 1, 4 * 1024**3, 1024**3)
    assert insufficient_docker_vm["accepted"] is False


def test_parser_preflight_fails_closed_without_linux_memory_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_proc_file(*_args: object, **_kwargs: object) -> None:
        raise OSError("no Linux procfs")

    def no_sysconf(_name: str) -> int:
        raise AttributeError("no Linux sysconf memory source")

    monkeypatch.setattr(builtins, "open", no_proc_file)
    monkeypatch.setattr(os, "sysconf", no_sysconf, raising=False)
    with pytest.raises(RuntimeError, match="inside the Linux Docker environment"):
        parser_capacity_preflight.available_linux_bytes()


def test_production_parser_capacity_profile_is_eight_gib_with_nine_gib_container() -> None:
    result = check_capacity(9 * 1024**3, 9 * 1024**3, 8 * 1024**3, 1024**3)
    assert result["accepted"] is True
    assert result["container_memory_limit_bytes"] == 9 * 1024**3
