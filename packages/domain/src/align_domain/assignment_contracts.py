"""Closed, deterministic contracts for assignment packages and specification v2."""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Span(StrictContract):
    start: StrictInt = Field(ge=0)
    end: StrictInt = Field(ge=1)

    @model_validator(mode="after")
    def nonempty(self) -> Span:
        if self.end <= self.start:
            raise ValueError("span must be nonempty and end-exclusive")
        return self


class DerivationRef(StrictContract):
    source_kind: Literal["requirement", "criterion_descriptor", "instruction"]
    source_id: UUID
    span: Span | None

    @model_validator(mode="after")
    def span_shape(self) -> DerivationRef:
        if (self.source_kind == "requirement") != (self.span is None):
            raise ValueError("requirement refs use span=null; text refs require a span")
        return self


class Grounding(StrictContract):
    schema_version: Literal[1]
    mode: Literal["anchored_content", "linked_relationship", "comparable_numeric", "human_only"]
    absence_support: Literal["approved_coverage_policy"] | None


class Qualifiers(StrictContract):
    basis: StrictStr | None = None
    period: StrictStr | None = None
    scenario: StrictStr | None = None
    metric: StrictStr | None = None


class Relationship(StrictContract):
    relationship_code: StrictStr = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    predicate: Literal[
        "covers", "compares", "supports", "applies", "traces_to", "reports_same_quantity"
    ]
    subject: StrictStr = Field(min_length=1, max_length=500)
    object: StrictStr = Field(min_length=1, max_length=500)
    qualifiers: Qualifiers = Field(default_factory=Qualifiers)


class Applicability(StrictContract):
    mode: Literal["always", "lecturer_required", "fact_rule"]
    question: StrictStr | None = Field(default=None, min_length=1, max_length=2000)
    rule: dict[str, Any] | None = None

    @model_validator(mode="after")
    def match_mode(self) -> Applicability:
        if self.mode == "always" and (self.question is not None or self.rule is not None):
            raise ValueError("always applicability has no question or rule")
        if self.mode == "lecturer_required" and (not self.question or self.rule is not None):
            raise ValueError("lecturer_required needs question only")
        if self.mode == "fact_rule" and (self.rule is None or self.question is not None):
            raise ValueError("fact_rule needs rule only")
        if self.rule is not None:
            validate_fact_rule(self.rule)
        return self


class CoveragePolicy(StrictContract):
    positive_strategy: Literal["sufficient_support"]
    negative_strategy: Literal["exhaustive_compatible_source_units"]
    inspect_embedded_visuals: StrictBool
    unresolved_compatible_unit_blocks_negative: StrictBool
    preserve_table_context: StrictBool

    @model_validator(mode="after")
    def safe_defaults(self) -> CoveragePolicy:
        if not (
            self.inspect_embedded_visuals
            and self.unresolved_compatible_unit_blocks_negative
            and self.preserve_table_context
        ):
            raise ValueError("coverage guardrails cannot be disabled")
        return self


class TaskFact(StrictContract):
    key: StrictStr = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    value_type: Literal["boolean", "string", "decimal"]
    allowed_values: list[StrictStr] | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def enum_values_only_for_strings(self) -> TaskFact:
        if self.value_type != "string" and self.allowed_values is not None:
            raise ValueError("allowed_values applies only to string facts")
        if self.allowed_values is not None and len(set(self.allowed_values)) != len(
            self.allowed_values
        ):
            raise ValueError("fact allowed_values must be unique")
        return self


