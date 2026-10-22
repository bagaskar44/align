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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--active-json-file", type=Path)
    args = parser.parse_args()
    active_text = args.active_json_file.read_text(encoding="utf-8") if args.active_json_file else sys.stdin.read()
    try:
        active = json.loads(active_text)
        target = json.loads(args.target.read_text(encoding="utf-8"))
        if not isinstance(active, list) or not all(isinstance(item, dict) for item in active):
            raise ValueError("active release snapshot must be a JSON array")
        plan = classify_active_releases(active, target)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"action": "stop", "diagnosis": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(plan, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
