from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from align_api.config import Settings, get_settings
from align_api.context_provider_profiles import context_provider_payload
from align_api.idempotency import canonical_hash
from align_api.models import (
    AssignmentVersion,
    AuditEvent,
    ContextConflict,
    ContextVersion,
    EvidenceItem,
    FileAsset,
    Job,
    JobStage,
    MethodManifest,
    NormativeFileLink,
    OutboxEvent,
    ParseArtifact,
    ParserCheckpoint,
    ProcessingTimeReservation,
    SourceUnit,
    SubmissionVersion,
    SubmissionVersionFile,
)
from align_api.parser_profiles import profile_payload
from align_api.storage import storage_client
from align_worker.context_provider import propose_context_fields
from align_worker.database import SessionFactory
from align_worker.parser_admission import (
    AdmissionLease,
    acquire_parser_lease,
    release_parser_lease,
    renew_parser_lease,
)

MAX_CHILD_RESULT_BYTES = 64 * 1024 * 1024


def _parser_time_limits(
    remaining_ms: int, soft_limit_seconds: int, hard_limit_seconds: int
) -> tuple[int, int] | None:
    if remaining_ms < 1000:
        return None
    budget_seconds = remaining_ms // 1000
    soft_seconds = max(1, min(soft_limit_seconds, budget_seconds))
    hard_seconds = max(soft_seconds, min(hard_limit_seconds, budget_seconds))
    return soft_seconds, hard_seconds


def _safe_limitation(code: str) -> dict[str, str]:
    messages = {
        "parser_capacity_unavailable": "Kapasitas parser belum cukup; berkas tetap dapat ditinjau manual.",
        "parser_resource_unavailable": "Resource parser atau model lokal belum tersedia; berkas tetap dapat ditinjau manual.",
        "parser_isolation_unavailable": "Isolasi proses parser tidak tersedia pada runtime ini.",
        "parser_timeout": "Parser mencapai batas waktu; unit sumber tetap tercatat.",
        "parser_memory_limit": "Sistem operasi mengakhiri parser karena batas memori; unit sumber tetap tercatat.",
        "parser_failed": "Parser gagal membaca sebagian atau seluruh isi; sumber asli tetap tersedia.",
        "parser_storage_unavailable": "Penyimpanan sumber sementara tidak tersedia; coba lagi setelah layanan pulih.",
        "processing_limit": "Batas waktu aktif konteks tercapai; unit berikutnya belum diproses.",
        "parser_pending": "Unit sumber terinventaris; ekstraksi belum selesai.",
        "source_integrity_failed": "Hash atau ukuran sumber tidak cocok; unggah ulang berkas pada draft baru.",
        "parser_lease_lost": "Lease parser tidak lagi sah; hasil belum dapat digunakan.",
    }
    return {"code": code, "message": messages.get(code, "Parser belum menghasilkan ekstraksi yang dapat diverifikasi.")}


def _fallback_inventory(asset: FileAsset, failure_code: str) -> list[dict[str, Any]]:
    preflight = asset.preflight_json
    limitation = _safe_limitation(failure_code)
    root_label = (
        "XLSX package root" if asset.mime == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else "DOCX package root" if asset.mime == "application/vnd.openxmlformats"
        else "PDF root" if asset.mime == "application/pdf" else "IMAGE root"
    )
    root_anchor = {"format": "file", "locator_note": root_label}
    units: list[dict[str, Any]] = [
        {
            "unit_key": canonical_hash(root_anchor),
            "unit_type": "root",
            "anchor": root_anchor,
            "parse_status": "partially_parsed",
            "limitations": [limitation],
        }
    ]
    if asset.mime == "application/pdf":
        for page_no in range(1, int(preflight.get("pages", 0)) + 1):
            anchor = {"format": "pdf", "page": page_no, "locator_note": "Page inventory; exact geometry populated after parsing"}
            units.append(
                {
                    "unit_key": canonical_hash(anchor),
                    "unit_type": "page",
                    "anchor": anchor,
                    "parse_status": "partially_parsed",
                    "limitations": [limitation],
                }
            )
    elif asset.mime in {"image/jpeg", "image/png"}:
        width, height = int(preflight.get("width", 0)), int(preflight.get("height", 0))
        anchor = {
            "format": "image",
            "width": width,
            "height": height,
            "bbox": [0, 0, width, height],
            "coordinate_system": "original_pixels_top_left",
            "transform_id": None,
        }
        units.append(
            {
                    "unit_key": canonical_hash(anchor),
                "unit_type": "image",
                "anchor": anchor,
                "parse_status": "partially_parsed",
                "limitations": [limitation],
            }
        )
    elif asset.mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        content_parts = set(preflight.get("docx_parts", []))
        media_parts = preflight.get("docx_media_parts", [])
        media_set = set(media_parts) if isinstance(media_parts, list) else set()
        package_parts = preflight.get("docx_package_parts") or [*content_parts, *media_set]
        if isinstance(package_parts, list):
            for index, part in enumerate(package_parts, start=1):
                if not isinstance(part, str):
                    continue
                kind = "container" if part in content_parts else "media" if part in media_set else "unsupported"
                anchor = {"format": "docx", "part": part, "structural_path": f"/{part}",
                          "item_kind": "media", "item_index": 0 if part == "word/document.xml" else index}
                units.append({"unit_key": canonical_hash(anchor), "unit_type": kind, "anchor": anchor,
                              "parse_status": "partially_parsed" if kind != "unsupported" else "unsupported",
                              "limitations": [limitation]})
    elif asset.mime == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        sheets = preflight.get("sheets", [])
        if isinstance(sheets, list):
            for _index, item in enumerate(sheets, start=1):
                if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                    continue
                anchor = {"format": "xlsx", "sheet_name": item["name"], "cell_range": "A1"}
                sheet_limitations = [limitation]
                if item.get("state", "visible") != "visible":
                    sheet_limitations.append({"code": "hidden_sheet", "message": "Sheet tersembunyi tetap diinventaris."})
                units.append({"unit_key": canonical_hash(anchor), "unit_type": "container", "anchor": anchor,
                              "parse_status": "partially_parsed", "limitations": sheet_limitations})
        package_parts = preflight.get("xlsx_package_parts", [])
        known_parts = {
            "[Content_Types].xml", "_rels/.rels", "docProps/app.xml", "docProps/core.xml",
            "xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/styles.xml",
            "xl/sharedStrings.xml", "xl/calcChain.xml",
        }
        sheets = preflight.get("sheets", [])
        sheet_name = next((item.get("name") for item in sheets if isinstance(item, dict)), "Sheet1")
        if isinstance(package_parts, list):
            for part in package_parts:
                if not isinstance(part, str) or part in known_parts or (
                    part.startswith("xl/worksheets/") and part.endswith(".xml")
                ):
                    continue
                anchor = {"format": "xlsx", "sheet_name": sheet_name, "cell_range": "A1", "part": part}
                units.append({"unit_key": canonical_hash(anchor), "unit_type": "unsupported", "anchor": anchor,
                              "parse_status": "unsupported", "limitations": [limitation]})
    return units


