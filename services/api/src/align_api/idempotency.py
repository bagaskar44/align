from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from align_api.models import IdempotencyRecord


def canonical_hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def replay_or_lock(
    db: DbSession,
    actor_id: UUID,
    method: str,
    route: str,
    key: str,
    body: Any,
) -> dict[str, Any] | None:
    try:
        UUID(key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="idempotency key must be a UUID") from exc
    body_hash = canonical_hash(body)
    record = db.scalar(
        select(IdempotencyRecord)
        .where(
            IdempotencyRecord.actor_id == actor_id,
            IdempotencyRecord.method == method,
            IdempotencyRecord.route == route,
            IdempotencyRecord.key == key,
        )
        .with_for_update()
    )
    now = datetime.now(UTC)
    if record is not None and record.expires_at > now:
        if record.body_hash != body_hash:
            raise HTTPException(status_code=409, detail="idempotency key reused with a different request")
        if record.response_json is None:
            raise HTTPException(status_code=409, detail="request with this idempotency key is in progress")
        return record.response_json
    if record is None:
        record = IdempotencyRecord(
            actor_id=actor_id,
            method=method,
            route=route,
            key=key,
            body_hash=body_hash,
            response_status=0,
            response_json=None,
            expires_at=now + timedelta(hours=24),
        )
        db.add(record)
    else:
        record.body_hash = body_hash
        record.response_status = 0
        record.response_json = None
        record.expires_at = now + timedelta(hours=24)
    db.flush()
    return None


def store_response(db: DbSession, actor_id: UUID, method: str, route: str, key: str, response: dict[str, Any]) -> None:
    record = db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.actor_id == actor_id,
            IdempotencyRecord.method == method,
            IdempotencyRecord.route == route,
            IdempotencyRecord.key == key,
        )
    )
    if record is None:
        raise RuntimeError("idempotency record disappeared inside the transaction")
    record.response_status = 201
    record.response_json = response
