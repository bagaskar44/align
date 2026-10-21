from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

MAX_OUTPUT_BYTES = 64 * 1024 * 1024
MAX_EVIDENCE_ITEMS = 20_000
MAX_XLSX_EVIDENCE_ITEMS = 100_000
_FIELD_PATTERNS = {
    "course_code": re.compile(r"(?im)^\s*(?:kode\s*(?:mata\s*)?kuliah|kode\s*mk|course[_ ]code)\s*[:=\-]\s*(\S[^\r\n]{0,63})\s*$"),
    "course_title": re.compile(r"(?im)^\s*(?:nama\s*(?:mata\s*)?kuliah|judul\s*kuliah|course[_ ]title)\s*[:=\-]\s*(\S[^\r\n]{0,239})\s*$"),
    "context_description": re.compile(r"(?im)^\s*(?:deskripsi\s*konteks|konteks\s*mata\s*kuliah|context[_ ]description)\s*[:=\-]\s*(\S[^\r\n]{0,3999})\s*$"),
    "number_locale": re.compile(r"(?im)^\s*(?:number[_ ]locale|locale\s*angka)\s*[:=\-]\s*(\S[^\r\n]{0,39})\s*$"),
    "language": re.compile(r"(?im)^\s*(?:bahasa|language)\s*[:=\-]\s*(\S[^\r\n]{0,39})\s*$"),
}


