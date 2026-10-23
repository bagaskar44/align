#!/usr/bin/env python3
"""Prepare and explicitly activate one local live/synthetic run.

The command reads model IDs and modes from the private root .env, copies a
reviewed rates manifest into an ignored config/live-local run directory, pins
all method manifests, and asks the operator container to register/activate the
release and create its atomic budget cap. It never invokes the provider.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
SAFE_LABEL = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
MODEL_ENV = {
    "efficient": "MODEL_EFFICIENT",
    "complex": "MODEL_COMPLEX",
    "vision": "MODEL_VISION",
    "embedding": "EMBEDDING_MODEL",
}
RATE_KEYS = (
    "input_per_million",
    "output_per_million",
    "embedding_per_million",
)


def _read_env(path: Path) -> dict[str, str]:
    """Read dotenv values for validation without printing or rewriting secrets."""
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if value[:1] in {"'", '"'} and value[-1:] == value[:1]:
            value = value[1:-1]
        values[name] = value
    return values


def _compose_environment(port: int) -> dict[str, str]:
    """Keep Docker CLI context while letting Compose read this checkout's .env."""
    passthrough = {"PATH", "HOME", "TMPDIR", "SSH_AUTH_SOCK", "LANG", "LC_ALL"}
    environment = {
        key: value for key, value in os.environ.items()
        if key in passthrough or key.startswith("DOCKER_")
    }
    environment["ALIGN_HOST_PORT"] = str(port)
    environment["APP_ORIGIN"] = f"http://localhost:{port}"
    return environment


def _decimal_rate(value: object, *, field: str) -> Decimal:
    try:
        rate = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SystemExit(f"rates manifest has invalid {field}") from exc
    if not rate.is_finite() or rate < 0:
        raise SystemExit(f"rates manifest has invalid {field}")
    return rate


def _load_rates(path_value: str, model_ids: dict[str, str]) -> dict[str, object]:
    path = Path(path_value).resolve()
    config_root = CONFIG.resolve()
    if config_root not in path.parents or not path.is_file():
        raise SystemExit("rates input must be a file inside the repository config directory")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit("rates input is missing or invalid JSON") from exc
    if not isinstance(payload, dict) or payload.get("template_only") is True:
        raise SystemExit("provide a completed local rates manifest, not a template")
    verification = payload.get("verification")
    models = payload.get("models")
    if (
        payload.get("schema_version") != 1
        or payload.get("status") != "verified"
        or payload.get("currency") != "USD"
        or not isinstance(verification, dict)
        or not all(
            isinstance(verification.get(key), str) and verification[key].strip()
            for key in ("source_ref", "reviewed_by", "effective_date")
        )
        or not isinstance(models, dict)
        or not set(model_ids.values()) <= set(models)
    ):
        raise SystemExit("rates require verified USD status, source/reviewer/date, and the three selected provider roles plus fixed embedding model")
    try:
        datetime.fromisoformat(str(verification["effective_date"]))
        for model_id in set(model_ids.values()):
            row = models[model_id]
            if not isinstance(row, dict):
                raise ValueError
            for key in RATE_KEYS:
                if key not in row:
                    raise ValueError
                _decimal_rate(row[key], field=key)
        vision_rates = models[model_ids["vision"]]
        if not isinstance(vision_rates, dict) or "image_per_unit" not in vision_rates:
            raise ValueError
        _decimal_rate(vision_rates["image_per_unit"], field="image_per_unit")
    except (ValueError, TypeError) as exc:
        raise SystemExit("rates require dated token rates for every role and an image rate for the vision model") from exc
    # Return a detached JSON object; caller adds a unique immutable manifest ID.
    return payload


def _copy_json(source: str, destination: Path) -> dict[str, object]:
    payload = json.loads((CONFIG / source).read_text(encoding="utf-8"))
    shutil.copyfile(CONFIG / source, destination)
    return payload


