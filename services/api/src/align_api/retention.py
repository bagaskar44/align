from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from align_api.models import FileAsset, RetentionTombstone


def source_purged(db: Session, *, file_asset_id: UUID | None = None, submission_id: UUID | None = None) -> bool:
    query = select(RetentionTombstone.id).join(
        FileAsset, FileAsset.id == RetentionTombstone.file_asset_id
    )
    if file_asset_id is not None:
        query = query.where(RetentionTombstone.file_asset_id == file_asset_id)
    elif submission_id is not None:
        query = query.where(FileAsset.submission_id == submission_id)
    else:
        raise ValueError("file_asset_id or submission_id is required")
    return db.scalar(query.limit(1)) is not None


def raise_source_purged() -> None:
    raise HTTPException(
        status_code=410,
        detail={"code": "SOURCE_PURGED", "message": "Sumber dihapus sesuai kebijakan retensi."},
    )
