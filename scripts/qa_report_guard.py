"""Validate complete pytest and Playwright reports for release QA."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

from defusedxml import ElementTree as ET


class ReportError(ValueError):
    """Raised when a test report does not prove the required checks passed."""


def _read_report(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ReportError(f"test report is unavailable: {path}") from exc


def _test_id(testcase: ET.Element) -> str:
    classname = testcase.attrib.get("classname", "")
    name = testcase.attrib.get("name", "")
    return f"{classname}::{name}".strip(":")


def validate_junit(
    report_path: Path,
    *,
    label: str,
    expected_tests: int | None = None,
    expected_passed: int | None = None,
    allowed_skips: dict[str, int] | None = None,
) -> dict[str, Any]:
    raw = _read_report(report_path)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ReportError(f"{label}: JUnit XML is malformed: {exc}") from exc
    cases = list(root.iter("testcase"))
    if not cases:
        raise ReportError(f"{label}: JUnit XML contains no test cases")
    if expected_tests is not None and len(cases) != expected_tests:
        raise ReportError(f"{label}: expected {expected_tests} test cases, found {len(cases)}")
    if root.tag == "testsuites" and "tests" in root.attrib:
        try:
            declared_root_count = int(root.attrib["tests"])
        except ValueError as exc:
            raise ReportError(f"{label}: JUnit root has an invalid declared test count") from exc
        if declared_root_count != len(cases):
            raise ReportError(
                f"{label}: JUnit root declares {declared_root_count} tests but contains {len(cases)}"
            )
    for suite in root.iter("testsuite"):
        declared = suite.attrib.get("tests")
        if declared is not None:
            try:
                declared_count = int(declared)
            except ValueError as exc:
                raise ReportError(f"{label}: JUnit suite has an invalid declared test count") from exc
            observed_count = len(list(suite.iter("testcase")))
            if declared_count != observed_count:
                raise ReportError(
                    f"{label}: JUnit suite declares {declared_count} tests but contains {observed_count}"
                )

    failed: list[str] = []
    skipped: list[str] = []
    for case in cases:
        test_id = _test_id(case) or "unnamed testcase"
        case_status = case.attrib.get("status")
        if case_status is not None and case_status not in {"passed", "run"}:
            raise ReportError(f"{label}: testcase has incomplete status {case_status!r}: {test_id}")
        if case.find("failure") is not None or case.find("error") is not None:
            failed.append(test_id)
        elif case.find("skipped") is not None:
            skipped.append(test_id)
    if failed:
        raise ReportError(f"{label}: {len(failed)} test case(s) failed: {failed[:5]}")

    expected_skip_counts = allowed_skips or {}
    matched_skips: dict[str, int] = {}
    for prefix, required_count in expected_skip_counts.items():
        matched_skips[prefix] = sum(prefix in test_id for test_id in skipped)
        if matched_skips[prefix] != required_count:
            raise ReportError(
                f"{label}: expected {required_count} skipped case(s) matching {prefix!r}, "
                f"found {matched_skips[prefix]}"
            )
    unexpected_skips = [
        test_id for test_id in skipped
        if not any(prefix in test_id for prefix in expected_skip_counts)
    ]
    if unexpected_skips:
        raise ReportError(f"{label}: unexpected skipped test case(s): {unexpected_skips[:5]}")

    passed = len(cases) - len(failed) - len(skipped)
    if passed <= 0:
        raise ReportError(f"{label}: no tests passed")
    if expected_passed is not None and passed != expected_passed:
        raise ReportError(f"{label}: expected {expected_passed} passes, found {passed}")
    return {
        "label": label,
        "format": "junit-xml",
        "status": "passed",
        "tests": len(cases),
        "passed": passed,
        "failed": len(failed),
        "skipped": len(skipped),
        "allowed_skips": matched_skips,
        "report_sha256": hashlib.sha256(raw).hexdigest(),
    }


def _playwright_cases(suites: Any) -> list[tuple[str, str]]:
    cases: list[tuple[str, str]] = []
    if not isinstance(suites, list):
        raise ReportError("Playwright report suites must be an array")
    for suite in suites:
        if not isinstance(suite, dict):
            raise ReportError("Playwright report contains an invalid suite")
        for spec in suite.get("specs", []):
            if not isinstance(spec, dict):
                raise ReportError("Playwright report contains an invalid spec")
            for test in spec.get("tests", []):
                if not isinstance(test, dict):
                    raise ReportError("Playwright report contains an invalid test")
                results = test.get("results", [])
                if not isinstance(results, list):
                    raise ReportError("Playwright test results must be an array")
                if results:
                    result = results[-1]
                    if not isinstance(result, dict):
                        raise ReportError("Playwright report contains an invalid test result")
                    status = result.get("status")
                else:
                    status = test.get("outcome", "skipped")
                cases.append((str(test.get("title", "unnamed test")), str(status)))
        nested = suite.get("suites", [])
        cases.extend(_playwright_cases(nested))
    return cases


def validate_playwright(
    report_path: Path,
    *,
    label: str,
    expected_passed: int,
) -> dict[str, Any]:
    raw = _read_report(report_path)
    try:
        report = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ReportError(f"{label}: Playwright JSON is malformed") from exc
    if not isinstance(report, dict) or not isinstance(report.get("stats"), dict):
        raise ReportError(f"{label}: Playwright report has no stats object")
    stats = report["stats"]
    expected = stats.get("expected")
    if not isinstance(expected, int) or expected != expected_passed:
        raise ReportError(f"{label}: expected {expected_passed} runnable tests, found {expected!r}")
    # Playwright's JSON reporter omits `interrupted` for successful runs; treat
    # that optional counter as zero and let the per-test result records prove
    # that every expected test completed.
    for key in ("skipped", "unexpected", "flaky"):
        value = stats.get(key)
        if not isinstance(value, int) or value != 0:
            raise ReportError(f"{label}: Playwright {key} count must be zero, found {value!r}")
    interrupted = stats.get("interrupted", 0)
    if not isinstance(interrupted, int) or interrupted != 0:
        raise ReportError(f"{label}: Playwright interrupted count must be zero, found {interrupted!r}")
    if report.get("errors"):
        raise ReportError(f"{label}: Playwright reported top-level errors")
    cases = _playwright_cases(report.get("suites"))
    if len(cases) != expected_passed:
        raise ReportError(f"{label}: report contains {len(cases)} test records, expected {expected_passed}")
    not_passed = [(title, status) for title, status in cases if status != "passed"]
    if not_passed:
        raise ReportError(f"{label}: tests are not fully passed: {not_passed[:5]}")
    return {
        "label": label,
        "format": "playwright-json",
        "status": "passed",
        "tests": len(cases),
        "passed": len(cases),
        "failed": 0,
        "skipped": 0,
        "report_sha256": hashlib.sha256(raw).hexdigest(),
    }


def healthcheck_deadline_seconds(config: Any) -> int:
    """Compute a bounded health deadline from Docker inspect's Healthcheck object."""
    if not isinstance(config, dict):
        raise ReportError("Docker healthcheck configuration must be an object")
    try:
        interval_ns = int(config["Interval"])
        timeout_ns = int(config["Timeout"])
        retries = int(config["Retries"])
        start_period_ns = int(config.get("StartPeriod", 0))
    except (KeyError, TypeError, ValueError) as exc:
        raise ReportError("Docker healthcheck interval, timeout, or retries is invalid") from exc
    if interval_ns <= 0 or timeout_ns <= 0 or retries <= 0 or start_period_ns < 0:
        raise ReportError("Docker healthcheck values are outside supported bounds")
    nanoseconds = start_period_ns + retries * (interval_ns + timeout_ns) + interval_ns
    return math.ceil(nanoseconds / 1_000_000_000) + 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("junit", "playwright"):
        child = subparsers.add_parser(command)
        child.add_argument("--report", type=Path, required=True)
        child.add_argument("--label", required=True)
        if command == "junit":
            child.add_argument("--expected-tests", type=int)
            child.add_argument("--expected-passed", type=int)
            child.add_argument("--allow-skipped", action="append", default=[], metavar="PREFIX=COUNT")
        else:
            child.add_argument("--expected-passed", type=int, required=True)
    health = subparsers.add_parser("health-deadline")
    health.add_argument("--docker-config", required=True)
    args = parser.parse_args()

    try:
        if args.command == "junit":
            allowed: dict[str, int] = {}
            for value in args.allow_skipped:
                prefix, separator, count_text = value.rpartition("=")
                if not separator or not prefix or prefix in allowed:
                    raise ReportError(f"invalid or duplicate --allow-skipped value: {value!r}")
                try:
                    count = int(count_text)
                except ValueError as exc:
                    raise ReportError(f"invalid skipped count: {count_text!r}") from exc
                if count <= 0:
                    raise ReportError("allowed skipped counts must be positive")
                allowed[prefix] = count
            result = validate_junit(
                args.report,
                label=args.label,
                expected_tests=args.expected_tests,
                expected_passed=args.expected_passed,
                allowed_skips=allowed,
            )
        elif args.command == "playwright":
            result = validate_playwright(
                args.report, label=args.label, expected_passed=args.expected_passed
            )
        else:
            result = {"unhealthy_deadline_seconds": healthcheck_deadline_seconds(json.loads(args.docker_config))}
    except ReportError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(json.dumps({"status": "failed", "error": f"invalid Docker healthcheck JSON: {exc}"}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
