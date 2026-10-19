from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, cast

from fastapi import Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator
from redis import Redis, RedisError
from sqlalchemy import select, update
from sqlalchemy.orm import Session as DbSession

from align_api.config import Settings, get_settings
from align_api.db import get_db
from align_api.models import AuditEvent, Enrollment, User
from align_api.models import Session as LoginSession
from align_api.security import (
    csrf_digest,
    hash_password,
    new_csrf_token,
    new_session_token,
    normalize_email,
    token_digest,
    verify_csrf,
    verify_password,
)

SESSION_COOKIE = "align_session"
SAME_ORIGIN_CSRF_HEADER = "X-CSRF-Token"


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class LoginRequest(StrictRequest):
    email: StrictStr = Field(min_length=3, max_length=320)
    password: StrictStr = Field(min_length=1, max_length=128)

    @field_validator("password")
    @classmethod
    def check_password_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 128:
            raise ValueError("password is too long")
        return value


class PasswordChangeRequest(StrictRequest):
    current_password: StrictStr = Field(min_length=1, max_length=128)
    new_password: StrictStr = Field(min_length=12, max_length=128)

    @field_validator("new_password")
    @classmethod
    def check_new_password_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 128:
            raise ValueError("password is too long")
        return value


@dataclass(frozen=True)
class Identity:
    user: User
    session: LoginSession
    csrf_token: str


def _redis(settings: Settings) -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2)


def _origin_matches(request: Request, settings: Settings) -> bool:
    origin = request.headers.get("origin")
    return origin is not None and origin.rstrip("/") == settings.app_origin.rstrip("/")


def _check_origin(request: Request, settings: Settings) -> None:
    if not _origin_matches(request, settings):
        raise HTTPException(status_code=403, detail="origin rejected")


def _check_login_limit(settings: Settings, email_key: str, ip_key: str) -> None:
    client = _redis(settings)
    try:
        email_count = int(cast(str | int | None, client.get(email_key)) or 0)
        ip_count = int(cast(str | int | None, client.get(ip_key)) or 0)
    except RedisError as exc:
        raise HTTPException(status_code=503, detail="rate limiter unavailable") from exc
    finally:
        client.close()
    if int(email_count) >= settings.login_account_limit or int(ip_count) >= settings.login_ip_limit:
        raise HTTPException(status_code=429, detail="rate limit exceeded")


def _record_login_failure(settings: Settings, email_key: str, ip_key: str) -> None:
    client = _redis(settings)
    try:
        with client.pipeline(transaction=True) as pipeline:
            pipeline.incr(email_key)
            pipeline.expire(email_key, settings.login_window_seconds, nx=True)
            pipeline.incr(ip_key)
            pipeline.expire(ip_key, settings.login_window_seconds, nx=True)
            pipeline.execute()
    except RedisError as exc:
        raise HTTPException(status_code=503, detail="rate limiter unavailable") from exc
    finally:
        client.close()


def _clear_login_failures(settings: Settings, email_key: str) -> None:
    client = _redis(settings)
    try:
        # Keep the IP bucket until its window expires so a successful login to
        # one account cannot reset brute-force attempts against other accounts.
        client.delete(email_key)
    except RedisError as exc:
        raise HTTPException(status_code=503, detail="rate limiter unavailable") from exc
    finally:
        client.close()


def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    _check_origin(request, settings)
    normalized_email = normalize_email(body.email)
    redis_prefix = settings.redis_queue_prefix
    email_key = f"{redis_prefix}:login:email:{hashlib.sha256(normalized_email.encode()).hexdigest()}"
    forwarded = request.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
    ip = forwarded or (request.client.host if request.client else "unknown")
    ip_key = f"{redis_prefix}:login:ip:{hashlib.sha256(ip.encode()).hexdigest()}"
    _check_login_limit(settings, email_key, ip_key)

    user = db.scalar(select(User).where(User.normalized_email == normalized_email))
    valid = verify_password(body.password, user.password_hash if user and user.active else None)
    if user is None or not user.active or not valid:
        _record_login_failure(settings, email_key, ip_key)
        raise HTTPException(status_code=401, detail="invalid login")

    _clear_login_failures(settings, email_key)
    now = datetime.now(UTC)
    raw_token = new_session_token()
    session = LoginSession(
        user_id=user.id,
        token_hash=token_digest(raw_token),
        csrf_hash="0" * 64,
        last_seen_at=now,
        expires_at=now + timedelta(hours=settings.session_idle_hours),
        absolute_expires_at=now + timedelta(hours=settings.session_absolute_hours),
    )
    db.add(session)
    db.flush()
    csrf_token = new_csrf_token(settings.csrf_secret_bytes, str(session.id), session.token_hash)
    session.csrf_hash = csrf_digest(csrf_token)
    db.add(
        AuditEvent(
            actor_user_id=user.id,
            action="auth.login",
            object_type="session",
            object_id=session.id,
            request_id=request.state.request_id,
        )
    )
    db.commit()

    secure_cookie = settings.app_origin.startswith("https://")
    response.set_cookie(
        key=SESSION_COOKIE,
        value=raw_token,
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
        path="/",
        max_age=settings.session_absolute_hours * 60 * 60,
    )
    response.headers["Cache-Control"] = "private, no-store"
    return {"user": _user_summary(user), "csrf_token": csrf_token}


