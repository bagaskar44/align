from __future__ import annotations

import json
import runpy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
LIVE = runpy.run_path(str(ROOT / "scripts/live-smoke.py"))
PRODUCTION = runpy.run_path(str(ROOT / "scripts/production-preflight.py"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _live_fixture(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    methods = tmp_path / "methods"
    methods.mkdir()
    frozen_names = {
        "parser": "parser.baseline.json",
        "parser_resource": "parser-resource.baseline.json",
        "retrieval": "retrieval.baseline.json",
        "rules": "rules.baseline.json",
        "prompt": "prompt.baseline.json",
    }
    for _name, filename in frozen_names.items():
        source = ROOT / "config" / filename
        (methods / filename).write_bytes(source.read_bytes())

    role_ids = {
        "efficient": "fixture-efficient-model",
        "complex": "fixture-complex-model",
        "vision": "fixture-vision-model",
        "embedding": "text-embedding-3-small",
    }
    model = {
        "schema_version": 1,
        "manifest_id": "fixture-live-model",
        "provider_mode": "live",
        "live_model_ids": role_ids,
        "embedding_dimensions": 1536,
        "rates_manifest": "rates.json",
        "paid_calls": True,
        "capability_smoke": True,
    }
    _write_json(methods / "model.json", model)
    rates = {
        "schema_version": 1,
        "status": "verified",
        "currency": "USD",
        "verification": {
            "source_ref": "fixture-only",
            "reviewed_by": "fixture-reviewer",
            "effective_date": "2026-10-23",
        },
        "models": {
            model_id: {
                "input_per_million": "1",
                "output_per_million": "1",
                "embedding_per_million": "1",
                "image_per_unit": "0.01",
            }
            for model_id in role_ids.values()
        },
    }
    _write_json(methods / "rates.json", rates)
    routing = json.loads((ROOT / "config/routing.baseline.json").read_text(encoding="utf-8"))
    routing["allow_live"] = True
    _write_json(methods / "routing.json", routing)
    batching = json.loads((ROOT / "config/batching.baseline.json").read_text(encoding="utf-8"))
    _write_json(methods / "batching.json", batching)

    manifest = methods / "release.json"
    manifest_names = {
        **frozen_names,
        "model": "model.json",
        "rates": "rates.json",
        "routing": "routing.json",
        "batching": "batching.json",
    }
    _write_json(manifest, {
        "schema_version": 2,
        "version_label": "fixture-live-synthetic",
        "provider_mode": "live",
        "data_mode": "synthetic",
        "manifests": manifest_names,
        "activation_checks": {
            "baseline_batch_size": 1,
            "capability_smoke": True,
            "empirical_verification": False,
        },
    })
    return manifest, {
        "APP_ENV": "development",
        "APP_ORIGIN": "http://localhost:3000",
        "APP_DEMO_MODE": "true",
        "PROVIDER_MODE": "live",
        "DATA_MODE": "synthetic",
        "PAID_CALLS_ENABLED": "true",
        "OPENAI_API_KEY": "fixture-only-not-a-real-key",
        "MODEL_EFFICIENT": role_ids["efficient"],
        "MODEL_COMPLEX": role_ids["complex"],
        "MODEL_VISION": role_ids["vision"],
        "EMBEDDING_MODEL": role_ids["embedding"],
        "EMBEDDING_DIMENSIONS": "1536",
        "BUDGET_CONFIRMED_USD": "25",
        "BUDGET_RESERVE_USD": "5",
        "BUDGET_PLANNED_TOPUP_USD": "0",
    }


def _live_report(manifest: Path, env_file: Path, *, execute: bool = False) -> dict[str, Any]:
    args = SimpleNamespace(
        project="align-livecheck-test-run",
        base_url="http://localhost:3000",
        manifest=manifest,
        output=env_file.parent / "report.json",
        env_file=env_file,
        execute_live=execute,
        max_spend_usd="1.00",
        authorization_ref="synthetic-test-authorization-ref",
    )
    return LIVE["build_report"](args)


def test_live_smoke_preflight_is_offline_and_fails_closed_for_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, values = _live_fixture(tmp_path)
    env_file = tmp_path / ".env.livecheck"
    env_file.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8")
    for key in values:
        monkeypatch.delenv(key, raising=False)

    report = _live_report(manifest, env_file)
    assert report["status"] == "incomplete"
    assert report["execution_performed"] is False
    assert report["provider_network_calls"] == 0
    assert all(item["status"] == "passed" for item in report["checks"] if item["name"] != "live_execution")
    assert "fixture-only-not-a-real-key" not in json.dumps(report)

    attempted = _live_report(manifest, env_file, execute=True)
    assert attempted["status"] == "failed"
    assert attempted["execution_performed"] is False
    assert attempted["provider_network_calls"] == 0
    execution = next(item for item in attempted["checks"] if item["name"] == "live_execution")
    assert execution["status"] == "failed"


def test_live_smoke_rejects_policy_drift_model_mismatch_and_missing_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, values = _live_fixture(tmp_path)
    env_file = tmp_path / ".env.livecheck"
    env_file.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8")
    for key in values:
        monkeypatch.delenv(key, raising=False)
    checks: list[dict[str, str]] = []
    payload = LIVE["_load_release_bundle"](manifest, checks)
    assert payload is not None
    assert all(item["status"] == "passed" for item in checks)

    routing_path = manifest.parent / "routing.json"
    routing = json.loads(routing_path.read_text(encoding="utf-8"))
    routing["semantic_batch_size"] = 3
    _write_json(routing_path, routing)
    report = _live_report(manifest, env_file)
    assert next(item for item in report["checks"] if item["name"] == "live_routing")["status"] == "failed"

    routing["semantic_batch_size"] = 1
    _write_json(routing_path, routing)
    values["MODEL_VISION"] = "other-model"
    env_file.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8")
    report = _live_report(manifest, env_file)
    assert next(item for item in report["checks"] if item["name"] == "release_runtime_binding")["status"] == "failed"

    values["MODEL_VISION"] = "fixture-vision-model"
    values["OPENAI_API_KEY"] = ""
    env_file.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8")
    report = _live_report(manifest, env_file)
    assert next(item for item in report["checks"] if item["name"] == "provider_credential")["status"] == "failed"


def _private_file(path: Path, content: str, mode: int = 0o600) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return path


def test_production_preflight_understands_override_tags_and_stays_read_only(tmp_path: Path) -> None:
    checks: list[dict[str, str]] = []
    base = PRODUCTION["_load_yaml"](ROOT / "docker-compose.yml", checks, "base")
    production = PRODUCTION["_load_yaml"](ROOT / "docker-compose.production.yml", checks, "production")
    services = {
        name: PRODUCTION["_deep_merge"](base["services"].get(name, {}), service)
        for name, service in production["services"].items()
    }
    assert PRODUCTION["_published_ports"](services["proxy"]) == {80, 443}
    assert PRODUCTION["_published_ports"](services["object-storage"]) == set()
    assert PRODUCTION["_published_ports"](services["postgres"]) == set()
    assert services["parser-worker"]["mem_limit"] == "9g"
    assert "provider-egress" not in services["parser-worker"]["networks"]

    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    roles: dict[str, Path] = {}
    for role in PRODUCTION["ROLE_ENV_KEYS"]:
        roles[role] = _private_file(private / f"{role}.env", "SYNTHETIC_TEST_ONLY=value\n")
    bucket = "align-test-production"
    app_values = {
        "DATABASE_URL": "postgresql+psycopg://align_api:test@postgres:5432/align",
        "REDIS_URL": "redis://redis:6379/0",
        "S3_ENDPOINT": "https://object-storage:8334",
        "S3_BUCKET": bucket,
        "S3_ACCESS_KEY_ID": "synthetic-app-test-key",
        "S3_SECRET_ACCESS_KEY": "synthetic-app-test-secret",
        "CSRF_SERVER_SECRET": "synthetic-test-csrf-secret-with-32-bytes",
    }
    worker_values = {
        "WORKER_DATABASE_URL": "postgresql+psycopg://align_worker:test@postgres:5432/align",
        "REDIS_URL": "redis://redis:6379/0",
        "S3_ENDPOINT": "https://object-storage:8334",
        "S3_BUCKET": bucket,
        "S3_ACCESS_KEY_ID": "synthetic-app-test-key",
        "S3_SECRET_ACCESS_KEY": "synthetic-app-test-secret",
    }
    _private_file(roles["api"], "\n".join(f"{k}={v}" for k, v in app_values.items()) + "\n")
    _private_file(roles["worker"], "\n".join(f"{k}={v}" for k, v in worker_values.items()) + "\n")

    storage_auth = {
        "identities": [
            {
                "name": "align-application",
                "credentials": [{"accessKey": "synthetic-app-key", "secretKey": "synthetic-app-secret"}],
                "actions": [f"Read:{bucket}", f"Write:{bucket}", f"List:{bucket}", f"Tagging:{bucket}"],
            },
            {
                "name": "align-backup",
                "credentials": [{"accessKey": "synthetic-backup-key", "secretKey": "synthetic-backup-secret"}],
                "actions": [f"Read:{bucket}", f"Write:{bucket}", f"List:{bucket}"],
            },
        ]
    }
    auth_path = _private_file(private / "seaweedfs-s3.json", json.dumps(storage_auth))
    cert_path = _private_file(private / "storage.crt", "synthetic cert\n", 0o644)
    ca_path = _private_file(private / "storage-ca.crt", "synthetic ca\n", 0o644)
    key_path = _private_file(private / "storage.key", "synthetic key\n")

    role_path_keys = {value: str(path) for key, path in roles.items() for value in [PRODUCTION["ROLE_ENV_KEYS"][key]]}
    env_values = {
        **role_path_keys,
        "APP_ENV": "production", "APP_DEBUG": "false", "APP_DEMO_MODE": "true",
        "PROVIDER_MODE": "mock", "DATA_MODE": "synthetic", "APP_ORIGIN": "https://align.test.invalid",
        "APP_HOST": "align.test.invalid", "ALIGN_CADDYFILE": "./docker/Caddyfile.production",
        "S3_BUCKET": bucket, "OPERATOR_DB_PASSWORD": "synthetic-test-only",
        "ALIGN_SEAWEEDFS_S3_CONFIG_FILE": str(auth_path),
        "ALIGN_SEAWEEDFS_TLS_CERT_FILE": str(cert_path),
        "ALIGN_SEAWEEDFS_TLS_KEY_FILE": str(key_path),
        "ALIGN_SEAWEEDFS_CA_CERT_FILE": str(ca_path),
    }
    pin = "a" * 64
    image_names = {
        "ALIGN_POSTGRES_IMAGE": "pgvector/pgvector:pg17",
        "ALIGN_REDIS_IMAGE": "redis:7-alpine",
        "ALIGN_SEAWEEDFS_IMAGE": "chrislusf/seaweedfs:synthetic-test",
        "ALIGN_API_IMAGE": "registry.test.invalid/align-api:synthetic-test",
        "ALIGN_WORKER_IMAGE": "registry.test.invalid/align-worker:synthetic-test",
        "ALIGN_WEB_IMAGE": "registry.test.invalid/align-web:synthetic-test",
        "ALIGN_CADDY_IMAGE": "caddy:2.10.2-alpine",
    }
    for key, name in image_names.items():
        env_values[key] = f"{name}@sha256:{pin}"
    env_path = _private_file(tmp_path / "compose.env", "\n".join(f"{k}={v}" for k, v in env_values.items()) + "\n")

    report = PRODUCTION["build_report"](SimpleNamespace(
        compose_file=ROOT / "docker-compose.production.yml", env_file=env_path,
        project="align-prod-test", render_compose=False, docker="docker",
        backup_restore_evidence=None, policy_evidence=None, release_evidence=None,
        alert_evidence=None, rollback_evidence=None, egress_evidence=None,
    ))
    assert report["status"] == "incomplete"
    assert report["read_only"] is True
    assert report["provider_call_count"] == 0
    assert report["release_activation_performed"] is False
    assert "synthetic-app-test-secret" not in json.dumps(report)
    assert "bucket_scoped_s3_identities" in {item["name"] for item in report["checks"]}
    assert not [item for item in report["checks"] if item["status"] == "failed"]

