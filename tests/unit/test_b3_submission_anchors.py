from __future__ import annotations

from types import SimpleNamespace

import pytest

from align_api.submissions import _verify_source_anchor, _xlsx_range_within_bounds


def asset(preflight: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(preflight_json=preflight)


def test_pdf_and_image_anchors_are_finite_and_within_original_bounds() -> None:
    pdf = asset({"pages": 2, "page_sizes": [[612.0, 792.0], [612.0, 792.0]]})
    assert _verify_source_anchor({"format": "pdf", "page": 2, "bbox": [10, 20, 30, 40]}, pdf)
    assert not _verify_source_anchor({"format": "pdf", "page": 3, "bbox": [10, 20, 30, 40]}, pdf)
    assert not _verify_source_anchor({"format": "pdf", "page": 1, "bbox": [0, 0, float("nan"), 10]}, pdf)
    assert _verify_source_anchor({"format": "pdf", "page": 1, "locator_note": "page inventory"}, pdf)

    image = asset({"width": 1000, "height": 600})
    assert _verify_source_anchor(
        {"format": "image", "width": 1000, "height": 600, "bbox": [0, 0, 10, 10]}, image
    )
    assert not _verify_source_anchor(
        {"format": "image", "width": 1000, "height": 600, "bbox": [0, 0, 1001, 10]}, image
    )


def test_file_docx_and_xlsx_anchors_are_structural_and_bounded() -> None:
    docx = asset({
        "docx_parts": ["word/document.xml"], "docx_media_parts": ["word/media/image1.png"],
        "docx_package_parts": ["word/document.xml", "word/media/image1.png"],
    })
    assert _verify_source_anchor({"format": "file", "locator_note": "DOCX package root"}, docx)
    assert not _verify_source_anchor({"format": "file", "locator_note": "  "}, docx)
    assert _verify_source_anchor(
        {"format": "docx", "part": "word/document.xml", "structural_path": "/word/document.xml/w:p[1]",
         "item_kind": "paragraph", "item_index": 1}, docx
    )
    assert not _verify_source_anchor(
        {"format": "docx", "part": "word/document.xml", "structural_path": "/../secret",
         "item_kind": "paragraph", "item_index": 1}, docx
    )
    assert not _verify_source_anchor(
        {"format": "docx", "part": "word/document.xml", "structural_path": "/word/document.xml/w:p[1]",
         "item_kind": "paragraph", "item_index": True}, docx
    )

    xlsx = asset({"sheets": [{"name": "Data"}, {"name": "Hidden", "state": "hidden"}]})
    assert _verify_source_anchor({"format": "xlsx", "sheet_name": "Hidden", "cell_range": "A1:XFD1048576"}, xlsx)
    assert not _verify_source_anchor({"format": "xlsx", "sheet_name": "Hidden", "cell_range": "XFE1"}, xlsx)
    assert not _verify_source_anchor({"format": "xlsx", "sheet_name": "Missing", "cell_range": "A1"}, xlsx)


@pytest.mark.parametrize("value", ["A0", "A1:B0", "B2:A1", "XFE1", "A1048577", "A1:ZZZZ1", "A1\n"])
def test_xlsx_range_rejects_invalid_reversed_or_out_of_bounds_addresses(value: str) -> None:
    assert not _xlsx_range_within_bounds(value)