def _safe_environment(temp_dir: str) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": temp_dir,
        "TMPDIR": temp_dir,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "HF_HUB_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "DOCLING_ARTIFACTS_PATH": os.environ.get("DOCLING_ARTIFACTS_PATH", "/opt/docling-models"),
        "ALIGN_PARSER_RESOURCE_CONFIG": "/app/config/parser-context-resources.json",
        "TOKENIZERS_PARALLELISM": "false",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    }


def _preexec_limits(memory_bytes: int, cpu_seconds: int) -> Any:
    parent_pid = os.getpid()
    def apply() -> None:
        if sys.platform == "win32":
            raise RuntimeError("POSIX parser isolation is required")
        import resource

        if sys.platform == "linux":
            import ctypes
            # Kill the direct child on an abrupt worker death. The admission
            # reservation remains held if cleanup cannot confirm descendants.
            if ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL) != 0:
                os._exit(125)
            if os.getppid() != parent_pid:
                os._exit(125)

        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_CHILD_RESULT_BYTES, MAX_CHILD_RESULT_BYTES))
        resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    return apply


def _cgroup_oom_kills() -> int | None:
    for path in ("/sys/fs/cgroup/memory.events",):
        try:
            values = dict(line.split() for line in Path(path).read_text(encoding="ascii").splitlines() if len(line.split()) == 2)
            if "oom_kill" in values:
                return int(values["oom_kill"])
        except (OSError, ValueError):
            continue
    return None


def _child_peak_rss(pid: int) -> int | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="ascii").splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def _run_child(
    path: str,
    filename: str,
    output_path: str,
    memory_bytes: int,
    soft_seconds: int,
    hard_seconds: int,
    heartbeat: Callable[[], bool] | None = None,
    page: int | None = None,
    memory_observer: Callable[[], None] | None = None,
    poll_interval_seconds: float = 0.25,
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    if sys.platform == "win32" or os.name != "posix":
        return None, "parser_isolation_unavailable", None
    with tempfile.TemporaryDirectory(prefix="align-parser-") as temp_dir:
        try:
            # Fixed Python module invocation; all untrusted filenames are positional arguments and no shell is used.
            oom_before = _cgroup_oom_kills()
            process = subprocess.Popen(  # noqa: S603
                [sys.executable, "-m", "align_worker.parser_child", path, filename, output_path, *([str(page)] if page else [])],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=temp_dir,
                env=_safe_environment(temp_dir),
                close_fds=True,
                start_new_session=True,
                preexec_fn=_preexec_limits(memory_bytes, hard_seconds),
            )
        except (OSError, subprocess.SubprocessError):
            return None, "parser_resource_unavailable", None
        deadline = time.monotonic() + max(1, min(soft_seconds, hard_seconds))
        failure: str | None = None
        peak_rss = 0
        last_heartbeat = time.monotonic()
        try:
            while process.poll() is None:
                peak_rss = max(peak_rss, _child_peak_rss(process.pid) or 0)
                if memory_observer is not None:
                    memory_observer()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    failure = "parser_timeout"
                    break
                if heartbeat is not None and time.monotonic() - last_heartbeat >= 10 and not heartbeat():
                    failure = "parser_lease_lost"
                    break
                if heartbeat is not None and time.monotonic() - last_heartbeat >= 10:
                    last_heartbeat = time.monotonic()
                try:
                    process.wait(timeout=min(max(0.01, poll_interval_seconds), remaining))
                except subprocess.TimeoutExpired:
                    pass
            if failure:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=max(1, hard_seconds - soft_seconds))
                except subprocess.TimeoutExpired:
                    pass
        finally:
            # Also terminate descendants after a successful parent exit. Release
            # the admission lease only after this process-group cleanup returns.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        peak_rss = max(peak_rss, _child_peak_rss(process.pid) or 0)
        if failure:
            return None, failure, str(process.returncode)
        oom_after = _cgroup_oom_kills()
        if process.returncode == -signal.SIGKILL and oom_before is not None and oom_after is not None and oom_after > oom_before:
            return None, "parser_memory_limit", str(process.returncode)
        if not Path(output_path).is_file():
            return None, "parser_failed", str(process.returncode)
        output = Path(output_path)
        if output.stat().st_size > MAX_CHILD_RESULT_BYTES:
            return None, "parser_failed", str(process.returncode)
        try:
            payload = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None, "parser_failed", str(process.returncode)
        if not isinstance(payload, dict):
            return None, "parser_failed", str(process.returncode)
        if process.returncode != 0 and not payload.get("failure_code"):
            return None, "parser_failed", str(process.returncode)
        payload["_runtime"] = {"peak_rss_bytes": peak_rss or None, "cgroup_oom_kills_before": oom_before,
                                "cgroup_oom_kills_after": oom_after}
        return payload, None, str(process.returncode)