DEFAULT_COVERAGE = {
    "positive_strategy": "sufficient_support",
    "negative_strategy": "exhaustive_compatible_source_units",
    "inspect_embedded_visuals": True,
    "unresolved_compatible_unit_blocks_negative": True,
    "preserve_table_context": True,
}
EVIDENCE_TYPES = {
    "paragraph",
    "table",
    "diagram",
    "image_text",
    "spreadsheet_cell",
    "spreadsheet_range",
}
OPERATORS = {
    "locate_evidence",
    "expand_context",
    "inspect_source_coverage",
    "interpret_relationship",
    "check_coverage",
    "establish_comparability",
    "compare_numbers",
    "request_human_review",
}
HUMAN_OPERATORS = {
    "locate_evidence",
    "expand_context",
    "inspect_source_coverage",
    "request_human_review",
}
REQUIRED_ROLES = {
    "covers": {"content", "relation"},
    "compares": {"left", "right", "basis", "relation"},
    "supports": {"claim", "support", "relation"},
    "applies": {"method", "application", "relation"},
    "traces_to": {"artifact", "trace", "relation"},
    "reports_same_quantity": {"left", "right", "relation"},
}
ABSTENTION_CODES = {
    "unreadable_relevant_region",
    "incomplete_source_inspection",
    "ambiguous_descriptor",
    "unresolved_applicability",
    "unverified_numeric_operand",
    "incomparable_numeric_context",
    "ambiguous_number_format",
    "unsupported_source_format",
    "processing_limit",
}
UNIT_REGISTRY: dict[str, tuple[str, Decimal]] = {
    "IDR": ("currency:IDR", Decimal("1")),
    "IDR_thousand": ("currency:IDR", Decimal("1000")),
    "IDR_million": ("currency:IDR", Decimal("1000000")),
    "ratio": ("ratio", Decimal("1")),
    "percent": ("ratio", Decimal("0.01")),
    "percentage_point": ("percentage_point", Decimal("0.01")),
    "unit": ("count", Decimal("1")),
    "count_thousand": ("count", Decimal("1000")),
    "m": ("length", Decimal("1")),
    "cm": ("length", Decimal("0.01")),
    "km": ("length", Decimal("1000")),
    "kg": ("mass", Decimal("1")),
    "g": ("mass", Decimal("0.001")),
    "s": ("time", Decimal("1")),
    "min": ("time", Decimal("60")),
    "h": ("time", Decimal("3600")),
}


