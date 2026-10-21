from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote
from uuid import UUID, uuid4

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from align_api.auth import (
    Identity,
    require_password_change_completed,
    require_verified_active_identity,
)
from align_api.config import Settings, get_settings
from align_api.db import get_db
from align_api.file_validation import validate_context_file
from align_api.idempotency import replay_or_lock, store_response
from align_api.models import Assignment, AuditEvent, Course, Enrollment, FileAsset
from align_api.storage import storage_client

router = APIRouter(tags=["context files"])


def _require_lecturer(course_id: UUID, identity: Identity, db: DbSession) -> Course:
    course = db.scalar(
        select(Course)
        .join(Enrollment, Enrollment.course_id == Course.id)
        .where(
            Course.id == course_id,
            Course.active.is_(True),
            Enrollment.user_id == identity.user.id,
            Enrollment.role == "lecturer",
            Enrollment.active.is_(True),
        )
    )
    if course is None:
        raise HTTPException(status_code=404, detail="course not found")
    return course


def _asset_summary(asset: FileAsset) -> dict[str, object]:
    return {
        "id": str(asset.id),
        "course_id": str(asset.course_id),
        "original_name": asset.original_name,
        "size_bytes": asset.size_bytes,
        "mime": asset.mime,
        "sha256": asset.sha256,
        "validation_state": asset.validation_state,
        "preflight": asset.preflight_json,
        "created_at": asset.created_at.isoformat(),
        "revision": asset.revision,
    }


def _check_upload_limit(settings: Settings, user_id: UUID) -> None:
    redis = Redis.from_url(
        settings.redis_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2
    )
    key = f"{settings.redis_queue_prefix}:context-upload:{user_id}"
    try:
        with redis.pipeline(transaction=True) as pipeline:
            pipeline.incr(key)
            pipeline.expire(key, 3600, nx=True)
            count, _ = pipeline.execute()
    except RedisError as exc:
        raise HTTPException(status_code=503, detail="upload limiter unavailable") from exc
    finally:
        redis.close()
    if int(count) > 20:
        raise HTTPException(status_code=429, detail="upload rate limit exceeded")


@router.post("/courses/{course_id}/context-files", status_code=201)
async def upload_context_file(
    course_id: UUID,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    file: Annotated[UploadFile, File()],
) -> dict[str, object]:
    course = _require_lecturer(course_id, identity, db)
    _check_upload_limit(settings, identity.user.id)
    raw_name = file.filename or ""
    suffix = Path(raw_name.replace("\\", "/")).suffix.lower()
    stored_key: str | None = None
    with tempfile.NamedTemporaryFile(prefix="align-upload-", suffix=suffix, delete=False) as staged:
        staged_path = staged.name
        digest = hashlib.sha256()
        byte_count = 0
        try:
            while chunk := await file.read(64 * 1024):
                byte_count += len(chunk)
                if byte_count > settings.max_file_bytes:
                    raise HTTPException(status_code=413, detail="file limit exceeded")
                digest.update(chunk)
                staged.write(chunk)
            staged.flush()
        except Exception:
            Path(staged_path).unlink(missing_ok=True)
            raise
    try:
        safe_name, detected_mime, preflight = validate_context_file(
            staged_path,
            raw_name,
            file.content_type,
            settings.max_file_bytes,
            settings.max_image_pixels,
            settings.max_docx_nodes,
            settings.max_docx_media,
            settings.max_ooxml_entries,
            settings.max_ooxml_expanded_bytes,
        )
        asset_id = uuid4()
        stored_key = f"context/{course.id}/{asset_id}"
        s3 = storage_client(settings)
        try:
            with open(staged_path, "rb") as source:
                s3.upload_fileobj(
                    source,
                    settings.s3_bucket,
                    stored_key,
                    ExtraArgs={
                        "ContentType": detected_mime,
                        "Metadata": {"sha256": digest.hexdigest()},
                    },
                )
        except (BotoCoreError, ClientError) as exc:
            raise HTTPException(status_code=503, detail="storage unavailable") from exc
        asset = FileAsset(
            id=asset_id,
            course_id=course.id,
            purpose="context",
            uploaded_by=identity.user.id,
            original_name=safe_name,
            size_bytes=byte_count,
            mime=detected_mime,
            sha256=digest.hexdigest(),
            object_key=stored_key,
            validation_state="validated",
            preflight_json=preflight,
        )
        db.add(asset)
        db.flush()
        db.add(
            AuditEvent(
                actor_user_id=identity.user.id,
                action="context.file.uploaded",
                object_type="file_asset",
                object_id=asset.id,
                new_version_refs_json={"sha256": asset.sha256, "size_bytes": asset.size_bytes},
                request_id=request.state.request_id,
            )
        )
        db.commit()
        db.refresh(asset)
        return {"asset": _asset_summary(asset)}
    except Exception:
        db.rollback()
        if stored_key is not None:
            try:
                storage_client(settings).delete_object(Bucket=settings.s3_bucket, Key=stored_key)
            except (BotoCoreError, ClientError):
                pass
        raise
    finally:
        Path(staged_path).unlink(missing_ok=True)
        await file.close()


