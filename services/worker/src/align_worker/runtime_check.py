"""Fail fast when the parser image cannot satisfy its immutable runtime contract."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

from align_worker.celery_app import celery_app

PARSER_TASKS = {
    "align.parse_context",
    "align.parse_rubric",
    "align.parse_submission",
}


def verify_parser_runtime() -> dict[str, Any]:
    """Check settings, task registration, parser packages, OCR, models and profiles."""
    from align_api.config import get_settings
    from align_api.idempotency import canonical_hash
    from align_api.parser_profiles import profile_payload
    from align_worker import tasks as _tasks  # noqa: F401 -- register shared Celery tasks
    from align_worker.parser_child import _verify_docling_resources

    settings = get_settings()
    config_path = Path(os.environ.get("ALIGN_PARSER_RESOURCE_CONFIG", "/app/config/parser-context-resources.json"))
    if not config_path.is_file():
        raise RuntimeError(f"parser resource configuration is missing: {config_path}")
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise RuntimeError("parser resource configuration has an invalid schema")
    missing_tasks = PARSER_TASKS - set(celery_app.tasks)
    if missing_tasks:
        raise RuntimeError(f"parser tasks are not registered: {', '.join(sorted(missing_tasks))}")

    for module in ("docling", "docx", "openpyxl", "pypdf"):
        if importlib.util.find_spec(module) is None:
            raise RuntimeError(f"required parser dependency is unavailable: {module}")
    tesseract = shutil.which("tesseract")
    if tesseract is None:
        raise RuntimeError("required OCR executable is unavailable: tesseract")
    ocr_version = subprocess.check_output(  # noqa: S603 -- resolved installed OCR executable
        [tesseract, "--version"], text=True
    ).splitlines()[0]
    if ocr_version != f"tesseract {payload['ocr']['version']}":
        raise RuntimeError("OCR executable version does not match the resource manifest")

    # Hash pinned model and OCR bytes once at process startup. Healthchecks use
    # broker ping only and never repeat this comparatively expensive work.
    _verify_docling_resources()
    receipt_path = Path(os.environ.get("DOCLING_ARTIFACTS_PATH", "/opt/docling-models")) / "ocr-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    expected_receipt = [
        {"path": item["path"], "sha256": item["sha256"]}
        for item in payload["ocr"].get("artifacts", [])
    ]
    if sorted(receipt, key=lambda item: item["path"]) != sorted(
        expected_receipt, key=lambda item: item["path"]
    ):
        raise RuntimeError("OCR startup receipt does not match the pinned resources")

    profiles = {
        key: canonical_hash(profile_payload(settings, key))
        for key in (settings.parser_profile_native, settings.parser_profile_document)
    }
    return {"status": "ready", "parser_profiles": profiles, "task_count": len(PARSER_TASKS)}


def worker_responding() -> bool:
    """Confirm the target worker answers a bounded broker ping without loading tasks."""
    destination = f"celery@{socket.gethostname()}"
    previous_timeout = celery_app.conf.broker_connection_timeout
    previous_retries = celery_app.conf.broker_connection_max_retries
    try:
        # This command runs in a separate healthcheck process. Bound only its
        # broker connect/retry policy, leaving the long-lived worker unchanged.
        celery_app.conf.broker_connection_timeout = 2
        celery_app.conf.broker_connection_max_retries = 0
        replies = celery_app.control.ping(timeout=2, destination=[destination])
    except Exception:
        return False
    finally:
        celery_app.conf.broker_connection_timeout = previous_timeout
        celery_app.conf.broker_connection_max_retries = previous_retries
    for reply in replies or []:
        response = reply.get(destination)
        if response == "pong" or isinstance(response, dict) and response.get("ok") == "pong":
            return True
    return False


def main() -> None:
    if "--healthcheck" in sys.argv[1:]:
        raise SystemExit(0 if worker_responding() else 1)
    result = verify_parser_runtime()
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
