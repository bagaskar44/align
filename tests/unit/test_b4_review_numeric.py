from __future__ import annotations

from decimal import Decimal

import pytest

from align_domain.review_numeric import (
    NumericPolicyError,
    compare_numeric_policy,
    parse_decimal_literal,
)


def policy(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "number_locale": "id-ID",
        "comparison_mode": "pairwise",
        "canonical_unit": "kg",
        "allowed_conversions": [{"kind": "linear", "from": "g", "to": "kg", "factor": "0.001"}],
        "required_context_fields": ["metric", "period", "unit"],
        "absolute_tolerance": "0.01",
        "relative_tolerance": "0.02",
        "rounding": {"mode": "none"},
        "metric_definition": "massa bersih yang dilaporkan",
    }
    value.update(changes)
    return value


def operand(role: str, literal: str, unit: str = "kg", **changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "evidence_id": role,
        "role": role,
        "literal": literal,
        "context": {"metric": "massa", "period": "2026", "unit": unit},
        "source_quality": "native_literal",
    }
    value.update(changes)
    return value


def test_locale_parser_respects_explicit_group_and_decimal_separators() -> None:
    assert parse_decimal_literal("1.234,50", "id-ID") == Decimal("1234.50")
    assert parse_decimal_literal("1,234.50", "en-US") == Decimal("1234.50")
    with pytest.raises(NumericPolicyError, match="ambiguous_number_format"):
        parse_decimal_literal("1,23,4", "en-US")
    with pytest.raises(NumericPolicyError, match="unsupported_number_locale"):
        parse_decimal_literal("1.2", "fr-FR")


def test_pairwise_normalizes_unit_and_uses_maximum_of_absolute_and_relative_tolerance() -> None:
    result = compare_numeric_policy(policy(), [operand("left", "10"), operand("right", "10,15", "kg")])
    assert result["comparability_status"] == "comparable"
    assert result["difference_text"] == "0.15"
    assert result["allowed_difference_text"] == "0.2030"
    assert result["inconsistent"] is False

    converted = compare_numeric_policy(policy(), [operand("left", "10000", "g"), operand("right", "10,3")])
    assert converted["difference_text"] == "0.300"
    assert converted["allowed_difference_text"] == "0.206"
    assert converted["inconsistent"] is True


def test_unverified_sources_and_mismatched_context_cannot_become_findings() -> None:
    with pytest.raises(NumericPolicyError, match="unverified_numeric_source"):
        compare_numeric_policy(policy(), [
            operand("left", "1", source_quality="unverified"), operand("right", "2")
        ])
    mismatched = operand("right", "2")
    mismatched["context"] = {"metric": "massa", "period": "2025", "unit": "kg"}
    with pytest.raises(NumericPolicyError, match="numeric_context_not_comparable"):
        compare_numeric_policy(policy(), [operand("left", "1"), mismatched])
    with pytest.raises(NumericPolicyError, match="numeric_operand_roles_invalid"):
        compare_numeric_policy(policy(), [operand("left", "1"), operand("left", "2")])


def test_decimal_precision_and_rounding_are_deterministic() -> None:
    result = compare_numeric_policy(
        policy(rounding={"mode": "half_even", "quantum": "0.1"}),
        [operand("left", "1,25"), operand("right", "1,35")],
    )
    assert result["result_decimal_text"] == "1.2"
    assert result["operands_snapshot"][-1]["rounding_after"] == ["1.2", "1.4"]