class CheckElement(StrictContract):
    element_id: UUID
    element_code: StrictStr = Field(pattern=r"^[A-Za-z][A-Za-z0-9._-]{0,79}$")
    criterion_id: UUID
    requirement_ids: list[UUID] = Field(max_length=30)
    obligation_key: StrictStr = Field(min_length=1, max_length=240)
    target_kind: Literal["requirement", "descriptor", "numeric"]
    decision_question: StrictStr = Field(min_length=1, max_length=2000)
    derivation_refs: list[DerivationRef] = Field(min_length=1, max_length=20)
    expected_evidence: StrictStr = Field(min_length=1, max_length=2000)
    grounding_contract: Grounding
    accepted_evidence_types: list[
        Literal[
            "paragraph", "table", "diagram", "image_text", "spreadsheet_cell", "spreadsheet_range"
        ]
    ] = Field(min_length=1, max_length=6)
    relationships: list[Relationship] = Field(max_length=10)
    applicability: Applicability
    source_hints: list[StrictStr] = Field(max_length=10)
    operators: list[
        Literal[
            "locate_evidence",
            "expand_context",
            "inspect_source_coverage",
            "interpret_relationship",
            "check_coverage",
            "establish_comparability",
            "compare_numbers",
            "request_human_review",
        ]
    ] = Field(min_length=1, max_length=8)
    dependencies: list[UUID] = Field(max_length=100)
    coverage_policy: CoveragePolicy = Field(
        default_factory=lambda: CoveragePolicy.model_validate(DEFAULT_COVERAGE)
    )
    abstention_conditions: list[StrictStr] = Field(max_length=20)
    numeric_policy_version_id: UUID | None
    human_judgment_boundary: StrictStr = Field(min_length=1, max_length=2000)

    @field_validator(
        "decision_question", "expected_evidence", "obligation_key", "human_judgment_boundary"
    )
    @classmethod
    def strip_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value cannot be blank")
        return value

    @model_validator(mode="after")
    def validate_modes(self) -> CheckElement:
        if not set(self.abstention_conditions) <= ABSTENTION_CODES:
            raise ValueError("unknown abstention condition")
        grounding = self.grounding_contract
        if (
            grounding.mode in {"anchored_content", "linked_relationship"}
            and grounding.absence_support != "approved_coverage_policy"
        ):
            raise ValueError("content grounding requires approved coverage absence support")
        if (
            grounding.mode in {"comparable_numeric", "human_only"}
            and grounding.absence_support is not None
        ):
            raise ValueError("numeric and human-only grounding require null absence_support")
        if grounding.mode == "linked_relationship" and not self.relationships:
            raise ValueError("linked_relationship requires relationships")
        if grounding.mode == "anchored_content" and self.relationships:
            raise ValueError("relationship elements require linked_relationship grounding")
        if grounding.mode == "comparable_numeric":
            if self.target_kind != "numeric" or self.numeric_policy_version_id is None:
                raise ValueError("comparable_numeric requires numeric target and policy")
            if (
                "compare_numbers" not in self.operators
                or "establish_comparability" not in self.operators
            ):
                raise ValueError(
                    "numeric automation requires comparability before numeric comparison"
                )
        elif self.numeric_policy_version_id is not None:
            raise ValueError("numeric policy is only valid for comparable_numeric elements")
        if self.target_kind == "numeric" and grounding.mode not in {
            "comparable_numeric",
            "human_only",
        }:
            raise ValueError("numeric target requires comparable_numeric or human_only grounding")
        if grounding.mode == "human_only" and not set(self.operators) <= HUMAN_OPERATORS:
            raise ValueError("human_only operator subset exceeded")
        if (
            "reports_same_quantity" in {r.predicate for r in self.relationships}
            and grounding.mode != "comparable_numeric"
        ):
            raise ValueError("reports_same_quantity is numeric-only")
        if len(set(self.accepted_evidence_types)) != len(self.accepted_evidence_types):
            raise ValueError("accepted evidence types must be unique")
        if len(set(self.operators)) != len(self.operators):
            raise ValueError("operators must be unique")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ValueError("dependencies must be unique")
        if len(set(self.source_hints)) != len(self.source_hints):
            raise ValueError("source hints must be unique")
        if len(set(self.abstention_conditions)) != len(self.abstention_conditions):
            raise ValueError("abstention conditions must be unique")
        if len({r.relationship_code for r in self.relationships}) != len(self.relationships):
            raise ValueError("relationship codes must be unique per element")
        return self

    @property
    def required_support_roles(self) -> dict[str, list[str]]:
        return {
            relationship.relationship_code: sorted(REQUIRED_ROLES[relationship.predicate])
            for relationship in self.relationships
        }


