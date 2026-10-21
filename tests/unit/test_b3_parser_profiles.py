from __future__ import annotations

import pytest
from pydantic import ValidationError

from align_api.config import Settings
from align_api.parser_profiles import profile_payload
from align_worker.benchmark import _markdown
from align_worker.context_parser import _parser_time_limits


def test_b3_profiles_keep_native_and_docling_memory_profiles_immutable() -> None:
    settings = Settings(_env_file=None)
    native = profile_payload(settings, settings.parser_profile_native)
    docling = profile_payload(settings, settings.parser_profile_document)

    assert native["memory_limit_bytes"] == 2 * 1024**3
    assert native["heavy"] is False
    assert native["formats"] == ["docx", "xlsx"]
    assert docling["memory_limit_bytes"] == 8 * 1024**3
    assert docling["heavy"] is True
    assert docling["formats"] == ["pdf", "jpg", "jpeg", "png"]
    assert docling["package_lock"]["docling-slim"] == "2.140.0"
    assert docling["package_lock"]["openpyxl"] == "3.1.5"


def test_candidate_or_environment_selected_parser_profiles_fail_closed() -> None:
    settings = Settings(_env_file=None)
    with pytest.raises(ValueError, match="not active"):
        profile_payload(settings, "docling_light_v1")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, parser_profile_document="docling_light_v1")


@pytest.mark.parametrize(
    ("remaining_ms", "expected"),
    [
        (0, None),
        (999, None),
        (1000, (1, 1)),
        (1999, (1, 1)),
        (120_000, (120, 120)),
        (150_000, (120, 150)),
    ],
)
def test_aggregate_active_budget_never_starts_without_positive_reservation(
    remaining_ms: int, expected: tuple[int, int] | None
) -> None:
    assert _parser_time_limits(remaining_ms, 120, 150) == expected


def test_benchmark_report_separates_preflight_page_wall_and_measured_stage_times() -> None:
    run = {
        "status": "completed", "tasks": [{"page": 2, "wall_ms": 125, "peak_rss_bytes": 512,
            "stage_timings_ms": {"docling_pipeline_conversion": 96, "anchor_and_evidence_assembly": 12}}],
        "anchor_checks": {"verified": 1, "invalid": 0, "unanchored_or_unsupported": 0},
        "table_evidence_count": 0, "failures": [], "container_cgroup_peak_observed_bytes": 2048,
    }
    report = {
        "generated_at": "synthetic", "profile_id": "docling_native_v1", "profile_sha256": "a" * 64,
        "parser_mode": "actual-isolated-parser-child; mock=false",
        "hardware": {"platform": "synthetic", "machine": "aarch64", "cpu_count": 8,
                     "host_or_vm_memory_bytes": 10, "container_memory_limit_bytes": 9},
        "packages": {}, "resources": {}, "support_limits": [],
        "fixtures": [{"name": "sample.pdf", "format": "pdf", "sha256": "b" * 64,
                      "preflight_ms": 4, "runs": {"cold": run, "warm": run}}],
    }

    markdown = _markdown(report)

    assert "| `sample.pdf` |" in markdown
    assert "| cold | 2 | 125 |" in markdown
    assert '"docling_pipeline_conversion": 96' in markdown
    assert "Docling exposes render/layout/OCR/table work as one conversion call" in markdown
