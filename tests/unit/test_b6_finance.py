from __future__ import annotations

from decimal import Decimal

import pytest

from align_domain.finance import (
    BudgetState,
    FinanceGuardError,
    enforce_job_limits,
    maximum_call_cost,
    reserve_ceiling,
    verified_model_rates,
)


def test_spendable_respects_floor_planned_topup_and_deduplicates_pending_liability() -> None:
    state = BudgetState(
        confirmed_total=Decimal("25"),
        reserve_floor=Decimal("5"),
        known_spend=Decimal("3"),
        held_reservations=Decimal("4"),
        pending_liability=Decimal("4"),
        planned_topup=Decimal("100"),
    )
    assert state.spendable == Decimal("13")


def test_reservation_upper_bound_uses_verified_rates_and_ceiling_rounding() -> None:
    manifest = {
        "schema_version": 1,
        "status": "verified",
        "currency": "USD",
        "models": {"m1": {
            "input_per_million": "1.00",
            "output_per_million": "5.00",
            "embedding_per_million": "0.10",
            "image_per_unit": "0.02",
        }},
    }
    rates = verified_model_rates(manifest, "m1")
    amount = maximum_call_cost(rates=rates, input_tokens=1000, output_tokens=100, image_count=1)
    assert amount == Decimal("0.02150000")
    assert reserve_ceiling(Decimal("0.000000001")) == Decimal("0.00000001")
    with pytest.raises(FinanceGuardError, match="rate_manifest_unverified"):
        verified_model_rates({**manifest, "status": "unverified"}, "m1")


def test_job_limits_reject_parallel_overcommit_and_unknown_cost_bounds() -> None:
    limits = {
        "max_calls": 2,
        "max_vision_calls": 0,
        "max_text_input_tokens": 1000,
        "max_text_output_tokens": 200,
        "max_embedding_tokens": 500,
        "budget_usd": "0.03",
    }
    current = {"calls": 1, "input_tokens": 500, "cost": Decimal("0.02")}
    with pytest.raises(FinanceGuardError, match="job_budget_exhausted"):
        enforce_job_limits(
            limits=limits,
            current=current,
            proposed={"calls": 1, "input_tokens": 500},
            reservation=Decimal("0.02"),
        )
    with pytest.raises(FinanceGuardError, match="job_limit_exceeded:max_calls"):
        enforce_job_limits(
            limits=limits,
            current={**current, "cost": Decimal("0")},
            proposed={"calls": 2},
            reservation=Decimal("0"),
        )

    for proposed, limit in (
        ({"vision_calls": 1}, "max_vision_calls"),
        ({"input_tokens": 501}, "max_text_input_tokens"),
        ({"output_tokens": 201}, "max_text_output_tokens"),
        ({"embedding_tokens": 501}, "max_embedding_tokens"),
    ):
        with pytest.raises(FinanceGuardError, match=f"job_limit_exceeded:{limit}"):
            enforce_job_limits(
                limits=limits,
                current={"calls": 0, "vision_calls": 0, "input_tokens": 500,
                         "output_tokens": 0, "embedding_tokens": 0, "cost": Decimal("0")},
                proposed=proposed,
                reservation=Decimal("0"),
            )


def test_unknown_image_rate_and_unknown_token_rate_never_fall_back_to_zero() -> None:
    with pytest.raises(FinanceGuardError, match="image_rate_missing"):
        maximum_call_cost(
            rates={"input_per_million": "1", "output_per_million": "1", "embedding_per_million": "1"},
            input_tokens=1,
            image_count=1,
        )
    with pytest.raises(FinanceGuardError, match="input_rate_missing"):
        maximum_call_cost(
            rates={"output_per_million": "1", "embedding_per_million": "1"},
            input_tokens=1,
        )
