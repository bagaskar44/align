"""Exact financial calculations for guarded provider-call reservations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal, InvalidOperation, localcontext
from typing import Any


class FinanceGuardError(ValueError):
    """A call cannot be safely priced or reserved under current policy."""


_ZERO = Decimal("0")
_MONEY_QUANTUM = Decimal("0.00000001")
_MILLION = Decimal("1000000")


def decimal_amount(value: Any, reason: str = "invalid_financial_amount") -> Decimal:
    if not isinstance(value, (str, int, Decimal)) or isinstance(value, bool):
        raise FinanceGuardError(reason)
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise FinanceGuardError(reason) from exc
    if not amount.is_finite() or amount < 0:
        raise FinanceGuardError(reason)
    return amount


def reserve_ceiling(amount: Decimal) -> Decimal:
    if not amount.is_finite() or amount < 0:
        raise FinanceGuardError("invalid_financial_amount")
    return amount.quantize(_MONEY_QUANTUM, rounding=ROUND_CEILING)


@dataclass(frozen=True)
class BudgetState:
    confirmed_total: Decimal
    reserve_floor: Decimal
    known_spend: Decimal
    held_reservations: Decimal
    pending_liability: Decimal = _ZERO
    planned_topup: Decimal = _ZERO

    @property
    def spendable(self) -> Decimal:
        if min(
            self.confirmed_total,
            self.reserve_floor,
            self.known_spend,
            self.held_reservations,
            self.pending_liability,
            self.planned_topup,
        ) < 0:
            raise FinanceGuardError("negative_budget_component")
        # A pending UsageEntry is represented by its reconciliation_required reservation.
        # When both are supplied, count the larger bound once, never both.
        liability = max(self.held_reservations, self.pending_liability)
        return max(
            _ZERO,
            self.confirmed_total - self.reserve_floor - self.known_spend - liability,
        )


def verified_model_rates(manifest: dict[str, Any], model_id: str) -> dict[str, Any]:
    if manifest.get("schema_version") != 1 or manifest.get("status") != "verified":
        raise FinanceGuardError("rate_manifest_unverified")
    if manifest.get("currency") != "USD":
        raise FinanceGuardError("rate_currency_unsupported")
    models = manifest.get("models")
    rates = models.get(model_id) if isinstance(models, dict) else None
    if not isinstance(rates, dict):
        raise FinanceGuardError("model_rate_missing")
    for key in ("input_per_million", "output_per_million", "embedding_per_million"):
        value = rates.get(key)
        if value is not None and decimal_amount(value) < 0:
            raise FinanceGuardError("invalid_rate")
    return rates


def maximum_call_cost(
    *,
    rates: dict[str, Any],
    input_tokens: int,
    output_tokens: int = 0,
    embedding_tokens: int = 0,
    image_count: int = 0,
) -> Decimal:
    """Return a conservative USD upper bound; unknown billable dimensions block calls."""
    if min(input_tokens, output_tokens, embedding_tokens, image_count) < 0:
        raise FinanceGuardError("invalid_usage_bound")
    if not all(
        isinstance(value, int) and not isinstance(value, bool)
        for value in (input_tokens, output_tokens, embedding_tokens, image_count)
    ):
        raise FinanceGuardError("invalid_usage_bound")
    if image_count and rates.get("image_per_unit") is None:
        raise FinanceGuardError("image_rate_missing")
    input_rate = decimal_amount(rates.get("input_per_million"), "input_rate_missing") if input_tokens else _ZERO
    output_rate = decimal_amount(rates.get("output_per_million"), "output_rate_missing") if output_tokens else _ZERO
    embedding_rate = decimal_amount(rates.get("embedding_per_million"), "embedding_rate_missing") if embedding_tokens else _ZERO
    image_rate = decimal_amount(rates.get("image_per_unit"), "image_rate_missing") if image_count else _ZERO
    with localcontext() as context:
        context.prec = 50
        raw = (
            Decimal(input_tokens) * input_rate
            + Decimal(output_tokens) * output_rate
            + Decimal(embedding_tokens) * embedding_rate
        ) / _MILLION + Decimal(image_count) * image_rate
        return reserve_ceiling(raw)


def actual_call_cost(*, rates: dict[str, Any], usage: dict[str, Any]) -> Decimal:
    dimensions = {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens", 0),
        "embedding_tokens": usage.get("embedding_tokens", 0),
        "image_count": usage.get("image_count", 0),
    }
    if any(type(value) is not int for value in dimensions.values()):
        raise FinanceGuardError("provider_usage_incomplete")
    cost = maximum_call_cost(rates=rates, **dimensions)
    return cost


def enforce_job_limits(
    *,
    limits: dict[str, Any],
    current: Mapping[str, int | Decimal],
    proposed: Mapping[str, int | Decimal],
    reservation: Decimal,
) -> None:
    """Check aggregate attempt totals before reserving another physical call."""
    budget = decimal_amount(limits.get("budget_usd", "0"), "job_budget_invalid")
    if Decimal(current.get("cost", _ZERO)) + reservation > budget:
        raise FinanceGuardError("job_budget_exhausted")
    dimensions = (
        ("calls", "max_calls", 1),
        ("vision_calls", "max_vision_calls", 0),
        ("input_tokens", "max_text_input_tokens", 0),
        ("output_tokens", "max_text_output_tokens", 0),
        ("embedding_tokens", "max_embedding_tokens", 0),
    )
    for used_key, limit_key, default in dimensions:
        amount = int(proposed.get(used_key, 0))
        maximum = limits.get(limit_key, default)
        used = int(current.get(used_key, 0))
        if type(maximum) is not int or maximum < 0 or amount < 0 or used + amount > maximum:
            raise FinanceGuardError(f"job_limit_exceeded:{limit_key}")

