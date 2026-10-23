#!/usr/bin/env python3
"""Offline preflight for a future isolated live/synthetic capability smoke.

The default mode never opens a network connection. Live execution remains
fail-closed until the end-to-end runner and the required operator evidence are
available; an API key alone is not authorization to spend.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SAFE_PROJECT = re.compile(r"^align-livecheck-[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
MODEL_ROLES = ("MODEL_EFFICIENT", "MODEL_COMPLEX", "MODEL_VISION", "EMBEDDING_MODEL")
REQUIRED_MANIFESTS = {
    "parser", "parser_resource", "model", "rates", "retrieval", "rules", "prompt", "routing", "batching"
}


def _read_env(path: Path) -> dict[str, str]:
    """Read simple dotenv assignments without ever logging values."""
    result: dict[str, str] = {}
    if not path.is_file():
        return result
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] in {"'", '"'} and value[-1:] == value[:1]:
            value = value[1:-1]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            result[key] = value
    return result


def _status(checks: list[dict[str, str]]) -> str:
    if any(item["status"] == "failed" for item in checks):
        return "failed"
    if any(item["status"] == "pending" for item in checks):
        return "incomplete"
    return "passed"


def _check(checks: list[dict[str, str]], name: str, status: str, detail: str) -> None:
    checks.append({"name": name, "status": status, "detail": detail})


def _loopback_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "http"
            and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            and parsed.port is not None
            and parsed.username is None
            and parsed.password is None
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


def _load_release_bundle(manifest_path: Path, checks: list[dict[str, str]]) -> dict[str, Any] | None:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _check(checks, "release_manifest", "failed", "manifest file is missing or invalid JSON")
        return None
    if not isinstance(payload, dict):
        _check(checks, "release_manifest", "failed", "manifest root must be an object")
        return None
    if payload.get("template_only") is True:
        _check(checks, "release_manifest", "pending", "template contains placeholders and cannot be registered")
        return payload

    _check(checks, "release_mode", "passed" if (
        payload.get("provider_mode") == "live" and payload.get("data_mode") == "synthetic"
    ) else "failed", "release must be live/synthetic")
    manifests = payload.get("manifests")
    if not isinstance(manifests, dict):
        _check(checks, "release_methods", "failed", "release method manifest map is missing")
        return payload
    if set(manifests) != REQUIRED_MANIFESTS:
        _check(checks, "release_methods", "failed", "release must bind the complete required method manifest set")
        return payload

    files: dict[str, dict[str, Any]] = {}
    config_root = manifest_path.resolve().parent
    digests: dict[str, str] = {}
    for kind, relative in manifests.items():
        if not isinstance(kind, str) or not isinstance(relative, str):
            _check(checks, "release_methods", "failed", "method manifest entries must be strings")
            continue
        candidate = (config_root / relative).resolve()
        if config_root not in candidate.parents or not candidate.is_file():
            _check(checks, "release_methods", "failed", f"{kind} manifest is missing or outside the manifest directory")
            continue
        try:
            content = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _check(checks, "release_methods", "failed", f"{kind} manifest is invalid JSON")
            continue
        if not isinstance(content, dict):
            _check(checks, "release_methods", "failed", f"{kind} manifest root must be an object")
            continue
        files[kind] = content
        digests[kind] = hashlib.sha256(candidate.read_bytes()).hexdigest()

    model = files.get("model", {})
    rates = files.get("rates", {})
    routing = files.get("routing", {})
    batching = files.get("batching", {})
    model_ids = model.get("live_model_ids")
    model_ready = (
        model.get("provider_mode") == "live"
        and model.get("paid_calls") is True
        and model.get("capability_smoke") is True
        and model.get("embedding_dimensions") == 1536
        and isinstance(model_ids, dict)
        and set(model_ids) == {"efficient", "complex", "vision", "embedding"}
        and all(isinstance(value, str) and value.strip() for value in model_ids.values())
        and model_ids.get("embedding") == "text-embedding-3-small"
    )
    _check(checks, "live_model_manifest", "passed" if model_ready else "failed",
           "model manifest must pin four exact live roles, 1536-dimensional embeddings, and capability_smoke")

    verification = rates.get("verification")
    rate_models = rates.get("models")
    rates_ready = (
        rates.get("status") == "verified"
        and rates.get("currency") == "USD"
        and isinstance(verification, dict)
        and all(isinstance(verification.get(field), str) and verification[field].strip()
                for field in ("source_ref", "reviewed_by", "effective_date"))
        and isinstance(rate_models, dict)
        and isinstance(model_ids, dict)
        and set(model_ids.values()) <= set(rate_models)
    )
    if rates_ready:
        try:
            date.fromisoformat(verification["effective_date"])
            for model_id in model_ids.values():
                item = rate_models[model_id]
                if not isinstance(item, dict):
                    raise ValueError
                for rate_name in ("input_per_million", "output_per_million", "embedding_per_million"):
                    rate = Decimal(str(item[rate_name]))
                    if not rate.is_finite() or rate < 0:
                        raise ValueError
                image_rate = Decimal(str(item["image_per_unit"]))
                if not image_rate.is_finite() or image_rate < 0:
                    raise ValueError
        except (KeyError, ValueError, InvalidOperation):
            rates_ready = False
    _check(checks, "verified_rates", "passed" if rates_ready else "failed",
                "verified dated USD token and image rates must cover every pinned model role")

    try:
        baseline_routing = json.loads((ROOT / "config/routing.baseline.json").read_text(encoding="utf-8"))
        baseline_batching = json.loads((ROOT / "config/batching.baseline.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        baseline_routing, baseline_batching = {}, {}
    expected_routing = copy.deepcopy(baseline_routing)
    expected_routing.update({"policy_id": "routing-live-synthetic-v1", "allow_live": True})
    routing_ready = routing == expected_routing
    _check(checks, "live_routing", "passed" if routing_ready else "failed",
           "routing must equal the frozen baseline except for the livecheck ID and allow_live=true")
    batching_ready = batching == baseline_batching
    _check(checks, "batching", "passed" if batching_ready else "failed",
           "batching must match the immutable batch-1 baseline exactly")
    activation = payload.get("activation_checks")
    activation_ready = (
        isinstance(activation, dict)
        and activation.get("baseline_batch_size") == 1
        and activation.get("capability_smoke") is True
        and activation.get("empirical_verification") is False
    )
    _check(checks, "activation_checks", "passed" if activation_ready else "failed",
           "capability smoke must be true, batch 1, and empirical verification false")
    payload["_method_sha256"] = digests
    payload["_release_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    return payload


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    env_values = _read_env(args.env_file)
    env_values.update({key: value for key, value in os.environ.items() if key in {
        "APP_ENV", "APP_DEMO_MODE", "PROVIDER_MODE", "DATA_MODE", "OPENAI_API_KEY",
        "PAID_CALLS_ENABLED", *MODEL_ROLES, "EMBEDDING_DIMENSIONS", "BUDGET_CONFIRMED_USD",
        "BUDGET_RESERVE_USD", "BUDGET_PLANNED_TOPUP_USD",
    }})
    checks: list[dict[str, str]] = []

    isolated = bool(SAFE_PROJECT.fullmatch(args.project))
    _check(checks, "isolated_project", "passed" if isolated else "failed",
           "project must use align-livecheck-<run-id>, never the development project")
    _check(checks, "local_origin", "passed" if _loopback_url(args.base_url) else "failed",
           "live capability check is restricted to an explicit localhost HTTP origin")
    expected_mode = (
        env_values.get("APP_ENV") == "development"
        and env_values.get("APP_DEMO_MODE", "").lower() == "true"
        and env_values.get("PROVIDER_MODE") == "live"
        and env_values.get("DATA_MODE") == "synthetic"
    )
    _check(checks, "runtime_mode", "passed" if expected_mode else "failed",
           "runtime must be development + demo + live/synthetic")
    paid_calls_enabled = env_values.get("PAID_CALLS_ENABLED", "").lower() == "true"
    _check(checks, "paid_calls_flag", "passed" if paid_calls_enabled else "failed",
           "paid calls must be enabled explicitly in the isolated livecheck environment")
    key_set = bool(env_values.get("OPENAI_API_KEY", "").strip())
    _check(checks, "provider_credential", "passed" if key_set else "failed",
           "OPENAI_API_KEY is present" if key_set else "OPENAI_API_KEY is missing")

    model_names_set = all(env_values.get(name, "").strip() for name in MODEL_ROLES)
    dimensions_ok = env_values.get("EMBEDDING_DIMENSIONS", "1536") == "1536"
    _check(checks, "runtime_model_binding", "passed" if model_names_set and dimensions_ok else "failed",
           "all four model role IDs must be set and embedding dimensions must be 1536")
    runtime_origin_matches = _loopback_url(args.base_url) and env_values.get("APP_ORIGIN") == args.base_url
    _check(checks, "runtime_origin_binding", "passed" if runtime_origin_matches else "failed",
           "APP_ORIGIN must match the explicit localhost base URL")
    if env_values.get("BUDGET_PLANNED_TOPUP_USD", "0").strip() not in {"0", "0.0", "0.00", "0.00000000"}:
        _check(checks, "planned_topup", "failed", "planned top-up is not spendable")
    else:
        _check(checks, "planned_topup", "passed", "no planned top-up is counted")
    try:
        confirmed = Decimal(env_values.get("BUDGET_CONFIRMED_USD", "0"))
        reserve = Decimal(env_values.get("BUDGET_RESERVE_USD", "0"))
        budget_valid = (
            confirmed.is_finite() and reserve.is_finite() and confirmed > reserve >= 0
        )
    except InvalidOperation:
        confirmed, reserve = Decimal("0"), Decimal("0")
        budget_valid = False
    _check(checks, "configured_budget", "passed" if budget_valid else "failed",
           "config values are well-formed only; database funds and an approved run cap are not verified here")

    payload = _load_release_bundle(args.manifest, checks)
    if payload is not None and isinstance(payload.get("manifests"), dict):
        model_entry = payload.get("manifests", {}).get("model")
        model_path = (args.manifest.resolve().parent / model_entry).resolve() if isinstance(model_entry, str) else None
        model_data: dict[str, Any] = {}
        if model_path is not None and model_path.is_file():
            try:
                loaded = json.loads(model_path.read_text(encoding="utf-8"))
                model_data = loaded if isinstance(loaded, dict) else {}
            except (OSError, json.JSONDecodeError):
                model_data = {}
        pinned = model_data.get("live_model_ids")
        env_to_role = {
            "MODEL_EFFICIENT": "efficient", "MODEL_COMPLEX": "complex",
            "MODEL_VISION": "vision", "EMBEDDING_MODEL": "embedding",
        }
        binding_ok = isinstance(pinned, dict) and all(
            env_values.get(env_name) == pinned.get(role) for env_name, role in env_to_role.items()
        )
        _check(checks, "release_runtime_binding", "passed" if binding_ok else "failed",
               "release role model IDs must exactly match the runtime configuration")
    else:
        _check(checks, "release_runtime_binding", "failed", "a complete live release manifest is required")

    authorization = args.authorization_ref or ""
    _check(checks, "spend_authorization", "passed" if authorization.strip() else "pending",
           "authorization reference supplied" if authorization.strip() else "operator spend authorization reference is required")
    max_spend_ok = False
    if args.max_spend_usd is not None:
        try:
            max_spend = Decimal(args.max_spend_usd)
            max_spend_ok = (
                max_spend.is_finite() and max_spend > 0
                and budget_valid and max_spend <= confirmed - reserve
            )
        except InvalidOperation:
            pass
    _check(checks, "run_spend_cap", "passed" if max_spend_ok else "pending",
           "per-run cap is positive and no larger than the configured spendable ceiling" if max_spend_ok
           else "explicit positive --max-spend-usd within configured confirmed-minus-reserve ceiling is required")

    if args.execute_live:
        _check(checks, "live_execution", "failed",
               "end-to-end API/worker orchestration and atomic run-level spend-cap enforcement are not implemented; no provider call was made")
    else:
        _check(checks, "live_execution", "pending", "offline preflight only; no API or provider request was made")

    release_payload = payload or {}
    return {
        "schema_version": 1,
        "run_id": args.project.removeprefix("align-livecheck-"),
        "generated_at": datetime.now(UTC).isoformat(),
        "revision": os.environ.get("ALIGN_SOURCE_REVISION", "unknown"),
        "provider_mode": env_values.get("PROVIDER_MODE", "unset"),
        "data_mode": env_values.get("DATA_MODE", "unset"),
        "app_mode": env_values.get("APP_ENV", "unset"),
        "release_version_label": release_payload.get("version_label", "unknown"),
        "manifest_sha256": {
            "release": release_payload.get("_release_sha256"),
            "methods": release_payload.get("_method_sha256", {}),
        },
        "status": _status(checks),
        "execution_performed": False,
        "provider_network_calls": 0,
        "actual_cost_usd": None,
        "pending_liability_count": None,
        "checks": checks,
        "limitations": [
            "preflight is offline and does not prove an active database release or available budget",
            "provider execution is blocked until end-to-end API/worker orchestration and atomic run-level spend caps exist",
            "configuration and key presence do not authorize paid calls",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--base-url", default="http://localhost:3000")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--execute-live", action="store_true")
    parser.add_argument("--max-spend-usd")
    parser.add_argument("--authorization-ref")
    args = parser.parse_args()
    report = build_report(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        os.chmod(args.output, 0o600)
    except OSError:
        pass
    print(json.dumps({
        "status": report["status"],
        "execution_performed": False,
        "provider_network_calls": 0,
        "output": str(args.output),
        "failed_checks": [item["name"] for item in report["checks"] if item["status"] == "failed"],
        "pending_checks": [item["name"] for item in report["checks"] if item["status"] == "pending"],
    }))
    return 0 if report["status"] == "passed" and not args.execute_live else 2


if __name__ == "__main__":
    raise SystemExit(main())
