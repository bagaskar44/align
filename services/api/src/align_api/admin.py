from __future__ import annotations

import secrets
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import Field, StrictBool, StrictStr, field_validator
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from align_api.auth import (
    Identity,
    StrictRequest,
    require_password_change_completed,
    require_verified_active_identity,
)
from align_api.db import get_db
from align_api.idempotency import replay_or_lock, store_response
from align_api.models import AuditEvent, Course, User
from align_api.models import Session as LoginSession
from align_api.security import hash_password, normalize_email

router = APIRouter(prefix="/admin", tags=["admin"])


class UserCreate(StrictRequest):
    email: StrictStr = Field(min_length=3, max_length=320)
    display_name: StrictStr = Field(min_length=1, max_length=160)
    role: StrictStr = Field(pattern=r"^(admin|member)$")

    @field_validator("email")
    @classmethod
    def email_is_normalizable(cls, value: str) -> str:
        return normalize_email(value)

    @field_validator("display_name")
    @classmethod
    def display_name_is_nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("display name cannot be blank")
        return value


class UserUpdate(StrictRequest):
    expected_revision: int = Field(ge=1)
    active: StrictBool | None = None
    display_name: StrictStr | None = Field(default=None, min_length=1, max_length=160)
    global_role: StrictStr | None = Field(default=None, pattern=r"^(admin|member)$")

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("display name cannot be blank")
        return value


class PasswordResetRequest(StrictRequest):
    expected_revision: int = Field(ge=1)


class CourseUpdate(StrictRequest):
    expected_revision: int = Field(ge=1)
    active: StrictBool | None = None
    code: StrictStr | None = Field(default=None, min_length=1, max_length=64)
    title: StrictStr | None = Field(default=None, min_length=1, max_length=240)

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str | None) -> str | None:
        return value.strip().upper() if value is not None else None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


def _admin(identity: Identity) -> None:
    if identity.user.global_role != "admin":
        raise HTTPException(status_code=403, detail="admin required")


def _summary(user: User) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "email": user.normalized_email,
        "display_name": user.display_name,
        "global_role": user.global_role,
        "active": user.active,
        "must_change_password": user.must_change_password,
        "revision": user.revision,
    }


def _course_summary(course: Course) -> dict[str, Any]:
    return {
        "id": str(course.id),
        "code": course.code,
        "title": course.title,
        "active": course.active,
        "revision": course.revision,
        "created_at": course.created_at.isoformat(),
        "updated_at": course.updated_at.isoformat(),
    }


@router.get("/courses")
def list_admin_courses(
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
) -> dict[str, object]:
    _admin(identity)
    courses = db.scalars(select(Course).order_by(Course.code).limit(200)).all()
    return {"items": [_course_summary(course) for course in courses], "next_cursor": None}


@router.patch("/courses/{course_id}")
def update_admin_course(
    course_id: UUID,
    body: CourseUpdate,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
) -> dict[str, object]:
    _admin(identity)
    course = db.scalar(select(Course).where(Course.id == course_id).with_for_update())
    if course is None:
        raise HTTPException(status_code=404, detail="course not found")
    if course.revision != body.expected_revision:
        raise HTTPException(status_code=409, detail="revision mismatch")
    if body.active is None and body.code is None and body.title is None:
        raise HTTPException(status_code=422, detail="at least one course field is required")
    if body.code == "" or body.title == "":
        raise HTTPException(status_code=422, detail="course fields cannot be blank")
    old = {"active": course.active, "code": course.code, "title": course.title}
    if body.active is not None:
        course.active = body.active
    if body.code is not None:
        course.code = body.code
    if body.title is not None:
        course.title = body.title
    course.revision += 1
    db.add(
        AuditEvent(
            actor_user_id=identity.user.id,
            action="course.updated",
            object_type="course",
            object_id=course.id,
            old_version_refs_json=old,
            new_version_refs_json={"active": course.active, "code": course.code, "title": course.title},
            request_id=request.state.request_id,
        )
    )
    db.commit()
    db.refresh(course)
    return {"course": _course_summary(course)}


@router.get("/users")
def list_users(
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
) -> dict[str, object]:
    _admin(identity)
    users = db.scalars(select(User).order_by(User.normalized_email).limit(100)).all()
    return {"items": [_summary(user) for user in users], "next_cursor": None}


