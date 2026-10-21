from __future__ import annotations

from types import SimpleNamespace

import pytest

from align_api.submissions import _retry_scheduled


@pytest.mark.parametrize(
    "error_code",
    ["parser_capacity_unavailable", "parser_storage_unavailable"],
)
def test_student_status_can_poll_a_recoverable_parser_block(error_code: str) -> None:
    assert _retry_scheduled(SimpleNamespace(status="blocked", last_error_code=error_code))


@pytest.mark.parametrize(
    ("status", "error_code"),
    [
        ("blocked", "parser_failed"),
        ("blocked", None),
        ("ready_for_review", "parser_capacity_unavailable"),
        ("failed", "parser_storage_unavailable"),
    ],
)
def test_student_status_stops_polling_for_nonretryable_parser_states(
    status: str, error_code: str | None,
) -> None:
    assert not _retry_scheduled(SimpleNamespace(status=status, last_error_code=error_code))
