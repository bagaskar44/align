#!/usr/bin/env python3
"""Read-only software/config preflight for standalone production operations.

No LLM request, release activation, migration, or application data write is
performed. Evidence and external operator inputs are required for a ready
result; missing inputs remain incomplete.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE_ENV_KEYS = {
    "postgres": "ALIGN_POSTGRES_ENV_FILE",
    "migration": "ALIGN_MIGRATION_ENV_FILE",
    "api": "ALIGN_API_ENV_FILE",
    "worker": "ALIGN_WORKER_ENV_FILE",
    "parser": "ALIGN_PARSER_WORKER_ENV_FILE",
    "dispatcher": "ALIGN_DISPATCHER_ENV_FILE",
    "scheduler": "ALIGN_SCHEDULER_ENV_FILE",
    "operator": "ALIGN_OPERATOR_ENV_FILE",
}
EXTERNAL_IMAGE_KEYS = {
    "postgres": "ALIGN_POSTGRES_IMAGE",
    "redis": "ALIGN_REDIS_IMAGE",
    "object-storage": "ALIGN_SEAWEEDFS_IMAGE",
    "api": "ALIGN_API_IMAGE",
    "migrate": "ALIGN_API_IMAGE",
    "operator": "ALIGN_API_IMAGE",
    "worker": "ALIGN_WORKER_IMAGE",
    "parser-worker": "ALIGN_WORKER_IMAGE",
    "outbox-dispatcher": "ALIGN_WORKER_IMAGE",
    "scheduler": "ALIGN_WORKER_IMAGE",
    "web": "ALIGN_WEB_IMAGE",
    "proxy": "ALIGN_CADDY_IMAGE",
}


class ComposeOverride(list[Any]):
    """Sequence tagged !override so static merge matches Compose behavior."""


def read_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.is_file():
        return result
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if value[:1] in {"'", '"'} and value[-1:] == value[:1]:
            value = value[1:-1]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            result[key] = value
    return result


def add(checks: list[dict[str, str]], name: str, status: str, detail: str) -> None:
    checks.append({"name": name, "status": status, "detail": detail})


def finish(checks: list[dict[str, str]]) -> str:
    if any(item["status"] == "failed" for item in checks):
        return "failed"
    if any(item["status"] == "pending" for item in checks):
        return "incomplete"
    return "passed"


def _load_yaml(path: Path, checks: list[dict[str, str]], label: str) -> dict[str, Any]:
    try:
        class ComposeLoader(yaml.SafeLoader):
            pass

        def construct_override(loader: ComposeLoader, node: yaml.Node) -> Any:
            if isinstance(node, yaml.SequenceNode):
                return ComposeOverride(loader.construct_sequence(node, deep=True))
            if isinstance(node, yaml.MappingNode):
                return loader.construct_mapping(node, deep=True)
            return loader.construct_scalar(node)

        ComposeLoader.add_constructor("!override", construct_override)
        ComposeLoader.add_constructor("!reset", construct_override)
        payload = yaml.load(
            path.read_text(encoding="utf-8"), Loader=ComposeLoader  # noqa: S506 -- SafeLoader plus two Compose tags.
        )
    except (OSError, yaml.YAMLError):
        add(checks, label, "failed", "file is missing or invalid YAML")
        return {}
    if not isinstance(payload, dict):
        add(checks, label, "failed", "root must be a YAML object")
        return {}
    add(checks, label, "passed", "valid YAML")
    return payload


def _published_ports(service: dict[str, Any]) -> set[int]:
    ports: set[int] = set()
    for item in service.get("ports", []) or []:
        target: Any = None
        if isinstance(item, int):
            target = item
        elif isinstance(item, str):
            target = item.rsplit(":", 1)[-1].split("/", 1)[0]
        elif isinstance(item, dict):
            target = item.get("target")
        try:
            ports.add(int(target))
        except (TypeError, ValueError):
            continue
    return ports


def _published_hosts(service: dict[str, Any]) -> set[str]:
    hosts: set[str] = set()
    for item in service.get("ports", []) or []:
        if isinstance(item, str):
            parts = item.rsplit(":", 2)
            if len(parts) == 3:
                hosts.add(parts[0].strip("[]"))
            elif len(parts) == 2:
                hosts.add("")
        elif isinstance(item, dict):
            hosts.add(str(item.get("host_ip", "")))
    return hosts


def _service_env(service: dict[str, Any]) -> dict[str, str]:
    raw = service.get("environment", {}) or {}
    if isinstance(raw, list):
        return {item.split("=", 1)[0]: item.split("=", 1)[1] if "=" in item else "" for item in raw}
    return {str(key): str(value) for key, value in raw.items()}


def _image_value(value: Any, env: dict[str, str]) -> str:
    raw = str(value or "")
    match = re.fullmatch(r"\$\{([A-Z0-9_]+)(?::[^}]*)?\}", raw)
    return env.get(match.group(1), "") if match else raw


def _digest_pinned(value: str) -> bool:
    return bool(re.search(r"@sha256:[0-9a-f]{64}$", value))


def _is_configured(value: str) -> bool:
    return bool(value.strip()) and not any(
        marker in value for marker in ("REPLACE_", "URL_ENCODED_SECRET", "example.edu")
    )


def _deep_merge(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Approximate Compose's mapping merge for static checks; Compose remains authoritative."""
    merged = dict(left)
    for key, value in right.items():
        if isinstance(value, ComposeOverride):
            merged[key] = list(value)
        elif isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        elif isinstance(merged.get(key), list) and isinstance(value, list):
            merged[key] = [*merged[key], *value]
        else:
            merged[key] = value
    return merged