@router.post("/assignments/{assignment_id}/rubric-files", status_code=201)
async def upload_rubric_file(
    assignment_id: UUID,
    request: Request,
    identity: Annotated[Identity, Depends(require_verified_active_identity)],
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    assignment = db.scalar(
        select(Assignment)
        .join(Course, Course.id == Assignment.course_id)
        .join(Enrollment, Enrollment.course_id == Course.id)
        .where(
            Assignment.id == assignment_id,
            Course.active.is_(True),
            Enrollment.user_id == identity.user.id,
            Enrollment.role == "lecturer",
            Enrollment.active.is_(True),
        )
    )
    if assignment is None:
        raise HTTPException(status_code=404, detail="assignment not found")
    course = db.get(Course, assignment.course_id)
    if course is None or not course.active:
        raise HTTPException(status_code=404, detail="assignment not found")
    _check_upload_limit(settings, identity.user.id)
    raw_name = file.filename or ""
    suffix = Path(raw_name.replace("\\", "/")).suffix.lower()
    stored_key: str | None = None
    with tempfile.NamedTemporaryFile(prefix="align-rubric-", suffix=suffix, delete=False) as staged:
        staged_path = staged.name
        digest = hashlib.sha256()
        byte_count = 0
        try:
            while chunk := await file.read(64 * 1024):
                byte_count += len(chunk)
                if byte_count > settings.max_file_bytes:
                    raise HTTPException(status_code=413, detail="file limit exceeded")
                digest.update(chunk)
                staged.write(chunk)
            staged.flush()
        except Exception:
            Path(staged_path).unlink(missing_ok=True)
            raise
    try:
        safe_name, detected_mime, preflight = validate_context_file(
            staged_path,
            raw_name,
            file.content_type,
            settings.max_file_bytes,
            settings.max_image_pixels,
            settings.max_docx_nodes,
            settings.max_docx_media,
            settings.max_ooxml_entries,
            settings.max_ooxml_expanded_bytes,
        )
        route = f"/api/v1/assignments/{assignment.id}/rubric-files"
        cached = replay_or_lock(
            db,
            identity.user.id,
            "POST",
            route,
            idempotency_key,
            {"name": safe_name, "sha256": digest.hexdigest(), "size_bytes": byte_count},
        )
        if cached is not None:
            return cached
        asset_id = uuid4()
        stored_key = f"rubric/{assignment.id}/{asset_id}"
        s3 = storage_client(settings)
        try:
            with open(staged_path, "rb") as source:
                s3.upload_fileobj(
                    source,
                    settings.s3_bucket,
                    stored_key,
                    ExtraArgs={
                        "ContentType": detected_mime,
                        "Metadata": {"sha256": digest.hexdigest()},
                    },
                )
        except (BotoCoreError, ClientError) as exc:
            raise HTTPException(status_code=503, detail="storage unavailable") from exc
        asset = FileAsset(
            id=asset_id,
            course_id=course.id,
            assignment_id=assignment.id,
            purpose="rubric",
            uploaded_by=identity.user.id,
            original_name=safe_name,
            size_bytes=byte_count,
            mime=detected_mime,
            sha256=digest.hexdigest(),
            object_key=stored_key,
            validation_state="validated",
            preflight_json=preflight,
        )
        db.add(asset)
        db.flush()
        db.add(
            AuditEvent(
                actor_user_id=identity.user.id,
                action="assignment.rubric.uploaded",
                object_type="file_asset",
                object_id=asset.id,
                new_version_refs_json={"sha256": asset.sha256, "size_bytes": asset.size_bytes},
                request_id=request.state.request_id,
            )
        )
        result: dict[str, object] = {
            "asset": {
                **_asset_summary(asset),
                "assignment_id": str(assignment.id),
                "purpose": "rubric",
            }
        }
        store_response(db, identity.user.id, "POST", route, idempotency_key, result)
        db.commit()
        return result
    except Exception:
        db.rollback()
        if stored_key is not None:
            try:
                storage_client(settings).delete_object(Bucket=settings.s3_bucket, Key=stored_key)
            except (BotoCoreError, ClientError):
                pass
        raise
    finally:
        Path(staged_path).unlink(missing_ok=True)
        await file.close()


@router.get("/rubric-files/{file_id}/download")
def download_rubric_file(
    file_id: UUID,
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    asset = db.scalar(
        select(FileAsset)
        .join(Assignment, Assignment.id == FileAsset.assignment_id)
        .join(Course, Course.id == Assignment.course_id)
        .join(Enrollment, Enrollment.course_id == Course.id)
        .where(
            FileAsset.id == file_id,
            FileAsset.purpose == "rubric",
            FileAsset.validation_state == "validated",
            Course.active.is_(True),
            Enrollment.user_id == identity.user.id,
            Enrollment.role == "lecturer",
            Enrollment.active.is_(True),
        )
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="file not found")
    staged = tempfile.TemporaryFile()
    try:
        response = storage_client(settings).get_object(
            Bucket=settings.s3_bucket, Key=asset.object_key
        )
        body = response["Body"]
        try:
            digest = hashlib.sha256()
            size = 0
            for chunk in body.iter_chunks(chunk_size=64 * 1024):
                if chunk:
                    size += len(chunk)
                    if size > min(asset.size_bytes, settings.max_file_bytes):
                        raise HTTPException(status_code=409, detail="source integrity check failed")
                    digest.update(chunk)
                    staged.write(chunk)
            if size != asset.size_bytes or digest.hexdigest() != asset.sha256:
                raise HTTPException(status_code=409, detail="source integrity check failed")
            staged.seek(0)
        finally:
            body.close()
    except HTTPException:
        staged.close()
        raise
    except (BotoCoreError, ClientError, OSError) as exc:
        staged.close()
        raise HTTPException(status_code=503, detail="storage unavailable") from exc

    def content() -> Any:
        try:
            while chunk := staged.read(64 * 1024):
                yield chunk
        finally:
            staged.close()

    return StreamingResponse(
        content(),
        media_type=asset.mime,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(asset.original_name)}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            "Content-Length": str(asset.size_bytes),
        },
    )