def _create_or_update_units(db: Any, artifact: ParseArtifact, result_units: list[dict[str, Any]]) -> dict[str, UUID]:
    root_id: UUID | None = None
    unit_ids: dict[str, UUID] = {}
    normalized_units: list[tuple[dict[str, Any], UUID]] = []
    for index, item in enumerate(result_units):
        if not isinstance(item, dict):
            continue
        key = item.get("unit_key")
        anchor = item.get("anchor")
        if not isinstance(key, str) or not isinstance(anchor, dict):
            continue
        source_order = 0 if item.get("unit_type") == "root" else int(anchor.get("page", 0)) * 100_000 + index
        existing = db.scalar(
            select(SourceUnit).where(SourceUnit.parse_artifact_id == artifact.id, SourceUnit.locator_key == key).with_for_update()
        )
        if existing is None:
            existing = SourceUnit(
                parse_artifact_id=artifact.id,
                locator_key=key,
                unit_type=str(item.get("unit_type", "unsupported"))[:24],
                source_order=source_order,
                anchor_json=anchor,
                parse_status=str(item.get("parse_status", "partially_parsed")),
                limitations_json=item.get("limitations", []),
            )
            db.add(existing)
            db.flush()
        else:
            existing.unit_type = str(item.get("unit_type", "unsupported"))[:24]
            existing.source_order = min(existing.source_order, source_order)
            existing.anchor_json = anchor
            existing.parse_status = str(item.get("parse_status", "partially_parsed"))
            existing.limitations_json = item.get("limitations", [])
        if item.get("unit_type") == "root":
            root_id = existing.id
        normalized_units.append((item, existing.id))
        unit_ids[key] = existing.id
    page_ids: dict[int, UUID] = {}
    image_id: UUID | None = None
    container_ids: dict[str, UUID] = {}
    spreadsheet_ids: dict[str, UUID] = {}
    for item, unit_id in normalized_units:
        anchor = item.get("anchor", {})
        if item.get("unit_type") == "page" and anchor.get("format") == "pdf":
            page_ids[int(anchor["page"])] = unit_id
        elif item.get("unit_type") == "image" and anchor.get("format") == "image":
            image_id = unit_id
        elif item.get("unit_type") == "container" and anchor.get("format") == "docx":
            container_ids[str(anchor.get("part", ""))] = unit_id
        elif item.get("unit_type") == "container" and anchor.get("format") == "xlsx":
            spreadsheet_ids[str(anchor.get("sheet_name", ""))] = unit_id
    for item, unit_id in normalized_units:
        if item.get("unit_type") == "root":
            continue
        anchor = item.get("anchor", {})
        parent_id = root_id
        if anchor.get("format") == "pdf" and item.get("unit_type") != "page":
            parent_id = page_ids.get(int(anchor.get("page", 0)), root_id)
        elif anchor.get("format") == "image" and item.get("unit_type") != "image":
            parent_id = image_id or root_id
        elif anchor.get("format") == "docx" and item.get("unit_type") not in {"container", "root"}:
            parent_id = container_ids.get(str(anchor.get("part", "")), root_id)
        elif anchor.get("format") == "xlsx" and item.get("unit_type") not in {"container", "root"}:
            parent_id = spreadsheet_ids.get(str(anchor.get("sheet_name", "")), root_id)
        existing = db.get(SourceUnit, unit_id)
        if existing is not None:
            existing.parent_id = parent_id
    return unit_ids


def _inventory_for_failure(db: Any, artifact: ParseArtifact, asset: FileAsset, failure_code: str) -> tuple[dict[str, UUID], list[dict[str, Any]]]:
    units = _fallback_inventory(asset, failure_code)
    unit_ids = _create_or_update_units(db, artifact, units)
    return unit_ids, units


def _insert_evidence(
    db: Any,
    artifact: ParseArtifact,
    asset: FileAsset,
    namespace_id: UUID,
    namespace_kind: str,
    unit_ids: dict[str, UUID],
    result: dict[str, Any],
) -> int:
    records = result.get("evidence", [])
    if not isinstance(records, list):
        return 0
    inserted = 0
    for item in records:
        if not isinstance(item, dict):
            continue
        unit_id = unit_ids.get(str(item.get("unit_key", "")))
        content = item.get("content")
        anchor = item.get("anchor")
        kind = item.get("type")
        if unit_id is None or not isinstance(content, dict) or not isinstance(anchor, dict) or kind not in {
            "paragraph", "table", "diagram", "image_text", "numeric_observation", "spreadsheet_cell", "spreadsheet_range"
        }:
            continue
        exists = db.scalar(
            select(EvidenceItem.id).where(
                EvidenceItem.source_unit_id == unit_id,
                EvidenceItem.file_asset_id == asset.id,
                EvidenceItem.type == kind,
            )
        )
        if exists is not None:
            continue
        namespace = (
            {"context_version_id": namespace_id} if namespace_kind == "context"
            else {"submission_version_id": namespace_id} if namespace_kind == "submission"
            else {"assignment_version_id": namespace_id}
        )
        db.add(
            EvidenceItem(
                source_unit_id=unit_id,
                file_asset_id=asset.id,
                source_sha256=asset.sha256,
                type=kind,
                anchor_json=anchor,
                content_original_json=content,
                content_normalized_json=content,
                parse_status=str(item.get("parse_status", "parsed")),
                limitations_json=item.get("limitations", []),
                parser_manifest_id=artifact.parser_manifest_id,
                **namespace,
            )
        )
        inserted += 1
    return inserted


def _candidate_ref(asset: FileAsset, source_role: str, candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_id": str(asset.id),
        "source_sha256": asset.sha256,
        "source_role": source_role,
        "anchor": candidate.get("anchor", {}),
    }


