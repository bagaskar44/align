from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from align_api.models import (
    CoverageRecord,
    ElementCheck,
    EvidenceItem,
    NumericCheck,
    NumericObservation,
    NumericPolicyVersion,
)
from align_domain.assignment_contracts import NumericPolicyPayload
from align_worker.executor import _run_numeric_policy


def _context(unit: str = "kg") -> dict[str, str]:
    return {
        "metric": "massa",
        "entity": "produk-A",
        "period": "2026",
        "definition": "massa bersih",
        "unit": unit,
    }


def _policy_payload() -> dict[str, object]:
    return NumericPolicyPayload.model_validate({
        "metric_definition": "massa bersih yang dilaporkan",
        "required_context_fields": ["metric", "entity", "period", "definition", "unit"],
        "number_locale": "id-ID",
        "canonical_unit": "kg",
        "allowed_conversions": [],
        "comparison_mode": "pairwise",
        "absolute_tolerance": "0.5",
        "relative_tolerance": "0",
        "rounding": {"mode": "none", "quantum": None},
        "operand_roles": {"left": "Realisasi", "right": "Laporan"},
        "source_selectors": {
            "left": {"sheet_name": "Hasil", "row_label": "Massa", "column_label": "Realisasi"},
            "right": {"sheet_name": "Hasil", "row_label": "Massa", "column_label": "Laporan"},
        },
        "context_by_role": {"left": _context(), "right": _context()},
    }).model_dump(mode="json")


def _evidence(text: str, column: str, *, formula: str | None = None) -> EvidenceItem:
    return EvidenceItem(
        id=uuid4(),
        source_unit_id=uuid4(),
        submission_version_id=uuid4(),
        file_asset_id=uuid4(),
        source_sha256="a" * 64,
        type="spreadsheet_cell",
        anchor_json={"format": "xlsx", "sheet_name": "Hasil", "cell_range": "B2"},
        content_original_json={
            "text": text,
            "row_label": "Massa",
            "column_label": column,
            "formula": formula,
            "cache_verified": False if formula else None,
            "hidden_sheet": False,
        },
        content_normalized_json={"text": text},
        parse_status="parsed",
        limitations_json=[],
        parser_manifest_id=uuid4(),
    )


def _setup(policy_payload: dict[str, object]) -> tuple[MagicMock, ElementCheck, CoverageRecord, NumericPolicyVersion, SimpleNamespace]:
    db = MagicMock()
    parent_id = uuid4()
    check = ElementCheck(id=parent_id, analysis_run_id=uuid4(), check_element_id=uuid4())
    coverage = CoverageRecord(
        id=uuid4(), analysis_run_id=uuid4(), check_element_id=check.check_element_id,
        retrieval_status="unavailable", compatible_count=2, inspected_count=2,
        unresolved_count=0, sufficient_for_positive=False, exhaustive_for_negative=False,
        inventory_json={}, retrieval_json={}, limitations_json=[],
    )
    policy_id = uuid4()
    policy = NumericPolicyVersion(
        id=policy_id, assignment_id=uuid4(), policy_key="mass", version_no=1, state="approved",
        payload_json=policy_payload, content_hash="b" * 64,
    )
    element = SimpleNamespace(numeric_policy_version_id=policy_id)
    return db, check, coverage, policy, element


def test_exact_selector_and_policy_context_produce_deterministic_numeric_check() -> None:
    db, check, coverage, policy, element = _setup(_policy_payload())
    left = _evidence("10", "Realisasi")
    right = _evidence("11", "Laporan")
    numeric, evidence_ids, reason = _run_numeric_policy(
        db, run=SimpleNamespace(manifest_snapshot_json={}), check=check, coverage=coverage,
        element=element, policy_row=policy, evidence_items={left.id: left, right.id: right}, locale=None,
    )
    rows = [call.args[0] for call in db.add.call_args_list]
    observations = [row for row in rows if isinstance(row, NumericObservation)]
    numeric_rows = [row for row in rows if isinstance(row, NumericCheck)]
    assert reason is None
    assert numeric is numeric_rows[0]
    assert numeric.comparability_status == "comparable"
    assert numeric.result_decimal_text == "10"
    assert numeric.inconsistent is True
    assert {row.source_quality for row in observations} == {"native_literal"}
    assert set(evidence_ids) == {left.id, right.id}
    assert coverage.sufficient_for_positive is True


