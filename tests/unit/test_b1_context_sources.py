from __future__ import annotations

from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi import HTTPException

from align_api.config import Settings
from align_api.file_validation import _zip_preflight, safe_display_name, validate_context_file
from align_worker.parser_child import _field_candidates, _make_evidence, _make_unit


def validate_source_file(path: str, name: str, mime: str):
    settings = Settings(_env_file=None)
    return validate_context_file(path, name, mime, settings.max_file_bytes, settings.max_image_pixels,
                                 settings.max_docx_nodes, settings.max_docx_media, settings.max_ooxml_entries,
                                 settings.max_ooxml_expanded_bytes)


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


def test_encrypted_and_over_page_limit_pdf_are_rejected(tmp_path) -> None:
    from pypdf import PdfWriter
    encrypted = tmp_path / "encrypted.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt("synthetic-encryption")
    writer.write(encrypted)
    with pytest.raises(HTTPException) as error:
        validate_source_file(str(encrypted), encrypted.name, "application/pdf")
    assert error.value.status_code == 415
    oversized = tmp_path / "pages.pdf"
    writer = PdfWriter()
    for _ in range(101):
        writer.add_blank_page(width=612, height=792)
    writer.write(oversized)
    with pytest.raises(HTTPException) as error:
        validate_source_file(str(oversized), oversized.name, "application/pdf")
    assert error.value.status_code == 413


def test_image_pixel_limit_precedes_decoding_and_mime_spoof_is_rejected(tmp_path) -> None:
    import struct
    import zlib

    from PIL import Image
    source = tmp_path / "pixel-bomb.png"
    Image.new("RGB", (1, 1)).save(source)
    payload = bytearray(source.read_bytes())
    payload[16:24] = struct.pack(">II", 5001, 5000)
    payload[29:33] = struct.pack(">I", zlib.crc32(payload[12:29]))
    source.write_bytes(payload)
    with pytest.raises(HTTPException) as error:
        validate_source_file(str(source), source.name, "image/png")
    assert error.value.status_code == 413
    Image.new("RGB", (1, 1)).save(source)
    with pytest.raises(HTTPException) as error:
        validate_source_file(str(source), source.name, "application/pdf")
    assert error.value.status_code == 415
