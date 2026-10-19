from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from align_api.auth import (
    Identity,
    require_password_change_completed,
    require_verified_active_identity,
)


def identity(must_change_password: bool) -> Identity:
    return Identity(
        user=SimpleNamespace(must_change_password=must_change_password),  # type: ignore[arg-type]
        session=SimpleNamespace(),  # type: ignore[arg-type]
        csrf_token="",
    )


@pytest.mark.parametrize("guard", [require_password_change_completed, require_verified_active_identity])
def test_temporary_password_cannot_reach_course_access_guards(
    guard: Callable[[Identity], Identity],
) -> None:
    with pytest.raises(HTTPException) as error:
        guard(identity(True))
    assert error.value.status_code == 403


def test_password_change_complete_identity_passes_course_access_guard() -> None:
    assert require_password_change_completed(identity(False)).user.must_change_password is False
