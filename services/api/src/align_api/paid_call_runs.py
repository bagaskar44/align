"""Operator lifecycle for atomic, release-bound paid-call spend runs."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from .config import get_settings
from .db import SessionFactory
from .finance_lock import lock_paid_finance_scope
from .models import (
    AuditEvent,
    BudgetAccount,
    MethodManifest,
    PaidCallRun,
    ReleaseManifest,
    UsageEntry,
    UsageReconciliation,
    UsageReservation,
    User,
)
from .paid_calls import live_model_binding_matches, runtime_live_model_ids
from align_domain.finance import BudgetState


def _operator_id() -> UUID:
    with SessionFactory() as db:
        actor = db.scalar(
            select(User)
            .where(User.global_role == "admin", User.active.is_(True))
            .order_by(User.created_at)
        )
        if actor is None:
            raise SystemExit("an active admin operator account is required")
        return actor.id


def _operator_factory() -> tuple[Any, Any]:
    url = get_settings().operator_database_url
    if not url:
        raise SystemExit("OPERATOR_DATABASE_URL is required for paid-call run changes")
    engine = create_engine(url, pool_pre_ping=True)
    return engine, sessionmaker(bind=engine, class_=DbSession, autoflush=False, expire_on_commit=False)


def validate_run_cap(value: str) -> Decimal:
    try:
        cap = Decimal(value)
    except InvalidOperation as exc:
        raise SystemExit("run cap must be a positive finite USD amount") from exc
    try:
        exact_scale = cap.quantize(Decimal("0.00000001")) == cap
    except InvalidOperation:
        exact_scale = False
    if not cap.is_finite() or cap <= 0 or not exact_scale or len(value) > 32:
        raise SystemExit("run cap must be positive and use at most eight decimal places")
    return cap


def validate_local_live_runtime() -> dict[str, str]:
    settings = get_settings()
    models = runtime_live_model_ids(settings)
    if (
        settings.app_env != "development"
        or not settings.app_demo_mode
        or settings.data_mode != "synthetic"
        or settings.provider_mode != "live"
        or not settings.paid_calls_enabled
        or settings.openai_api_key is None
        or not settings.openai_api_key.get_secret_value().strip()
        or models is None
        or settings.embedding_dimensions != 1536
    ):
        raise SystemExit(
            "paid-call run requires development/demo/synthetic, live provider, explicit paid-call enablement, a configured key, exact role model IDs, and 1536 embedding dimensions"
        )
    return models


def _spendable(db: DbSession, account: BudgetAccount) -> Decimal:
    reservations = db.scalars(
        select(UsageReservation).where(UsageReservation.budget_account_id == account.id)
    ).all()
    call_ids = [item.call_id for item in reservations]
    entries = (
        db.scalars(select(UsageEntry).where(UsageEntry.call_id.in_(call_ids))).all()
        if call_ids else []
    )
    known = sum(
        (entry.actual_cost or Decimal("0") for entry in entries if entry.status == "known"),
        Decimal("0"),
    )
    entry_ids = [entry.id for entry in entries]
    reconciled = (
        db.scalars(
            select(UsageReconciliation.actual_cost).where(
                UsageReconciliation.usage_entry_id.in_(entry_ids)
            )
        ).all()
        if entry_ids else []
    )
    known += sum(reconciled, Decimal("0"))
    held = sum(
        (
            item.amount_reserved
            for item in reservations
            if item.status in {"held", "reconciliation_required"}
        ),
        Decimal("0"),
    )
    return BudgetState(
        confirmed_total=account.confirmed_total,
        reserve_floor=account.reserve_floor,
        known_spend=known,
        held_reservations=held,
        planned_topup=account.planned_topup,
    ).spendable


def open_paid_call_run(*, release_id: str, max_spend_usd: str, authorization_ref: str) -> dict[str, str]:
    """Open the only active cap for the active, verified live/synthetic release."""
    settings = get_settings()
    runtime_models = validate_local_live_runtime()
    cap = validate_run_cap(max_spend_usd)
    authorization = authorization_ref.strip()
    if not authorization or len(authorization) > 240:
        raise SystemExit("an explicit spend authorization reference is required")
    try:
        parsed_release_id = UUID(release_id)
    except ValueError as exc:
        raise SystemExit("release ID is invalid") from exc
    actor_id = _operator_id()
    engine, factory = _operator_factory()
    try:
        with factory.begin() as db:
            lock_paid_finance_scope(db)
            accounts = db.scalars(
                select(BudgetAccount).where(BudgetAccount.active.is_(True)).with_for_update()
            ).all()
            if len(accounts) != 1:
                raise SystemExit("exactly one active budget account is required")
            account = accounts[0]
            if account.spend_frozen:
                raise SystemExit("budget spending is frozen; resolve it before opening a run")
            if db.scalar(
                select(PaidCallRun.id).where(
                    PaidCallRun.budget_account_id == account.id,
                    PaidCallRun.state == "active",
                ).limit(1)
            ) is not None:
                raise SystemExit("a paid-call run is already active; close it before opening another")
            if db.scalar(
                select(UsageReservation.id).where(
                    UsageReservation.budget_account_id == account.id,
                    UsageReservation.status == "reconciliation_required",
                ).limit(1)
            ) is not None:
                raise SystemExit("pending billing liability must be reconciled before opening a run")
            release = db.scalar(
                select(ReleaseManifest).where(ReleaseManifest.state == "active")
            )
            if release is None or release.id != parsed_release_id:
                raise SystemExit("the requested release is not the active release")
            if (
                release.manifest_payload.get("provider_mode") != "live"
                or release.manifest_payload.get("data_mode") != "synthetic"
            ):
                raise SystemExit("only a live/synthetic release can open a local paid-call run")
            model = db.get(MethodManifest, release.model_manifest_id)
            rates = db.get(MethodManifest, release.rate_manifest_id) if release.rate_manifest_id else None
            routing = db.get(MethodManifest, release.routing_policy_manifest_id)
            batching = db.get(MethodManifest, release.batching_policy_manifest_id)
            rate_models = rates.content_json.get("models") if rates else None
            if (
                model is None
                or model.content_json.get("provider_mode") != "live"
                or model.content_json.get("paid_calls") is not True
                or model.content_json.get("capability_smoke") is not True
                or model.content_json.get("embedding_dimensions") != 1536
                or not live_model_binding_matches(model.content_json, settings)
                or rates is None
                or rates.kind != "rates"
                or rates.verified_at is None
                or rates.content_json.get("status") != "verified"
                or not isinstance(rate_models, dict)
                or not set(runtime_models.values()) <= set(rate_models)
                or routing is None
                or routing.content_json.get("allow_live") is not True
                or routing.content_json.get("semantic_batch_size") != 1
                or batching is None
                or batching.content_json.get("maximum_elements") != 1
            ):
                raise SystemExit("active release is not a verified batch-1 live capability-smoke release")
            spendable = _spendable(db, account)
            if cap > spendable:
                raise SystemExit(
                    f"run cap exceeds current spendable budget after reserve and liabilities ({spendable} USD)"
                )
            run = PaidCallRun(
                budget_account_id=account.id,
                release_manifest_id=release.id,
                cap_usd=cap,
                authorization_ref=authorization,
                created_by=actor_id,
                state="active",
            )
            db.add(run)
            db.flush()
            db.add(AuditEvent(
                actor_user_id=actor_id,
                action="budget.paid_call_run.opened",
                object_type="paid_call_run",
                object_id=run.id,
                new_version_refs_json={
                    "cap_usd": str(cap),
                    "release_manifest_id": str(release.id),
                    "authorization_ref": authorization,
                },
                request_id=uuid4(),
            ))
            return {
                "paid_call_run_id": str(run.id),
                "release_id": str(release.id),
                "state": run.state,
                "cap_usd": str(cap),
                "available_before_run_usd": str(spendable),
            }
    finally:
        engine.dispose()


def close_paid_call_run(*, run_id: str, reason: str) -> dict[str, str]:
    try:
        parsed_run_id = UUID(run_id)
    except ValueError as exc:
        raise SystemExit("paid-call run ID is invalid") from exc
    close_reason = reason.strip()
    if not close_reason or len(close_reason) > 1000:
        raise SystemExit("closing a paid-call run requires a short reason")
    actor_id = _operator_id()
    engine, factory = _operator_factory()
    try:
        with factory.begin() as db:
            lock_paid_finance_scope(db)
            account_id = db.scalar(
                select(PaidCallRun.budget_account_id).where(PaidCallRun.id == parsed_run_id)
            )
            if account_id is None:
                raise SystemExit("paid-call run not found")
            db.scalar(
                select(BudgetAccount).where(BudgetAccount.id == account_id).with_for_update()
            )
            run = db.scalar(
                select(PaidCallRun).where(PaidCallRun.id == parsed_run_id).with_for_update()
            )
            if run is None:
                raise SystemExit("paid-call run not found")
            if run.state == "closed":
                return {"paid_call_run_id": str(run.id), "state": "closed", "already_closed": "true"}
            if run.state != "active":
                raise SystemExit("paid-call run has an unsupported state")
            run.state = "closed"
            run.closed_at = datetime.now(UTC)
            run.close_reason = close_reason
            db.add(AuditEvent(
                actor_user_id=actor_id,
                action="budget.paid_call_run.closed",
                object_type="paid_call_run",
                object_id=run.id,
                new_version_refs_json={"reason": close_reason},
                request_id=uuid4(),
            ))
            return {"paid_call_run_id": str(run.id), "state": "closed"}
    finally:
        engine.dispose()


def close_active_paid_call_run(*, release_version_label: str, reason: str) -> dict[str, str]:
    """Close the sole active run in an isolated project, including crash recovery."""
    close_reason = reason.strip()
    if not close_reason or len(close_reason) > 1000:
        raise SystemExit("closing a paid-call run requires a short reason")
    expected_release_label = release_version_label.strip()
    if not expected_release_label or len(expected_release_label) > 120:
        raise SystemExit("expected live release label is invalid")
    engine, factory = _operator_factory()
    try:
        with factory.begin() as db:
            lock_paid_finance_scope(db)
            active_runs = db.scalars(
                select(PaidCallRun)
                .where(PaidCallRun.state == "active")
                .order_by(PaidCallRun.id)
            ).all()
            if not active_runs:
                return {"state": "none"}
            if len(active_runs) != 1:
                raise SystemExit("multiple active paid-call runs found; inspect the isolated finance state")
            active_release = db.get(ReleaseManifest, active_runs[0].release_manifest_id)
            if active_release is None or active_release.version_label != expected_release_label:
                raise SystemExit("active paid-call run belongs to a different local release; stack was not stopped")
            actor_id = _operator_id()
            run_id = active_runs[0].id
            account_id = active_runs[0].budget_account_id
            db.scalar(
                select(BudgetAccount).where(BudgetAccount.id == account_id).with_for_update()
            )
            run = db.scalar(
                select(PaidCallRun).where(PaidCallRun.id == run_id).with_for_update()
            )
            if run is None or run.state != "active":
                raise SystemExit("active paid-call run changed unexpectedly")
            run.state = "closed"
            run.closed_at = datetime.now(UTC)
            run.close_reason = close_reason
            db.add(AuditEvent(
                actor_user_id=actor_id,
                action="budget.paid_call_run.closed",
                object_type="paid_call_run",
                object_id=run.id,
                new_version_refs_json={"reason": close_reason},
                request_id=uuid4(),
            ))
            return {"paid_call_run_id": str(run.id), "state": "closed"}
    finally:
        engine.dispose()
