from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.qa_report_guard import (
    ReportError,
    healthcheck_deadline_seconds,
    validate_junit,
    validate_playwright,
)


def junit(*cases: str) -> str:
    return f"<testsuites><testsuite>{''.join(cases)}</testsuite></testsuites>"


def case(
    name: str, result: str = "", classname: str = "tests.unit.test_gate"
) -> str:
    return f'<testcase classname="{classname}" name="{name}">{result}</testcase>'


def test_junit_accepts_a_complete_pass_and_records_its_digest(tmp_path: Path) -> None:
    report = tmp_path / "unit.xml"
    report.write_text(junit(case("first"), case("second")), encoding="utf-8")

    result = validate_junit(report, label="unit", expected_tests=2, expected_passed=2)

    assert result["status"] == "passed"
    assert len(result["report_sha256"]) == 64


def test_junit_rejects_a_mixed_pass_and_skip_summary(tmp_path: Path) -> None:
    report = tmp_path / "mixed.xml"
    report.write_text(junit(case("passed"), case("skipped", "<skipped/>")), encoding="utf-8")

    with pytest.raises(ReportError, match="unexpected skipped"):
        validate_junit(report, label="mixed")


def test_junit_allows_only_the_declared_opt_in_cases(tmp_path: Path) -> None:
    report = tmp_path / "integration.xml"
    parser_prefix = "test_b1_parser_runtime::test_real_docling"
    oom_prefix = "test_b3_parser_oom::test_confirmed_cgroup_oom"
    report.write_text(
        junit(
            case("integration-pass"),
            case("test_real_docling[docx]", "<skipped/>", "tests.integration.test_b1_parser_runtime"),
            case("test_real_docling[pdf]", "<skipped/>", "tests.integration.test_b1_parser_runtime"),
            case("test_confirmed_cgroup_oom", "<skipped/>", "tests.integration.test_b3_parser_oom"),
        ),
        encoding="utf-8",
    )

    result = validate_junit(
        report,
        label="integration",
        allowed_skips={parser_prefix: 2, oom_prefix: 1},
    )

    assert result["passed"] == 1
    with pytest.raises(ReportError, match="unexpected skipped"):
        validate_junit(report, label="integration", allowed_skips={parser_prefix: 2})


@pytest.mark.parametrize(
    "contents",
    ["<testsuites/>", "<testsuites><testcase><failure/></testcase></testsuites>"],
)
def test_junit_rejects_empty_or_failed_reports(tmp_path: Path, contents: str) -> None:
    report = tmp_path / "bad.xml"
    report.write_text(contents, encoding="utf-8")

    with pytest.raises(ReportError):
        validate_junit(report, label="bad")


def test_junit_rejects_a_report_with_an_incomplete_declared_test_count(tmp_path: Path) -> None:
    report = tmp_path / "incomplete.xml"
    report.write_text('<testsuites><testsuite tests="2"><testcase name="only-one"/></testsuite></testsuites>', encoding="utf-8")

    with pytest.raises(ReportError, match="declares 2 tests but contains 1"):
        validate_junit(report, label="incomplete")


def playwright_report(*, skipped: int = 0, flaky: int = 0, status: str = "passed") -> dict[str, object]:
    return {
        "errors": [],
        "stats": {"expected": 1, "skipped": skipped, "unexpected": 0, "flaky": flaky, "interrupted": 0},
        "suites": [{"specs": [{"tests": [{"title": "case", "results": [{"status": status}]}]}]}],
    }


def test_playwright_requires_every_expected_case_to_pass(tmp_path: Path) -> None:
    report = tmp_path / "playwright.json"
    report.write_text(json.dumps(playwright_report()), encoding="utf-8")

    result = validate_playwright(report, label="browser", expected_passed=1)

    assert result["passed"] == 1


@pytest.mark.parametrize(
    "payload",
    [
        playwright_report(skipped=1, status="skipped"),
        playwright_report(flaky=1),
        playwright_report(status="failed"),
        {"stats": {}, "suites": "invalid"},
    ],
)
def test_playwright_rejects_skipped_flaky_failed_and_malformed_cases(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    report = tmp_path / "playwright.json"
    report.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ReportError):
        validate_playwright(report, label="browser", expected_passed=1)


def test_healthcheck_deadline_uses_docker_interval_timeout_retries_and_start_period() -> None:
    config = {"Interval": 10_000_000_000, "Timeout": 10_000_000_000, "Retries": 3, "StartPeriod": 20_000_000_000}

    assert healthcheck_deadline_seconds(config) == 92


@pytest.mark.parametrize(
    "config",
    [{}, {"Interval": 0, "Timeout": 1, "Retries": 1}, {"Interval": 1, "Timeout": 1, "Retries": 0}],
)
def test_healthcheck_deadline_rejects_invalid_docker_values(config: dict[str, int]) -> None:
    with pytest.raises(ReportError):
        healthcheck_deadline_seconds(config)
