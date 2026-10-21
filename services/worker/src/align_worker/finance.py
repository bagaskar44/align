"""Transactional reservation and append-only usage accounting for paid calls.

This module deliberately has no provider client. A caller must commit ``reserve_call``
before starting I/O, then call ``settle_call`` or ``mark_unknown`` afterwards.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from align_api.models import (
    BudgetAccount,
    Job,
    MethodManifest,
    ReleaseManifest,
    UsageEntry,
    UsageReconciliation,
    UsageReservation,
)
from align_domain.finance import (
    BudgetState,
    FinanceGuardError,
    actual_call_cost,
    decimal_amount,
    enforce_job_limits,
    maximum_call_cost,
    verified_model_rates,
)
from align_domain.providers import PaidCallBlocked, ProviderContext

from .database import SessionFactory


@dataclass(frozen=True)
class CallBounds:
    input_tokens: int
    output_tokens: int = 0
    embedding_tokens: int = 0
    vision_calls: int = 0
    image_count: int = 0
    active_ms: int = 0

    def __post_init__(self) -> None:
        values = self.as_json()
        if any(type(value) is not int or value < 0 for value in values.values()) or self.active_ms == 0:
            raise FinanceGuardError("invalid_usage_bound")

    def as_json(self) -> dict[str, int]:
        return {
            "calls": 1,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "embedding_tokens": self.embedding_tokens,
            "vision_calls": self.vision_calls,
            "image_count": self.image_count,
            "active_ms": self.active_ms,
        }


@dataclass(frozen=True)
class ReservationPermit:
    call_id: UUID
    amount_reserved: Decimal
    rate_manifest_id: UUID
    model_id: str
    rates_snapshot: dict[str, Any]


@dataclass(frozen=True)
class WorkerPaidCallLedger:
    """Adapter hook that reserves before I/O and settles or holds uncertainty."""

    enabled: bool

    def reserve(self, context: ProviderContext, model_id: str, bounds: dict[str, int]) -> ReservationPermit:
        if (
            not self.enabled
            or not context.paid_calls_enabled
            or context.rate_manifest_id is None
            or context.paid_release_manifest_id is None
        ):
            raise PaidCallBlocked("PAID_CALL_GUARD_INCOMPLETE")
        try:
            with SessionFactory() as db:
                release_context = provider_finance_context(
                    db, release_id=context.paid_release_manifest_id, enabled=True, bounds=bounds
                )
            if (
                not release_context["paid_calls_enabled"]
                or release_context["rate_manifest_id"] != context.rate_manifest_id
            ):
                raise FinanceGuardError("paid_release_binding_invalid")
            call_bounds = CallBounds(
                input_tokens=bounds.get("input_tokens", 0),
                output_tokens=bounds.get("output_tokens", 0),
                embedding_tokens=bounds.get("embedding_tokens", 0),
                vision_calls=bounds.get("vision_calls", 0),
                image_count=bounds.get("image_count", 0),
                active_ms=bounds.get("active_ms", 0),
            )
            return reserve_call(
                job_id=context.job_id,
                call_id=context.call_id,
                logical_call_id=context.logical_call_id or context.call_id,
                attempt_no=context.attempt_no,
                rate_manifest_id=context.rate_manifest_id,
                model_id=model_id,
                bounds=call_bounds,
            )
        except FinanceGuardError as exc:
            raise PaidCallBlocked(str(exc)) from exc

    def settle(self, call_id: UUID, *, provider_request_id: str | None, usage: dict[str, int]) -> None:
        settle_call(call_id, provider_request_id=provider_request_id, usage=usage)

    def unknown(self, call_id: UUID, *, provider_request_id: str | None, reason: str) -> None:
        mark_unknown(call_id, provider_request_id=provider_request_id, reason=reason)


def _sum_decimal(values: Sequence[Decimal | None]) -> Decimal:
    return sum((item for item in values if item is not None), Decimal("0"))


def _manifest_rates(manifest: MethodManifest, model_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if manifest.kind != "rates" or manifest.verified_at is None:
        raise FinanceGuardError("rate_manifest_unverified")
    model_rates = verified_model_rates(manifest.content_json, model_id)
    snapshot = {
        "manifest_id": str(manifest.id),
        "manifest_sha256": manifest.sha256,
        "model_id": model_id,
        "currency": manifest.content_json["currency"],
        "rates": model_rates,
    }
    return model_rates, snapshot


def provider_finance_context(
    db: Session,
    *,
    release_id: UUID | None,
    enabled: bool,
    bounds: dict[str, int],
) -> dict[str, Any]:
    """Bind adapter permission and tariff to the immutable active release."""
    if release_id is None or not hasattr(db, "get"):
        return {
            "paid_release_manifest_id": None,
            "rate_manifest_id": None,
            "paid_calls_enabled": False,
            "billing_bounds": bounds,
        }
    release = db.get(ReleaseManifest, release_id) if release_id else None
    routing = db.get(MethodManifest, release.routing_policy_manifest_id) if release else None
    model = db.get(MethodManifest, release.model_manifest_id) if release else None
    rate_id = release.rate_manifest_id if release else None
    allowed = bool(
        enabled
        and release is not None
        and release.state == "active"
        and release.manifest_payload.get("provider_mode") == "live"
        and routing is not None
        and routing.content_json.get("allow_live") is True
        and model is not None
        and model.content_json.get("paid_calls") is True
        and rate_id is not None
    )
    return {
        "paid_release_manifest_id": release.id if release else None,
        "rate_manifest_id": rate_id,
        "paid_calls_enabled": allowed,
        "billing_bounds": bounds,
    }


def _attempt_usage(db: Session, reservations: Sequence[UsageReservation]) -> dict[UUID, UsageEntry]:
    call_ids = [row.call_id for row in reservations]
    if not call_ids:
        return {}
    return {
        row.call_id: row
        for row in db.scalars(select(UsageEntry).where(UsageEntry.call_id.in_(call_ids))).all()
    }


def _current_totals(
    db: Session, reservations: list[UsageReservation], entries: dict[UUID, UsageEntry]
) -> dict[str, int | Decimal]:
    totals: dict[str, int | Decimal] = {
        "calls": 0,
        "vision_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "embedding_tokens": 0,
        "active_ms": 0,
        "cost": Decimal("0"),
    }
    for reservation in reservations:
        entry = entries.get(reservation.call_id)
        # A provably unstarted call has no usage row and consumes no attempt
        # budget. A reconciled not-billed call still happened, so retain its
        # conservative token/time bounds for job limits.
        if reservation.status == "released" and entry is None:
            continue
        totals["calls"] = int(totals["calls"]) + 1
        if reservation.status in {"held", "reconciliation_required"} or entry is None or entry.status == "pending":
            usage: dict[str, Any] = reservation.bounds_json
        else:
            usage = entry.usage_json
        for key in ("vision_calls", "input_tokens", "output_tokens", "embedding_tokens", "active_ms"):
            amount = usage.get(key, 0)
            if type(amount) is int and amount >= 0:
                totals[key] = int(totals[key]) + amount
        if entry and entry.status == "known" and entry.actual_cost is not None:
            totals["cost"] = Decimal(totals["cost"]) + entry.actual_cost
        elif reservation.status in {"held", "reconciliation_required"}:
            totals["cost"] = Decimal(totals["cost"]) + reservation.amount_reserved
        elif entry and entry.status == "pending":
            reconciliation = db.scalar(
                select(UsageReconciliation).where(UsageReconciliation.usage_entry_id == entry.id)
            )
            if reconciliation:
                totals["cost"] = Decimal(totals["cost"]) + reconciliation.actual_cost
    return totals


def reserve_call(
    *,
    job_id: UUID,
    call_id: UUID,
    logical_call_id: UUID,
    attempt_no: int,
    rate_manifest_id: UUID,
    model_id: str,
    bounds: CallBounds,
) -> ReservationPermit:
    """Lock account/job, validate exact rates and limits, then commit the hold."""
    with SessionFactory.begin() as db:
        active_accounts = db.scalars(
            select(BudgetAccount).where(BudgetAccount.active.is_(True)).order_by(BudgetAccount.id).with_for_update()
        ).all()
        if not active_accounts:
            raise FinanceGuardError("budget_account_missing")
        if len(active_accounts) != 1:
            raise FinanceGuardError("budget_account_ambiguous")
        account = active_accounts[0]
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if job is None:
            raise FinanceGuardError("job_missing")
        if account.spend_frozen:
            raise FinanceGuardError("budget_spending_frozen")
        rate_manifest = db.get(MethodManifest, rate_manifest_id)
        if rate_manifest is None:
            raise FinanceGuardError("rate_manifest_missing")
        rates, snapshot = _manifest_rates(rate_manifest, model_id)
        reservation_cost = maximum_call_cost(
            rates=rates,
            input_tokens=bounds.input_tokens,
            output_tokens=bounds.output_tokens,
            embedding_tokens=bounds.embedding_tokens,
            image_count=bounds.image_count,
        )
        reservations = db.scalars(
            select(UsageReservation).where(UsageReservation.budget_account_id == account.id)
        ).all()
        entries = _attempt_usage(db, reservations)
        reconciliation_costs = db.scalars(select(UsageReconciliation.actual_cost)).all()
        known_cost = _sum_decimal(
            db.scalars(
                select(UsageEntry.actual_cost).where(
                    UsageEntry.status == "known", UsageEntry.actual_cost.is_not(None)
                )
            ).all()
        ) + _sum_decimal(reconciliation_costs)
        held = _sum_decimal([
            row.amount_reserved
            for row in reservations
            if row.status in {"held", "reconciliation_required"}
        ])
        state = BudgetState(
            confirmed_total=account.confirmed_total,
            reserve_floor=account.reserve_floor,
            known_spend=known_cost,
            held_reservations=held,
            planned_topup=account.planned_topup,
        )
        if reservation_cost > state.spendable:
            raise FinanceGuardError("global_budget_exhausted")
        job_reservations = [row for row in reservations if row.job_id == job_id]
        job_entries = {row.call_id: entries[row.call_id] for row in job_reservations if row.call_id in entries}
        current = _current_totals(db, job_reservations, job_entries)
        proposed = bounds.as_json()
        limits = job.job_limits_json
        enforce_job_limits(
            limits=limits,
            current=current,
            proposed=proposed,
            reservation=reservation_cost,
        )
        max_active_ms = limits.get("max_active_seconds", 0) * 1000
        if type(max_active_ms) is not int or max_active_ms < 0:
            raise FinanceGuardError("job_active_limit_invalid")
        if int(current.get("active_ms", 0)) + bounds.active_ms > max_active_ms:
            raise FinanceGuardError("job_limit_exceeded:max_active_seconds")
        if db.scalar(select(UsageReservation.id).where(UsageReservation.call_id == call_id)) is not None:
            raise FinanceGuardError("duplicate_physical_call_id")
        if db.scalar(
            select(UsageReservation.id).where(
                UsageReservation.logical_call_id == logical_call_id,
                UsageReservation.attempt_no == attempt_no,
            )
        ) is not None:
            raise FinanceGuardError("duplicate_logical_attempt")
        row = UsageReservation(
            budget_account_id=account.id,
            job_id=job_id,
            call_id=call_id,
            logical_call_id=logical_call_id,
            attempt_no=attempt_no,
            amount_reserved=reservation_cost,
            bounds_json={**proposed, "model_id": model_id, "rates_snapshot": snapshot},
            status="held",
        )
        db.add(row)
        db.flush()
        return ReservationPermit(call_id, reservation_cost, rate_manifest_id, model_id, snapshot)


def mark_unknown(call_id: UUID, *, provider_request_id: str | None, reason: str) -> None:
    """Keep the full reserve as liability when response billing cannot be proven."""
    with SessionFactory.begin() as db:
        reservation = db.scalar(
            select(UsageReservation).where(UsageReservation.call_id == call_id).with_for_update()
        )
        if reservation is None:
            raise FinanceGuardError("reservation_missing")
        if db.scalar(select(UsageEntry.id).where(UsageEntry.call_id == call_id)) is not None:
            return
        reservation.status = "reconciliation_required"
        bounds = reservation.bounds_json
        rate_snapshot = bounds.get("rates_snapshot", {})
        rate_manifest_id = UUID(str(rate_snapshot.get("manifest_id")))
        db.add(
            UsageEntry(
                call_id=call_id,
                logical_call_id=reservation.logical_call_id,
                attempt_no=reservation.attempt_no,
                provider_request_id=provider_request_id,
                manifest_id=rate_manifest_id,
                usage_json={
                    "billing_status": "unknown",
                    "reason_code": reason[:120],
                    "upper_bounds": {key: value for key, value in bounds.items() if key != "rates_snapshot"},
                },
                rates_snapshot_json=rate_snapshot,
                actual_cost=None,
                status="pending",
            )
        )


def reconcile_stale_reservations(*, minimum_age_seconds: int, limit: int = 100) -> int:
    """Fence orphaned held calls into pending liability after a worker crash/lease loss."""
    if minimum_age_seconds < 1 or not 1 <= limit <= 1000:
        raise FinanceGuardError("stale_reservation_reaper_limits_invalid")
    cutoff = datetime.now(UTC) - timedelta(seconds=minimum_age_seconds)
    recovered = 0
    with SessionFactory.begin() as db:
        reservations = db.scalars(
            select(UsageReservation)
            .where(UsageReservation.status == "held", UsageReservation.created_at < cutoff)
            .order_by(UsageReservation.created_at, UsageReservation.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
        ).all()
        for reservation in reservations:
            if db.scalar(select(UsageEntry.id).where(UsageEntry.call_id == reservation.call_id)) is not None:
                continue
            reservation.status = "reconciliation_required"
            bounds = reservation.bounds_json
            rate_snapshot = bounds.get("rates_snapshot", {})
            try:
                rate_manifest_id = UUID(str(rate_snapshot.get("manifest_id")))
            except (ValueError, TypeError) as exc:
                raise FinanceGuardError("orphaned_reservation_rate_snapshot_invalid") from exc
            db.add(
                UsageEntry(
                    call_id=reservation.call_id,
                    logical_call_id=reservation.logical_call_id,
                    attempt_no=reservation.attempt_no,
                    provider_request_id=None,
                    manifest_id=rate_manifest_id,
                    usage_json={
                        "billing_status": "unknown",
                        "reason_code": "worker_crash_or_lease_expired",
                        "upper_bounds": {key: value for key, value in bounds.items() if key != "rates_snapshot"},
                    },
                    rates_snapshot_json=rate_snapshot,
                    actual_cost=None,
                    status="pending",
                )
            )
            recovered += 1
    return recovered


def settle_call(
    call_id: UUID,
    *,
    provider_request_id: str | None,
    usage: dict[str, Any] | None,
) -> None:
    """Append actual usage; unknown IDs/usage remain liabilities, never free calls."""
    if not provider_request_id or not isinstance(usage, dict):
        mark_unknown(call_id, provider_request_id=provider_request_id, reason="provider_usage_or_request_id_missing")
        return
    with SessionFactory.begin() as db:
        reservation = db.scalar(
            select(UsageReservation).where(UsageReservation.call_id == call_id).with_for_update()
        )
        if reservation is None:
            raise FinanceGuardError("reservation_missing")
        if db.scalar(select(UsageEntry.id).where(UsageEntry.call_id == call_id)) is not None:
            raise FinanceGuardError("usage_entry_already_recorded")
        bounds = reservation.bounds_json
        rate_snapshot = bounds.get("rates_snapshot", {})
        rates = rate_snapshot.get("rates")
        if not isinstance(rates, dict):
            raise FinanceGuardError("reservation_rate_snapshot_invalid")
        normalized = {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens", 0),
            "embedding_tokens": usage.get("embedding_tokens", 0),
            "image_count": usage.get("image_count", 0),
            "vision_calls": usage.get("vision_calls", bounds.get("vision_calls", 0)),
            "active_ms": usage.get("active_ms", bounds.get("active_ms", 0)),
        }
        try:
            actual_cost = actual_call_cost(rates=rates, usage=normalized)
        except FinanceGuardError:
            reservation.status = "reconciliation_required"
            db.add(
                UsageEntry(
                    call_id=call_id,
                    logical_call_id=reservation.logical_call_id,
                    attempt_no=reservation.attempt_no,
                    provider_request_id=provider_request_id[:240],
                    manifest_id=UUID(str(rate_snapshot["manifest_id"])),
                    usage_json={"billing_status": "unknown", "reason_code": "provider_usage_invalid"},
                    rates_snapshot_json=rate_snapshot,
                    actual_cost=None,
                    status="pending",
                )
            )
            return
        actual_cost = decimal_amount(actual_cost)
        over_bound = (
            actual_cost > reservation.amount_reserved
            or any(
                type(normalized[key]) is not int or normalized[key] > int(bounds.get(key, 0))
                for key in ("input_tokens", "output_tokens", "embedding_tokens", "image_count", "vision_calls", "active_ms")
            )
        )
        db.add(
            UsageEntry(
                call_id=call_id,
                logical_call_id=reservation.logical_call_id,
                attempt_no=reservation.attempt_no,
                provider_request_id=provider_request_id[:240],
                manifest_id=UUID(str(rate_snapshot["manifest_id"])),
                usage_json=normalized,
                rates_snapshot_json=rate_snapshot,
                actual_cost=actual_cost,
                status="known",
            )
        )
        reservation.status = "settled"
        if over_bound:
            account = db.scalar(
                select(BudgetAccount).where(BudgetAccount.id == reservation.budget_account_id).with_for_update()
            )
            if account is not None:
                account.spend_frozen = True
                account.freeze_reason = "provider_usage_exceeded_reserved_bound"


def release_unstarted_call(call_id: UUID, *, proof: str) -> None:
    """Release only when caller can prove that provider I/O never began."""
    if not proof.strip():
        raise FinanceGuardError("release_requires_no_network_proof")
    with SessionFactory.begin() as db:
        reservation = db.scalar(
            select(UsageReservation).where(UsageReservation.call_id == call_id).with_for_update()
        )
        if reservation is None or reservation.status != "held":
            raise FinanceGuardError("reservation_not_releasable")
        if db.scalar(select(UsageEntry.id).where(UsageEntry.call_id == call_id)) is not None:
            raise FinanceGuardError("usage_already_recorded")
        reservation.status = "released"

