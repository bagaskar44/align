from __future__ import annotations

import base64

import pytest

from align_api.security import (
    csrf_digest,
    hash_password,
    new_csrf_token,
    new_session_token,
    token_digest,
    verify_csrf,
    verify_password,
)


def test_session_cookie_represents_32_random_bytes_and_database_hash_is_one_way() -> None:
    token = new_session_token()
    decoded = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    assert len(decoded) == 32
    assert token_digest(token) != token
    assert len(token_digest(token)) == 64
    assert token_digest(token) != token_digest(new_session_token())


def test_csrf_value_is_stable_per_session_and_bound_to_its_hash() -> None:
    secret = b"a" * 32
    csrf = new_csrf_token(secret, "session-id", "f" * 64)
    assert csrf == new_csrf_token(secret, "session-id", "f" * 64)
    assert csrf != new_csrf_token(secret, "other-session", "f" * 64)
    assert verify_csrf(csrf_digest(csrf), csrf)
    assert not verify_csrf(csrf_digest(csrf), "wrong-token")
    assert not verify_csrf(csrf_digest(csrf), "token-bertanda-雪")


def test_argon2_password_hash_verifies_and_rejects_out_of_range_lengths() -> None:
    candidate = "secure synthetic passphrase"
    encoded = hash_password(candidate)
    assert "argon2id" in encoded
    assert verify_password(candidate, encoded)
    assert not verify_password("incorrect password", encoded)
    with pytest.raises(ValueError):
        hash_password("too-short")
    with pytest.raises(ValueError):
        hash_password("x" * 129)


def test_password_limits_are_characters_and_allow_bounded_utf8() -> None:
    candidate = "雪" * 128
    encoded = hash_password(candidate)
    assert verify_password(candidate, encoded)
    with pytest.raises(ValueError):
        hash_password("雪" * 4)
    with pytest.raises(ValueError):
        hash_password("雪" * 129)

