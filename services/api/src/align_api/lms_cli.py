from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from align_api.config import get_settings
from align_api.models import (
    AuditEvent,
    Course,
    LtiCoursePermission,
    LtiIdentityBinding,
    LtiPlatform,
    User,
)
from align_api.moodle_adapter import (
    READ_FUNCTIONS,
    MoodleAdapterError,
    MoodleWebService,
    configured_moodle_token,
)


@contextmanager
def _operator_db() -> Iterator[Session]:
    settings = get_settings()
    engine = create_engine(
        settings.operator_database_url or settings.database_url, pool_pre_ping=True
    )
    factory = sessionmaker(bind=engine, class_=Session, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        engine.dispose()


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("LTI endpoint must be a trusted HTTPS URL")
    return f"https://{parsed.hostname.lower()}" + (f":{parsed.port}" if parsed.port else "")


def _actor(db: Session, actor_id: UUID) -> User:
    user = db.get(User, actor_id)
    if user is None or not user.active or user.global_role != "admin":
        raise SystemExit("actor must be an active ALIGN operator account")
    return user


def _audit(
    db: Session, actor: User, action: str, object_type: str, object_id: UUID, refs: dict[str, Any]
) -> None:
    db.add(
        AuditEvent(
            actor_user_id=actor.id,
            action=action,
            object_type=object_type,
            object_id=object_id,
            new_version_refs_json=refs,
            request_id=uuid4(),
        )
    )


def register_platform(args: Any) -> None:
    settings = get_settings()
    origin = _origin(args.moodle_origin)
    if origin not in settings.lti_allowed_origin_set:
        raise SystemExit("platform origin is not in LTI_ALLOWED_ORIGINS")
    if any(
        _origin(value) != origin
        for value in (args.authorization_url, args.token_url, args.jwks_url)
    ):
        raise SystemExit("authorization, token, JWKS, and Moodle origins must match")
    with _operator_db() as db:
        actor = _actor(db, UUID(args.actor_user_id))
        existing = db.scalar(
            select(LtiPlatform).where(
                LtiPlatform.issuer == args.issuer,
                LtiPlatform.client_id == args.client_id,
                LtiPlatform.deployment_id == args.deployment_id,
            )
        )
        if existing is not None:
            same = (
                existing.authorization_url == args.authorization_url
                and existing.token_url == args.token_url
                and existing.jwks_url == args.jwks_url
                and existing.moodle_origin == origin
            )
            if not same:
                raise SystemExit("platform registration exists with different endpoints")
            if not existing.active:
                existing.active = True
                existing.revision += 1
                _audit(
                    db,
                    actor,
                    "lms.platform.enabled",
                    "lti_platform",
                    existing.id,
                    {"revision": existing.revision},
                )
            db.commit()
            print(
                json.dumps(
                    {
                        "platform_id": str(existing.id),
                        "active": existing.active,
                        "revision": existing.revision,
                    }
                )
            )
            return
        platform = LtiPlatform(
            issuer=args.issuer,
            client_id=args.client_id,
            deployment_id=args.deployment_id,
            authorization_url=args.authorization_url,
            token_url=args.token_url,
            jwks_url=args.jwks_url,
            moodle_origin=origin,
            active=True,
            created_by=actor.id,
            revision=1,
        )
        db.add(platform)
        db.flush()
        _audit(db, actor, "lms.platform.registered", "lti_platform", platform.id, {"revision": 1})
        db.commit()
        print(
            json.dumps(
                {"platform_id": str(platform.id), "active": True, "revision": platform.revision}
            )
        )


def list_platforms(_args: Any) -> None:
    with _operator_db() as db:
        rows = db.scalars(
            select(LtiPlatform).order_by(LtiPlatform.created_at, LtiPlatform.id)
        ).all()
        print(
            json.dumps(
                [
                    {
                        "id": str(row.id),
                        "issuer": row.issuer,
                        "client_id": row.client_id,
                        "deployment_id": row.deployment_id,
                        "moodle_origin": row.moodle_origin,
                        "active": row.active,
                        "revision": row.revision,
                    }
                    for row in rows
                ],
                ensure_ascii=False,
                sort_keys=True,
            )
        )


def set_platform_active(args: Any) -> None:
    with _operator_db() as db:
        actor = _actor(db, UUID(args.actor_user_id))
        platform = db.scalar(
            select(LtiPlatform).where(LtiPlatform.id == UUID(args.platform_id)).with_for_update()
        )
        if platform is None:
            raise SystemExit("platform not found")
        if platform.active != args.active:
            platform.active = args.active
            platform.revision += 1
            _audit(
                db,
                actor,
                "lms.platform.enabled" if args.active else "lms.platform.disabled",
                "lti_platform",
                platform.id,
                {"active": platform.active, "revision": platform.revision},
            )
        db.commit()
        print(
            json.dumps(
                {
                    "platform_id": str(platform.id),
                    "active": platform.active,
                    "revision": platform.revision,
                }
            )
        )


def bind_identity(args: Any) -> None:
    with _operator_db() as db:
        actor = _actor(db, UUID(args.actor_user_id))
        platform = db.get(LtiPlatform, UUID(args.platform_id))
        user = db.get(User, UUID(args.user_id))
        if platform is None or not platform.active or user is None or not user.active:
            raise SystemExit("active platform and local user are required")
        row = db.scalar(
            select(LtiIdentityBinding).where(
                LtiIdentityBinding.platform_id == platform.id,
                LtiIdentityBinding.subject == args.subject,
            )
        )
        other = db.scalar(
            select(LtiIdentityBinding).where(
                LtiIdentityBinding.platform_id == platform.id,
                LtiIdentityBinding.moodle_user_id == args.moodle_user_id,
            )
        )
        if row is not None:
            if row.user_id != user.id or row.moodle_user_id != args.moodle_user_id:
                raise SystemExit("LTI subject is already bound differently")
            if not row.active:
                row.active = True
                row.revision += 1
                _audit(
                    db,
                    actor,
                    "lms.identity_binding.enabled",
                    "lti_identity_binding",
                    row.id,
                    {"revision": row.revision, "user_id": str(row.user_id)},
                )
            db.commit()
            print(
                json.dumps(
                    {"binding_id": str(row.id), "active": row.active, "revision": row.revision}
                )
            )
            return
        if other is not None:
            raise SystemExit("Moodle user ID is already bound to another LTI subject")
        binding = LtiIdentityBinding(
            platform_id=platform.id,
            subject=args.subject,
            moodle_user_id=args.moodle_user_id,
            user_id=user.id,
            active=True,
            created_by=actor.id,
            revision=1,
        )
        db.add(binding)
        db.flush()
        _audit(
            db,
            actor,
            "lms.identity_binding.created",
            "lti_identity_binding",
            binding.id,
            {"revision": 1, "user_id": str(user.id), "moodle_user_id": args.moodle_user_id},
        )
        db.commit()
        print(
            json.dumps(
                {"binding_id": str(binding.id), "active": True, "revision": binding.revision}
            )
        )


def set_binding_active(args: Any) -> None:
    with _operator_db() as db:
        actor = _actor(db, UUID(args.actor_user_id))
        binding = db.scalar(
            select(LtiIdentityBinding)
            .where(LtiIdentityBinding.id == UUID(args.binding_id))
            .with_for_update()
        )
        if binding is None:
            raise SystemExit("identity binding not found")
        if binding.active != args.active:
            binding.active = args.active
            binding.revision += 1
            _audit(
                db,
                actor,
                "lms.identity_binding.enabled" if args.active else "lms.identity_binding.revoked",
                "lti_identity_binding",
                binding.id,
                {
                    "active": binding.active,
                    "revision": binding.revision,
                    "user_id": str(binding.user_id),
                },
            )
        db.commit()
        print(
            json.dumps(
                {
                    "binding_id": str(binding.id),
                    "active": binding.active,
                    "revision": binding.revision,
                }
            )
        )


def set_course_permission(args: Any) -> None:
    with _operator_db() as db:
        actor = _actor(db, UUID(args.actor_user_id))
        platform = db.get(LtiPlatform, UUID(args.platform_id))
        course = db.get(Course, UUID(args.course_id))
        if platform is None or course is None or not course.active:
            raise SystemExit("active platform and course are required")
        row = db.scalar(
            select(LtiCoursePermission)
            .where(
                LtiCoursePermission.platform_id == platform.id,
                LtiCoursePermission.course_id == course.id,
            )
            .with_for_update()
        )
        if row is None:
            if not args.active:
                raise SystemExit("course permission is not registered")
            row = LtiCoursePermission(
                platform_id=platform.id,
                course_id=course.id,
                active=True,
                changed_by=actor.id,
            )
            db.add(row)
            db.flush()
            action = "lms.course_permission.granted"
        elif row.active != args.active:
            row.active = args.active
            row.changed_by = actor.id
            action = (
                "lms.course_permission.granted" if args.active else "lms.course_permission.revoked"
            )
        else:
            action = ""
        if action:
            _audit(
                db,
                actor,
                action,
                "lti_course_permission",
                row.id,
                {
                    "platform_id": str(platform.id),
                    "course_id": str(course.id),
                    "active": row.active,
                },
            )
        db.commit()
        print(
            json.dumps(
                {
                    "permission_id": str(row.id),
                    "platform_id": str(platform.id),
                    "course_id": str(course.id),
                    "active": row.active,
                }
            )
        )


def verify_webservice(args: Any) -> None:
    settings = get_settings()
    try:
        token = configured_moodle_token(settings)
    except MoodleAdapterError as exc:
        raise SystemExit("MOODLE_WS_TOKEN is not configured") from exc
    with _operator_db() as db:
        platform = db.get(LtiPlatform, UUID(args.platform_id))
        if platform is None or not platform.active:
            raise SystemExit("active platform not found")
        moodle_origin = platform.moodle_origin

        async def inspect() -> dict[str, Any]:
            parsed = urlsplit(moodle_origin)
            if parsed.hostname is None:
                raise SystemExit("registered Moodle origin is invalid")
            origin = f"https://{parsed.hostname.lower()}" + (
                f":{parsed.port}" if parsed.port else ""
            )
            client = MoodleWebService(
                origin=origin,
                token=token,
                timeout_seconds=settings.moodle_ws_timeout_seconds,
                max_response_bytes=settings.moodle_ws_max_response_bytes,
            )
            enabled = await client.verify_functions(
                set(READ_FUNCTIONS) - {"core_webservice_get_site_info"}
            )
            info = await client.call("core_webservice_get_site_info")
            return {
                "release": info.get("release"),
                "version": info.get("version"),
                "enabled_read_functions": sorted(enabled),
            }

        try:
            result = asyncio.run(inspect())
        except MoodleAdapterError as exc:
            raise SystemExit(f"Moodle Web Services check failed: {exc.code}") from exc
        print(json.dumps(result, sort_keys=True))