class SpecificationPayload(StrictContract):
    schema_version: Literal[2]
    elements: list[CheckElement] = Field(min_length=1, max_length=100)
    operator_registry_version: StrictStr = Field(min_length=1, max_length=80)
    predicate_registry_version: StrictStr = Field(min_length=1, max_length=80)
    grounding_registry_version: StrictStr = Field(min_length=1, max_length=80)
    lint_registry_version: StrictStr = Field(min_length=1, max_length=80)
    limits: dict[str, Any]
    task_facts: list[TaskFact] = Field(max_length=100)

    @model_validator(mode="after")
    def unique_ids_and_dag(self) -> SpecificationPayload:
        expected_versions = {
            "operator_registry_version": "operators-v1",
            "predicate_registry_version": "predicates-v1",
            "grounding_registry_version": "grounding-v1",
            "lint_registry_version": "lint-v1",
        }
        for field_name, expected in expected_versions.items():
            if getattr(self, field_name) != expected:
                raise ValueError(f"unsupported {field_name}")
        required_limits = {
            "draft_max_cost_usd",
            "max_text_calls",
            "max_vision_calls",
            "max_input_tokens",
            "max_output_tokens",
        }
        if set(self.limits) != required_limits:
            raise ValueError("specification limits shape is invalid")
        try:
            cost = decimal_value(self.limits["draft_max_cost_usd"], "draft_max_cost_usd")
            if cost < 0 or cost > Decimal("0.05"):
                raise ValueError("draft cost limit exceeds the B2 guard")
        except (TypeError, KeyError) as exc:
            raise ValueError("draft cost limit must be a decimal string") from exc
        maxima = {
            "max_text_calls": 3,
            "max_vision_calls": 2,
            "max_input_tokens": 20000,
            "max_output_tokens": 4000,
        }
        for name, maximum in maxima.items():
            value = self.limits.get(name)
            if type(value) is not int or value < 0 or value > maximum:
                raise ValueError(f"{name} exceeds the B2 limit")
        task_fact_keys = [fact.key for fact in self.task_facts]
        if len(set(task_fact_keys)) != len(task_fact_keys):
            raise ValueError("task fact keys must be unique")
        ids = [e.element_id for e in self.elements]
        codes = [e.element_code for e in self.elements]
        obligations = [e.obligation_key for e in self.elements]
        if len(set(ids)) != len(ids) or len(set(codes)) != len(codes):
            raise ValueError("element ids and codes must be unique")
        if len(set(obligations)) != len(obligations):
            raise ValueError("obligation keys must be unique in one specification")
        known = set(ids)
        if any(
            dependency not in known or dependency == element.element_id
            for element in self.elements
            for dependency in element.dependencies
        ):
            raise ValueError("dependencies must reference another element in this specification")
        graph = {element.element_id: element.dependencies for element in self.elements}
        visiting: set[UUID] = set()
        visited: set[UUID] = set()

        def visit(node: UUID) -> None:
            if node in visiting:
                raise ValueError("dependency graph contains a cycle")
            if node in visited:
                return
            visiting.add(node)
            for parent in graph[node]:
                visit(parent)
            visiting.remove(node)
            visited.add(node)

        for node in graph:
            visit(node)
        fact_registry = {f"task.{fact.key}": fact for fact in self.task_facts}
        for element in self.elements:
            rule = element.applicability.rule
            if rule is not None:
                validate_registered_rule(rule, fact_registry)
        return self


class NumericConversion(StrictContract):
    kind: Literal["linear"]
    from_unit: StrictStr = Field(min_length=1, max_length=80, alias="from")
    to_unit: StrictStr = Field(min_length=1, max_length=80, alias="to")
    factor: StrictStr

    @field_validator("factor")
    @classmethod
    def positive_factor(cls, value: str) -> str:
        number = decimal_value(value, "factor")
        if number <= 0:
            raise ValueError("conversion factor must be positive")
        return value


class Rounding(StrictContract):
    mode: Literal["none", "half_even"]
    quantum: StrictStr | None

    @model_validator(mode="after")
    def rounding_quantum(self) -> Rounding:
        if self.mode == "none" and self.quantum is not None:
            raise ValueError("none rounding requires null quantum")
        if self.mode == "half_even" and (
            self.quantum is None or decimal_value(self.quantum, "quantum") <= 0
        ):
            raise ValueError("half_even rounding requires positive quantum")
        return self


class NumericSourceSelector(StrictContract):
    """Exact source labels used to select values without semantic guesswork."""

    sheet_name: StrictStr | None = Field(default=None, max_length=120)
    row_label: StrictStr | None = Field(default=None, max_length=240)
    column_label: StrictStr | None = Field(default=None, max_length=240)

    @model_validator(mode="after")
    def has_locator(self) -> NumericSourceSelector:
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (self.sheet_name, self.row_label, self.column_label)
        ):
            raise ValueError("numeric source selector needs exact sheet, row, and column labels")
        return self


class NumericContext(StrictContract):
    metric: StrictStr | None = Field(default=None, max_length=240)
    entity: StrictStr | None = Field(default=None, max_length=240)
    period: StrictStr | None = Field(default=None, max_length=120)
    scenario: StrictStr | None = Field(default=None, max_length=240)
    population: StrictStr | None = Field(default=None, max_length=240)
    definition: StrictStr | None = Field(default=None, max_length=1000)
    assumptions: StrictStr | None = Field(default=None, max_length=1000)
    unit: StrictStr