def _evidence(path: Path | None, kind: str) -> tuple[str, str]:
    if path is None or not path.is_file():
        return "pending", f"{kind} evidence has not been supplied"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "failed", f"{kind} evidence is not valid JSON"
    if not isinstance(data, dict) or data.get("status") not in {"passed", "verified", "eligible", "accepted"}:
        return "pending", f"{kind} evidence does not record an accepted result"
    if not data.get("evidence_ref") and not data.get("artifact_ref"):
        return "pending", f"{kind} evidence must include a reviewable evidence reference"
    return "passed", f"{kind} evidence reference is present"


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    compose_path = args.compose_file.resolve()
    env_path = args.env_file.resolve()
    compose_env = read_env(env_path)
    add(checks, "compose_env", "passed" if compose_env else "failed",
        "private Compose interpolation file is readable" if compose_env else "Compose env file is missing or empty")
    base_path = ROOT / "docker-compose.yml"
    base = _load_yaml(base_path, checks, "base_compose")
    production = _load_yaml(compose_path, checks, "production_compose")
    base_services = base.get("services", {}) if isinstance(base.get("services"), dict) else {}
    prod_services = production.get("services", {}) if isinstance(production.get("services"), dict) else {}
    services = {
        name: _deep_merge(base_services.get(name, {}), service)
        if isinstance(service, dict) else service
        for name, service in prod_services.items()
    }
    for name, service in base_services.items():
        services.setdefault(name, service)

    required_services = {"postgres", "redis", "object-storage", "migrate", "api", "worker",
                         "outbox-dispatcher", "scheduler", "parser-worker", "web", "proxy"}
    missing_services = sorted(required_services - set(services))
    add(checks, "service_inventory", "passed" if not missing_services else "failed",
        "production service inventory is complete" if not missing_services else "required service is missing")

    role_files_ok = True
    role_envs: dict[str, dict[str, str]] = {}
    for role, key in ROLE_ENV_KEYS.items():
        value = compose_env.get(key, "")
        path = Path(value).expanduser() if value else None
        valid = bool(
            path and path.is_absolute() and path.is_file() and not path.is_symlink()
            and ROOT not in path.resolve().parents
        )
        if valid and path is not None:
            try:
                valid = path.stat().st_mode & 0o077 == 0
                if valid:
                    role_envs[role] = read_env(path)
            except OSError:
                valid = False
        role_files_ok = role_files_ok and valid
    add(checks, "private_role_env", "passed" if role_files_ok else "failed",
        "all role env files exist as absolute private files (mode 0600 or stricter)" if role_files_ok
        else "a role env file is missing, not absolute, symlinked, or accessible by group/other")

    app_env_values = role_envs.get("api", {})
    worker_env_values = role_envs.get("worker", {})
    credentials_present = all(
        _is_configured(app_env_values.get(key, ""))
        for key in ("DATABASE_URL", "REDIS_URL", "S3_ENDPOINT", "S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "CSRF_SERVER_SECRET")
    ) and all(
        _is_configured(worker_env_values.get(key, ""))
        for key in ("WORKER_DATABASE_URL", "REDIS_URL", "S3_ENDPOINT", "S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY")
    )
    add(checks, "application_runtime_secrets", "passed" if credentials_present else "failed",
        "API and worker private env files contain required database, broker, storage and CSRF settings"
        if credentials_present else "API/worker role env files are missing required runtime settings")
    s3_tls_endpoint = all(
        values.get("S3_ENDPOINT", "").startswith("https://object-storage:")
        for values in (app_env_values, worker_env_values)
    )
    add(checks, "s3_tls_endpoint", "passed" if s3_tls_endpoint else "failed",
        "API and worker use the internal HTTPS SeaweedFS endpoint" if s3_tls_endpoint
        else "API and worker S3_ENDPOINT must use https://object-storage:<TLS-port>")

    private_secret_keys = (
        "ALIGN_SEAWEEDFS_S3_CONFIG_FILE", "ALIGN_SEAWEEDFS_TLS_KEY_FILE",
    )
    private_secret_paths_ok = True
    for key in private_secret_keys:
        value = compose_env.get(key, "")
        path = Path(value).expanduser() if value else None
        private_secret_paths_ok = private_secret_paths_ok and bool(
            path and path.is_absolute() and path.is_file() and not path.is_symlink()
            and ROOT not in path.resolve().parents
            and path.stat().st_mode & 0o077 == 0
        )
    certificate_values = ("ALIGN_SEAWEEDFS_TLS_CERT_FILE", "ALIGN_SEAWEEDFS_CA_CERT_FILE")
    certificate_paths_ok = all(
        (path := Path(compose_env.get(key, "")).expanduser()).is_absolute()
        and path.is_file() and not path.is_symlink()
        for key in certificate_values
    )
    add(checks, "private_storage_secrets", "passed" if private_secret_paths_ok and certificate_paths_ok else "failed",
        "SeaweedFS auth config and TLS key are private files; certificate and CA files are present"
        if private_secret_paths_ok and certificate_paths_ok
        else "SeaweedFS auth/TLS files are missing or do not meet private-file requirements")

    storage_auth_path = Path(compose_env.get("ALIGN_SEAWEEDFS_S3_CONFIG_FILE", "")).expanduser()
    storage_auth_ok = False
    try:
        auth_payload = json.loads(storage_auth_path.read_text(encoding="utf-8"))
        bucket = app_env_values.get("S3_BUCKET", "")
        identities = auth_payload.get("identities", []) if isinstance(auth_payload, dict) else []
        by_name = {item.get("name"): item for item in identities if isinstance(item, dict)}
        app_actions = set(by_name.get("align-application", {}).get("actions", []))
        backup_actions = set(by_name.get("align-backup", {}).get("actions", []))
        expected_app = {f"Read:{bucket}", f"Write:{bucket}", f"List:{bucket}", f"Tagging:{bucket}"}
        expected_backup = {f"Read:{bucket}", f"Write:{bucket}", f"List:{bucket}"}
        credentials_non_placeholder = all(
            isinstance(credential, dict)
            and all(isinstance(credential.get(key), str) and _is_configured(credential[key])
                    for key in ("accessKey", "secretKey"))
            for identity in by_name.values()
            for credential in identity.get("credentials", [])
        ) and bool(by_name)
        storage_auth_ok = (
            bool(bucket) and set(by_name) >= {"align-application", "align-backup"}
            and app_actions == expected_app and backup_actions == expected_backup
            and credentials_non_placeholder
        )
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        pass
    add(checks, "bucket_scoped_s3_identities", "passed" if storage_auth_ok else "failed",
        "application and backup identities are separately scoped to the configured bucket"
        if storage_auth_ok else
        "private SeaweedFS auth file must contain separate exact bucket-scoped application and backup policies")

    app_env = compose_env.get("APP_ENV", "")
    app_origin = compose_env.get("APP_ORIGIN", "")
    provider_mode = compose_env.get("PROVIDER_MODE", "")
    data_mode = compose_env.get("DATA_MODE", "")
    app_demo = compose_env.get("APP_DEMO_MODE", "").lower() == "true"
    seed_password = compose_env.get("ALIGN_SEED_PASSWORD", "")
    production_mode_ok = (
        app_env == "production"
        and app_origin.startswith("https://")
        and ((provider_mode == "mock" and data_mode == "synthetic" and app_demo)
             or (provider_mode == "live" and data_mode == "institutional" and not app_demo))
        and not seed_password
        and compose_env.get("APP_DEBUG", "false").lower() == "false"
    )
    add(checks, "environment_mode", "passed" if production_mode_ok else "failed",
        "production HTTPS mode, provider/data pairing, debug-off and no demo seed password are required")

    storage = services.get("object-storage", {})
    storage_image = str(storage.get("image", ""))
    storage_cmd = " ".join(str(item) for item in storage.get("command", []) or [])
    seaweed_ok = (
        "seaweedfs" in storage_image.lower()
        and _digest_pinned(_image_value(storage_image, compose_env))
        and "-s3.config=" in storage_cmd
        and "-s3.cert.file=" in storage_cmd
        and "-s3.key.file=" in storage_cmd
        and "-s3.port.https=0" in storage_cmd
    )
    add(checks, "authenticated_tls_storage", "passed" if seaweed_ok else "failed",
        "SeaweedFS must be digest-pinned with S3 auth config and TLS-only S3 listener flags")

    image_errors = []
    for service_name, key in EXTERNAL_IMAGE_KEYS.items():
        service = services.get(service_name, {})
        image = _image_value(service.get("image"), compose_env)
        expected = compose_env.get(key, "")
        if not image or not _digest_pinned(image) or image != expected:
            image_errors.append(service_name)
    add(checks, "digest_pinned_images", "passed" if not image_errors else "failed",
        "all runtime images match the digest-pinned Compose environment" if not image_errors
        else "runtime images must match digest-pinned values for: " + ", ".join(image_errors))

    proxy = services.get("proxy", {})
    proxy_ports = _published_ports(proxy)
    caddy_env = _service_env(proxy)
    caddy_source = compose_env.get("ALIGN_CADDYFILE", "")
    caddy_mount = any("ALIGN_CADDYFILE" in str(item) for item in proxy.get("volumes", []) or [])
    proxy_hosts = _published_hosts(proxy)
    caddy_ok = (
        proxy_ports == {80, 443}
        and all(host in {"", "0.0.0.0", "::"} for host in proxy_hosts)  # noqa: S104
        and caddy_source.endswith("Caddyfile.production")
        and caddy_mount
        and "APP_ORIGIN" in caddy_env
        and "APP_HOST" in caddy_env
    )
    add(checks, "single_https_origin", "passed" if caddy_ok else "failed",
        "only the Caddy proxy should publish HTTP redirect and HTTPS origin ports")

    exposed_forbidden = {name: sorted(_published_ports(services.get(name, {})))
                         for name in ("postgres", "redis", "object-storage")}
    no_private_ports = all(not ports for ports in exposed_forbidden.values())
    add(checks, "private_dependencies", "passed" if no_private_ports else "failed",
        "database, broker and S3/filer services must not publish host ports")

    parser = services.get("parser-worker", {})
    parser_env = _service_env(parser)
    try:
        profile = int(parser_env.get("PARSER_DOCUMENT_MEMORY_BYTES", "0"))
    except ValueError:
        profile = 0
    memory_limit = str(parser.get("mem_limit", "")).lower()
    parser_ok = profile >= 8_589_934_592 and memory_limit in {"9g", "9gb", "9663676416"}
    add(checks, "parser_resource_profile", "passed" if parser_ok else "failed",
        "parser profile must be 8 GiB with a container limit of at least 9 GiB")

    for service_name in ("api", "worker", "parser-worker", "outbox-dispatcher", "scheduler"):
        service_env = _service_env(services.get(service_name, {}))
        service_profile = service_env.get("PARSER_DOCUMENT_MEMORY_BYTES", "")
        service_node_budget = service_env.get("PARSER_NODE_MEMORY_BUDGET_BYTES", "")
        service_ok = service_profile == "8589934592" and service_node_budget == "8589934592"
        add(checks, "parser_profile_" + service_name, "passed" if service_ok else "failed",
            "application and parser consumers must bind the production 8 GiB profile")

    for attr, kind in (
        ("backup_restore_evidence", "backup_restore"), ("policy_evidence", "policy"),
        ("release_evidence", "release_eligibility"), ("alert_evidence", "operator_alert"),
        ("rollback_evidence", "rollback"), ("egress_evidence", "egress_firewall"),
    ):
        state, detail = _evidence(getattr(args, attr), kind)
        add(checks, kind + "_evidence", state, detail)

    if args.render_compose:
        command = [args.docker, "compose", "--project-name", args.project,
                   "--env-file", str(env_path), "-f", str(base_path), "-f", str(compose_path),
                   "config", "--quiet"]
        try:
            completed = subprocess.run(  # noqa: S603 -- fixed Docker config args, no shell, captured output.
                command, cwd=ROOT, capture_output=True, text=True, timeout=60, check=False
            )
            ok = completed.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        add(checks, "compose_config", "passed" if ok else "failed",
            "Docker Compose config validation passed without printing rendered configuration" if ok
            else "Docker Compose config validation failed; captured output was suppressed")
    else:
        add(checks, "compose_config", "pending", "Compose render check was explicitly skipped")

    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": finish(checks),
        "read_only": True,
        "provider_call_count": 0,
        "release_activation_performed": False,
        "production_compose": str(compose_path),
        "environment_file": str(env_path),
        "checks": checks,
        "limitations": [
            "static readiness does not replace a Linux target deployment, authenticated S3 operation test, restore drill, or load measurement",
            "no secrets or rendered Compose environment values are included in this report",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-file", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--render-compose", action="store_true")
    parser.add_argument("--backup-restore-evidence", type=Path)
    parser.add_argument("--policy-evidence", type=Path)
    parser.add_argument("--release-evidence", type=Path)
    parser.add_argument("--alert-evidence", type=Path)
    parser.add_argument("--rollback-evidence", type=Path)
    parser.add_argument("--egress-evidence", type=Path)
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
        "read_only": True,
        "provider_call_count": 0,
        "release_activation_performed": False,
        "output": str(args.output),
        "failed_checks": [item["name"] for item in report["checks"] if item["status"] == "failed"],
        "pending_checks": [item["name"] for item in report["checks"] if item["status"] == "pending"],
    }))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
