from __future__ import annotations

from decimal import Decimal

import pytest

from align_domain.review_numeric import (
    NumericPolicyError,
    compare_numeric_policy,
    parse_decimal_literal,
    parse_numeric_operand,
)


def policy(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "number_locale": "id-ID",
        "comparison_mode": "pairwise",
        "canonical_unit": "kg",
        "allowed_conversions": [{"kind": "linear", "from": "g", "to": "kg", "factor": "0.001"}],
        "required_context_fields": ["metric", "entity", "period", "definition", "unit"],
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
        "context": {"metric": "massa", "entity": "produk-A", "period": "2026",
                     "definition": "massa bersih yang dilaporkan", "unit": unit},
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
    mismatched["context"] = {"metric": "massa", "entity": "produk-A", "period": "2025",
                              "definition": "massa bersih yang dilaporkan", "unit": "kg"}
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


def test_policy_requires_full_comparability_and_rejects_unapproved_units_and_range() -> None:
    missing_entity = operand("right", "2")
    missing_entity["context"] = {"metric": "massa", "period": "2026", "definition": "massa bersih", "unit": "kg"}
    with pytest.raises(NumericPolicyError, match="ambiguous_numeric_context"):
        compare_numeric_policy(policy(), [operand("left", "1"), missing_entity])
    with pytest.raises(NumericPolicyError, match="numeric_units_not_comparable"):
        compare_numeric_policy(policy(allowed_conversions=[]), [
            operand("left", "1000", "IDR"), operand("right", "1", "kg")
        ])
    with pytest.raises(NumericPolicyError, match="unapproved_conversion_factor"):
        compare_numeric_policy(policy(allowed_conversions=[
            {"kind": "linear", "from": "g", "to": "kg", "factor": "0.01"}
        ]), [operand("left", "1000", "g"), operand("right", "1", "kg")])
    with pytest.raises(NumericPolicyError, match="numeric_value_out_of_range"):
        parse_decimal_literal("0," + "0" * 40 + "1", "id-ID")


def test_tolerance_equality_is_consistent_and_zero_pair_is_valid() -> None:
    result = compare_numeric_policy(
        policy(absolute_tolerance="1", relative_tolerance="0"),
        [operand("left", "0"), operand("right", "1")],
    )
    assert result["difference_text"] == "1"
    assert result["allowed_difference_text"] == "1"
    assert result["inconsistent"] is False


def test_explicit_percent_and_currency_scaling_suffixes_compare_with_decimal_units() -> None:
    percent_policy = policy(
        canonical_unit="ratio",
        allowed_conversions=[{"kind": "linear", "from": "percent", "to": "ratio", "factor": "0.01"}],
        required_context_fields=["metric", "entity", "definition", "unit"],
    )
    left = operand("left", "0,25", "ratio")
    right = operand("right", "25%", "ratio")
    right["context"] = {**right["context"], "unit": "ratio"}
    percent_result = compare_numeric_policy(percent_policy, [left, right])
    assert percent_result["result_decimal_text"] == "0.25"
    assert percent_result["difference_text"] == "0.00"
    assert percent_result["inconsistent"] is False

    money_policy = policy(
        number_locale="id-ID",
        canonical_unit="IDR",
        allowed_conversions=[{"kind": "linear", "from": "IDR_million", "to": "IDR", "factor": "1000000"}],
        required_context_fields=["metric", "entity", "definition", "unit"],
    )
    left = operand("left", "Rp125 juta", "IDR_million")
    right = operand("right", "125.000.000", "IDR")
    money_result = compare_numeric_policy(money_policy, [left, right])
    assert money_result["result_decimal_text"] == "125000000"
    assert money_result["difference_text"] == "0"
    assert money_result["inconsistent"] is False


def test_percentage_points_are_distinct_from_percent_and_ratio() -> None:
    value, unit = parse_numeric_operand("2 pp", "en-US", "percentage_point")
    assert value == Decimal("2") and unit == "percentage_point"
    with pytest.raises(NumericPolicyError, match="numeric_context_unit_mismatch"):
        parse_numeric_operand("2 pp", "en-US", "percent")
    with pytest.raises(NumericPolicyError, match="numeric_units_not_comparable"):
        compare_numeric_policy(
            policy(
                canonical_unit="ratio",
                allowed_conversions=[{"kind": "linear", "from": "percentage_point", "to": "ratio", "factor": "0.01"}],
            ),
            [
                operand("left", "2 pp", "percentage_point"),
                operand("right", "0,02", "ratio"),
            ],
        )


def test_sum_rejects_duplicate_source_operands_and_ratio_zero_is_unassessable() -> None:
    sum_policy = policy(comparison_mode="sum_against_reported", canonical_unit="kg")
    summands = [
        operand("operand", "2", evidence_id="cell-A"),
        operand("operand", "3", evidence_id="cell-B"),
        operand("reported", "5", evidence_id="cell-total"),
    ]
    result = compare_numeric_policy(sum_policy, summands)
    assert result["result_decimal_text"] == "5"
    assert result["inconsistent"] is False
    duplicate = [*summands[:1], {**summands[1], "evidence_id": "cell-A"}, summands[2]]
    with pytest.raises(NumericPolicyError, match="duplicate_numeric_sum_operand"):
        compare_numeric_policy(sum_policy, duplicate)

    ratio_policy = policy(
        comparison_mode="ratio_against_reported",
        canonical_unit="ratio",
        allowed_conversions=[{"kind": "linear", "from": "percent", "to": "ratio", "factor": "0.01"}],
    )
    ratio_operands = [
        operand("numerator", "1", "unit"),
        operand("denominator", "4", "unit"),
        operand("reported", "25%", "ratio"),
    ]
    ratio = compare_numeric_policy(ratio_policy, ratio_operands)
    assert ratio["result_decimal_text"] == "0.25"
    assert ratio["difference_text"] == "0.00"
    assert ratio["inconsistent"] is False

    zero_denominator = [
        operand("numerator", "10", "count_thousand"),
        operand("denominator", "0", "count_thousand"),
        operand("reported", "25%", "ratio"),
    ]
    with pytest.raises(NumericPolicyError, match="numeric_ratio_denominator_zero"):
        compare_numeric_policy(ratio_policy, zero_denominator)
