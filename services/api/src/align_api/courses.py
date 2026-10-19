from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field, StrictBool, StrictStr, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from align_api.auth import (
    Identity,
    StrictRequest,
    require_password_change_completed,
    require_verified_active_identity,
)
from align_api.db import get_db
from align_api.models import AuditEvent, Course, Enrollment, User

router = APIRouter(prefix="/courses", tags=["courses"])


class CourseCreate(StrictRequest):
    code: StrictStr = Field(min_length=1, max_length=64)
    title: StrictStr = Field(min_length=1, max_length=240)

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("course code cannot be blank")
        return normalized

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("course title cannot be blank")
        return normalized


class EnrollmentInput(StrictRequest):
    user_id: UUID = Field(strict=False)
    role: StrictStr = Field(pattern=r"^(lecturer|student)$")
    active: StrictBool


class EnrollmentUpdate(StrictRequest):
    expected_revision: int = Field(ge=1)
    enrollments: list[EnrollmentInput] = Field(max_length=500)


@router.get("")
def list_courses(
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
) -> dict[str, object]:
    if identity.user.global_role == "admin":
        courses = db.scalars(select(Course).where(Course.active.is_(True)).order_by(Course.code)).all()
        return {"items": [_course_summary(course) for course in courses], "next_cursor": None}
    courses = db.scalars(
        select(Course)
        .join(Enrollment, Enrollment.course_id == Course.id)
        .where(
            Enrollment.user_id == identity.user.id,
            Enrollment.active.is_(True),
            Course.active.is_(True),
        )
        .order_by(Course.code)
    ).all()
    return {"items": [_course_summary(course) for course in courses], "next_cursor": None}


@router.get("/{course_id}")
def get_course(
    course_id: UUID,
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
) -> dict[str, object]:
    course = db.get(Course, course_id)
    if course is None or not course.active:
        raise HTTPException(status_code=404, detail="course not found")
    if identity.user.global_role == "admin":
        return _course_summary(course)
    membership = db.scalar(
        select(Enrollment.id).where(
            Enrollment.course_id == course_id,
            Enrollment.user_id == identity.user.id,
            Enrollment.active.is_(True),
        )
    )
    if membership is None:
        raise HTTPException(status_code=404, detail="course not found")
    return _course_summary(course)


@router.post("", status_code=201)
def create_course(
    body: CourseCreate,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    request: Request,
) -> dict[str, object]:
    if identity.user.global_role != "admin":
        raise HTTPException(status_code=403, detail="admin required")
    course = Course(code=body.code, title=body.title, active=True)
    db.add(course)
    try:
        db.flush()
        db.add(
            AuditEvent(
                actor_user_id=identity.user.id,
                action="course.create",
                object_type="course",
                object_id=course.id,
                request_id=request.state.request_id,
            )
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="course code already exists") from exc
    db.refresh(course)
    return _course_summary(course)


@router.get("/{course_id}/enrollments")
def get_enrollments(
    course_id: UUID,
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
) -> dict[str, object]:
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="course not found")
    is_lecturer = db.scalar(
        select(Enrollment.id).where(
            Enrollment.course_id == course_id,
            Enrollment.user_id == identity.user.id,
            Enrollment.active.is_(True),
            Enrollment.role == "lecturer",
        )
    ) is not None
    if not is_lecturer and identity.user.global_role != "admin":
        raise HTTPException(status_code=404, detail="course not found")
    rows = db.scalars(
        select(Enrollment)
        .where(Enrollment.course_id == course_id)
        .order_by(Enrollment.role, Enrollment.user_id)
    ).all()
    return {
        "course_id": str(course_id),
        "items": [
            {
                "user_id": str(item.user_id),
                "display_name": item.user.display_name,
                "role": item.role,
                "active": item.active,
            }
            for item in rows
        ],
        "next_cursor": None,
    }


@router.put("/{course_id}/enrollments")
def update_enrollments(
    course_id: UUID,
    body: EnrollmentUpdate,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    request: Request,
) -> dict[str, object]:
    if identity.user.global_role != "admin":
        raise HTTPException(status_code=403, detail="admin required")
    course = db.scalar(select(Course).where(Course.id == course_id).with_for_update())
    if course is None:
        raise HTTPException(status_code=404, detail="course not found")
    if course.revision != body.expected_revision:
        raise HTTPException(status_code=409, detail="revision mismatch")
    seen: set[UUID] = set()
    new_values: dict[UUID, EnrollmentInput] = {}
    for enrollment in body.enrollments:
        if enrollment.user_id in seen:
            raise HTTPException(status_code=422, detail="duplicate enrollment")
        seen.add(enrollment.user_id)
        new_values[enrollment.user_id] = enrollment
    if new_values:
        users = db.scalars(select(User).where(User.id.in_(new_values))).all()
        if len(users) != len(new_values):
            raise HTTPException(status_code=422, detail="unknown user")
    rows = db.scalars(
        select(Enrollment).where(Enrollment.course_id == course_id).with_for_update()
    ).all()
    by_user = {row.user_id: row for row in rows}
    for row in rows:
        desired = new_values.get(row.user_id)
        if desired is None:
            row.active = False
            row.revision += 1
        else:
            row.role = desired.role
            row.active = desired.active
            row.revision += 1
    for user_id, desired in new_values.items():
        if user_id not in by_user:
            db.add(Enrollment(course_id=course_id, user_id=user_id, role=desired.role, active=desired.active))
    course.revision += 1
    db.add(
        AuditEvent(
            actor_user_id=identity.user.id,
            action="course.enrollments.replace",
            object_type="course",
            object_id=course.id,
            request_id=request.state.request_id,
        )
    )
    db.commit()
    return {"course_id": str(course.id), "revision": course.revision}


def _course_summary(course: Course) -> dict[str, object]:
    return {
        "id": str(course.id),
        "code": course.code,
        "title": course.title,
        "active": course.active,
        "revision": course.revision,
        "created_at": course.created_at.isoformat(),
        "updated_at": course.updated_at.isoformat(),
    }
