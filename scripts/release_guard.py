"""Classify an active development release before any deployment mutation."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

DEFAULT_B0_LABEL = "align-b0-synthetic-baseline-v1"
DEFAULT_B0_CAPABILITIES = {"manual_auth", "mock_provider_schema"}


def classify_active_releases(
    active_releases: list[dict[str, Any]], target: dict[str, Any]
) -> dict[str, Any]:
    if not active_releases:
        return {"action": "register_activate", "reason": "no active release"}
    if len(active_releases) != 1:
        raise ValueError(f"expected one active release, found {len(active_releases)}")

    active = active_releases[0]
    capabilities = set(active.get("capabilities_available") or [])
    if (
        active.get("version_label") == DEFAULT_B0_LABEL
        and active.get("provider_mode") == "mock"
        and active.get("data_mode") == "synthetic"
        and capabilities == DEFAULT_B0_CAPABILITIES
    ):
        return {
            "action": "register_activate",
            "reason": "recognized default B0 mock/synthetic release",
            "active_release_id": active.get("id"),
            "active_config_hash": active.get("config_hash"),
        }

    target_capabilities = set(target.get("capabilities_available") or [])
    if (
        active.get("version_label") == target.get("version_label")
        and active.get("provider_mode") == target.get("provider_mode") == "mock"
        and active.get("data_mode") == target.get("data_mode") == "synthetic"
        and capabilities == target_capabilities
        and "b5_executor" in capabilities
    ):
        return {
            "action": "verify_target",
            "reason": "active release has the target baseline identity; verify its hash before promotion",
            "active_release_id": active.get("id"),
            "active_config_hash": active.get("config_hash"),
        }

    raise ValueError(
        "active release is custom, live, or unrecognized; deployment stopped before promotion "
        f"(label={active.get('version_label')!r}, provider={active.get('provider_mode')!r}, "
        f"data={active.get('data_mode')!r}, capabilities={sorted(capabilities)!r})"
    )


def verify_registered_target(plan: dict[str, Any], registered: dict[str, Any]) -> dict[str, Any]:
    """Fail closed on a retired target or an identity/hash mismatch before promotion."""
    action = plan.get("action")
    release_id = registered.get("release_id")
    config_hash = registered.get("config_hash")
    state = registered.get("state")
    if action not in {"register_activate", "verify_target"}:
        raise ValueError(f"unsupported release plan action: {action!r}")
    if not isinstance(release_id, str) or not release_id:
        raise ValueError("registered target has no release ID")
    if not isinstance(config_hash, str) or len(config_hash) != 64:
        raise ValueError("registered target has no valid config hash")
    if state == "retired":
        raise ValueError("target release is retired and cannot be reactivated")
    if action == "verify_target":
        if state != "active":
            raise ValueError("existing target release is not active")
        if release_id != plan.get("active_release_id"):
            raise ValueError("active target release ID differs from the preflight snapshot")
        if config_hash != plan.get("active_config_hash"):
            raise ValueError("active target release hash differs from the tested target")
    elif state not in {"draft", "verified", "active"}:
        raise ValueError(f"registered target has unsupported state: {state!r}")
    return {"release_id": release_id, "config_hash": config_hash, "state": state}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--active-json-file", type=Path)
    parser.add_argument("--plan-json-file", type=Path)
    parser.add_argument("--plan-json")
    parser.add_argument("--registered-json-file", type=Path)
    parser.add_argument("--verify-registered", action="store_true")
    args = parser.parse_args()
    try:
        if args.verify_registered:
            if args.plan_json_file is None and args.plan_json is None:
                raise ValueError("--verify-registered requires a plan JSON file or value")
            plan_text = (
                args.plan_json_file.read_text(encoding="utf-8")
                if args.plan_json_file
                else args.plan_json
            )
            if plan_text is None:
                raise ValueError("release plan JSON is required")
            plan = json.loads(plan_text)
            registered_text = (
                args.registered_json_file.read_text(encoding="utf-8")
                if args.registered_json_file
                else sys.stdin.read()
            )
            registered = json.loads(registered_text)
            if not isinstance(plan, dict) or not isinstance(registered, dict):
                raise ValueError("release plan and registered target must be JSON objects")
            result = verify_registered_target(plan, registered)
        else:
            if args.target is None:
                raise ValueError("--target is required when classifying active releases")
            active_text = args.active_json_file.read_text(encoding="utf-8") if args.active_json_file else sys.stdin.read()
            active = json.loads(active_text)
            target = json.loads(args.target.read_text(encoding="utf-8"))
            if not isinstance(active, list) or not all(isinstance(item, dict) for item in active):
                raise ValueError("active release snapshot must be a JSON array")
            result = classify_active_releases(active, target)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"action": "stop", "diagnosis": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
