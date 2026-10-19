from __future__ import annotations

from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi import HTTPException

from align_api.file_validation import _zip_preflight, safe_display_name
from align_worker.parser_child import _field_candidates, _make_evidence, _make_unit


def _minimal_docx(path: str, extra: tuple[str, bytes] | None = None) -> None:
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("word/document.xml", b"<document/>")
        if extra is not None:
            archive.writestr(*extra)


def test_context_source_filename_is_normalized_and_xlsx_is_not_accepted() -> None:
    assert safe_display_name(r"C:\temp\KK final.pdf") == ("KK final.pdf", ".pdf")
    with pytest.raises(HTTPException) as error:
        safe_display_name("nilai.xlsx")
    assert error.value.status_code == 415


def test_docx_preflight_rejects_traversal_and_returns_expansion_inventory(tmp_path) -> None:
    valid = tmp_path / "valid.docx"
    _minimal_docx(str(valid))
    inventory = _zip_preflight(str(valid), 10_000, 536_870_912)
    assert inventory["zip_entries"] == 2
    assert inventory["expanded_bytes"] > 0

    invalid = tmp_path / "traversal.docx"
    _minimal_docx(str(invalid), ("word/../../payload.xml", b"<x/>"))
    with pytest.raises(HTTPException) as error:
        _zip_preflight(str(invalid), 10_000, 536_870_912)
    assert error.value.status_code == 415


def test_parser_field_candidates_keep_source_anchor_and_only_accept_labeled_values() -> None:
    unit = _make_unit("paragraph", {"format": "docx", "part": "word/document.xml", "item_index": 4})
    evidence = _make_evidence(
        unit,
        "paragraph",
        {"text": "Kode Mata Kuliah: IF101\nBahasa: id\nInstruksi bebas tanpa label"},
    )
    candidates = _field_candidates([evidence])
    assert [item["field_path"] for item in candidates] == ["course_code", "language"]
    assert all(item["anchor"] == unit["anchor"] for item in candidates)
