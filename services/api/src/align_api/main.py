from __future__ import annotations

import logging
import uuid
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, FastAPI, HTTPException, Request
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import text

from align_api.admin import router as admin_router
from align_api.analyses import router as analyses_router
from align_api.assignments import router as assignments_router
from align_api.auth import change_password, login, logout, me
from align_api.config import get_settings
from align_api.contexts import router as contexts_router
from align_api.courses import router as courses_router
from align_api.db import engine
from align_api.errors import install_error_handlers
from align_api.integrations import router as integrations_router
from align_api.lti import router as lti_router
from align_api.request_limits import RequestBodyLimit
from align_api.reviews import router as reviews_router
from align_api.submissions import router as submissions_router

settings = get_settings()
logging.basicConfig(level=settings.app_log_level)


def create_app() -> FastAPI:
    app = FastAPI(
        title="ALIGN API",
        version="0.1.0",
        description="API v1 untuk fondasi ALIGN. Provider default menggunakan simulasi sintetis.",
        docs_url="/api/v1/docs" if settings.app_env != "production" else None,
        redoc_url=None,
        openapi_url="/api/v1/openapi.json" if settings.app_env != "production" else None,
    )
    install_error_handlers(app)
    app.add_middleware(RequestBodyLimit, upload_bytes=settings.max_file_bytes)

    @app.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Any:
        request_id = request.headers.get("X-Request-ID")
        try:
            request.state.request_id = uuid.UUID(request_id) if request_id else uuid.uuid4()
        except ValueError:
            request.state.request_id = uuid.uuid4()
        response = await call_next(request)
        response.headers["X-Request-ID"] = str(request.state.request_id)
        if request.url.path.startswith("/api/") or request.url.path.startswith("/health/"):
            response.headers["Cache-Control"] = "private, no-store"
        return response

    router = APIRouter(prefix="/api/v1")
    router.add_api_route("/auth/login", login, methods=["POST"], name="login")
    router.add_api_route("/auth/logout", logout, methods=["POST"], status_code=204, name="logout")
    router.add_api_route("/me", me, methods=["GET"], name="me")
    router.add_api_route(
        "/me/password", change_password, methods=["PUT"], status_code=204, name="change_password"
    )
    router.include_router(courses_router)
    router.include_router(admin_router)
    router.include_router(contexts_router)
    router.include_router(assignments_router)
    router.include_router(submissions_router)
    router.include_router(reviews_router)
    router.include_router(analyses_router)
    router.include_router(lti_router)
    router.include_router(integrations_router)
    app.include_router(router)

    @app.get("/health/live", tags=["health"])
    def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready", tags=["health"])
    def ready() -> dict[str, str]:
        checks: dict[str, str] = {}
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            checks["database"] = "ready"
        except Exception:
            checks["database"] = "unavailable"
        client = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
        try:
            client.ping()
            checks["broker"] = "ready"
        except RedisError:
            checks["broker"] = "unavailable"
        finally:
            client.close()
        storage = _storage_client()
        try:
            storage.head_bucket(Bucket=settings.s3_bucket)
            checks["storage"] = "ready"
        except (BotoCoreError, ClientError):
            checks["storage"] = "unavailable"
        if any(value != "ready" for value in checks.values()):
            raise HTTPException(status_code=503, detail="dependency not ready")
        return {"status": "ready", **checks}

    @app.get("/health/providers", tags=["internal"], include_in_schema=False)
    def providers() -> dict[str, object]:
        if settings.provider_mode == "off":
            return {"mode": "off", "status": "disabled", "paid_calls": "disabled"}
        if settings.provider_mode == "mock":
            return {
                "mode": "mock",
                "status": "simulated",
                "label": "SIMULASI • data sintetis",
                "paid_calls": "disabled",
            }
        return {"mode": "live", "status": "blocked", "paid_calls": "blocked_before_network"}

    return app


def _storage_client() -> Any:
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
        region_name=settings.s3_region,
        config=BotoConfig(
            s3={"addressing_style": "path" if settings.s3_use_path_style else "virtual"},
            connect_timeout=2,
            read_timeout=3,
            retries={"max_attempts": 1},
        ),
    )


app = create_app()
