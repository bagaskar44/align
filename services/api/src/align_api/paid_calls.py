"""Snapshot whether an academic job inherits an active, explicitly enabled live release."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .models import MethodManifest, ReleaseManifest


def runtime_live_model_ids(settings: Settings | None = None) -> dict[str, str] | None:
    current = settings or get_settings()
    values = {
        "efficient": current.model_efficient,
        "complex": current.model_complex,
        "vision": current.model_vision,
        "embedding": current.embedding_model,
    }
    if any(not isinstance(value, str) or not value.strip() for value in values.values()):
        return None
    return {key: str(value) for key, value in values.items()}


def live_model_binding_matches(
    model_content: dict[str, Any], settings: Settings | None = None
) -> bool:
    configured = runtime_live_model_ids(settings)
    pinned = model_content.get("live_model_ids")
    return bool(
        configured
        and isinstance(pinned, dict)
        and set(pinned) == set(configured)
        and pinned == configured
    )


def call_model_matches_manifest(model_content: dict[str, Any], model_id: str) -> bool:
    pinned_role_models = model_content.get("live_model_ids")
    if isinstance(pinned_role_models, dict):
        return model_id in pinned_role_models.values()
    return model_content.get("model") == model_id


def active_paid_release_scope(db: Session, *, enabled: bool) -> dict[str, Any]:
    release = db.scalar(select(ReleaseManifest).where(ReleaseManifest.state == "active"))
    if release is None:
        return {"paid_release_manifest_id": None, "rate_manifest_id": None, "paid_calls_enabled": False}
    routing = db.get(MethodManifest, release.routing_policy_manifest_id)
    model = db.get(MethodManifest, release.model_manifest_id)
    rates = db.get(MethodManifest, release.rate_manifest_id) if release.rate_manifest_id else None
    settings = get_settings()
    rate_models = rates.content_json.get("models") if rates else None
    runtime_models = runtime_live_model_ids(settings)
    permitted = bool(
        enabled
        and release.manifest_payload.get("provider_mode") == "live"
        and release.manifest_payload.get("data_mode") in {"synthetic", "institutional"}
        and (
            release.manifest_payload.get("data_mode") != "synthetic"
            or (
                settings.app_env == "development"
                and settings.data_mode == "synthetic"
                and settings.app_demo_mode
            )
        )
        and routing is not None
        and routing.content_json.get("allow_live") is True
        and model is not None
        and model.content_json.get("provider_mode") == "live"
        and model.content_json.get("paid_calls") is True
        and live_model_binding_matches(model.content_json, settings)
        and rates is not None
        and rates.kind == "rates"
        and rates.verified_at is not None
        and rates.content_json.get("status") == "verified"
        and isinstance(rate_models, dict)
        and runtime_models is not None
        and set(runtime_models.values()) <= set(rate_models)
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