def test_formula_and_missing_selectors_are_recorded_unverified_and_abstain() -> None:
    db, check, coverage, policy, element = _setup(_policy_payload())
    left = _evidence("=SUM(A1:A3)", "Realisasi", formula="=SUM(A1:A3)")
    right = _evidence("11", "Laporan")
    numeric, _, reason = _run_numeric_policy(
        db, run=SimpleNamespace(manifest_snapshot_json={}), check=check, coverage=coverage,
        element=element, policy_row=policy, evidence_items={left.id: left, right.id: right}, locale=None,
    )
    observations = [call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], NumericObservation)]
    assert numeric is not None
    assert numeric.comparability_status == "unresolved"
    assert reason == "numeric_source_unverified"
    assert any(row.source_quality == "unverified" for row in observations)

    db, check, coverage, policy, element = _setup(_policy_payload())
    payload = dict(policy.payload_json)
    payload.pop("source_selectors")
    payload.pop("context_by_role")
    policy.payload_json = payload
    numeric, _, reason = _run_numeric_policy(
        db, run=SimpleNamespace(manifest_snapshot_json={}), check=check, coverage=coverage,
        element=element, policy_row=policy, evidence_items={right.id: right}, locale=None,
    )
    assert numeric is not None and numeric.comparability_status == "unresolved"
    assert reason == "numeric_source_selectors_missing"


def test_sum_acquisition_uses_complete_parallel_selector_context_lists() -> None:
    payload = _policy_payload()
    payload.update({
        "comparison_mode": "sum_against_reported",
        "operand_roles": {"operands": "Komponen", "reported": "Total laporan"},
        "source_selectors": {
            "operands": [
                {"sheet_name": "Hasil", "row_label": "Komponen A", "column_label": "Nilai"},
                {"sheet_name": "Hasil", "row_label": "Komponen B", "column_label": "Nilai"},
            ],
            "reported": {"sheet_name": "Hasil", "row_label": "Total", "column_label": "Laporan"},
        },
        "context_by_role": {
            "operands": [_context(), _context()],
            "reported": _context(),
        },
        "absolute_tolerance": "0.5",
    })
    typed = NumericPolicyPayload.model_validate(payload)
    db, check, coverage, _, element = _setup(typed.model_dump(mode="json"))
    rows = [
        _evidence("10", "Nilai"),
        _evidence("15", "Nilai"),
        _evidence("30", "Laporan"),
    ]
    rows[0].content_original_json["row_label"] = "Komponen A"
    rows[1].content_original_json["row_label"] = "Komponen B"
    rows[2].content_original_json["row_label"] = "Total"
    numeric, evidence_ids, reason = _run_numeric_policy(
        db, run=SimpleNamespace(manifest_snapshot_json={}), check=check, coverage=coverage,
        element=element, policy_row=SimpleNamespace(
            id=element.numeric_policy_version_id, state="approved", payload_json=typed.model_dump(mode="json")
        ), evidence_items={row.id: row for row in rows}, locale=None,
    )
    assert reason is None
    assert numeric is not None and numeric.result_decimal_text == "25"
    assert numeric.difference_text == "5" and numeric.inconsistent is True
    assert set(evidence_ids) == {row.id for row in rows}

    duplicate = dict(payload)
    duplicate["source_selectors"] = {
        **payload["source_selectors"],
        "operands": [payload["source_selectors"]["operands"][0]] * 2,
    }
    with pytest.raises(ValueError, match="sum operand selectors must be unique"):
        NumericPolicyPayload.model_validate(duplicate)
