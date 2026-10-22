"""Run reproducible, real parser fixtures through the production child boundary."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from align_api.config import get_settings
from align_api.file_validation import validate_context_file
from align_api.parser_profiles import profile_payload
from align_worker.context_parser import _run_child

SUPPORTED = {
    "native_ooxml_v1": {".docx", ".xlsx"},
    "docling_native_v1": {".pdf", ".jpg", ".jpeg", ".png"},
}
PACKAGE_NAMES = (
    "docling",
    "docling-core",
    "docling-slim",
    "python-docx",
    "openpyxl",
    "pillow",
    "pypdf",
    "torch",
    "torchvision",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(64 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _memory_current() -> int | None:
    for path in (Path("/sys/fs/cgroup/memory.current"), Path("/sys/fs/cgroup/memory/memory.usage_in_bytes")):
        try:
            return int(path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            continue
    return None


def _versions() -> dict[str, str]:
    found: dict[str, str] = {}
    for package in PACKAGE_NAMES:
        try:
            found[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            found[package] = "not-installed"
    return found


def _hardware() -> dict[str, Any]:
    memory_total: int | None = None
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            if line.startswith("MemTotal:"):
                memory_total = int(line.split()[1]) * 1024
                break
    except (OSError, ValueError, IndexError):
        pass
    cgroup_limit: int | str | None = None
    try:
        cgroup_limit = Path("/sys/fs/cgroup/memory.max").read_text(encoding="ascii").strip()
        if cgroup_limit != "max":
            cgroup_limit = int(cgroup_limit)
    except (OSError, ValueError):
        try:
            cgroup_limit = int(Path("/sys/fs/cgroup/memory/memory.limit_in_bytes").read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            cgroup_limit = None
    tesseract = shutil.which("tesseract")
    tesseract_version = None
    if tesseract:
        try:
            tesseract_version = subprocess.run(  # noqa: S603 -- fixed version probe, no shell or user arguments
                [tesseract, "--version"], capture_output=True, text=True, timeout=3, check=False,
            ).stdout.splitlines()[0]
        except (OSError, subprocess.SubprocessError, IndexError):
            tesseract_version = "available-version-unreadable"
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or "unknown",
        "cpu_count": os.cpu_count(),
        "host_or_vm_memory_bytes": memory_total,
        "container_memory_limit_bytes": cgroup_limit,
        "tesseract": tesseract_version,
    }


def _preflight(path: Path, settings: Any) -> dict[str, Any]:
    try:
        _name, _mime, details = validate_context_file(
            str(path), path.name, None, settings.max_file_bytes, settings.max_image_pixels,
            settings.max_docx_nodes, settings.max_docx_media, settings.max_ooxml_entries,
            settings.max_ooxml_expanded_bytes, settings.max_xlsx_sheets,
            settings.max_xlsx_nonempty_cells,
            allowed_suffixes={".pdf", ".docx", ".xlsx", ".jpg", ".jpeg", ".png"},
        )
    except HTTPException as exc:
        raise SystemExit(f"fixture rejected by production preflight ({exc.status_code})") from exc
    return details


def _task_pages(profile: str, suffix: str, details: dict[str, Any]) -> list[int | None]:
    if profile == "docling_native_v1" and suffix == ".pdf":
        return list(range(1, int(details["pages"]) + 1))
    return [None]


def _anchor_checks(units: list[dict[str, Any]], details: dict[str, Any]) -> dict[str, int]:
    checks = {"verified": 0, "invalid": 0, "unanchored_or_unsupported": 0}
    sheets = {
        item.get("name") for item in details.get("sheets", [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    page_sizes = details.get("page_sizes", [])
    docx_parts = set(details.get("docx_package_parts", []))
    xlsx_parts = set(details.get("xlsx_package_parts", []))
    for unit in units:
        if not isinstance(unit, dict) or unit.get("unit_type") == "root":
            continue
        anchor = unit.get("anchor")
        if not isinstance(anchor, dict):
            checks["unanchored_or_unsupported"] += 1
            continue
        fmt = anchor.get("format")
        valid = False
        if fmt == "pdf":
            page = anchor.get("page")
            valid = isinstance(page, int) and 1 <= page <= int(details.get("pages", 0))
            box = anchor.get("bbox")
            if valid and isinstance(page, int) and box is not None:
                if isinstance(box, list) and len(box) == 4 and page <= len(page_sizes):
                    width, height = page_sizes[page - 1]
                    valid = _valid_box(box, float(width), float(height))
                else:
                    valid = False
        elif fmt == "image":
            width, height = int(details.get("width", 0)), int(details.get("height", 0))
            box = anchor.get("bbox")
            valid = (anchor.get("width"), anchor.get("height")) == (width, height)
            if valid and isinstance(box, list) and len(box) == 4:
                valid = _valid_box(box, width, height)
            elif valid:
                valid = False
        elif fmt == "docx":
            part = anchor.get("part")
            valid = (
                isinstance(part, str)
                and part in docx_parts
                and isinstance(anchor.get("structural_path"), str)
                and anchor["structural_path"].startswith("/")
                and ".." not in anchor["structural_path"]
                and anchor.get("item_kind") in {"paragraph", "table", "cell", "media"}
                and isinstance(anchor.get("item_index"), int)
                and not isinstance(anchor.get("item_index"), bool)
                and anchor["item_index"] >= 0
            )
        elif fmt == "xlsx":
            cell_range = anchor.get("cell_range")
            match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]*)(?::([A-Z]{1,3})([1-9][0-9]*))?", cell_range) if isinstance(cell_range, str) else None

            def column_number(column: str) -> int:
                number = 0
                for character in column:
                    number = number * 26 + ord(character) - ord("A") + 1
                return number

            bounded = False
            if match is not None:
                col1, row1, col2, row2 = match.groups()
                first = column_number(col1), int(row1)
                last = column_number(col2 or col1), int(row2 or row1)
                bounded = 1 <= first[0] <= last[0] <= 16_384 and 1 <= first[1] <= last[1] <= 1_048_576
            part = anchor.get("part")
            valid = (
                anchor.get("sheet_name") in sheets
                and bounded
                and (part is None or isinstance(part, str) and part in xlsx_parts)
            )
        if valid:
            checks["verified"] += 1
        elif fmt in {"pdf", "image", "docx", "xlsx"}:
            checks["invalid"] += 1
        else:
            checks["unanchored_or_unsupported"] += 1
    return checks


def _valid_box(box: list[Any], width: float, height: float) -> bool:
    try:
        x1, y1, x2, y2 = (float(value) for value in box)
    except (TypeError, ValueError):
        return False
    return (
        all(map(lambda value: value == value and abs(value) != float("inf"), (x1, y1, x2, y2)))
        and 0 <= x1 <= x2 <= width
        and 0 <= y1 <= y2 <= height
    )


def _run_sample(
    path: Path,
    profile: str,
    details: dict[str, Any],
    pages: list[int | None],
    memory_bytes: int,
    settings: Any,
) -> dict[str, Any]:
    units: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    task_results: list[dict[str, Any]] = []
    baseline = _memory_current()
    peak_current = baseline
    failures: list[dict[str, str | None]] = []
    stages: set[str] = set()

    def observe_memory() -> None:
        nonlocal peak_current
        current = _memory_current()
        if current is not None:
            peak_current = current if peak_current is None else max(peak_current, current)

    for page in pages:
        with tempfile.TemporaryDirectory(prefix="align-benchmark-") as temp_dir:
            output = str(Path(temp_dir) / "result.json")
            started = time.monotonic()
            result, failure, process_exit = _run_child(
                str(path), path.name, output, memory_bytes, settings.parser_task_soft_seconds,
                settings.parser_task_hard_seconds, page=page, memory_observer=observe_memory,
                poll_interval_seconds=0.05,
            )
            wall_ms = max(0, round((time.monotonic() - started) * 1000))
        task_record: dict[str, Any] = {
            "page": page,
            "wall_ms": wall_ms,
            "stage_timings_ms": {},
            "failure_code": failure,
            "process_exit": process_exit,
            "peak_rss_bytes": None,
            "cgroup_oom_kills_before": None,
            "cgroup_oom_kills_after": None,
            "parse_status": None,
            "unit_count": 0,
            "evidence_count": 0,
        }
        if result is not None:
            raw_stage_timings = result.get("stage_timings_ms", {})
            if isinstance(raw_stage_timings, dict):
                task_record["stage_timings_ms"] = {
                    str(name): value for name, value in raw_stage_timings.items()
                    if isinstance(name, str) and isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0
                }
            runtime = result.get("_runtime", {})
            if isinstance(runtime, dict):
                task_record["peak_rss_bytes"] = runtime.get("peak_rss_bytes")
                task_record["cgroup_oom_kills_before"] = runtime.get("cgroup_oom_kills_before")
                task_record["cgroup_oom_kills_after"] = runtime.get("cgroup_oom_kills_after")
            task_record["parse_status"] = result.get("parse_status")
            records = result.get("units", [])
            if isinstance(records, list):
                units.extend(item for item in records if isinstance(item, dict))
                task_record["unit_count"] = len(records)
            records = result.get("evidence", [])
            if isinstance(records, list):
                evidence.extend(item for item in records if isinstance(item, dict))
                task_record["evidence_count"] = len(records)
            covered_stages = result.get("covered_stages", [])
            if isinstance(covered_stages, list):
                stages.update(str(item) for item in covered_stages)
            if result.get("failure_code"):
                failures.append({"code": str(result["failure_code"]), "page": str(page) if page else None})
        elif failure:
            failures.append({"code": failure, "page": str(page) if page else None})
        task_results.append(task_record)

    unique_units = {item.get("unit_key"): item for item in units if isinstance(item.get("unit_key"), str)}
    checks = _anchor_checks(list(unique_units.values()), details)
    cgroup_oom_before = next((item["cgroup_oom_kills_before"] for item in task_results if item["cgroup_oom_kills_before"] is not None), None)
    cgroup_oom_after = next((item["cgroup_oom_kills_after"] for item in reversed(task_results) if item["cgroup_oom_kills_after"] is not None), None)
    return {
        "status": "failed" if failures else "completed",
        "parse_statuses": sorted({str(item["parse_status"]) for item in task_results if item["parse_status"]}),
        "failures": failures,
        "tasks": task_results,
        "unit_count": len(unique_units),
        "evidence_count": len(evidence),
        "anchor_checks": checks,
        "table_evidence_count": sum(item.get("type") == "table" for item in evidence),
        "spreadsheet_header_context_count": sum(
            item.get("type") == "spreadsheet_cell"
            and isinstance(item.get("content"), dict)
            and bool(item["content"].get("column_label"))
            and bool(item["content"].get("row_label"))
            for item in evidence
        ),
        "formula_cache_unverified_count": sum(
            item.get("type") == "spreadsheet_cell"
            and isinstance(item.get("content"), dict)
            and item["content"].get("formula") is not None
            and item["content"].get("cache_verified") is False
            for item in evidence
        ),
        "stage_names": sorted(stages),
        "container_cgroup_baseline_bytes": baseline,
        "container_cgroup_peak_observed_bytes": peak_current,
        "container_cgroup_peak_scope": "whole parser-worker container cgroup; includes the benchmark process and other resident container processes; sampled every 50 ms",
        "cgroup_oom_kills_before": cgroup_oom_before,
        "cgroup_oom_kills_after": cgroup_oom_after,
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# ALIGN actual parser benchmark",
        "",
        f"Generated at: `{report['generated_at']}`",
        f"Profile: `{report['profile_id']}` · profile SHA-256 `{report['profile_sha256']}`",
        f"Execution: `{report['parser_mode']}`; synthetic fixtures only; no mock parser or provider call.",
        "",
        "## Environment",
        "",
        f"- Platform: `{report['hardware']['platform']}` / `{report['hardware']['machine']}`",
        f"- CPU count: `{report['hardware']['cpu_count']}`",
        f"- Host/VM memory: `{report['hardware']['host_or_vm_memory_bytes']}` bytes; container limit: `{report['hardware']['container_memory_limit_bytes']}`",
        f"- Packages: `{json.dumps(report['packages'], sort_keys=True)}`",
        f"- Parser resource profile: `{json.dumps(report['resources'], sort_keys=True)}`",
        "- Cold means the first isolated child run in this benchmark invocation; warm means a second isolated child run after OS file/model artifacts may be cached. Each child reloads the parser and model.",
        "- Child RSS is `/proc/<pid>/status:VmHWM`; cgroup peak is a 50 ms sampled whole-container `memory.current`, including the benchmark parent and any other resident container processes, not a per-child cgroup measurement.",
        "",
        "## Results",
        "",
        "| Fixture | Format | SHA-256 | Preflight (ms) | Cold wall (ms) | Warm wall (ms) | Peak child RSS (bytes) | Peak container cgroup sample (bytes) | Anchor checks | Tables | Failures |",
        "|---|---|---|---:|---:|---:|---:|---:|---|---:|---|",
    ]
    for item in report["fixtures"]:
        runs = item["runs"]
        cold, warm = runs["cold"], runs["warm"]
        rss = [task["peak_rss_bytes"] for run in (cold, warm) for task in run["tasks"] if task["peak_rss_bytes"]]
        lines.append(
            f"| `{item['name']}` | {item['format']} | `{item['sha256']}` | "
            f"{item['preflight_ms']} | {sum(task['wall_ms'] for task in cold['tasks'])} | "
            f"{sum(task['wall_ms'] for task in warm['tasks'])} | "
            f"{max(rss) if rss else 'n/a'} | {max((run['container_cgroup_peak_observed_bytes'] or 0) for run in (cold, warm)) or 'n/a'} | "
            f"{cold['anchor_checks']} / {warm['anchor_checks']} | {cold['table_evidence_count']} / {warm['table_evidence_count']} | "
            f"{cold['failures']} / {warm['failures']} |"
        )
    lines.extend([
        "", "## Stage and page timing", "",
        "Preflight is timed separately. Child wall time includes interpreter startup, parser work, result serialization, and process cleanup; stage timings below are measured inside the child and are not added to wall time.",
        "Docling exposes render/layout/OCR/table work as one conversion call in this pinned integration, so `docling_pipeline_conversion` is reported as a combined stage rather than splitting unobservable internal timings.",
        "", "| Fixture | Run | Page | Child wall (ms) | Measured stage timings (ms) |", "|---|---|---:|---:|---|",
    ])
    for item in report["fixtures"]:
        for run_name in ("cold", "warm"):
            for task in item["runs"][run_name]["tasks"]:
                page_label = str(task["page"]) if task["page"] is not None else "file"
                lines.append(
                    f"| `{item['name']}` | {run_name} | {page_label} | {task['wall_ms']} | "
                    f"`{json.dumps(task['stage_timings_ms'], sort_keys=True)}` |"
                )
    lines.extend(["", "## Support boundaries", "", *[f"- {item}" for item in report["support_limits"]], ""])
    return "\n".join(lines)


def run_parser_benchmark(profile_id: str, fixtures_path: Path, output_path: Path) -> None:
    settings = get_settings()
    if profile_id not in SUPPORTED:
        raise SystemExit("profile is not active; docling_light_v1 remains candidate/non-active")
    expected_profile = settings.parser_profile_native if profile_id == settings.parser_profile_native else settings.parser_profile_document
    if profile_id != expected_profile:
        raise SystemExit("requested profile does not match the configured immutable parser profile")
    if not fixtures_path.exists():
        raise SystemExit("fixture path does not exist")
    candidates = sorted(path for path in fixtures_path.rglob("*") if path.is_file() and path.suffix.lower() in SUPPORTED[profile_id])
    if not candidates:
        raise SystemExit("fixture directory contains no files supported by the selected profile")
    profile = profile_payload(settings, profile_id)
    memory_limit = int(profile["memory_limit_bytes"])
    report: dict[str, Any] = {
        "schema_version": 1,
        "report_type": "align_parser_benchmark_report",
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "incomplete",
        "measurement_mode": "actual_bounded_parser_child",
        "data_origin": "synthetic_fixture",
        "provider_mode": "not_used",
        "provider_calls": 0,
        "provider_actual_cost_usd": "0.00",
        "infrastructure_cost": {"amount": None, "currency": None, "status": "not_metered"},
        "profile_id": profile_id,
        "profile_sha256": hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "parser_mode": "actual-isolated-parser-child; mock=false",
        "hardware": _hardware(),
        "packages": _versions(),
        "versions": {
            "uv_lock_sha256": _sha256(Path("uv.lock")),
            "resource_manifest_sha256": _sha256(Path("config/parser-context-resources.json")),
        },
        "resources": profile,
        "fixtures": [],
        "support_limits": [
            "OCR output is an extracted technical artifact, not verified handwriting recognition.",
            "Spreadsheet formulas are never executed; cached values remain unverified.",
            "Charts, drawings and embedded media are inventory items and remain unsupported unless the parser emits verified content.",
            "A successful sample is not evidence that every allowed 100-page or maximum-size bundle finishes within 600 active seconds.",
            "Docling 2.140.0 reports render/layout/OCR/table work as a combined conversion call; stage timings do not claim unexposed internal substage measurements.",
            "Provider spend is zero because no provider is invoked; Docker/host electricity or compute cost is not metered.",
        ],
    }
    for path in candidates:
        preflight_started = time.perf_counter()
        details = _preflight(path, settings)
        preflight_ms = max(0, round((time.perf_counter() - preflight_started) * 1000))
        pages = _task_pages(profile_id, path.suffix.lower(), details)
        fixture: dict[str, Any] = {
            "name": path.name,
            "relative_path": path.relative_to(fixtures_path).as_posix() if fixtures_path.is_dir() else path.name,
            "format": path.suffix.lower().lstrip("."),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
            "pages": details.get("pages", 1 if path.suffix.lower() in {".jpg", ".jpeg", ".png"} else None),
            "preflight": details,
            "preflight_ms": preflight_ms,
            "profile_memory_limit_bytes": memory_limit,
            "runs": {},
        }
        fixture["runs"]["cold"] = _run_sample(path, profile_id, details, pages, memory_limit, settings)
        fixture["runs"]["warm"] = _run_sample(path, profile_id, details, pages, memory_limit, settings)
        report["fixtures"].append(fixture)
    base = output_path.with_suffix("") if output_path.suffix.lower() in {".json", ".md"} else output_path
    json_path = base.with_suffix(".json")
    markdown_path = base.with_suffix(".md")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    all_runs_completed = all(
        run["status"] == "completed"
        for fixture in report["fixtures"]
        for run in fixture["runs"].values()
    )
    report["status"] = "measured" if all_runs_completed else "incomplete"
    report["report_sha256"] = hashlib.sha256(
        json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(markdown_path), "fixtures": len(candidates)}))
    if any(run["status"] != "completed" for fixture in report["fixtures"] for run in fixture["runs"].values()):
        raise SystemExit("one or more actual parser runs failed; report was written")