class NumericPolicyPayload(StrictContract):
    metric_definition: StrictStr = Field(min_length=1, max_length=2000)
    required_context_fields: list[
        Literal[
            "metric",
            "entity",
            "period",
            "scenario",
            "population",
            "definition",
            "assumptions",
            "unit",
        ]
    ] = Field(min_length=4, max_length=8)
    number_locale: StrictStr = Field(min_length=2, max_length=32)
    canonical_unit: StrictStr = Field(min_length=1, max_length=80)
    allowed_conversions: list[NumericConversion] = Field(max_length=20)
    comparison_mode: Literal["pairwise", "sum_against_reported", "ratio_against_reported"]
    absolute_tolerance: StrictStr
    relative_tolerance: StrictStr
    rounding: Rounding
    operand_roles: dict[str, StrictStr]
    source_selectors: dict[str, NumericSourceSelector | list[NumericSourceSelector]] | None = None
    context_by_role: dict[str, NumericContext | list[NumericContext]] | None = None

    @model_validator(mode="after")
    def validate_policy(self) -> NumericPolicyPayload:
        if self.canonical_unit not in UNIT_REGISTRY:
            raise ValueError("canonical_unit is not in the approved unit registry")
        mandatory = {"metric", "entity", "definition", "unit"}
        if not mandatory <= set(self.required_context_fields) or len(
            set(self.required_context_fields)
        ) != len(self.required_context_fields):
            raise ValueError(
                "required_context_fields must include metric, entity, definition and unit exactly once"
            )
        if decimal_value(self.absolute_tolerance, "absolute_tolerance") < 0:
            raise ValueError("absolute_tolerance must be nonnegative")
        relative = decimal_value(self.relative_tolerance, "relative_tolerance")
        if relative < 0 or relative > 1:
            raise ValueError("relative_tolerance must be between 0 and 1")
        if (
            not self.operand_roles
            or len(self.operand_roles) > 10
            or any(not v.strip() for v in self.operand_roles.values())
        ):
            raise ValueError("operand_roles must contain 1 to 10 nonblank roles")
        expected_roles = {
            "pairwise": {"left", "right"},
            "sum_against_reported": {"operands", "reported"},
            "ratio_against_reported": {"numerator", "denominator", "reported"},
        }[self.comparison_mode]
        if set(self.operand_roles) != expected_roles:
            raise ValueError("operand_roles do not match comparison_mode")
        if (self.source_selectors is None) != (self.context_by_role is None):
            raise ValueError("numeric selectors and explicit contexts must be configured together")
        if self.source_selectors is not None and self.context_by_role is not None:
            if set(self.source_selectors) != expected_roles or set(self.context_by_role) != expected_roles:
                raise ValueError("numeric selector/context roles do not match comparison_mode")
            for role in expected_roles:
                selectors = self.source_selectors[role]
                contexts = self.context_by_role[role]
                if role == "operands" and self.comparison_mode == "sum_against_reported":
                    if not isinstance(selectors, list) or not selectors or not isinstance(contexts, list):
                        raise ValueError("sum operands require parallel complete selector and context lists")
                    if len(selectors) != len(contexts):
                        raise ValueError("sum operand selectors and contexts must have matching lengths")
                    if len({(item.sheet_name, item.row_label, item.column_label) for item in selectors}) != len(selectors):
                        raise ValueError("sum operand selectors must be unique")
                    role_contexts = contexts
                else:
                    if isinstance(selectors, list) or isinstance(contexts, list):
                        raise ValueError("non-sum numeric roles require one selector and one context")
                    role_contexts = [contexts]
                for context in role_contexts:
                    context_values = context.model_dump(exclude_none=True)
                    if not set(self.required_context_fields) <= set(context_values):
                        raise ValueError("numeric role context omits a required comparability field")
                    if context.unit not in UNIT_REGISTRY:
                        raise ValueError("numeric role context unit is not in the approved unit registry")
        if len({(c.from_unit, c.to_unit) for c in self.allowed_conversions}) != len(
            self.allowed_conversions
        ):
            raise ValueError("conversion pair must be unique")
        for conversion in self.allowed_conversions:
            if conversion.from_unit not in UNIT_REGISTRY or conversion.to_unit not in UNIT_REGISTRY:
                raise ValueError("conversion unit is not in the approved unit registry")
            from_dimension, from_scale = UNIT_REGISTRY[conversion.from_unit]
            to_dimension, to_scale = UNIT_REGISTRY[conversion.to_unit]
            if from_dimension != to_dimension:
                raise ValueError("conversion units must have the same dimension")
            if decimal_value(conversion.factor, "factor") != from_scale / to_scale:
                raise ValueError("conversion factor does not match the approved unit registry")
        return self


