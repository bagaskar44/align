"""Deterministic Decimal core used for lecturer-confirmed numeric findings."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation, localcontext
from typing import Any


class NumericPolicyError(ValueError):
    """A source or policy cannot support an official numeric comparison."""


_PRECISION = Context(prec=34)
_CONTEXT_FIELDS = {
    "metric", "entity", "period", "scenario", "population", "definition", "assumptions", "unit"
}


def parse_decimal_literal(value: str, number_locale: str) -> Decimal:
    text = value.strip().replace("\u00a0", " ")
    if not text or len(text) > 120 or any(char in text for char in "eE"):
        raise NumericPolicyError("ambiguous_number_format")
    if number_locale == "id-ID":
        decimal_separator, grouping_separators = ",", {".", " "}
    elif number_locale == "en-US":
        decimal_separator, grouping_separators = ".", {",", " "}
    else:
        raise NumericPolicyError("unsupported_number_locale")

    signless = text[1:] if text[:1] in {"+", "-"} else text
    if not signless or signless.count(decimal_separator) > 1:
        raise NumericPolicyError("ambiguous_number_format")
    integer, separator, fraction = signless.partition(decimal_separator)
    if separator and (not fraction or not fraction.isdigit()):
        raise NumericPolicyError("ambiguous_number_format")
    if any(mark in integer for mark in grouping_separators):
        for mark in grouping_separators:
            if mark in integer:
                pieces = integer.split(mark)
                if not 1 <= len(pieces[0]) <= 3 or not pieces[0].isdigit() or any(
                    len(piece) != 3 or not piece.isdigit() for piece in pieces[1:]
                ):
                    raise NumericPolicyError("ambiguous_number_format")
                integer = "".join(pieces)
    if not integer.isdigit() or (fraction and not fraction.isdigit()):
        raise NumericPolicyError("ambiguous_number_format")
    normalized = ("-" if text.startswith("-") else "") + integer
    if separator:
        normalized += "." + fraction
    try:
        with localcontext(_PRECISION):
            result = Decimal(normalized)
            if not result.is_finite() or len(result.as_tuple().digits) > 34:
                raise NumericPolicyError("numeric_value_out_of_range")
            return result
    except InvalidOperation as exc:
        raise NumericPolicyError("ambiguous_number_format") from exc


def _decimal(value: Any, reason: str) -> Decimal:
    if not isinstance(value, str):
        raise NumericPolicyError(reason)
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise NumericPolicyError(reason) from exc
    if not result.is_finite():
        raise NumericPolicyError(reason)
    return result


def _to_unit(value: Decimal, source: str, target: str, conversions: list[dict[str, Any]]) -> Decimal:
    if source == target:
        return value
    for item in conversions:
        if not isinstance(item, dict) or item.get("kind") != "linear":
            continue
        origin, destination = item.get("from"), item.get("to")
        factor = _decimal(item.get("factor"), "invalid_conversion_factor")
        if factor <= 0:
            raise NumericPolicyError("invalid_conversion_factor")
        if origin == source and destination == target:
            return value * factor
        if origin == target and destination == source:
            return value / factor
    raise NumericPolicyError("numeric_units_not_comparable")


def _round(value: Decimal, policy: dict[str, Any]) -> tuple[Decimal, str | None]:
    rounding = policy.get("rounding")
    if not isinstance(rounding, dict) or rounding.get("mode") == "none":
        return value, None
    if rounding.get("mode") != "half_even":
        raise NumericPolicyError("unsupported_rounding_policy")
    quantum = _decimal(rounding.get("quantum"), "invalid_rounding_quantum")
    if quantum <= 0:
        raise NumericPolicyError("invalid_rounding_quantum")
    with localcontext(_PRECISION):
        rounded = (value / quantum).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN) * quantum
    return rounded, str(rounded)


def compare_numeric_policy(policy: dict[str, Any], operands: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare explicitly verified operands under an approved NumericPolicy.

    Raises NumericPolicyError for ambiguous/unverified inputs. Callers should retain the
    lecturer's prose as a feedback note instead of storing an unverified numeric finding.
    """
    locale = policy.get("number_locale")
    operation = policy.get("comparison_mode")
    canonical_unit = policy.get("canonical_unit")
    conversions = policy.get("allowed_conversions", [])
    required_context = policy.get("required_context_fields")
    if not isinstance(required_context, list) or any(field not in _CONTEXT_FIELDS for field in required_context):
        raise NumericPolicyError("invalid_numeric_policy_context")
    if not isinstance(conversions, list) or not isinstance(canonical_unit, str) or not canonical_unit:
        raise NumericPolicyError("invalid_numeric_policy")
    if operation == "pairwise":
        expected_roles = {"left", "right"}
    elif operation == "sum_against_reported":
        expected_roles = {"operand", "reported"}
    elif operation == "ratio_against_reported":
        expected_roles = {"numerator", "denominator", "reported"}
    else:
        raise NumericPolicyError("unsupported_numeric_operation")
    if not operands:
        raise NumericPolicyError("numeric_operands_missing")
    roles = [operand.get("role") for operand in operands]
    if any(not isinstance(role, str) for role in roles):
        raise NumericPolicyError("numeric_operand_role_missing")
    if operation == "pairwise" and (len(operands) != 2 or set(roles) != expected_roles):
        raise NumericPolicyError("numeric_operand_roles_invalid")
    if operation == "sum_against_reported" and (
        len(operands) < 2 or roles.count("reported") != 1 or any(role not in expected_roles for role in roles)
    ):
        raise NumericPolicyError("numeric_operand_roles_invalid")
    if operation == "ratio_against_reported" and (len(operands) != 3 or set(roles) != expected_roles):
        raise NumericPolicyError("numeric_operand_roles_invalid")

    parsed: list[dict[str, Any]] = []
    for operand in operands:
        if operand.get("source_quality") not in {"native_literal", "lecturer_confirmed"}:
            raise NumericPolicyError("unverified_numeric_source")
        literal = operand.get("literal")
        context = operand.get("context")
        if not isinstance(literal, str) or not isinstance(context, dict):
            raise NumericPolicyError("numeric_operand_invalid")
        value = parse_decimal_literal(literal, str(locale))
        unit = context.get("unit")
        if not isinstance(unit, str) or not unit:
            raise NumericPolicyError("ambiguous_numeric_context")
        parsed.append({**operand, "value": value, "unit": unit, "context": context})

    context_fields = [field for field in required_context if field != "unit"]
    context_values: dict[str, Any] = {}
    base_context = parsed[0]["context"]
    for field in context_fields:
        base_value = base_context.get(field)
        if base_value is None:
            raise NumericPolicyError("ambiguous_numeric_context")
        if any(item["context"].get(field) != base_value for item in parsed[1:]):
            raise NumericPolicyError("numeric_context_not_comparable")
        context_values[field] = base_value

    before_rounding: list[str] = []
    after_rounding: list[str] = []
    with localcontext(_PRECISION):
        if operation in {"pairwise", "sum_against_reported"}:
            normalized: list[Decimal] = []
            for item in parsed:
                converted = _to_unit(item["value"], item["unit"], canonical_unit, conversions)
                rounded, round_value = _round(converted, policy)
                normalized.append(rounded)
                before_rounding.append(str(converted))
                after_rounding.append(round_value or str(converted))
            if operation == "pairwise":
                left = normalized[roles.index("left")]
                right = normalized[roles.index("right")]
            else:
                left = sum((normalized[index] for index, role in enumerate(roles) if role == "operand"), Decimal(0))
                right = normalized[roles.index("reported")]
        else:
            numerator = parsed[roles.index("numerator")]
            denominator = parsed[roles.index("denominator")]
            reported = parsed[roles.index("reported")]
            numerator_value = _to_unit(numerator["value"], numerator["unit"], denominator["unit"], conversions)
            if denominator["value"] == 0:
                raise NumericPolicyError("numeric_ratio_denominator_zero")
            ratio = numerator_value / denominator["value"]
            left = _to_unit(ratio, "ratio", canonical_unit, conversions)
            right = _to_unit(reported["value"], reported["unit"], canonical_unit, conversions)
            left, round_left = _round(left, policy)
            right, round_right = _round(right, policy)
            before_rounding.extend([str(ratio), str(reported["value"])])
            after_rounding.extend([round_left or str(left), round_right or str(right)])
        absolute = _decimal(policy.get("absolute_tolerance"), "invalid_absolute_tolerance")
        relative = _decimal(policy.get("relative_tolerance"), "invalid_relative_tolerance")
        if absolute < 0 or relative < 0 or relative > 1:
            raise NumericPolicyError("invalid_numeric_tolerance")
        difference = abs(left - right)
        allowed = max(absolute, relative * max(abs(left), abs(right)))
    if not isinstance(policy.get("metric_definition"), str) or not policy["metric_definition"].strip():
        raise NumericPolicyError("invalid_numeric_policy_metric")
    context_values["unit"] = canonical_unit
    return {
        "comparability_status": "comparable",
        "operation": operation,
        "normalized_context": context_values,
        "operands_snapshot": [
            {
                "evidence_id": item.get("evidence_id"),
                "role": item["role"],
                "literal": item["literal"],
                "source_quality": item["source_quality"],
                "context": item["context"],
                "normalized_decimal": str(item["value"]),
            }
            for item in parsed
        ] + [{"rounding_before": before_rounding, "rounding_after": after_rounding}],
        "result_decimal_text": str(left),
        "difference_text": str(difference),
        "allowed_difference_text": str(allowed),
        "inconsistent": difference > allowed,
    }
