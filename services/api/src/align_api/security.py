from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()
_dummy_password_hash = _hasher.hash("align-dummy-credential-never-valid")


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def hash_password(password: str, maximum_bytes: int = 512) -> str:
    encoded = password.encode("utf-8")
    if not 12 <= len(password) <= 128 or len(encoded) > maximum_bytes:
        raise ValueError("password length is outside the allowed range")
    return _hasher.hash(password)


def verify_password(password: str, encoded: str | None) -> bool:
    target = encoded or _dummy_password_hash
    matched: bool
    try:
        matched = _hasher.verify(target, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        matched = False
    return matched and encoded is not None


def new_session_token() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_csrf_token(secret: bytes, session_id: str, token_hash: str) -> str:
    message = f"{session_id}:{token_hash}".encode("ascii")
    digest = hmac.new(secret, message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def csrf_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_csrf(expected_hash: str, provided: str) -> bool:
    return hmac.compare_digest(expected_hash, csrf_digest(provided))