def decimal_value(value: str, field: str) -> Decimal:
    if not re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value):
        raise ValueError(f"{field} must be a canonical decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{field} is invalid") from exc
    if not result.is_finite() or len(value) > 64:
        raise ValueError(f"{field} is outside the supported decimal range")
    return result


def validate_fact_rule(rule: dict[str, Any]) -> None:
    leaves = 0

    def walk(node: Any, depth: int) -> None:
        nonlocal leaves
        if depth > 3 or not isinstance(node, dict) or "op" not in node:
            raise ValueError("fact rule depth or node shape is invalid")
        op = node["op"]
        if op in {"all", "any"}:
            if (
                set(node) != {"op", "children"}
                or not isinstance(node["children"], list)
                or not node["children"]
            ):
                raise ValueError("all/any require nonempty children")
            for child in node["children"]:
                walk(child, depth + 1)
            return
        if op == "not":
            if set(node) != {"op", "child"}:
                raise ValueError("not requires one child")
            walk(node["child"], depth + 1)
            return
        if op not in {"eq", "in", "contains", "gt", "gte", "lt", "lte"}:
            raise ValueError("unknown fact comparator")
        leaves += 1
        if leaves > 10:
            raise ValueError("fact rule exceeds ten leaves")
        fact = node.get("fact")
        if fact not in {"assignment.mode", "submission.file_types"} and not (
            isinstance(fact, str) and re.fullmatch(r"task\.[a-z][a-z0-9_]{0,63}", fact)
        ):
            raise ValueError("fact is not registered")
        if fact == "assignment.mode" and op == "contains":
            raise ValueError("contains is not valid for scalar assignment.mode")
        if fact.startswith("task.") and op == "contains":
            raise ValueError("custom task facts are scalar")
        if op == "in":
            if (
                set(node) != {"op", "fact", "values"}
                or not isinstance(node["values"], list)
                or not node["values"]
            ):
                raise ValueError("in requires nonempty values")
        elif op == "contains":
            if fact != "submission.file_types" or set(node) != {"op", "fact", "value"}:
                raise ValueError("contains is only valid for submission.file_types")
        else:
            if set(node) != {"op", "fact", "value"}:
                raise ValueError("leaf has unknown/missing properties")
        value = node.get("value")
        if op in {"gt", "gte", "lt", "lte"}:
            if not isinstance(value, (int, float, str)) or isinstance(value, bool):
                raise ValueError("ordered comparator requires scalar")
            if fact.startswith("task.") and isinstance(value, str):
                decimal_value(value, "fact value")

    walk(rule, 1)