def prepare(args: argparse.Namespace) -> tuple[Path, Path, str]:
    env_file = (ROOT / ".env").resolve()
    if not env_file.is_file():
        raise SystemExit("private .env file is missing")
    env = _read_env(env_file)
    required_env = {
        "APP_ENV": "development",
        "APP_DEMO_MODE": "true",
        "DATA_MODE": "synthetic",
        "PROVIDER_MODE": "live",
        "PAID_CALLS_ENABLED": "true",
    }
    if any(env.get(name, "").lower() != expected for name, expected in required_env.items()):
        raise SystemExit("set the isolated local runtime to development/demo/live/synthetic with paid calls explicitly enabled")
    if not env.get("OPENAI_API_KEY", "").strip():
        raise SystemExit("OPENAI_API_KEY is not configured in the private environment")
    if env.get("EMBEDDING_DIMENSIONS", "1536") != "1536":
        raise SystemExit("live synthetic release requires 1536-dimensional embeddings")
    model_ids = {role: env.get(name, "").strip() for role, name in MODEL_ENV.items()}
    if any(not value for value in model_ids.values()):
        raise SystemExit("set all four exact model role IDs in the private root .env")
    if model_ids["embedding"] != "text-embedding-3-small":
        raise SystemExit("ALIGN master requires EMBEDDING_MODEL=text-embedding-3-small")
    rates_input = _load_rates(args.rates_file, model_ids)

    suffix = args.version_suffix or f"{datetime.now(UTC):%Y%m%dt%H%M%sz}-{uuid4().hex[:8]}"
    if not SAFE_LABEL.fullmatch(suffix):
        raise SystemExit("version suffix may contain lowercase letters, numbers, and hyphens only")
    output_dir = CONFIG / "live-local" / suffix
    if output_dir.exists():
        raise SystemExit("this live-local version directory already exists; choose another version suffix")
    output_dir.mkdir(parents=True, mode=0o755)

    for source in (
        "parser.baseline.json",
        "parser-resource.baseline.json",
        "parser-context-resources.json",
        "retrieval.baseline.json",
        "rules.baseline.json",
        "prompt.baseline.json",
        "batching.baseline.json",
    ):
        _copy_json(source, output_dir / source)

    routing = json.loads((CONFIG / "routing.baseline.json").read_text(encoding="utf-8"))
    routing["allow_live"] = True
    (output_dir / "routing.live-synthetic.json").write_text(
        json.dumps(routing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    rates = dict(rates_input)
    rates["manifest_id"] = f"rates-live-{suffix}"
    rates_name = f"model-rates-live-{suffix}.json"
    (output_dir / rates_name).write_text(json.dumps(rates, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    model_template = json.loads((CONFIG / "model-live-synthetic.template.json").read_text(encoding="utf-8"))
    model = {
        **model_template,
        "manifest_id": f"model-live-{suffix}",
        "template_only": False,
        "live_model_ids": model_ids,
        "rates_manifest": rates_name,
        "paid_calls": True,
        "capability_smoke": True,
        "semantic_verification": "pending_capability_smoke",
        "label": f"LIVE • data sintetis • {suffix}",
    }
    model_name = f"model-live-{suffix}.json"
    (output_dir / model_name).write_text(json.dumps(model, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    release = json.loads((CONFIG / "release.baseline.json").read_text(encoding="utf-8"))
    release.update({
        "version_label": f"align-live-synthetic-{suffix}",
        "provider_mode": "live",
        "data_mode": "synthetic",
        "template_only": False,
        "manifests": {
            "parser": "parser.baseline.json",
            "parser_resource": "parser-resource.baseline.json",
            "model": model_name,
            "rates": rates_name,
            "retrieval": "retrieval.baseline.json",
            "rules": "rules.baseline.json",
            "prompt": "prompt.baseline.json",
            "routing": "routing.live-synthetic.json",
            "batching": "batching.baseline.json",
        },
        "capabilities_available": [
            *release.get("capabilities_available", []),
            "paid_calls",
        ],
        "capabilities_unavailable": [
            value for value in release.get("capabilities_unavailable", [])
            if value != "paid_calls"
        ],
        "activation_checks": {
            **release.get("activation_checks", {}),
            "baseline_batch_size": 1,
            "capability_smoke": True,
            "empirical_verification": False,
        },
    })
    release_name = f"release-live-{suffix}.json"
    (output_dir / release_name).write_text(json.dumps(release, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    project_name = "align-livecheck-local"
    run_metadata = {
        "project_name": project_name,
        "host_port": args.port,
        "app_origin": f"http://localhost:{args.port}",
        "release_manifest": f"config/live-local/{suffix}/{release_name}",
        "release_version_label": release["version_label"],
        "run_id": None,
        "state": "prepared",
    }
    (output_dir / "local-run.json").write_text(
        json.dumps(run_metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return output_dir / release_name, output_dir / "local-run.json", project_name


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rates-file", required=True, help="verified USD rates JSON under config/")
    parser.add_argument("--max-spend-usd", required=True, help="operator-approved hard cap for this one run")
    parser.add_argument("--authorization-ref", required=True, help="operator authorization/ticket reference")
    parser.add_argument("--version-suffix", help="optional unique lowercase release label")
    parser.add_argument("--port", type=int, default=3311, help="isolated localhost web port (default 3311)")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        raise SystemExit("localhost port must be between 1 and 65535")
    try:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", args.port))
    except OSError as exc:
        raise SystemExit("chosen localhost port is already unavailable") from exc
    try:
        release_path, metadata_path, project_name = prepare(args)
    except SystemExit:
        raise
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(f"could not prepare non-secret live configuration: {type(exc).__name__}") from exc

    relative_manifest = release_path.relative_to(ROOT).as_posix()
    project_args = ["docker", "compose", "--project-name", project_name]
    compose_env = _compose_environment(args.port)
    seed_command = [
        *project_args, "--profile", "operator", "run", "--build", "--rm", "operator", "seed",
    ]
    result = subprocess.run(seed_command, cwd=ROOT, env=compose_env, check=False)  # noqa: S603 -- fixed executable and argument vector; no shell
    if result.returncode != 0:
        print("Isolated migration/seed did not complete; no release was activated.", file=sys.stderr)
        return result.returncode

    activation_command = [
        *project_args, "--profile", "operator", "run", "--rm", "operator",
        "live", "activate-local",
        "--manifest", f"/app/{relative_manifest}",
        "--max-spend-usd", args.max_spend_usd,
        "--authorization-ref", args.authorization_ref,
    ]
    print(json.dumps({
        "configuration_directory": str(release_path.parent),
        "release_manifest": relative_manifest,
        "isolated_compose_project": project_name,
        "localhost_origin": f"http://localhost:{args.port}",
        "provider_call_performed": False,
        "next_step": "isolated migration, synthetic seed, release activation, and atomic spend-run setup",
    }, ensure_ascii=False))
    activation = subprocess.run(
        activation_command, cwd=ROOT, env=compose_env, check=False, capture_output=True, text=True,
    )  # noqa: S603 -- fixed executable and argument vector; no shell
    if activation.returncode != 0:
        print("Release activation and run setup did not complete; no application stack was started.", file=sys.stderr)
        return activation.returncode
    activation_payload = None
    for line in reversed(activation.stdout.splitlines()):
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and candidate.get("paid_call_run_id"):
            activation_payload = candidate
            break
    if activation_payload is None:
        print(
            "The operator command returned without a paid-call run ID; application stack was not started. "
            "Inspect the operator output and isolated project before retrying.",
            file=sys.stderr,
        )
        return 2
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["run_id"] = activation_payload["paid_call_run_id"]
    metadata["release_id"] = activation_payload["release_id"]
    metadata["state"] = "active"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    start_command = [*project_args, "up", "--detach", "--build"]
    start = subprocess.run(start_command, cwd=ROOT, env=compose_env, check=False)  # noqa: S603 -- fixed executable and argument vector; no shell
    if start.returncode != 0:
        close_command = [
            *project_args, "--profile", "operator", "run", "--rm", "operator",
            "live", "close-local", str(activation_payload["paid_call_run_id"]),
            "--reason", "isolated local stack did not start successfully",
        ]
        close = subprocess.run(close_command, cwd=ROOT, env=compose_env, check=False)  # noqa: S603 -- fixed executable and argument vector; no shell
        metadata["state"] = "closed_after_start_failure" if close.returncode == 0 else "active"
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if close.returncode != 0:
            print(
                "The stack did not start and the cap could not be closed. "
                "Keep the isolated project available and retry scripts/stop-live-local.py with its local-run.json.",
                file=sys.stderr,
            )
        return start.returncode
    print(json.dumps({
        "status": "ready_for_manual_synthetic_live_flow",
        "origin": metadata["app_origin"],
        "compose_project": project_name,
        "paid_call_run_id": metadata["run_id"],
        "provider_network_calls": 0,
    }, ensure_ascii=False))
    return start.returncode


if __name__ == "__main__":
    raise SystemExit(main())