def _merge_field_candidates(db: Any, context_id: UUID, candidates: list[dict[str, Any]], request_id: UUID) -> bool:
    context = db.scalar(select(ContextVersion).where(ContextVersion.id == context_id).with_for_update())
    if context is None or context.state != "draft":
        return False
    grouped: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        key = candidate.get("field_path")
        value = candidate.get("value")
        source_ref = candidate.get("source_ref")
        if isinstance(key, str) and isinstance(value, str) and key in {
            "course_code", "course_title", "context_description", "number_locale", "language"
        } and isinstance(source_ref, dict):
            grouped.setdefault(key, []).append({"value": value.strip(), "source_ref": source_ref})
    fields = dict(context.fields_json)
    changed = False
    for key, source_candidates in grouped.items():
        normalized_values: dict[str, dict[str, Any]] = {}
        for candidate in source_candidates:
            normalized_values.setdefault(candidate["value"].strip().casefold(), candidate)
        current = fields.get(key, {})
        current_value = str(current.get("value", "")).strip() if isinstance(current, dict) else ""
        current_candidate = {
            "value": current_value,
            "source_ref": {"kind": "course_catalog"} if key in {"course_code", "course_title"} else None,
        }
        differs = bool(current_value) and any(value != current_value.casefold() for value in normalized_values)
        multiple_sources = len(normalized_values) > 1
        conflict = db.scalar(
            select(ContextConflict)
            .where(ContextConflict.context_version_id == context_id, ContextConflict.field_path == key)
            .with_for_update()
        )
        if conflict is not None and conflict.resolution_json is not None:
            prior_candidates = list(conflict.candidate_values_json)
            prior_values = {str(item.get("value", "")).strip().casefold() for item in prior_candidates}
            new_values = {
                str(item.get("value", "")).strip().casefold()
                for item in source_candidates
                if str(item.get("value", "")).strip().casefold() not in prior_values
            }
            merged_candidates = [*prior_candidates, *source_candidates]
            unique_unresolved_candidates: list[dict[str, Any]] = []
            seen_unresolved: set[tuple[str, str]] = set()
            for item in merged_candidates:
                value = str(item.get("value", "")).strip()
                ref = item.get("source_ref")
                signature = (value.casefold(), canonical_hash(ref) if isinstance(ref, dict) else "manual")
                if value and signature not in seen_unresolved:
                    unique_unresolved_candidates.append(item)
                    seen_unresolved.add(signature)
            if new_values:
                history = list(conflict.resolution_history_json or [])
                history.append(dict(conflict.resolution_json))
                conflict.resolution_history_json = history
                conflict.resolution_json = None
                conflict.resolved_by = None
                conflict.resolved_at = None
                conflict.candidate_values_json = unique_unresolved_candidates
                fields[key] = {**(current if isinstance(current, dict) else {}), "resolution_status": "conflicting"}
                changed = True
            elif unique_unresolved_candidates != prior_candidates:
                conflict.candidate_values_json = unique_unresolved_candidates
                refs = [item["source_ref"] for item in unique_unresolved_candidates if isinstance(item.get("source_ref"), dict)]
                distinct = {canonical_hash(ref): ref for ref in refs}
                if isinstance(current, dict):
                    fields[key] = {**current, "source_refs": list(distinct.values())}
                changed = True
            continue
        if differs or multiple_sources:
            candidate_values = [current_candidate] if current_value else []
            candidate_values.extend(source_candidates)
            unique_candidates: list[dict[str, Any]] = []
            seen: set[tuple[str, str]] = set()
            for candidate in candidate_values:
                value = str(candidate.get("value", "")).strip()
                ref = candidate.get("source_ref")
                ref_hash = canonical_hash(ref) if isinstance(ref, dict) else "manual"
                signature = (value.casefold(), ref_hash)
                if value and signature not in seen:
                    unique_candidates.append(candidate)
                    seen.add(signature)
            if conflict is None:
                conflict = ContextConflict(
                    context_version_id=context_id,
                    field_path=key,
                    candidate_values_json=unique_candidates,
                )
                db.add(conflict)
            else:
                previous_values = conflict.candidate_values_json
                conflict.candidate_values_json = unique_candidates
                if previous_values == unique_candidates and isinstance(current, dict) and current.get("resolution_status") == "conflicting":
                    continue
            fields[key] = {
                **(current if isinstance(current, dict) else {}),
                "source_refs": [candidate["source_ref"] for candidate in source_candidates],
                "resolution_status": "conflicting",
            }
            changed = True
            continue
        chosen = next(iter(normalized_values.values()))
        if current_value and current_value.casefold() == chosen["value"].casefold():
            current_refs = current.get("source_refs", []) if isinstance(current, dict) else []
            refs = [*current_refs, *(item["source_ref"] for item in source_candidates)]
            distinct = {canonical_hash(ref): ref for ref in refs if isinstance(ref, dict)}
            if isinstance(current, dict) and current.get("source_refs") != list(distinct.values()):
                fields[key] = {**current, "source_refs": list(distinct.values())}
                changed = True
            continue
        fields[key] = {
            "value": chosen["value"],
            "origin": "extracted",
            "source_refs": [item["source_ref"] for item in source_candidates],
            "resolution_status": "proposed",
        }
        changed = True
    if not changed:
        return False
    context.fields_json = fields
    context.revision += 1
    db.add(
        AuditEvent(
            worker_id="align-parser",
            action="context.parser_candidates.recorded",
            object_type="context_version",
            object_id=context.id,
            old_version_refs_json={"revision": context.revision - 1},
            new_version_refs_json={"revision": context.revision, "candidate_fields": sorted(grouped)},
            request_id=request_id,
        )
    )
    return True


def _download_and_run(asset: FileAsset, settings: Settings, lease: AdmissionLease, soft_seconds: int, hard_seconds: int, heartbeat: Callable[[], bool] | None = None, page: int | None = None) -> tuple[dict[str, Any] | None, str | None, str | None]:
    started = time.monotonic()
    suffix = Path(asset.original_name).suffix.lower()
    with tempfile.TemporaryDirectory(prefix="align-source-") as temp_dir:
        source_path = str(Path(temp_dir) / f"source{suffix}")
        output_path = str(Path(temp_dir) / "result.json")
        try:
            storage_client(settings).download_file(settings.s3_bucket, asset.object_key, source_path)
        except Exception:
            return None, "parser_storage_unavailable", None
        digest = hashlib.sha256()
        size = 0
        with open(source_path, "rb") as source:
            while chunk := source.read(64 * 1024):
                size += len(chunk)
                if size > settings.max_file_bytes:
                    return None, "source_integrity_failed", None
                digest.update(chunk)
        if size != asset.size_bytes or digest.hexdigest() != asset.sha256:
            return None, "source_integrity_failed", None
        elapsed = int(time.monotonic() - started)
        if elapsed >= hard_seconds:
            return None, "parser_timeout", None
        return _run_child(source_path, asset.original_name, output_path, lease.memory_bytes, max(1, soft_seconds - elapsed), hard_seconds - elapsed, heartbeat, page)