def validate_registered_rule(rule: dict[str, Any], task_facts: dict[str, TaskFact]) -> None:
    formats = {"pdf", "docx", "xlsx", "jpg", "jpeg", "png"}

    def walk(node: dict[str, Any]) -> None:
        op = node["op"]
        if op in {"all", "any"}:
            for child in node["children"]:
                walk(child)
            return
        if op == "not":
            walk(node["child"])
            return
        fact = node["fact"]
        if fact == "submission.file_types":
            if op != "contains" or node["value"] not in formats:
                raise ValueError("submission.file_types supports contains for registered formats")
            return
        if fact == "assignment.mode":
            if op not in {"eq", "in"}:
                raise ValueError("assignment.mode supports eq or in only")
            values = node.get("values", [node.get("value")])
            if any(value not in {"individual", "group"} for value in values):
                raise ValueError("assignment.mode value is not registered")
            return
        definition = task_facts.get(fact)
        if definition is None:
            raise ValueError("task fact is not registered in this specification")
        values = node.get("values", [node.get("value")])
        if definition.value_type == "boolean" and any(type(value) is not bool for value in values):
            raise ValueError("boolean fact requires boolean values")
        if definition.value_type == "decimal":
            if op not in {"eq", "in", "gt", "gte", "lt", "lte"}:
                raise ValueError("decimal fact comparator is invalid")
            for value in values:
                if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                    raise ValueError("decimal fact requires numeric scalar values")
                decimal_value(str(value), "task fact value")
        if definition.value_type == "string":
            if op not in {"eq", "in"} or any(not isinstance(value, str) for value in values):
                raise ValueError("string fact supports eq/in string values")
            if definition.allowed_values is not None and any(
                value not in definition.allowed_values for value in values
            ):
                raise ValueError("task fact value is not registered")

    walk(rule)


def canonical_hash(payload: Any) -> str:
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def validate_spec_against_sources(
    spec: SpecificationPayload,
    *,
    criteria: dict[UUID, str],
    requirements: dict[UUID, tuple[UUID | set[UUID], str, str]],
    instruction_id: UUID,
    instruction: str,
    numeric_policy_ids: set[UUID],
) -> None:
    for element in spec.elements:
        if element.criterion_id not in criteria:
            raise ValueError("criterion_id is outside the bound criterion set")
        if element.target_kind == "requirement" and not element.requirement_ids:
            raise ValueError("requirement target needs requirement_ids")
        if element.target_kind != "requirement" and element.requirement_ids:
            raise ValueError("only requirement targets may bind requirement_ids")
        if len(set(element.requirement_ids)) != len(element.requirement_ids):
            raise ValueError("requirement_ids must be unique")
        requirement_keys: set[str] = set()
        for req_id in element.requirement_ids:
            parent, requirement_text, requirement_key = requirements.get(req_id, (None, "", ""))
            linked_criteria = parent if isinstance(parent, set) else {parent}
            if element.criterion_id not in linked_criteria:
                raise ValueError("requirement is outside the element criterion")
            if element.target_kind == "requirement":
                # The public obligation lineage must remain the one approved in the package.
                # The requirement text itself remains the derivation source.
                if not requirement_text:
                    raise ValueError("requirement text is missing from the approved package")
                requirement_keys.add(requirement_key)
        if element.target_kind == "requirement" and requirement_keys != {element.obligation_key}:
            raise ValueError("obligation_key must match the bound requirement lineage")
        refs = {(r.source_kind, r.source_id) for r in element.derivation_refs}
        if (
            element.target_kind == "requirement"
            and not set(("requirement", r) for r in element.requirement_ids) <= refs
        ):
            raise ValueError("all target requirements need derivation refs")
        for ref in element.derivation_refs:
            if ref.source_kind == "requirement":
                if (
                    ref.source_id not in requirements
                    or ref.source_id not in element.requirement_ids
                ):
                    raise ValueError("requirement derivation is outside requirement_ids")
            else:
                if ref.source_kind == "instruction":
                    if ref.source_id != instruction_id:
                        raise ValueError("instruction ref does not identify bound version")
                    source = instruction
                else:
                    if ref.source_id != element.criterion_id:
                        raise ValueError(
                            "criterion descriptor ref must identify the element criterion"
                        )
                    source = criteria[ref.source_id]
                assert ref.span is not None
                if ref.span.end > len(source):
                    raise ValueError("derivation span is outside source text")
        if (
            element.numeric_policy_version_id is not None
            and element.numeric_policy_version_id not in numeric_policy_ids
        ):
            raise ValueError("numeric policy ref is not approved and bound")