def current_identity(
    request: Request,
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Identity:
    raw_token = request.cookies.get(SESSION_COOKIE)
    if not raw_token:
        raise HTTPException(status_code=401, detail="session required")
    now = datetime.now(UTC)
    session = db.scalar(
        select(LoginSession).where(
            LoginSession.token_hash == token_digest(raw_token),
            LoginSession.revoked_at.is_(None),
            LoginSession.expires_at > now,
            LoginSession.absolute_expires_at > now,
        )
    )
    if session is None:
        raise HTTPException(status_code=401, detail="invalid session")
    user = db.get(User, session.user_id)
    if user is None or not user.active:
        raise HTTPException(status_code=401, detail="inactive account")
    csrf_token = new_csrf_token(settings.csrf_secret_bytes, str(session.id), session.token_hash)
    if not verify_csrf(session.csrf_hash, csrf_token):
        raise HTTPException(status_code=401, detail="session secret changed")

    session.last_seen_at = now
    session.expires_at = min(now + timedelta(hours=settings.session_idle_hours), session.absolute_expires_at)
    db.commit()
    return Identity(user=user, session=session, csrf_token=csrf_token)


def verify_mutation(
    request: Request,
    identity: Annotated[Identity, Depends(current_identity)],
    settings: Annotated[Settings, Depends(get_settings)],
    csrf_token: Annotated[str | None, Header(alias=SAME_ORIGIN_CSRF_HEADER)] = None,
) -> Identity:
    _check_origin(request, settings)
    if csrf_token is None or not verify_csrf(identity.session.csrf_hash, csrf_token):
        raise HTTPException(status_code=403, detail="csrf rejected")
    return identity


def require_password_change_completed(
    identity: Annotated[Identity, Depends(current_identity)],
) -> Identity:
    if identity.user.must_change_password:
        raise HTTPException(status_code=403, detail="password change required")
    return identity


def require_verified_active_identity(
    identity: Annotated[Identity, Depends(verify_mutation)],
) -> Identity:
    if identity.user.must_change_password:
        raise HTTPException(status_code=403, detail="password change required")
    return identity


def logout(
    response: Response,
    identity: Annotated[Identity, Depends(verify_mutation)],
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    request: Request,
) -> Response:
    identity.session.revoked_at = datetime.now(UTC)
    db.add(
        AuditEvent(
            actor_user_id=identity.user.id,
            action="auth.logout",
            object_type="session",
            object_id=identity.session.id,
            request_id=request.state.request_id,
        )
    )
    db.commit()
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=settings.app_origin.startswith("https://"),
        httponly=True,
        samesite="lax",
    )
    response.status_code = 204
    response.headers["Cache-Control"] = "private, no-store"
    return response


def me(
    identity: Annotated[Identity, Depends(current_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    memberships = db.execute(
        select(Enrollment, Enrollment.course_id)
        .where(Enrollment.user_id == identity.user.id, Enrollment.active.is_(True))
        .order_by(Enrollment.course_id)
    ).all()
    courses: list[dict[str, object]] = []
    for enrollment, _course_id in memberships:
        if enrollment.course.active:
            courses.append(
                {
                    "course_id": str(enrollment.course.id),
                    "code": enrollment.course.code,
                    "title": enrollment.course.title,
                    "role": enrollment.role,
                }
            )
    return {
        "user": _user_summary(identity.user),
        "memberships": courses,
        "csrf_token": identity.csrf_token,
        "runtime_limits": {
            "max_files_per_bundle": settings.max_files_per_bundle,
            "max_file_bytes": settings.max_file_bytes,
            "max_image_pixels": settings.max_image_pixels,
            "max_pdf_image_pages_per_bundle": settings.max_pdf_image_pages_per_bundle,
            "max_xlsx_sheets": settings.max_xlsx_sheets,
            "max_xlsx_nonempty_cells": settings.max_xlsx_nonempty_cells,
        },
    }


def change_password(
    body: PasswordChangeRequest,
    identity: Annotated[Identity, Depends(verify_mutation)],
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    request: Request,
) -> Response:
    if not verify_password(body.current_password, identity.user.password_hash):
        raise HTTPException(status_code=401, detail="current password rejected")
    try:
        new_hash = hash_password(body.new_password, settings.max_password_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="new password rejected") from exc
    identity.user.password_hash = new_hash
    identity.user.must_change_password = False
    identity.user.revision += 1
    db.execute(
        update(LoginSession)
        .where(LoginSession.user_id == identity.user.id, LoginSession.id != identity.session.id)
        .values(revoked_at=datetime.now(UTC))
    )
    identity.session.revoked_at = datetime.now(UTC)
    db.add(
        AuditEvent(
            actor_user_id=identity.user.id,
            action="auth.password_changed",
            object_type="user",
            object_id=identity.user.id,
            request_id=request.state.request_id,
        )
    )
    db.commit()
    response = Response(status_code=204)
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=settings.app_origin.startswith("https://"),
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _user_summary(user: User) -> dict[str, object]:
    return {
        "id": str(user.id),
        "email": user.normalized_email,
        "display_name": user.display_name,
        "global_role": user.global_role,
        "must_change_password": user.must_change_password,
    }