def _anchor_key(anchor: dict[str, Any]) -> str:
    encoded = json.dumps(anchor, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(64 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_docling_resources() -> None:
    config = json.loads(Path(os.environ.get("ALIGN_PARSER_RESOURCE_CONFIG", "/app/config/parser-context-resources.json")).read_text())
    root = Path(os.environ.get("DOCLING_ARTIFACTS_PATH", "/opt/docling-models"))
    for model in config["models"]:
        for item in model["files"]:
            path = root / model["repo_id"].replace("/", "--") / item["path"]
            if not path.is_file() or _file_hash(path) != item["sha256"]:
                raise ModuleNotFoundError("pinned parser model resource unavailable")
    for item in config["ocr"].get("artifacts", []):
        if _file_hash(Path(item["path"])) != item["sha256"]:
            raise ModuleNotFoundError("pinned OCR resource unavailable")


def _make_unit(unit_type: str, anchor: dict[str, Any], status: str = "parsed", limitations: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {
        "unit_key": _anchor_key(anchor),
        "unit_type": unit_type,
        "anchor": anchor,
        "parse_status": status,
        "limitations": limitations or [],
    }


def _make_evidence(unit: dict[str, Any], kind: str, content: dict[str, Any], status: str = "parsed") -> dict[str, Any]:
    return {
        "unit_key": unit["unit_key"],
        "type": kind,
        "anchor": unit["anchor"],
        "content": content,
        "parse_status": status,
        "limitations": unit["limitations"],
    }


def _field_candidates(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for item in evidence:
        text = item.get("content", {}).get("text")
        if not isinstance(text, str):
            continue
        for field_path, pattern in _FIELD_PATTERNS.items():
            for match in pattern.finditer(text):
                value = match.group(1).strip()
                if value:
                    found.append(
                        {
                            "field_path": field_path,
                            "value": value,
                            "unit_key": item["unit_key"],
                            "anchor": item["anchor"],
                        }
                    )
    return found


def _parse_docx(path: Path) -> dict[str, Any]:
    started = time.perf_counter()
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(str(path))
    root_anchor = {"format": "file", "locator_note": "DOCX package root"}
    root = _make_unit("root", root_anchor)
    units: list[dict[str, Any]] = [root]
    evidence: list[dict[str, Any]] = []
    limitations: list[dict[str, str]] = []
    part = "word/document.xml"
    container_anchor = {"format": "docx", "part": part, "structural_path": "/word/document.xml", "item_kind": "media", "item_index": 0}
    container = _make_unit("container", container_anchor)
    units.append(container)
    paragraph_index = 0
    table_index = 0

    def parse_table(table: Any, table_path: str, index: int) -> None:
        nonlocal units, evidence
        anchor = {"format": "docx", "part": part, "structural_path": table_path, "item_kind": "table", "item_index": index}
        unit = _make_unit("table", anchor)
        units.append(unit)
        rows: list[list[str]] = []
        for row in table.rows:
            rows.append([cell.text for cell in row.cells])
        evidence.append(_make_evidence(unit, "table", {"rows": rows, "text": "\n".join(" | ".join(row) for row in rows)}))
        for row_index, row in enumerate(table.rows, start=1):
            for cell_index, cell in enumerate(row.cells, start=1):
                cell_path = f"{table_path}/w:tr[{row_index}]/w:tc[{cell_index}]"
                cell_anchor = {"format": "docx", "part": part, "structural_path": cell_path,
                               "item_kind": "cell", "item_index": (row_index - 1) * max(1, len(row.cells)) + cell_index}
                cell_unit = _make_unit("cell", cell_anchor)
                units.append(cell_unit)
                evidence.append(_make_evidence(cell_unit, "table", {"text": cell.text, "row": row_index,
                    "column": cell_index, "table_path": table_path, "rows": len(table.rows),
                    "columns": len(row.cells)}))
                for nested_index, nested in enumerate(cell.tables, start=1):
                    nested_path = f"{cell_path}/w:tbl[{nested_index}]"
                    parse_table(nested, nested_path, nested_index)

    for block_index, child in enumerate(document.element.body.iterchildren(), start=1):
        if child.tag == qn("w:p"):
            paragraph_index += 1
            path_name = f"/word/document.xml/w:document/w:body/w:p[{paragraph_index}]"
            anchor = {"format": "docx", "part": part, "structural_path": path_name, "item_kind": "paragraph", "item_index": paragraph_index}
            unit = _make_unit("paragraph", anchor)
            units.append(unit)
            paragraph = Paragraph(child, document)
            text = "".join(child.xpath(".//w:t/text()"))
            revision_marked = bool(child.xpath(".//w:ins | .//w:del"))
            revision_limit = [{"code": "revision_marks_present", "message": "Inserted text is included; deleted text is excluded. Revision intent still requires verification."}] if revision_marked else []
            if revision_marked:
                unit["parse_status"] = "partially_parsed"
                unit["limitations"] = revision_limit
                limitations.extend(revision_limit)
            evidence.append(_make_evidence(unit, "paragraph", {"text": text, "style": paragraph.style.name if paragraph.style else None}, unit["parse_status"]))
        elif child.tag == qn("w:tbl"):
            table_index += 1
            parse_table(Table(child, document), f"/word/document.xml/w:document/w:body/w:tbl[{table_index}]", table_index)
        elif child.tag in {qn("w:sectPr"), qn("w:bookmarkStart"), qn("w:bookmarkEnd")}:
            continue
        else:
            anchor = {"format": "docx", "part": part, "structural_path": f"/word/document.xml/w:document/w:body/node[{block_index}]", "item_kind": "media", "item_index": block_index}
            reason = {"code": "unsupported_document_node", "message": "A document node is inventoried but is not interpreted."}
            units.append(_make_unit("unsupported", anchor, "unsupported", [reason]))
            limitations.append(reason)

    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        content_parts = [
            name for name in names
            if name in {"word/document.xml", "word/footnotes.xml", "word/endnotes.xml", "word/comments.xml"}
            or re.fullmatch(r"word/(header|footer)\d*\.xml", name)
        ]
        for index, name in enumerate(content_parts, start=1):
            if name == part:
                continue
            anchor = {"format": "docx", "part": name, "structural_path": f"/{name}", "item_kind": "media", "item_index": index}
            content_container = _make_unit("container", anchor)
            units.append(content_container)
            if name.startswith(("word/header", "word/footer")) or name in {"word/footnotes.xml", "word/endnotes.xml"}:
                from defusedxml import ElementTree

                with archive.open(name) as xml_stream:
                    part_root = ElementTree.parse(xml_stream).getroot()
                paragraphs = part_root.findall(".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p")
                for paragraph_index, paragraph_node in enumerate(paragraphs, start=1):
                    structural = f"/{name}/w:p[{paragraph_index}]"
                    paragraph_anchor = {"format": "docx", "part": name, "structural_path": structural,
                                        "item_kind": "paragraph", "item_index": paragraph_index}
                    paragraph_unit = _make_unit("paragraph", paragraph_anchor)
                    units.append(paragraph_unit)
                    text = "".join(item.text or "" for item in paragraph_node.iter()
                                   if item.tag == "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")
                    evidence.append(_make_evidence(paragraph_unit, "paragraph", {"text": text, "part_role": name.split("/")[-1].split(".")[0]}))
                if not paragraphs:
                    content_container["parse_status"] = "partially_parsed"
                    reason = {"code": "empty_or_unsupported_ooxml_part", "message": "OOXML part terinventaris tetapi tidak memiliki paragraph yang dapat diekstrak."}
                    content_container["limitations"].append(reason)
                    limitations.append(reason)
            else:
                reason = {"code": "unsupported_ooxml_part", "message": "OOXML part ini disimpan dalam inventory tetapi tidak ditafsirkan."}
                content_container["parse_status"] = "unsupported"
                content_container["limitations"] = [reason]
                limitations.append(reason)
        media_parts = [name for name in names if name.startswith("word/media/") and not name.endswith("/")]
        for index, name in enumerate(media_parts, start=1):
            anchor = {"format": "docx", "part": name, "structural_path": f"/{name}", "item_kind": "media", "item_index": index}
            reason = {"code": "embedded_media_not_interpreted", "message": "Embedded media is inventoried and awaits visual interpretation."}
            units.append(_make_unit("media", anchor, "unsupported", [reason]))
            limitations.append(reason)
        handled_parts = set(content_parts) | set(media_parts)
        for index, name in enumerate(names, start=1):
            if name in handled_parts:
                continue
            anchor = {"format": "docx", "part": name, "structural_path": f"/{name}",
                      "item_kind": "media", "item_index": index}
            reason = {"code": "ooxml_package_part_unsupported",
                      "message": "Bagian package OOXML tercatat tetapi tidak ditafsirkan oleh profile ini."}
            units.append(_make_unit("unsupported", anchor, "unsupported", [reason]))
            limitations.append(reason)
        textbox_found = False
        revision_found = False
        with archive.open(part) as stream:
            carry = b""
            while chunk := stream.read(64 * 1024):
                content = carry + chunk
                textbox_found = textbox_found or b"w:txbxContent" in content
                revision_found = revision_found or b"<w:ins" in content or b"<w:del" in content
                carry = content[-32:]
        if textbox_found:
            anchor = {"format": "docx", "part": part, "structural_path": "/word/document.xml//w:txbxContent", "item_kind": "media", "item_index": 1}
            reason = {"code": "textbox_unavailable", "message": "Text-box contents are not interpreted by this parser profile."}
            units.append(_make_unit("unsupported", anchor, "partially_parsed", [reason]))
            limitations.append(reason)
        if revision_found:
            anchor = {"format": "docx", "part": part, "structural_path": "/word/document.xml//w:ins|w:del", "item_kind": "media", "item_index": 1}
            reason = {"code": "revision_marks_present", "message": "Revision-marked text needs lecturer verification."}
            units.append(_make_unit("unsupported", anchor, "partially_parsed", [reason]))
            limitations.append(reason)
    if len(evidence) > MAX_EVIDENCE_ITEMS:
        raise ValueError("document node limit exceeded")
    if limitations:
        root["parse_status"] = "partially_parsed"
        root["limitations"] = limitations
    return {
        "format": "docx",
        "parse_status": "partially_parsed" if limitations else "parsed",
        "units": units,
        "evidence": evidence,
        "candidate_fields": _field_candidates(evidence),
        "limitations": limitations,
        "parser": "python-docx+ooxml",
        "stage_timings_ms": {"docx_ooxml_extraction_inventory": max(0.0, round((time.perf_counter() - started) * 1000, 3))},
    }


def _parse_xlsx(path: Path) -> dict[str, Any]:
    started = time.perf_counter()
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    root_anchor = {"format": "file", "locator_note": "XLSX package root"}
    root = _make_unit("root", root_anchor)
    units: list[dict[str, Any]] = [root]
    evidence: list[dict[str, Any]] = []
    limitations: list[dict[str, str]] = []
    formula_count = 0
    cache_missing = 0
    try:
        formula_book = load_workbook(path, data_only=False, read_only=False, keep_links=False)
        cached_book = load_workbook(path, data_only=True, read_only=False, keep_links=False)
    except Exception as exc:
        raise ValueError("XLSX workbook could not be opened safely") from exc
    try:
        if len(formula_book.worksheets) > 25:
            raise ValueError("spreadsheet sheet limit exceeded")
        for _sheet_order, sheet in enumerate(formula_book.worksheets, start=1):
            cached_sheet = cached_book[sheet.title]
            sheet_anchor = {"format": "xlsx", "sheet_name": sheet.title, "cell_range": "A1"}
            sheet_unit = _make_unit("container", sheet_anchor)
            units.append(sheet_unit)
            hidden = sheet.sheet_state != "visible"
            if hidden:
                sheet_unit["limitations"] = [{"code": "hidden_sheet", "message": "Sheet disembunyikan pada workbook asli; isi tetap diinventaris."}]
                sheet_unit["parse_status"] = "partially_parsed"
                limitations.extend(sheet_unit["limitations"])
            merged_by_cell: dict[str, str] = {}
            for merged in sheet.merged_cells.ranges:
                merged_text = str(merged)
                for row in sheet[merged_text]:
                    for cell in row:
                        merged_by_cell[cell.coordinate] = merged_text
            nonempty = 0
            for row_index, row in enumerate(sheet.iter_rows(), start=1):
                for col_index, cell in enumerate(row, start=1):
                    value = cell.value
                    if value is None:
                        continue
                    nonempty += 1
                    if nonempty > 100_000:
                        raise ValueError("spreadsheet cell limit exceeded")
                    formula = value if isinstance(value, str) and value.startswith("=") else None
                    cached = cached_sheet[cell.coordinate].value if formula is not None else None
                    if formula is not None:
                        formula_count += 1
                        if cached is None:
                            cache_missing += 1
                    def stable(item: Any) -> Any:
                        if item is None or isinstance(item, (str, int, float, bool)):
                            return item
                        if hasattr(item, "isoformat"):
                            return item.isoformat()
                        return str(item)
                    header = sheet.cell(1, col_index).value if row_index > 1 else value
                    row_label = sheet.cell(row_index, 1).value if col_index > 1 else value
                    content = {
                        "text": str(value),
                        "value": stable(value),
                        "data_type": cell.data_type,
                        "formula": formula,
                        "cached_value": stable(cached),
                        "cache_verified": False if formula is not None else None,
                        "number_format": cell.number_format,
                        "row": row_index,
                        "column": col_index,
                        "row_label": stable(row_label),
                        "column_label": stable(header) if header is not None else get_column_letter(col_index),
                        "merged_range": merged_by_cell.get(cell.coordinate),
                        "hidden_sheet": hidden,
                    }
                    anchor = {"format": "xlsx", "sheet_name": sheet.title, "cell_range": cell.coordinate}
                    unit = _make_unit("cell", anchor, "partially_parsed" if formula is not None and cached is None else "parsed",
                                      [{"code": "formula_cache_unverified", "message": "Nilai cache formula tidak diverifikasi; formula tidak dieksekusi."}] if formula is not None else [])
                    units.append(unit)
                    evidence.append(_make_evidence(unit, "spreadsheet_cell", content, unit["parse_status"]))
                    if len(evidence) >= MAX_XLSX_EVIDENCE_ITEMS:
                        raise ValueError("spreadsheet cell limit exceeded")
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
        visuals = {name for name in names if name.startswith(("xl/charts/", "xl/drawings/", "xl/media/", "xl/embeddings/"))}
        external_parts = [name for name in names if name.startswith("xl/externalLinks/")]
        worksheets = {name for name in names if name.startswith("xl/worksheets/") and name.endswith(".xml")}
        recognized_parts = {
            "[Content_Types].xml", "_rels/.rels", "docProps/app.xml", "docProps/core.xml",
            "xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/styles.xml",
            "xl/sharedStrings.xml", "xl/calcChain.xml",
        } | worksheets
        unsupported_parts = [name for name in names if name not in recognized_parts]
        for _index, part in enumerate(unsupported_parts, start=1):
            sheet_name = formula_book.sheetnames[0] if formula_book.sheetnames else "Sheet1"
            anchor = {"format": "xlsx", "sheet_name": sheet_name, "cell_range": "A1", "part": part}
            if part in visuals:
                reason = {"code": "spreadsheet_visual_unsupported", "message": "Diagram/chart/media workbook tercatat tetapi tidak ditafsirkan."}
            elif part in external_parts:
                reason = {"code": "external_references_not_followed", "message": "External workbook references dicatat tanpa diikuti."}
            else:
                reason = {"code": "spreadsheet_ooxml_part_unsupported", "message": "Bagian package OOXML spreadsheet tercatat tetapi tidak ditafsirkan."}
            unit = _make_unit("unsupported", anchor, "unsupported", [reason])
            units.append(unit)
            limitations.append(reason)
        if external_parts:
            limitation = {"code": "external_references_not_followed", "message": "External workbook references dicatat tanpa diikuti."}
            limitations.append(limitation)
        if formula_count:
            limitations.append({"code": "formula_cache_unverified", "message": f"{cache_missing} dari {formula_count} formula tidak memiliki nilai cache; tidak ada formula yang dieksekusi."})
    finally:
        formula_book.close()
        cached_book.close()
    if limitations:
        root["parse_status"] = "partially_parsed"
        root["limitations"] = limitations
    return {
        "format": "xlsx",
        "parse_status": "partially_parsed" if limitations else "parsed",
        "units": units,
        "evidence": evidence,
        "candidate_fields": [],
        "limitations": limitations,
        "parser": "openpyxl-ooxml-v1",
        "covered_stages": ["metadata_inventory", "sheet_inventory", "cell_extraction", "formula_cache_pair"],
        "formula_count": formula_count,
        "external_reference_parts": external_parts,
        "stage_timings_ms": {"xlsx_ooxml_extraction_inventory": max(0.0, round((time.perf_counter() - started) * 1000, 3))},
    }


def _parse_docling(path: Path, suffix: str, page: int | None = None) -> dict[str, Any]:
    runtime_setup_started = time.perf_counter()
    _verify_docling_resources()
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractCliOcrOptions
    from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption
    stage_timings: dict[str, float] = {
        "resource_verification_and_runtime_setup": max(0.0, round((time.perf_counter() - runtime_setup_started) * 1000, 3)),
    }
    metadata_started = time.perf_counter()

    root_format = "pdf" if suffix == ".pdf" else "image"
    root_anchor = {"format": "file", "locator_note": f"{root_format.upper()} root"}
    root = _make_unit("root", root_anchor)
    units = [root]
    evidence: list[dict[str, Any]] = []
    limitations: list[dict[str, str]] = []
    original_dimensions: tuple[int, int] | None = None
    orientation = 1
    ocr_required = True
    transforms: list[dict[str, Any]] = []
    if suffix == ".pdf":
        from pypdf import PdfReader

        with path.open("rb") as source:
            reader = PdfReader(source, strict=True)
            page_count = len(reader.pages)
            if page:
                ocr_required = not bool((reader.pages[page - 1].extract_text() or "").strip())
        if page is not None and not 1 <= page <= page_count:
            raise ValueError("PDF page outside validated inventory")
        selected_pages = [page] if page else list(range(1, page_count + 1))
        for page_no in selected_pages:
            units.append(_make_unit("page", {"format": "pdf", "page": page_no, "locator_note": "Page inventory; exact geometry populated after parsing"}, "parsed"))
        allowed = [InputFormat.PDF]
    else:
        from PIL import Image, ImageOps
        from PIL import __version__ as pillow_version

        with Image.open(path) as image:
            width, height = image.size
            orientation = int(image.getexif().get(274, 1))
            if orientation not in range(1, 9):
                raise ValueError("invalid image orientation")
            if orientation != 1:
                transform = {"parent_sha256": _file_hash(path), "orientation": orientation,
                             "tool": "Pillow", "version": pillow_version, "operation": "exif_transpose"}
                transform["id"] = _anchor_key(transform)
                transforms.append(transform)
                with ImageOps.exif_transpose(image) as oriented:
                    derivative = Path(tempfile.gettempdir()) / f"align-oriented-{transform['id']}.png"
                    oriented.save(derivative)
                    transform["derivative_sha256"] = _file_hash(derivative)
                path = derivative
        original_dimensions = (width, height)
        units.append(_make_unit("image", {"format": "image", "width": width, "height": height, "bbox": [0, 0, width, height], "coordinate_system": "original_pixels_top_left", "transform_id": None}, "parsed"))
        page_count = 1
        allowed = [InputFormat.IMAGE]
    artifacts_path = os.environ.get("DOCLING_ARTIFACTS_PATH", "/opt/docling-models")
    pipeline_options = PdfPipelineOptions(artifacts_path=artifacts_path, allow_external_plugins=False, enable_remote_services=False)
    pipeline_options.do_ocr = ocr_required
    pipeline_options.ocr_options = TesseractCliOcrOptions(lang=["ind", "eng"], force_full_page_ocr=ocr_required)
    format_options: dict[Any, Any] = {}
    if suffix == ".pdf":
        format_options[InputFormat.PDF] = PdfFormatOption(pipeline_options=pipeline_options)
    else:
        format_options[InputFormat.IMAGE] = ImageFormatOption(pipeline_options=pipeline_options)
    converter = DocumentConverter(allowed_formats=allowed, format_options=format_options or None)
    stage_timings["source_metadata_inventory"] = max(0.0, round((time.perf_counter() - metadata_started) * 1000, 3))
    pipeline_started = time.perf_counter()
    result = converter.convert(str(path), **({"page_range": (page, page)} if page else {}))
    stage_timings["docling_pipeline_conversion"] = max(0.0, round((time.perf_counter() - pipeline_started) * 1000, 3))
    assembly_started = time.perf_counter()
    document = result.document
    if document is None:
        raise ValueError("Docling did not produce a document")
    pages = document.pages
    page_success: dict[int, bool] = {number: False for number in (selected_pages if suffix == ".pdf" else [1])}
    page_sizes: dict[int, tuple[float, float]] = {}
    for number, source_page in pages.items():
        size = source_page.size
        page_sizes[int(number)] = (float(size.width), float(size.height))

    items: list[tuple[Any, str]] = [(item, "image_text" if ocr_required else "paragraph") for item in document.texts]
    items.extend((item, "table") for item in document.tables)
    items.extend((item, "diagram") for item in document.pictures)
    for index, (item, kind) in enumerate(items, start=1):
        content: dict[str, Any]
        if kind in {"paragraph", "image_text"}:
            text = str(getattr(item, "text", ""))
            content = {"text": text, "ocr_unverified": ocr_required}
        elif kind == "table":
            text = str(item.export_to_markdown(document))
            content = {"text": text, "format": "markdown"}
        else:
            text = str(item.caption_text(document))
            content = {"text": text, "visual_caption": True}
        provenance = item.prov[0] if getattr(item, "prov", None) else None
        if provenance is None:
            locator: dict[str, Any] = {"format": "file", "locator_note": f"Docling item {index} without source geometry"}
            limitation = {"code": "source_geometry_unavailable", "message": "The parser did not provide a verified source anchor for this item."}
            unit = _make_unit(kind, locator, "partially_parsed", [limitation])
            limitations.append(limitation)
        else:
            page_no = int(provenance.page_no)
            page_size = page_sizes.get(page_no)
            bbox = provenance.bbox
            if page_size is not None:
                bbox = bbox.to_top_left_origin(page_height=page_size[1])
            left, top, right, bottom = (float(value) for value in bbox.as_tuple())
            if not all(map(lambda value: value == value and abs(value) != float("inf"), (left, top, right, bottom))) or left < 0 or top < 0 or right < left or bottom < top or page_size is None or right > page_size[0] + 0.1 or bottom > page_size[1] + 0.1:
                locator = {"format": "file", "locator_note": f"Docling item {index} page {page_no}; geometry outside verified bounds"}
                limitation = {"code": "source_geometry_invalid", "message": "The reported parser geometry is outside the validated source bounds."}
                unit = _make_unit(kind, locator, "partially_parsed", [limitation])
                limitations.append(limitation)
            elif suffix == ".pdf":
                source_item_id = str(getattr(item, "self_ref", f"item_{index}"))
                locator = {"format": "pdf", "page": page_no, "bbox": [left, top, right, bottom], "coordinate_system": "pdf_points_top_left", "source_item_id": source_item_id}
                unit = _make_unit(kind, locator)
            else:
                assert original_dimensions is not None
                expected_size = original_dimensions[::-1] if orientation in {5, 6, 7, 8} else original_dimensions
                if page_size != expected_size:
                    locator = {
                        "format": "image",
                        "width": original_dimensions[0],
                        "height": original_dimensions[1],
                        "bbox": [0, 0, original_dimensions[0], original_dimensions[1]],
                        "coordinate_system": "original_pixels_top_left",
                        "transform_id": None,
                    }
                    limitation = {"code": "image_transform_not_verified", "message": "Parser geometry did not match original image dimensions; this item uses a whole-image anchor."}
                    unit = _make_unit(kind, locator, "partially_parsed", [limitation])
                    limitations.append(limitation)
                else:
                    w, h = original_dimensions
                    def original_point(x: float, y: float, w: int = w, h: int = h) -> tuple[float, float]:
                        return {1: (x, y), 2: (w-x, y), 3: (w-x, h-y), 4: (x, h-y),
                                5: (y, x), 6: (y, h-x), 7: (w-y, h-x), 8: (w-y, x)}[orientation]
                    corners = [original_point(x, y) for x, y in ((left, top), (right, bottom), (left, bottom), (right, top))]
                    original_box = [min(x for x, _ in corners), min(y for _, y in corners), max(x for x, _ in corners), max(y for _, y in corners)]
                    locator = {"format": "image", "width": w, "height": h, "bbox": original_box, "coordinate_system": "original_pixels_top_left", "transform_id": transforms[0]["id"] if transforms else None}
                    unit = _make_unit(kind, locator)
            if page_no in page_success:
                page_success[page_no] = True
        units.append(unit)
        if kind == "diagram" and not text.strip():
            limitation = {"code": "visual_content_not_interpreted", "message": "Region visual tercatat; isi visual memerlukan peninjauan manual."}
            unit["parse_status"] = "partially_parsed"
            unit["limitations"].append(limitation)
            limitations.append(limitation)
        evidence.append(_make_evidence(unit, kind, content, unit["parse_status"]))
    if suffix == ".pdf":
        for unit in units[1:]:
            if unit["unit_type"] == "page" and not page_success.get(int(unit["anchor"]["page"]), False):
                unit["parse_status"] = "partially_parsed"
                unit["limitations"].append({"code": "page_without_extracted_items", "message": "The page is inventoried but no parser item was returned."})
        parsed_pages = sum(page_success.values())
        if parsed_pages < len(page_success):
            limitations.append({"code": "unresolved_pages", "message": f"{len(page_success) - parsed_pages} source pages have no extracted item."})
    if len(evidence) > MAX_EVIDENCE_ITEMS:
        raise ValueError("document node limit exceeded")
    if limitations:
        root["parse_status"] = "partially_parsed"
        root["limitations"] = limitations
    stage_timings["anchor_and_evidence_assembly"] = max(0.0, round((time.perf_counter() - assembly_started) * 1000, 3))
    return {
        "format": root_format,
        "parse_status": "partially_parsed" if limitations else "parsed",
        "units": units,
        "evidence": evidence,
        "candidate_fields": _field_candidates(evidence),
        "limitations": limitations,
        "parser": "docling",
        "transforms": transforms,
        "covered_stages": ["metadata_inventory", "render", "layout", "text", "ocr" if ocr_required else "native_text", "table_extraction", "assembly_page"],
        "ocr_decision": {"rule": "native_text_nonempty_or_full_page_v1", "full_page_ocr": ocr_required},
        "stage_timings_ms": stage_timings,
    }


def parse_file(path: str, filename: str, page: int | None = None) -> dict[str, Any]:
    source = Path(path)
    suffix = Path(filename).suffix.lower()
    if suffix == ".docx":
        return _parse_docx(source)
    if suffix == ".xlsx":
        return _parse_xlsx(source)
    if suffix in {".pdf", ".jpg", ".jpeg", ".png"}:
        return _parse_docling(source, suffix, page)
    raise ValueError("unsupported file format")


def main() -> None:
    if len(sys.argv) not in {4, 5}:
        raise SystemExit("usage: parser_child PATH FILENAME OUTPUT_JSON [PDF_PAGE]")
    try:
        result = parse_file(sys.argv[1], sys.argv[2], int(sys.argv[4]) if len(sys.argv) == 5 else None)
    except Exception as exc:
        module_name = type(exc).__module__
        is_resource_error = isinstance(exc, ModuleNotFoundError) or module_name.startswith(
            ("huggingface_hub", "requests", "urllib3", "httpx")
        )
        result = {
            "failure_code": "parser_resource_unavailable" if is_resource_error else "parser_failed",
            "format": Path(sys.argv[2]).suffix.lower().lstrip("."),
            "units": [],
            "evidence": [],
            "candidate_fields": [],
            "limitations": [
                {
                    "code": "parser_resource_unavailable" if is_resource_error else "parser_failed",
                    "message": "The parser did not produce a source artifact; review the original or use manual context entry.",
                }
            ],
        }
    output = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(output.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise SystemExit("parser output exceeds the approved limit")
    Path(sys.argv[3]).write_text(output, encoding="utf-8")


if __name__ == "__main__":
    main()