@router.get("/context-files/{file_id}")
def get_context_file(
    file_id: UUID,
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
) -> dict[str, object]:
    asset = db.scalar(
        select(FileAsset)
        .join(Course, Course.id == FileAsset.course_id)
        .join(Enrollment, Enrollment.course_id == Course.id)
        .where(
            FileAsset.id == file_id,
            FileAsset.purpose == "context",
            FileAsset.validation_state == "validated",
            Course.active.is_(True),
            Enrollment.user_id == identity.user.id,
            Enrollment.role == "lecturer",
            Enrollment.active.is_(True),
        )
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="file not found")
    return {"asset": _asset_summary(asset)}


@router.get("/context-files/{file_id}/download")
def download_context_file(
    file_id: UUID,
    identity: Annotated[Identity, Depends(require_password_change_completed)],
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    asset = db.scalar(
        select(FileAsset)
        .join(Course, Course.id == FileAsset.course_id)
        .join(Enrollment, Enrollment.course_id == Course.id)
        .where(
            FileAsset.id == file_id,
            FileAsset.purpose == "context",
            FileAsset.validation_state == "validated",
            Course.active.is_(True),
            Enrollment.user_id == identity.user.id,
            Enrollment.role == "lecturer",
            Enrollment.active.is_(True),
        )
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="file not found")
    staged = tempfile.TemporaryFile()
    try:
        response = storage_client(settings).get_object(
            Bucket=settings.s3_bucket, Key=asset.object_key
        )
        body = response["Body"]
        try:
            digest = hashlib.sha256()
            size = 0
            for chunk in body.iter_chunks(chunk_size=64 * 1024):
                if chunk:
                    size += len(chunk)
                    if size > min(asset.size_bytes, settings.max_file_bytes):
                        raise HTTPException(status_code=409, detail="source integrity check failed")
                    digest.update(chunk)
                    staged.write(chunk)
            if size != asset.size_bytes or digest.hexdigest() != asset.sha256:
                raise HTTPException(status_code=409, detail="source integrity check failed")
            staged.seek(0)
        finally:
            body.close()
    except HTTPException:
        staged.close()
        raise
    except (BotoCoreError, ClientError, OSError) as exc:
        staged.close()
        raise HTTPException(status_code=503, detail="storage unavailable") from exc

    def content() -> Any:
        try:
            while chunk := staged.read(64 * 1024):
                yield chunk
        finally:
            staged.close()

    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(asset.original_name)}",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
        "Content-Length": str(asset.size_bytes),
    }
    return StreamingResponse(content(), media_type=asset.mime, headers=headers)