def compile_lint(
    spec: SpecificationPayload,
    *,
    revision: int,
    spec_hash: str,
    package_hashes: dict[str, str],
    required_requirement_ids: set[str] | None = None,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    covered_requirement_ids = {
        str(requirement_id)
        for element in spec.elements
        for requirement_id in element.requirement_ids
    }
    if required_requirement_ids is not None:
        for requirement_id in sorted(required_requirement_ids - covered_requirement_ids):
            issues.append(
                {
                    "issue_code": f"unsupported_requirement:{requirement_id}",
                    "rule_code": "unsupported_requirement",
                    "severity": "blocking",
                    "element_ids": [],
                    "source_refs": [
                        {
                            "source_kind": "requirement",
                            "source_id": requirement_id,
                            "span": None,
                        }
                    ],
                    "message": "Requirement resmi belum didukung elemen specification.",
                }
            )
    question_owners: dict[str, str] = {}
    for element in spec.elements:
        question = element.decision_question.strip().lower()
        codes: list[tuple[str, str]] = []
        normalized_question = " ".join(question.split())
        prior_code = question_owners.get(normalized_question)
        if prior_code is not None:
            codes.append(
                (
                    "duplicate_obligation_review",
                    f"Pertanyaan juga digunakan oleh elemen {prior_code}; pastikan kewajibannya memang identik.",
                )
            )
        else:
            question_owners[normalized_question] = element.element_code
        if len(re.findall(r"\b(dan|serta|maupun)\b", question)) >= 2 or ";" in question:
            codes.append(
                (
                    "atomicity_review",
                    "Pertanyaan mungkin menggabungkan tuntutan yang dapat diperiksa terpisah.",
                )
            )
        if question.startswith(("tidak ", "apakah tidak ")) or "tidak boleh" in question:
            codes.append(
                (
                    "polarity_review",
                    "Periksa bahwa pertanyaan mempertahankan polarity kewajiban sumber.",
                )
            )
        if any(word in question for word in ("jika", "apabila", "kecuali", "boleh", "dapat")):
            codes.append(
                (
                    "condition_fidelity_review",
                    "Periksa syarat, pengecualian, alternatif, atau sifat opsional terhadap sumber.",
                )
            )
        if (
            element.grounding_contract.mode == "human_only"
            and not element.human_judgment_boundary.strip()
        ):
            codes.append(("checkability_review", "Batas keputusan manusia perlu diperjelas."))
        if not element.source_hints and element.grounding_contract.mode != "human_only":
            codes.append(
                (
                    "checkability_review",
                    "Petunjuk pencarian kosong; pastikan bentuk bukti tetap dapat ditelaah.",
                )
            )
        for code, message in codes:
            issues.append(
                {
                    "issue_code": f"{element.element_code}:{code}",
                    "rule_code": code,
                    "severity": "review",
                    "element_ids": [str(element.element_id)],
                    "source_refs": [r.model_dump(mode="json") for r in element.derivation_refs],
                    "message": message,
                }
            )
    status = (
        "failed"
        if any(issue["severity"] == "blocking" for issue in issues)
        else ("needs_review" if issues else "passed")
    )
    return {
        "source_spec_revision": revision,
        "source_content_hash": spec_hash,
        "source_package_hashes": package_hashes,
        "registry_versions": {
            "operator": spec.operator_registry_version,
            "predicate": spec.predicate_registry_version,
            "grounding": spec.grounding_registry_version,
            "lint": spec.lint_registry_version,
        },
        "initial_status": status,
        "issues": issues,
        "resolution_revision": 1,
    }


def lint_effective_status(
    report: dict[str, Any],
    resolutions: list[dict[str, Any]],
    *,
    current_revision: int,
    current_hash: str,
) -> str:
    if (
        report["source_spec_revision"] != current_revision
        or report["source_content_hash"] != current_hash
    ):
        return "stale"
    issues = report["issues"]
    if any(issue["severity"] == "blocking" for issue in issues):
        return "failed"
    resolved = {entry["issue_code"] for entry in resolutions}
    return (
        "passed"
        if all(issue["issue_code"] in resolved for issue in issues)
        else ("needs_review" if issues else "passed")
    )