@router.post("/users", status_code=201)
def create_user(
    body: UserCreate,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    _admin(identity)
    route = "/api/v1/admin/users"
    cached = replay_or_lock(db, identity.user.id, "POST", route, idempotency_key, body.model_dump())
    if cached is not None:
        return cached
    temporary_password = secrets.token_urlsafe(24)
    user = User(
        normalized_email=body.email,
        display_name=body.display_name,
        password_hash=hash_password(temporary_password),
        global_role=body.role,
        active=True,
        must_change_password=True,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="normalized email already exists") from exc
    payload: dict[str, object] = {"user": _summary(user), "temporary_password": temporary_password}
    store_response(
        db,
        identity.user.id,
        "POST",
        route,
        idempotency_key,
        {"user": payload["user"], "temporary_password": None, "credential_replay": True},
    )
    db.add(
        AuditEvent(
            actor_user_id=identity.user.id,
            action="identity.user.created",
            object_type="user",
            object_id=user.id,
            new_version_refs_json={"revision": user.revision, "role": user.global_role},
            request_id=request.state.request_id,
        )
    )
    db.commit()
    return payload


@router.patch("/users/{user_id}")
def update_user(
    user_id: UUID,
    body: UserUpdate,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
) -> dict[str, object]:
    _admin(identity)
    # Always acquire the shared admin-set lock before any individual user lock.
    # Otherwise concurrent demotions can deadlock while each owns a different row.
    db.execute(text("SELECT pg_advisory_xact_lock(553715848)"))
    user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    if user.revision != body.expected_revision:
        raise HTTPException(status_code=409, detail="revision mismatch")
    if body.active is None and body.display_name is None and body.global_role is None:
        raise HTTPException(status_code=422, detail="at least one user field is required")
    will_be_active_admin = (body.active if body.active is not None else user.active) and (
        body.global_role if body.global_role is not None else user.global_role
    ) == "admin"
    if user.active and user.global_role == "admin" and not will_be_active_admin:
        active_admins = db.scalars(
            select(User).where(User.active.is_(True), User.global_role == "admin").with_for_update()
        ).all()
        if len(active_admins) <= 1:
            raise HTTPException(status_code=409, detail="cannot deactivate the last active administrator")
    old = {"active": user.active, "display_name": user.display_name, "global_role": user.global_role}
    if body.active is not None:
        user.active = body.active
    if body.display_name is not None:
        user.display_name = body.display_name
    if body.global_role is not None:
        user.global_role = body.global_role
    user.revision += 1
    if not user.active:
        db.execute(
            update(LoginSession)
            .where(LoginSession.user_id == user.id, LoginSession.revoked_at.is_(None))
            .values(revoked_at=func.now())
        )
    db.add(
        AuditEvent(
            actor_user_id=identity.user.id,
            action="identity.user.updated",
            object_type="user",
            object_id=user.id,
            old_version_refs_json=old,
            new_version_refs_json={"active": user.active, "display_name": user.display_name, "global_role": user.global_role},
            request_id=request.state.request_id,
        )
    )
    db.commit()
    db.refresh(user)
    return {"user": _summary(user)}


@router.post("/users/{user_id}/reset-password", status_code=201)
def reset_password(
    user_id: UUID,
    body: PasswordResetRequest,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    _admin(identity)
    route = f"/api/v1/admin/users/{user_id}/reset-password"
    body_payload = body.model_dump()
    cached = replay_or_lock(db, identity.user.id, "POST", route, idempotency_key, body_payload)
    if cached is not None:
        return cached
    user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    if user.revision != body.expected_revision:
        raise HTTPException(status_code=409, detail="revision mismatch")
    temporary_password = secrets.token_urlsafe(24)
    user.password_hash = hash_password(temporary_password)
    user.must_change_password = True
    user.revision += 1
    db.execute(
        update(LoginSession)
        .where(LoginSession.user_id == user.id, LoginSession.revoked_at.is_(None))
        .values(revoked_at=func.now())
    )
    payload: dict[str, object] = {"user": _summary(user), "temporary_password": temporary_password}
    store_response(
        db,
        identity.user.id,
        "POST",
        route,
        idempotency_key,
        {"user": payload["user"], "temporary_password": None, "credential_replay": True},
    )
    db.add(
        AuditEvent(
            actor_user_id=identity.user.id,
            action="identity.user.password_reset",
            object_type="user",
            object_id=user.id,
            new_version_refs_json={"revision": user.revision},
            request_id=request.state.request_id,
        )
    )
    db.commit()
    return payload
