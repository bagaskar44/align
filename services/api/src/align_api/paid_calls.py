"""Snapshot whether an academic job inherits an active, explicitly enabled live release."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import MethodManifest, ReleaseManifest


def active_paid_release_scope(db: Session, *, enabled: bool) -> dict[str, Any]:
    release = db.scalar(select(ReleaseManifest).where(ReleaseManifest.state == "active"))
    if release is None:
        return {"paid_release_manifest_id": None, "rate_manifest_id": None, "paid_calls_enabled": False}
    routing = db.get(MethodManifest, release.routing_policy_manifest_id)
    model = db.get(MethodManifest, release.model_manifest_id)
    permitted = bool(
        enabled
        and release.manifest_payload.get("provider_mode") == "live"
        and routing is not None
        and routing.content_json.get("allow_live") is True
        and model is not None
        and model.content_json.get("paid_calls") is True
        and release.rate_manifest_id is not None
    )
    return {
        "paid_release_manifest_id": str(release.id),
        "rate_manifest_id": str(release.rate_manifest_id) if release.rate_manifest_id else None,
        "paid_calls_enabled": permitted,
    }


def parse_manifest_id(value: Any) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except (ValueError, TypeError):
        return None
