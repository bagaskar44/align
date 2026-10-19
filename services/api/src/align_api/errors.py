from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("align.api")


def error_payload(request: Request, code: str, message: str, field_errors: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "field_errors": field_errors or [],
            "request_id": str(getattr(request.state, "request_id", uuid.uuid4())),
        }
    }


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def request_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        safe_errors = [
            {"field": ".".join(str(part) for part in item.get("loc", ())), "code": str(item.get("type", "invalid"))}
            for item in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=error_payload(request, "VALIDATION_ERROR", "Permintaan tidak valid.", safe_errors),
            headers={"Cache-Control": "private, no-store"},
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code, message = _http_error_copy(exc.status_code)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(request, code, message),
            headers={"Cache-Control": "private, no-store"},
        )

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        logger.error(
            "unhandled_request_error",
            extra={"request_id": str(request_id), "error_type": type(exc).__name__},
        )
        return JSONResponse(
            status_code=500,
            content=error_payload(request, "INTERNAL_ERROR", "Terjadi kesalahan internal."),
            headers={"Cache-Control": "private, no-store"},
        )


def _http_error_copy(status: int) -> tuple[str, str]:
    return {
        400: ("INVALID_REQUEST", "Permintaan tidak dapat diproses."),
        401: ("UNAUTHENTICATED", "Sesi tidak valid atau telah berakhir."),
        403: ("FORBIDDEN", "Aksi tidak diizinkan."),
        404: ("NOT_FOUND", "Objek tidak ditemukan."),
        409: ("VERSION_CONFLICT", "Data telah berubah. Muat versi terbaru sebelum menyimpan."),
        413: ("PAYLOAD_TOO_LARGE", "Ukuran permintaan melebihi batas."),
        415: ("UNSUPPORTED_MEDIA_TYPE", "Format tidak didukung."),
        422: ("VALIDATION_ERROR", "Permintaan tidak valid."),
        429: ("RATE_LIMITED", "Terlalu banyak percobaan. Coba kembali nanti."),
        503: ("SERVICE_UNAVAILABLE", "Layanan sementara tidak tersedia."),
    }.get(status, ("REQUEST_FAILED", "Permintaan tidak dapat diproses."))

