"""Immutable B1 parser configuration shared by the API and the parser worker."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from align_api.config import Settings
from align_api.idempotency import canonical_hash
from align_api.models import MethodManifest


def profile_payload(settings: Settings, profile_key: str) -> dict[str, Any]:
    if profile_key not in {settings.parser_profile_native, settings.parser_profile_document}:
        raise ValueError("parser profile is not active")
    heavy = profile_key == settings.parser_profile_document
    resources = json.loads(Path("config/parser-context-resources.json").read_text())
    implementation_hash = canonical_hash({
        name: hashlib.sha256((Path("services/worker/src/align_worker") / name).read_bytes()).hexdigest()
        for name in ("parser_child.py", "context_parser.py", "parser_admission.py")
    })
    return {
        "schema_version": 1, "profile_key": profile_key, "version_no": 1,
        "backend": "docling" if heavy else "python-docx+ooxml",
        "formats": ["pdf", "jpg", "jpeg", "png"] if heavy else ["docx", "xlsx"],
        "package_lock": {"uv_lock_sha256": hashlib.sha256(Path("uv.lock").read_bytes()).hexdigest(),
                         "docling-slim": "2.140.0", "python-docx": "1.2.0", "openpyxl": "3.1.5"},
        "implementation_sha256": implementation_hash,
        "model_artifacts": resources["models"] if heavy else [],
        "ocr": resources["ocr"] if heavy else None,
        "options": resources["options"] if heavy else {},
        "options_hash": canonical_hash(resources["options"] if heavy else {}),
        "memory_limit_bytes": settings.parser_document_memory_bytes if heavy else settings.parser_native_memory_bytes,
        "soft_timeout_seconds": settings.parser_task_soft_seconds,
        "hard_timeout_seconds": settings.parser_task_hard_seconds,
        "initial_page_batch_size": settings.parser_page_batch_size, "heavy": heavy,
    }


def register_manifest(db: Session, kind: str, payload: dict[str, Any]) -> MethodManifest:
    digest = canonical_hash(payload)
    db.execute(insert(MethodManifest).values(
        id=uuid4(), kind=kind, version_label=f"b1-{kind}-{digest[:16]}",
        content_json=payload, sha256=digest,
    ).on_conflict_do_nothing(index_elements=[MethodManifest.sha256]))
    result = db.scalar(select(MethodManifest).where(MethodManifest.sha256 == digest))
    if result is None or result.kind != kind or result.content_json != payload:
        raise RuntimeError("parser manifest registration mismatch")
    return result
