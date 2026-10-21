from __future__ import annotations

import math
import re
import stat
import zipfile
from pathlib import PurePath, PureWindowsPath
from typing import Any, BinaryIO
from xml.sax.handler import ContentHandler
from xml.sax.xmlreader import AttributesImpl

from defusedxml import sax
from fastapi import HTTPException

SUPPORTED_SUFFIXES = {".pdf", ".docx", ".jpg", ".jpeg", ".png"}
EXPECTED_MIME = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


def safe_display_name(filename: str | None) -> tuple[str, str]:
    raw = filename or "context-source"
    basename = PureWindowsPath(PurePath(raw.replace("\\", "/")).name).name
    basename = re.sub(r"[\x00-\x1f\x7f]", "", basename).strip().strip(".")
    if not basename:
        raise HTTPException(status_code=415, detail="unsupported file")
    suffix = PurePath(basename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(status_code=415, detail="unsupported file")
    safe_name = re.sub(r"[^A-Za-z0-9 ._()\-\u0080-\uffff]", "_", basename)[-255:]
    return safe_name, suffix


def _zip_preflight(path: str, max_entries: int, max_expanded_bytes: int) -> dict[str, int]:
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
    except (OSError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=415, detail="unsupported file") from exc
    if not entries or len(entries) > max_entries:
        raise HTTPException(status_code=413, detail="archive limits exceeded")
    expanded = 0
    names: list[str] = []
    unique_names: set[str] = set()
    for entry in entries:
        name = entry.filename.replace("\\", "/")
        parts = name.split("/")
        if name.startswith("/") or PureWindowsPath(name).drive or any(part in {".", ".."} for part in parts):
            raise HTTPException(status_code=415, detail="unsupported file")
        file_type = (entry.external_attr >> 16) & 0o170000
        if file_type == stat.S_IFLNK:
            raise HTTPException(status_code=415, detail="unsupported file")
        if entry.flag_bits & 0x1:
            raise HTTPException(status_code=415, detail="encrypted archives are unsupported")
        if name.casefold() in unique_names:
            raise HTTPException(status_code=415, detail="unsupported file")
        unique_names.add(name.casefold())
        expanded += entry.file_size
        if expanded > max_expanded_bytes:
            raise HTTPException(status_code=413, detail="archive limits exceeded")
        names.append(name)
    required = {"[Content_Types].xml", "word/document.xml"}
    if not required.issubset(names) or any("vbaproject" in name.casefold() for name in names):
        raise HTTPException(status_code=415, detail="unsupported file")
    actual_expanded = 0
    try:
        with zipfile.ZipFile(path) as archive:
            for entry in archive.infolist():
                actual_entry = 0
                with archive.open(entry) as stream:
                    while chunk := stream.read(64 * 1024):
                        actual_entry += len(chunk)
                        actual_expanded += len(chunk)
                        if actual_expanded > max_expanded_bytes:
                            raise HTTPException(status_code=413, detail="archive limits exceeded")
                if actual_entry != entry.file_size:
                    raise HTTPException(status_code=415, detail="unsupported file")
    except HTTPException:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise HTTPException(status_code=415, detail="unsupported file") from exc
    return {"zip_entries": len(entries), "expanded_bytes": actual_expanded}


def validate_context_file(path: str, filename: str | None, reported_mime: str | None, max_bytes: int, max_pixels: int, max_docx_nodes: int, max_docx_media: int, max_ooxml_entries: int, max_ooxml_expanded_bytes: int) -> tuple[str, str, dict[str, Any]]:
    import os

    safe_name, suffix = safe_display_name(filename)
    size = os.path.getsize(path)
    if size <= 0:
        raise HTTPException(status_code=422, detail="empty file")
    if size > max_bytes:
        raise HTTPException(status_code=413, detail="file limit exceeded")
    with open(path, "rb") as source:
        prefix = source.read(16)
    if suffix == ".pdf":
        if not prefix.startswith(b"%PDF-"):
            raise HTTPException(status_code=415, detail="unsupported file")
        try:
            from pypdf import PdfReader

            with open(path, "rb") as source:
                reader = PdfReader(source, strict=True)
                if reader.is_encrypted:
                    raise HTTPException(status_code=415, detail="encrypted PDFs are unsupported")
                pages = len(reader.pages)
                page_sizes = [
                    [float(page.mediabox.width), float(page.mediabox.height)] for page in reader.pages
                ] if pages <= 100 else []
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=415, detail="unsupported file") from exc
        if pages < 1 or pages > 100:
            raise HTTPException(status_code=413, detail="document page limit exceeded")
        if any(not math.isfinite(value) or value <= 0 for size in page_sizes for value in size):
            raise HTTPException(status_code=415, detail="invalid PDF geometry")
        detected_mime = EXPECTED_MIME[suffix]
        details: dict[str, Any] = {"pages": pages, "page_sizes": page_sizes, "parser_profile": "docling_native_v1"}
    elif suffix == ".docx":
        if prefix[:4] != b"PK\x03\x04":
            raise HTTPException(status_code=415, detail="unsupported file")
        archive_info = _zip_preflight(path, max_ooxml_entries, max_ooxml_expanded_bytes)
        try:
            node_count = 0
            class NodeCounter(ContentHandler):
                def startElement(self, _name: str, _attrs: AttributesImpl) -> None:
                    nonlocal node_count
                    node_count += 1
                    if node_count > max_docx_nodes:
                        raise HTTPException(status_code=413, detail="document node limit exceeded")
            counter = NodeCounter()
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                media_count = sum(name.startswith("word/media/") for name in names)
                media_parts = [name for name in names if name.startswith("word/media/") and not name.endswith("/")]
                content_parts = [
                    name for name in names
                    if name in {"word/document.xml", "word/footnotes.xml", "word/endnotes.xml", "word/comments.xml"}
                    or re.fullmatch(r"word/(header|footer)\d*\.xml", name)
                ]
                for part in content_parts:
                    with archive.open(part) as stream:
                        sax.parse(stream, counter, forbid_dtd=True, forbid_entities=True, forbid_external=True)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=415, detail="unsupported file") from exc
        if media_count > max_docx_media:
            raise HTTPException(status_code=413, detail="document media limit exceeded")
        detected_mime = EXPECTED_MIME[suffix]
        details = {
            **archive_info,
            "nodes": node_count,
            "media": media_count,
            "docx_parts": content_parts,
            "docx_media_parts": media_parts,
            "parser_profile": "native_ooxml_v1",
        }
    else:
        if suffix == ".png":
            if not prefix.startswith(b"\x89PNG\r\n\x1a\n"):
                raise HTTPException(status_code=415, detail="unsupported file")
        elif not prefix.startswith(b"\xff\xd8\xff"):
            raise HTTPException(status_code=415, detail="unsupported file")
        try:
            from PIL import Image

            with Image.open(path) as image:
                width, height = image.size
                if width <= 0 or height <= 0 or width * height > max_pixels:
                    raise HTTPException(status_code=413, detail="image pixel limit exceeded")
                image.verify()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=415, detail="unsupported file") from exc
        if width <= 0 or height <= 0 or width * height > max_pixels:
            raise HTTPException(status_code=413, detail="image pixel limit exceeded")
        detected_mime = EXPECTED_MIME[suffix]
        details = {"width": width, "height": height, "pixels": width * height, "parser_profile": "docling_native_v1"}

    if reported_mime and reported_mime.lower() not in {detected_mime, "application/octet-stream"}:
        raise HTTPException(status_code=415, detail="file content type does not match signature")
    details["size_bytes"] = size
    details["mime"] = detected_mime
    return safe_name, detected_mime, details


def sha256_file(source: BinaryIO) -> str:
    import hashlib

    digest = hashlib.sha256()
    while chunk := source.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()
