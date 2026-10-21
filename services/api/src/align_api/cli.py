from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from align_api.config import get_settings
from align_api.db import SessionFactory
from align_api.models import (
    AuditEvent,
    BudgetAccount,
    Course,
    Enrollment,
    MethodManifest,
    Operation,
    OutboxEvent,
    ReleaseManifest,
    User,
)
from align_api.security import hash_password, normalize_email

RELEASE_MANIFEST_COLUMNS = {
    "parser": "parser_manifest_id",
    "model": "model_manifest_id",
    "retrieval": "retrieval_manifest_id",
    "rules": "rules_manifest_id",
    "prompt": "prompt_manifest_id",
    "routing": "routing_policy_manifest_id",
    "batching": "batching_policy_manifest_id",
}


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_json(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _operator(db: DbSession) -> User | None:
    return db.scalar(select(User).where(User.global_role == "admin", User.active.is_(True)).order_by(User.created_at))


def _audit(
    db: DbSession,
    action: str,
    object_type: str,
    object_id: UUID | None,
    actor: User | None,
) -> None:
    request_id = uuid4()
    db.add(
        AuditEvent(
            actor_user_id=actor.id if actor else None,
            action=action,
            object_type=object_type,
            object_id=object_id,
            request_id=request_id,
            occurred_at=datetime.now(UTC),
        )
    )


def run_migrations() -> None:
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    print(json.dumps({"migration": "head", "status": "completed"}))


def bootstrap_admin(email: str, display_name: str, password_from_stdin: bool) -> None:
    if not password_from_stdin:
        raise SystemExit("bootstrap admin requires --password-stdin")
    settings = get_settings()
    password = sys.stdin.readline().rstrip("\r\n")
    if len(password.encode("utf-8")) < 20:
        raise SystemExit("bootstrap temporary password must contain at least 20 bytes")
    password_hash = hash_password(password, settings.max_password_bytes)
    normalized = normalize_email(email)
    with SessionFactory.begin() as db:
        if db.scalar(select(User.id).where(User.normalized_email == normalized)) is not None:
            raise SystemExit("account already exists; bootstrap never resets an existing password")
        user = User(
            normalized_email=normalized,
            display_name=display_name.strip(),
            password_hash=password_hash,
            global_role="admin",
            active=True,
            must_change_password=True,
        )
        db.add(user)
        db.flush()
        _audit(db, "identity.bootstrap_admin", "user", user.id, user)
    print(json.dumps({"user_created": True, "email": normalized, "must_change_password": True}))


def seed_synthetic() -> None:
    settings = get_settings()
    if settings.app_env != "development" or settings.data_mode != "synthetic" or not settings.app_demo_mode:
        raise SystemExit("synthetic seed requires development, DATA_MODE=synthetic, and APP_DEMO_MODE=true")
    if settings.align_seed_password is None:
        raise SystemExit("ALIGN_SEED_PASSWORD must be set in the local environment")
    password = settings.align_seed_password.get_secret_value()
    password_hash = hash_password(password, settings.max_password_bytes)
    account_specs = [
        ("admin@align.local", "Admin Sintetis", "admin"),
        ("dosen@align.local", "Dosen Sintetis", "member"),
        ("mahasiswa@align.local", "Mahasiswa Sintetis", "member"),
        ("dosen2@align.local", "Dosen Sintetis Dua", "member"),
        ("mahasiswa2@align.local", "Mahasiswa Sintetis Dua", "member"),
    ]
    course_specs = [("IF101", "Pengantar Informatika"), ("IF202", "Sistem Informasi")]
    created_accounts = 0
    created_courses = 0
    created_memberships = 0
    with SessionFactory.begin() as db:
        users: dict[str, User] = {}
        for email, name, role in account_specs:
            normalized = normalize_email(email)
            user = db.scalar(select(User).where(User.normalized_email == normalized))
            if user is None:
                user = User(
                    normalized_email=normalized,
                    display_name=name,
                    password_hash=password_hash,
                    global_role=role,
                    active=True,
                    must_change_password=False,
                )
                db.add(user)
                db.flush()
                created_accounts += 1
            users[email] = user

        courses: dict[str, Course] = {}
        for code, title in course_specs:
            course = db.scalar(select(Course).where(Course.code == code))
            if course is None:
                course = Course(code=code, title=title, active=True)
                db.add(course)
                db.flush()
                created_courses += 1
            courses[code] = course

        for code, email, role in [
            ("IF101", "dosen@align.local", "lecturer"),
            ("IF101", "mahasiswa@align.local", "student"),
            ("IF202", "dosen2@align.local", "lecturer"),
            ("IF202", "mahasiswa2@align.local", "student"),
        ]:
            exists = db.scalar(
                select(Enrollment.id).where(
                    Enrollment.course_id == courses[code].id,
                    Enrollment.user_id == users[email].id,
                )
            )
            if exists is None:
                db.add(Enrollment(course_id=courses[code].id, user_id=users[email].id, role=role, active=True))
                created_memberships += 1

        if db.scalar(select(BudgetAccount.id)) is None:
            db.add(
                BudgetAccount(
                    currency="USD",
                    confirmed_total=Decimal("25.00000000"),
                    reserve_floor=Decimal("5.00000000"),
                    planned_topup=Decimal("0.00000000"),
                    active=True,
                )
            )
        _audit(db, "seed.synthetic.ensure", "development_seed", None, _operator(db))
    print(
        json.dumps(
            {
                "status": "ready",
                "mode": "synthetic_demo",
                "accounts_created": created_accounts,
                "courses_created": created_courses,
                "memberships_created": created_memberships,
                "accounts": [item[0] for item in account_specs],
                "password_source": "ALIGN_SEED_PASSWORD in local .env (not printed)",
                "provider_mode": settings.provider_mode,
            },
            ensure_ascii=False,
        )
    )


def register_release(filename: str) -> None:
    manifest_path = Path(filename).resolve()
    release = json.loads(manifest_path.read_text(encoding="utf-8"))
    if release.get("schema_version") != 1 or release.get("provider_mode") != "mock" or release.get("data_mode") != "synthetic":
        raise SystemExit("B0 only registers schema-1 synthetic mock releases")
    manifest_payloads: dict[str, dict[str, Any]] = {}
    for kind, relative_path in release["manifests"].items():
        if kind not in RELEASE_MANIFEST_COLUMNS:
            raise SystemExit(f"unsupported method manifest kind: {kind}")
        path = (manifest_path.parent / relative_path).resolve()
        payload = json.loads(path.read_text(encoding="utf-8"))
        manifest_payloads[kind] = payload
    for required_kind in RELEASE_MANIFEST_COLUMNS:
        if required_kind not in manifest_payloads:
            raise SystemExit(f"release is missing the {required_kind} manifest")
    config_hash = sha256_json(release)
    with SessionFactory.begin() as db:
        existing = db.scalar(select(ReleaseManifest).where(ReleaseManifest.config_hash == config_hash))
        if existing is not None:
            print(json.dumps({"release_id": str(existing.id), "state": existing.state, "config_hash": config_hash}))
            return
        method_rows: dict[str, MethodManifest] = {}
        for kind, payload in manifest_payloads.items():
            digest = sha256_json(payload)
            row = db.scalar(select(MethodManifest).where(MethodManifest.sha256 == digest))
            if row is None:
                row = MethodManifest(
                    kind=kind,
                    version_label=str(payload.get("manifest_id", payload.get("policy_id", f"{kind}-baseline-v1"))),
                    content_json=payload,
                    sha256=digest,
                )
                db.add(row)
                db.flush()
            elif row.kind != kind or row.content_json != payload:
                raise SystemExit("manifest hash is already registered with different immutable content")
            method_rows[kind] = row
        kwargs: dict[str, UUID] = {
            column: method_rows[kind].id for kind, column in RELEASE_MANIFEST_COLUMNS.items()
        }
        release_row = ReleaseManifest(
            version_label=release["version_label"],
            **kwargs,
            grounding_registry_version=release["grounding_registry_version"],
            lint_registry_version=release["lint_registry_version"],
            manifest_payload=release,
            config_hash=config_hash,
            state="draft",
        )
        db.add(release_row)
        db.flush()
        _audit(db, "release.register", "release_manifest", release_row.id, _operator(db))
        result = {"release_id": str(release_row.id), "state": release_row.state, "config_hash": config_hash}
    print(json.dumps(result, ensure_ascii=False))


def activate_release(release_id: str) -> None:
    parsed_id = UUID(release_id)
    with SessionFactory.begin() as db:
        release = db.scalar(select(ReleaseManifest).where(ReleaseManifest.id == parsed_id).with_for_update())
        if release is None:
            raise SystemExit("release not found")
        if release.state == "active":
            print(json.dumps({"release_id": release_id, "state": "active", "empirical_verification": False}))
            return
        if release.state not in {"draft", "verified"}:
            raise SystemExit("retired releases cannot be activated again")
        payload = release.manifest_payload
        checks = payload.get("activation_checks", {})
        available = set(payload.get("capabilities_available", []))
        unavailable = set(payload.get("capabilities_unavailable", []))
        if payload.get("provider_mode") != "mock" or payload.get("data_mode") != "synthetic":
            raise SystemExit("B0 baseline activation requires synthetic mock mode")
        if not {"manual_auth", "mock_provider_schema"}.issubset(available):
            raise SystemExit("required B0 baseline capabilities are not available")
        if any(name not in unavailable for name in ("native_parser", "live_provider", "paid_calls")):
            raise SystemExit("unavailable parser/live/paid capabilities must remain explicit")
        if not checks.get("mock_schema_contract") or not checks.get("paid_calls_fail_closed"):
            raise SystemExit("release capability checks have not passed")
        if checks.get("baseline_batch_size") != 1:
            raise SystemExit("only baseline batch size one may be activated in B0")
        if checks.get("empirical_verification") is not False:
            raise SystemExit("B0 cannot claim empirical verification")
        actor = _operator(db)
        if actor is None:
            raise SystemExit("bootstrap a local operator account before activation")
        active_releases = db.scalars(
            select(ReleaseManifest).where(ReleaseManifest.state == "active").with_for_update()
        ).all()
        for active in active_releases:
            active.state = "retired"
        db.flush()
        if release.state == "draft":
            release.state = "verified"
            db.flush()
        release.state = "active"
        release.activated_by = actor.id
        release.activated_at = datetime.now(UTC)
        _audit(db, "release.activate", "release_manifest", release.id, actor)
    print(json.dumps({"release_id": release_id, "state": "active", "empirical_verification": False}))


def enqueue_foundation_smoke(idempotency_key: str) -> None:
    with SessionFactory.begin() as db:
        operation = db.scalar(
            select(Operation).where(
                Operation.kind == "foundation_smoke",
                Operation.idempotency_key == idempotency_key,
            )
        )
        if operation is None:
            operation = Operation(kind="foundation_smoke", idempotency_key=idempotency_key, status="queued")
            db.add(operation)
            db.flush()
            db.add(
                OutboxEvent(
                    event_type="operation.foundation_smoke",
                    subject_id=operation.id,
                    payload_json={"operation_id": str(operation.id)},
                )
            )
        result_id = str(operation.id)
        result_status = operation.status
    print(json.dumps({"operation_id": result_id, "status": result_status, "academic_work": False}))


def get_operation(operation_id: str) -> None:
    parsed_id = UUID(operation_id)
    with SessionFactory() as db:
        operation = db.get(Operation, parsed_id)
        if operation is None:
            raise SystemExit("operation not found")
        print(
            json.dumps(
                {
                    "operation_id": str(operation.id),
                    "kind": operation.kind,
                    "status": operation.status,
                    "attempt_count": operation.attempt_count,
                    "result": operation.result_json,
                    "academic_work": False,
                }
            )
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="align", description="ALIGN local operator and development CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    db = subparsers.add_parser("db", help="database operations")
    db.add_subparsers(dest="db_command", required=True).add_parser("upgrade", help="apply Alembic migrations")

    admin = subparsers.add_parser("admin", help="operator account operations")
    admin_sub = admin.add_subparsers(dest="admin_command", required=True)
    bootstrap = admin_sub.add_parser("bootstrap", help="create the first operator from stdin")
    bootstrap.add_argument("--email", required=True)
    bootstrap.add_argument("--display-name", required=True)
    bootstrap.add_argument("--password-stdin", action="store_true")

    subparsers.add_parser("seed", help="ensure rerunnable synthetic development users and courses")

    release = subparsers.add_parser("release", help="register and activate immutable baseline releases")
    release_sub = release.add_subparsers(dest="release_command", required=True)
    register = release_sub.add_parser("register")
    register.add_argument("--file", required=True)
    activate = release_sub.add_parser("activate")
    activate.add_argument("release_id")

    operations = subparsers.add_parser("operations", help="non-academic worker operations")
    operation_sub = operations.add_subparsers(dest="operations_command", required=True)
    smoke = operation_sub.add_parser("smoke")
    smoke.add_argument("--idempotency-key", default=None)
    status = operation_sub.add_parser("status")
    status.add_argument("operation_id")

    parser_command = subparsers.add_parser("parser", help="source parser operations")
    parser_sub = parser_command.add_subparsers(dest="parser_command", required=True)
    benchmark = parser_sub.add_parser("benchmark", help="run actual isolated parser fixtures")
    benchmark.add_argument("--profile", required=True)
    benchmark.add_argument("--fixtures", required=True, type=Path)
    benchmark.add_argument("--output", required=True, type=Path)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "db" and args.db_command == "upgrade":
        run_migrations()
    elif args.command == "admin" and args.admin_command == "bootstrap":
        bootstrap_admin(args.email, args.display_name, args.password_stdin)
    elif args.command == "seed":
        seed_synthetic()
    elif args.command == "release" and args.release_command == "register":
        register_release(args.file)
    elif args.command == "release" and args.release_command == "activate":
        activate_release(args.release_id)
    elif args.command == "operations" and args.operations_command == "smoke":
        enqueue_foundation_smoke(args.idempotency_key or str(uuid4()))
    elif args.command == "operations" and args.operations_command == "status":
        get_operation(args.operation_id)
    elif args.command == "parser" and args.parser_command == "benchmark":
        from align_worker.benchmark import run_parser_benchmark

        run_parser_benchmark(args.profile, args.fixtures, args.output)
    else:
        raise SystemExit("unknown command")


if __name__ == "__main__":
    main()