def _finish_checkpoint(
    db: Any,
    job: Job,
    artifact: ParseArtifact,
    asset: FileAsset,
    checkpoint: ParserCheckpoint,
    result: dict[str, Any] | None,
    failure_code: str | None,
    process_exit: str | None,
    namespace_id: UUID,
    namespace_kind: str,
    reservation: ProcessingTimeReservation,
    started: float,
    request_id: UUID,
    page: int | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
    reservation.actual_active_ms = elapsed_ms
    reservation.state = "settled"
    reservation.finished_at = datetime.now(UTC)
    if result is not None and result.get("failure_code"):
        failure_code = str(result["failure_code"])
    limitations = result.get("limitations", []) if result is not None else []
    if not isinstance(limitations, list):
        limitations = []
    if failure_code:
        limitations = [*limitations, _safe_limitation(failure_code)]
        result_units = _fallback_inventory(asset, failure_code)
        if page is not None:
            result_units = [item for item in result_units if item["unit_type"] == "root" or item["anchor"].get("page") == page]
        unit_ids = _create_or_update_units(db, artifact, result_units)
        evidence_count = 0
        artifact.status = "blocked" if failure_code in {"parser_capacity_unavailable", "parser_storage_unavailable"} else "unsupported" if failure_code in {"parser_resource_unavailable", "parser_isolation_unavailable"} else "failed"
        checkpoint.status = "blocked" if artifact.status == "blocked" else "failed"
        checkpoint.limitations_json = limitations
        checkpoint.covered_unit_refs_json = [item["anchor"] for item in result_units]
        checkpoint.output_refs_json = {"failure_code": failure_code, "process_exit": process_exit}
    else:
        assert result is not None
        result_units = result.get("units", [])
        if not isinstance(result_units, list):
            result_units = []
        unit_ids = _create_or_update_units(db, artifact, result_units)
        evidence_count = _insert_evidence(
            db, artifact, asset, namespace_id, namespace_kind, unit_ids, result
        )
        artifact_status = result.get("parse_status", "parsed")
        artifact.status = artifact_status if artifact_status in {"parsed", "partially_parsed", "unreadable", "unsupported", "failed"} else "partially_parsed"
        artifact.limitations_json = limitations
        checkpoint.status = "succeeded"
        checkpoint.limitations_json = limitations
        checkpoint.covered_unit_refs_json = [item.get("anchor", {}) for item in result_units if isinstance(item, dict)]
        runtime_value = result.get("_runtime")
        runtime_metrics: dict[str, Any] = runtime_value if isinstance(runtime_value, dict) else {}
        parser_result = {key: value for key, value in result.items() if key != "_runtime"}
        serialized = json.dumps(parser_result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        peak_rss_bytes = runtime_metrics.get("peak_rss_bytes")
        if isinstance(peak_rss_bytes, int) and peak_rss_bytes > 0:
            checkpoint.peak_memory_bytes = peak_rss_bytes
        checkpoint.output_refs_json = {
            "result_sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            "unit_count": len(unit_ids),
            "evidence_count": evidence_count,
            "parser": result.get("parser", "unknown"),
            "transforms": result.get("transforms", []),
            "covered_stages": result.get("covered_stages", []),
            "ocr_decision": result.get("ocr_decision"),
            "has_tables": any(item.get("type") == "table" for item in result.get("evidence", [])),
            "runtime": runtime_metrics,
        }
        raw_candidates = result.get("candidate_fields", [])
        if isinstance(raw_candidates, list):
            checkpoint.output_refs_json["candidate_fields"] = [
                {
                    "field_path": item["field_path"],
                    "value": item["value"],
                    "anchor": item.get("anchor", {}),
                }
                for item in raw_candidates[:100]
                if isinstance(item, dict)
                and item.get("field_path") in {"course_code", "course_title", "context_description", "number_locale", "language"}
                and isinstance(item.get("value"), str)
                and item["value"].strip()
                and len(item["value"]) <= 4000
            ]
    checkpoint.finished_at = datetime.now(UTC)
    checkpoint.actual_active_ms = reservation.actual_active_ms
    if process_exit is not None:
        checkpoint.output_refs_json["process_exit"] = process_exit
    checkpoint.output_refs_json["file_sha256"] = asset.sha256
    checkpoint.output_refs_json["task_key"] = checkpoint.task_key
    checkpoint.output_refs_json["child_isolated"] = True
    artifact.limitations_json = limitations
    return evidence_count, limitations


def _execute_parser_job(
    job_id: UUID,
    expected_fence: int,
    expected_fingerprint: str,
    namespace_kind: str,
) -> dict[str, Any]:
    settings = get_settings()
    expected_job_type = {"context": "context_draft", "assignment": "rubric_draft", "submission": "submission_parse"}[namespace_kind]
    stage_name = {"context": "parse_context", "assignment": "parse_rubric", "submission": "parse_submission"}[namespace_kind]
    with SessionFactory() as db:
        job = db.get(Job, job_id)
        if job is None or job.type != expected_job_type:
            return {"status": "missing"}
        if job.lease_fence != expected_fence or job.input_fingerprint != expected_fingerprint:
            return {"status": "stale", "fenced": True}
        namespace_id = job.subject_id
        source_ids = job.scope_json.get("file_ids", [])
        parse_ids = job.scope_json.get("parse_artifact_ids", [])
        if not isinstance(source_ids, list) or not isinstance(parse_ids, list) or len(source_ids) != len(parse_ids):
            return {"status": "failed", "reason": "PARSER_SOURCE_SCOPE_INVALID"}
        rows: list[tuple[FileAsset, NormativeFileLink | None, ParseArtifact]]
        if namespace_kind == "submission":
            submission_query = (
                select(FileAsset, ParseArtifact)
                .join(SubmissionVersionFile, SubmissionVersionFile.file_asset_id == FileAsset.id)
                .join(ParseArtifact, ParseArtifact.file_asset_id == FileAsset.id)
                .where(
                    SubmissionVersionFile.submission_version_id == namespace_id,
                    ParseArtifact.submission_version_id == namespace_id,
                    FileAsset.purpose == "submission",
                )
            )
            selected = db.execute(submission_query.where(
                FileAsset.id.in_([UUID(item) for item in source_ids]),
                ParseArtifact.id.in_([UUID(item) for item in parse_ids]),
            ).order_by(FileAsset.id)).all()
            rows = [(asset, None, artifact) for asset, artifact in selected]
        else:
            namespace_query = (
                select(FileAsset, NormativeFileLink, ParseArtifact)
                .join(NormativeFileLink, NormativeFileLink.file_asset_id == FileAsset.id)
                .join(ParseArtifact, ParseArtifact.file_asset_id == FileAsset.id)
            )
            if namespace_kind == "context":
                namespace_query = namespace_query.where(
                    NormativeFileLink.context_version_id == namespace_id,
                    ParseArtifact.context_version_id == namespace_id,
                    FileAsset.purpose == "context",
                )
            else:
                namespace_query = namespace_query.where(
                    NormativeFileLink.assignment_version_id == namespace_id,
                    ParseArtifact.assignment_version_id == namespace_id,
                    NormativeFileLink.source_role == "rubric",
                    FileAsset.purpose == "rubric",
                )
            rows = list(db.execute(namespace_query.where(
                FileAsset.id.in_([UUID(item) for item in source_ids]),
                ParseArtifact.id.in_([UUID(item) for item in parse_ids]),
            ).order_by(FileAsset.id)).all())
        if len(rows) != len(source_ids):
            return {"status": "failed", "reason": "PARSER_SOURCE_SCOPE_INVALID"}
        stage = db.scalar(select(JobStage).where(JobStage.job_id == job.id, JobStage.stage_name == stage_name).with_for_update())
        if stage is None:
            return {"status": "failed", "reason": "PARSER_STAGE_MISSING"}
        if namespace_kind == "context":
            context_namespace = db.get(ContextVersion, namespace_id)
            namespace_revision = context_namespace.revision if context_namespace is not None else 0
        elif namespace_kind == "assignment":
            assignment_namespace = db.get(AssignmentVersion, namespace_id)
            namespace_revision = assignment_namespace.revision if assignment_namespace is not None else 0
        else:
            submission_version = db.get(SubmissionVersion, namespace_id)
            namespace_revision = submission_version.version_no if submission_version is not None else 0
        request_id = job.id

    retryable_failure = False
    all_candidates: list[dict[str, Any]] = []
    evidence_total = 0
    terminal_limits: list[dict[str, Any]] = []
    # PDF pages are independent checkpoint tasks. A retry never reparses pages
    # whose source/profile-bound checkpoint has already committed successfully.
    tasks = [(asset, link, artifact, page) for asset, link, artifact in rows
             for page in (range(1, int(asset.preflight_json["pages"]) + 1) if asset.mime == "application/pdf" else [None])]
    for asset, link, artifact, page in tasks:
        profile = str(asset.preflight_json.get("parser_profile", settings.parser_profile_document))
        profile_active = profile in {settings.parser_profile_native, settings.parser_profile_document}
        heavy = profile == settings.parser_profile_document
        parser_module = "docling" if heavy else "openpyxl" if asset.mime.endswith("spreadsheetml.sheet") else "docx"
        missing_dependency = not profile_active or importlib.util.find_spec(parser_module) is None or (
            heavy and (importlib.util.find_spec("rtree") is None or shutil.which("tesseract") is None)
        )
        with SessionFactory() as manifest_db:
            resource = manifest_db.get(MethodManifest, artifact.parser_resource_manifest_id) if artifact.parser_resource_manifest_id else None
            resource_matches = (
                profile_active and resource is not None and resource.kind == "parser_resource"
                and resource.sha256 == canonical_hash(profile_payload(settings, profile))
            )
        task_key = canonical_hash(
            {
                f"{namespace_kind}_version_id": str(namespace_id),
                "file_sha256": asset.sha256,
                "parser_profile": profile,
                "parser_resource_manifest_id": str(artifact.parser_resource_manifest_id),
                "parser_manifest_id": str(artifact.parser_manifest_id),
                "stage": "parse_source_units",
                "unit_locator": {"format": "pdf", "page": page} if page else {"format": "file", "locator_note": asset.original_name},
            }
        )
        with SessionFactory.begin() as db:
            job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job is None or job.lease_fence != expected_fence or job.input_fingerprint != expected_fingerprint:
                return {"status": "stale", "fenced": True}
            if namespace_kind == "context":
                active_context = db.scalar(
                    select(ContextVersion)
                    .where(ContextVersion.id == namespace_id)
                    .with_for_update()
                )
                namespace_is_draft = active_context is not None and active_context.state == "draft"
            elif namespace_kind == "assignment":
                active_assignment = db.scalar(
                    select(AssignmentVersion)
                    .where(AssignmentVersion.id == namespace_id)
                    .with_for_update()
                )
                namespace_is_draft = active_assignment is not None and active_assignment.state == "draft"
            else:
                namespace_is_draft = db.get(SubmissionVersion, namespace_id) is not None
            if not namespace_is_draft:
                job.status = "ready_for_review"
                job.completion_kind = "completed_with_limits"
                job.lease_until = None
                return {"status": "ready_for_review", "reason": "source_version_not_draft"}
            checkpoint = db.scalar(
                select(ParserCheckpoint)
                .where(ParserCheckpoint.parse_artifact_id == artifact.id, ParserCheckpoint.task_key == task_key)
                .with_for_update()
            )
            if checkpoint is not None and checkpoint.status == "succeeded":
                evidence_total += int(checkpoint.output_refs_json.get("evidence_count", 0))
                terminal_limits.extend(checkpoint.limitations_json or [])
                replay_candidates = checkpoint.output_refs_json.get("candidate_fields", [])
                if isinstance(replay_candidates, list):
                    for candidate in replay_candidates:
                        if isinstance(candidate, dict):
                            item = dict(candidate)
                            if namespace_kind == "context" and link is not None:
                                item["source_ref"] = _candidate_ref(asset, link.source_role, item)
                            all_candidates.append(item)
                continue
            attempt_no = int(
                db.scalar(
                    select(func.max(ProcessingTimeReservation.attempt_no)).where(
                        ProcessingTimeReservation.job_id == job_id,
                        ProcessingTimeReservation.task_key == task_key,
                    )
                )
                or 0
            ) + 1
            limit_seconds = int(job.job_limits_json.get("max_active_seconds", settings.job_max_active_seconds))
            reservations = db.scalars(
                select(ProcessingTimeReservation).where(ProcessingTimeReservation.job_id == job_id)
            ).all()
            used_ms = sum(
                item.actual_active_ms if item.actual_active_ms is not None else item.reserved_active_ms
                for item in reservations
                if item.state in {"held", "settled"}
            )
            remaining_ms = limit_seconds * 1000 - used_ms
            time_limits = _parser_time_limits(
                remaining_ms, settings.parser_task_soft_seconds, settings.parser_task_hard_seconds
            )
            failure_code: str | None
            if time_limits is None:
                failure_code = "processing_limit"
                lease = None
            elif missing_dependency or not resource_matches:
                failure_code = "parser_resource_unavailable"
                lease = None
            else:
                lease, admission_reason = acquire_parser_lease(settings, profile)
                failure_code = None if lease is not None else admission_reason
            soft_seconds, hard_seconds = time_limits or (1, 1)
            reservation = ProcessingTimeReservation(
                job_id=job_id,
                task_key=task_key,
                attempt_no=attempt_no,
                reserved_active_ms=hard_seconds * 1000 if time_limits is not None else 0,
                state="held",
                started_at=datetime.now(UTC),
            )
            db.add(reservation)
            db.flush()
            if checkpoint is None:
                checkpoint = ParserCheckpoint(
                    job_id=job_id,
                    parse_artifact_id=artifact.id,
                    task_key=task_key,
                    file_hash=asset.sha256,
                    namespace_version_id=namespace_id,
                    parser_resource_manifest_id=artifact.parser_resource_manifest_id,
                    canonical_unit_locator_json={"format": "pdf", "page": page} if page else {"format": "file", "locator_note": asset.original_name},
                    stage="parse_source_units",
                    status="running",
                    output_refs_json={},
                    covered_unit_refs_json=[],
                    limitations_json=[],
                    admission_node=lease.node_id if lease else settings.parser_node_id,
                    admission_lease=lease.lease_id if lease else None,
                    admission_fence=lease.fence if lease else None,
                )
                db.add(checkpoint)
            else:
                checkpoint.status = "running"
                checkpoint.admission_node = lease.node_id if lease else settings.parser_node_id
                checkpoint.admission_lease = lease.lease_id if lease else None
                checkpoint.admission_fence = lease.fence if lease else None
            checkpoint.started_at = datetime.now(UTC)
            checkpoint.limitations_json = []
            db.add(checkpoint)
            artifact_row = db.scalar(select(ParseArtifact).where(ParseArtifact.id == artifact.id).with_for_update())
            if artifact_row is not None:
                artifact_row.status = "running"
                if db.scalar(select(SourceUnit.id).where(SourceUnit.parse_artifact_id == artifact.id).limit(1)) is None:
                    _create_or_update_units(db, artifact_row, _fallback_inventory(asset, "parser_pending"))
        started = time.monotonic()
        result: dict[str, Any] | None = None
        process_exit: str | None = None
        if failure_code is None and lease is not None:
            def heartbeat(lease: AdmissionLease | None = lease) -> bool:
                if lease is None or not renew_parser_lease(settings, lease):
                    return False
                with SessionFactory.begin() as heartbeat_db:
                    running = heartbeat_db.scalar(select(Job).where(Job.id == job_id).with_for_update())
                    if running is None or running.lease_fence != expected_fence or running.input_fingerprint != expected_fingerprint:
                        return False
                    running.lease_until = datetime.now(UTC) + timedelta(seconds=settings.worker_lease_seconds)
                return True
            result, failure_code, process_exit = _download_and_run(asset, settings, lease, soft_seconds, hard_seconds, heartbeat, page)
        try:
            if lease is not None and not renew_parser_lease(settings, lease):
                result, failure_code = None, "parser_lease_lost"
            with SessionFactory.begin() as db:
                current_job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
                if current_job is None or current_job.lease_fence != expected_fence or current_job.input_fingerprint != expected_fingerprint:
                    return {"status": "stale", "fenced": True}
                artifact_row = db.scalar(select(ParseArtifact).where(ParseArtifact.id == artifact.id).with_for_update())
                checkpoint_row = db.scalar(
                    select(ParserCheckpoint)
                    .where(ParserCheckpoint.parse_artifact_id == artifact.id, ParserCheckpoint.task_key == task_key)
                    .with_for_update()
                )
                reservation_row = db.scalar(
                    select(ProcessingTimeReservation).where(
                        ProcessingTimeReservation.job_id == job_id,
                        ProcessingTimeReservation.task_key == task_key,
                        ProcessingTimeReservation.attempt_no == attempt_no,
                    ).with_for_update()
                )
                if artifact_row is None or checkpoint_row is None or reservation_row is None:
                    return {"status": "failed", "reason": "PARSER_CHECKPOINT_MISSING"}
                count, limitations = _finish_checkpoint(
                    db,
                    current_job,
                    artifact_row,
                    asset,
                    checkpoint_row,
                    result,
                    failure_code,
                    process_exit,
                    namespace_id,
                    namespace_kind,
                    reservation_row,
                    started,
                    request_id,
                    page,
                )
                evidence_total += count
                terminal_limits.extend(limitations)
                if artifact_row.status == "blocked":
                    retryable_failure = retryable_failure or attempt_no < settings.parser_max_attempts
                    if attempt_no >= settings.parser_max_attempts:
                        terminal_limits.append(
                            {
                                "code": "parser_retry_limit_reached",
                                "asset_id": str(asset.id),
                                "attempts": attempt_no,
                            }
                        )
                elif namespace_kind == "context" and result is not None and not result.get("failure_code"):
                    candidate_items = result.get("candidate_fields", [])
                    if isinstance(candidate_items, list):
                        for candidate in candidate_items:
                            if isinstance(candidate, dict):
                                item = dict(candidate)
                                if link is not None:
                                    item["source_ref"] = _candidate_ref(asset, link.source_role, item)
                                all_candidates.append(item)
                db.add(
                    AuditEvent(
                        worker_id="align-parser",
                        action=(
                            ("context.file.parsed" if artifact_row.status not in {"blocked", "failed", "unsupported"} else "context.file.parse_limited")
                            if namespace_kind == "context" else
                            ("assignment.rubric.parsed" if artifact_row.status not in {"blocked", "failed", "unsupported"} else "assignment.rubric.parse_limited")
                            if namespace_kind == "assignment" else
                            ("submission.file.parsed" if artifact_row.status not in {"blocked", "failed", "unsupported"} else "submission.file.parse_limited")
                        ),
                        object_type="file_asset",
                        object_id=asset.id,
                        old_version_refs_json={"sha256": asset.sha256},
                        new_version_refs_json={"parse_status": artifact_row.status, "unit_count": len(checkpoint_row.covered_unit_refs_json), "evidence_count": count},
                        request_id=request_id,
                    )
                )
        finally:
            if lease is not None:
                release_parser_lease(settings, lease)

    with SessionFactory.begin() as db:
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if job is None:
            return {"status": "missing"}
        if job.lease_fence != expected_fence or job.input_fingerprint != expected_fingerprint:
            return {"status": "stale", "fenced": True}
        stage = db.scalar(
            select(JobStage)
            .where(JobStage.job_id == job_id, JobStage.stage_name == stage_name)
            .with_for_update()
        )
        if stage is None:
            job.status = "failed"
            job.last_error_code = "PARSER_STAGE_MISSING"
            job.lease_until = None
            return {"status": "failed", "reason": "PARSER_STAGE_MISSING"}
        provider_config = job.scope_json.get("context_provider", {})
        proposals: list[dict[str, Any]]
        provider_manifest = db.get(MethodManifest, UUID(provider_config["manifest_id"])) if namespace_kind == "context" and provider_config.get("manifest_id") else None
        provider_policy = db.get(MethodManifest, UUID(provider_config["policy_id"])) if namespace_kind == "context" and provider_config.get("policy_id") else None
        if namespace_kind != "context":
            proposals, provider_outcome = [], {"status": "not_requested", "paid_calls": 0}
        elif (provider_manifest is None or provider_policy is None
                or provider_manifest.kind != "model" or provider_policy.kind != "routing"
                or provider_manifest.sha256 != provider_config.get("manifest_sha256")
                or provider_policy.sha256 != provider_config.get("policy_sha256")
                or provider_manifest.sha256 != canonical_hash(context_provider_payload(settings))):
            proposals, provider_outcome = [], {"status": "provider_disabled", "simulated": False}
        else:
            proposals, provider_outcome = propose_context_fields(job_id, namespace_id, job.scope_json, all_candidates)
        if proposals:
            _merge_field_candidates(db, namespace_id, proposals, request_id)
        if provider_outcome["status"] not in {"mocked", "completed"}:
            terminal_limits.append({"code": provider_outcome["status"],
                                   "message": "Usulan model tidak tersedia atau tidak sah; konteks dapat dilengkapi manual."})
        # Reflect every page's outcome, including failures before the last page.
        for _asset, _link, parsed_artifact in rows:
            aggregate = db.get(ParseArtifact, parsed_artifact.id)
            checkpoints = db.scalars(select(ParserCheckpoint).where(
                ParserCheckpoint.parse_artifact_id == parsed_artifact.id,
            )).all()
            if aggregate is not None and checkpoints:
                limits = [item for cp in checkpoints for item in cp.limitations_json]
                if _asset.mime == "application/pdf" and int(_asset.preflight_json.get("pages", 0)) > 1 and any(cp.output_refs_json.get("has_tables") for cp in checkpoints):
                    limits.append({"code": "cross_page_table_relations_unverified", "message": "Fragmen tabel per halaman tersedia; hubungan tabel lintas halaman belum diverifikasi."})
                aggregate.limitations_json = limits
                aggregate.status = "blocked" if any(cp.status == "blocked" for cp in checkpoints) else "partially_parsed" if limits else "parsed"
                root = db.scalar(select(SourceUnit).where(
                    SourceUnit.parse_artifact_id == aggregate.id, SourceUnit.unit_type == "root",
                ))
                if root is not None:
                    root.parse_status = "partially_parsed" if limits else "parsed"
                    root.limitations_json = limits
        if retryable_failure:
            job.status = "blocked"
            retry_code = next(
                (
                    item.get("code")
                    for item in terminal_limits
                    if item.get("code") in {"parser_capacity_unavailable", "parser_storage_unavailable"}
                ),
                "parser_capacity_unavailable",
            )
            job.last_error_code = retry_code
            stage.status = "blocked"
            stage.failure_code = retry_code
            stage.output_refs_json = {"evidence_count": evidence_total, "limitations": terminal_limits, "provider": provider_outcome}
            db.add(
                OutboxEvent(
                    event_type={"context": "job.parse_context", "assignment": "job.parse_rubric", "submission": "job.parse_submission"}[namespace_kind],
                    subject_id=job.id,
                    payload_json={"job_id": str(job.id)},
                    next_attempt_at=datetime.now(UTC) + timedelta(seconds=60),
                )
            )
            result_status = "blocked"
        else:
            job.status = "ready_for_review"
            job.completion_kind = "completed_with_limits" if terminal_limits else "completed"
            job.last_error_code = None
            stage.status = "succeeded"
            stage.failure_code = None
            stage.output_refs_json = {"evidence_count": evidence_total, "limitations": terminal_limits, "provider": provider_outcome}
            result_status = "ready_for_review"
        job.lease_until = None
        stage.finished_at = datetime.now(UTC)
        stage.attempt_count += 0
        return {
            "status": result_status,
            "job_id": str(job_id),
            f"{namespace_kind}_version_id": str(namespace_id),
            "evidence_count": evidence_total,
            "limitations": terminal_limits,
            "namespace_revision_at_start": namespace_revision,
        }


def execute_context_job(job_id: UUID, expected_fence: int, expected_fingerprint: str) -> dict[str, Any]:
    return _execute_parser_job(job_id, expected_fence, expected_fingerprint, "context")


def execute_rubric_job(job_id: UUID, expected_fence: int, expected_fingerprint: str) -> dict[str, Any]:
    return _execute_parser_job(job_id, expected_fence, expected_fingerprint, "assignment")


def execute_submission_job(job_id: UUID, expected_fence: int, expected_fingerprint: str) -> dict[str, Any]:
    return _execute_parser_job(job_id, expected_fence, expected_fingerprint, "submission")
