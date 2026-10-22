"""Synthetic object-store backup helpers for the B7 operator workflow."""

from __future__ import annotations

import hashlib
import json
import re
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import select

from align_api.config import Settings
from align_api.db import SessionFactory
from align_api.models import (
    FileAsset,
    ParseArtifact,
    ParserCheckpoint,
    ProcessingTimeReservation,
    RetentionTombstone,
    UsageEntry,
    UsageReservation,
)
from align_api.storage import storage_client

MANIFEST_NAME = "manifest.json"
ARCHIVE_SCHEMA = 1
MAX_MANIFEST_BYTES = 32 * 1024 * 1024
MAX_OBJECTS = 100_000
MAX_TOTAL_OBJECT_BYTES = 20 * 1024 * 1024 * 1024


def export_storage_archive(settings: Settings, output: Path) -> dict[str, Any]:
    if settings.data_mode != "synthetic":
        raise ValueError("this backup workflow accepts synthetic data only")
    client = storage_client(settings)
    paginator = client.get_paginator("list_objects_v2")
    objects = [
        item
        for page in paginator.paginate(Bucket=settings.s3_bucket)
        for item in page.get("Contents", [])
        if isinstance(item, dict) and isinstance(item.get("Key"), str)
    ]
    objects.sort(key=lambda item: str(item["Key"]))
    if len(objects) > MAX_OBJECTS:
        raise ValueError("object count exceeds the synthetic backup safety limit")
    manifest_objects: list[dict[str, Any]] = []
    total_bytes = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, mode="w:gz") as archive:
        for index, listed in enumerate(objects):
            key = str(listed["Key"])
            member_name = f"objects/{index:08d}"
            response = client.get_object(Bucket=settings.s3_bucket, Key=key)
            body = response["Body"]
            try:
                digest = hashlib.sha256()
                size = 0
                with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as staged:
                    for chunk in body.iter_chunks(chunk_size=64 * 1024):
                        if not chunk:
                            continue
                        staged.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                        total_bytes += len(chunk)
                        if total_bytes > MAX_TOTAL_OBJECT_BYTES:
                            raise ValueError("synthetic storage snapshot exceeds the 20 GiB safety limit")
                    staged.seek(0)
                    info = tarfile.TarInfo(member_name)
                    info.size = size
                    info.mtime = 0
                    info.mode = 0o600
                    archive.addfile(info, staged)
                manifest_objects.append({
                    "key": key,
                    "member": member_name,
                    "size_bytes": size,
                    "sha256": digest.hexdigest(),
                })
            finally:
                body.close()
        manifest = {
            "schema_version": ARCHIVE_SCHEMA,
            "snapshot_id": str(uuid4()),
            "created_at": datetime.now(UTC).isoformat(),
            "data_mode": settings.data_mode,
            "bucket": settings.s3_bucket,
            "object_count": len(manifest_objects),
            "objects": manifest_objects,
        }
        encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        info = tarfile.TarInfo(MANIFEST_NAME)
        info.size = len(encoded)
        info.mtime = 0
        info.mode = 0o600
        import io

        archive.addfile(info, io.BytesIO(encoded))
    return {"snapshot_id": manifest["snapshot_id"], "object_count": len(manifest_objects),
            "archive": str(output), "data_mode": settings.data_mode}


def _read_manifest(archive: tarfile.TarFile) -> tuple[dict[str, Any], dict[str, tarfile.TarInfo]]:
    member_list = archive.getmembers()
    members = {item.name: item for item in member_list}
    if len(members) != len(member_list):
        raise ValueError("storage archive contains duplicate member names")
    manifest_info = members.get(MANIFEST_NAME)
    if manifest_info is None or not manifest_info.isfile() or manifest_info.size > MAX_MANIFEST_BYTES:
        raise ValueError("storage archive manifest is missing or too large")
    handle = archive.extractfile(manifest_info)
    if handle is None:
        raise ValueError("storage archive manifest is unreadable")
    try:
        manifest = json.loads(handle.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("storage archive manifest is invalid JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != ARCHIVE_SCHEMA:
        raise ValueError("unsupported storage archive manifest")
    entries = manifest.get("objects")
    if not isinstance(entries, list) or len(entries) > MAX_OBJECTS or manifest.get("object_count") != len(entries):
        raise ValueError("storage archive object inventory is invalid")
    expected_members = {MANIFEST_NAME}
    seen_keys: set[str] = set()
    by_name: dict[str, tarfile.TarInfo] = {}
    total_bytes = 0
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("storage archive object entry is invalid")
        key, name, size, digest = entry.get("key"), entry.get("member"), entry.get("size_bytes"), entry.get("sha256")
        if not isinstance(key, str) or not key or len(key) > 1024 or "\x00" in key or key in seen_keys:
            raise ValueError("storage archive contains an invalid or duplicate object key")
        if not isinstance(name, str) or not re.fullmatch(r"objects/[0-9]{8}", name) or name in expected_members:
            raise ValueError("storage archive object member path is invalid")
        if (type(size) is not int or size < 0 or not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)):
            raise ValueError("storage archive object size or digest is invalid")
        total_bytes += size
        if total_bytes > MAX_TOTAL_OBJECT_BYTES:
            raise ValueError("storage archive exceeds the 20 GiB safety limit")
        info = members.get(name)
        if info is None or not info.isfile() or info.size != size:
            raise ValueError("storage archive object member is missing or has the wrong size")
        expected_members.add(name)
        seen_keys.add(key)
        by_name[name] = info
    if set(members) != expected_members:
        raise ValueError("storage archive contains unlisted members")
    return manifest, by_name


def import_storage_archive(settings: Settings, source: Path) -> dict[str, Any]:
    client = storage_client(settings)
    existing = client.list_objects_v2(Bucket=settings.s3_bucket, MaxKeys=1).get("Contents", [])
    if existing:
        raise ValueError("restore target bucket must be empty; refusing to merge a snapshot")
    imported = 0
    with tarfile.open(source, mode="r:gz") as archive:
        manifest, by_name = _read_manifest(archive)
        if manifest.get("data_mode") != "synthetic":
            raise ValueError("this restore workflow accepts synthetic snapshots only")
        for entry in manifest["objects"]:
            member = by_name[entry["member"]]
            body = archive.extractfile(member)
            if body is None:
                raise ValueError("storage archive object is unreadable")
            digest = hashlib.sha256()
            size = 0
            try:
                with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as staged:
                    while chunk := body.read(64 * 1024):
                        digest.update(chunk)
                        size += len(chunk)
                        staged.write(chunk)
                    if size != entry["size_bytes"] or digest.hexdigest() != entry["sha256"]:
                        raise ValueError("storage archive object digest does not match manifest")
                    staged.seek(0)
                    client.upload_fileobj(staged, settings.s3_bucket, entry["key"])
                imported += 1
            finally:
                body.close()
    return {"status": "objects_restored", "object_count": imported,
            "snapshot_id": manifest["snapshot_id"], "data_mode": "synthetic"}


def reconcile_restored_state(settings: Settings) -> dict[str, Any]:
    with SessionFactory() as db:
        tombstone_ids = set(db.scalars(select(RetentionTombstone.file_asset_id)).all())
        assets = list(db.scalars(select(FileAsset).where(FileAsset.validation_state == "validated")))
        expected_assets = [item for item in assets if item.id not in tombstone_ids]
        artifacts = list(db.scalars(select(ParseArtifact).where(ParseArtifact.artifact_object_key.is_not(None))))
        expected_asset_keys = {item.object_key: item for item in expected_assets}
        expected_artifact_keys = {item.artifact_object_key for item in artifacts if item.artifact_object_key}
        expected_keys = set(expected_asset_keys) | expected_artifact_keys
        pending_billing = len(db.scalars(select(UsageEntry.id).where(UsageEntry.status == "pending")).all())
        held_reservations = len(db.scalars(select(UsageReservation.id).where(
            UsageReservation.status.in_(["held", "reconciliation_required"])
        )).all())
        child_leases = len(db.scalars(select(ParserCheckpoint.id).where(ParserCheckpoint.status == "running")).all())
        processing_reservations = len(db.scalars(select(ProcessingTimeReservation.id).where(
            ProcessingTimeReservation.state == "held"
        )).all())

    client = storage_client(settings)
    paginator = client.get_paginator("list_objects_v2")
    actual = {str(item["Key"]) for page in paginator.paginate(Bucket=settings.s3_bucket)
              for item in page.get("Contents", []) if isinstance(item, dict) and isinstance(item.get("Key"), str)}
    missing = sorted(expected_keys - actual)
    orphaned = sorted(actual - expected_keys)
    digest_mismatches: list[str] = []
    for key, asset in expected_asset_keys.items():
        try:
            response = client.get_object(Bucket=settings.s3_bucket, Key=key)
            body = response["Body"]
            digest = hashlib.sha256()
            size = 0
            try:
                for chunk in body.iter_chunks(chunk_size=64 * 1024):
                    if chunk:
                        digest.update(chunk)
                        size += len(chunk)
            finally:
                body.close()
            if digest.hexdigest() != asset.sha256 or size != asset.size_bytes:
                digest_mismatches.append(key)
        except (BotoCoreError, ClientError, OSError):
            digest_mismatches.append(key)
    status = "reconciled" if not missing and not digest_mismatches else "incomplete"
    # Unknown objects may be purged submissions or stale academic derivatives.
    # They must not be silently copied to another bucket during restore.
    if orphaned or child_leases or processing_reservations:
        status = "incomplete"
    return {
        "status": status,
        "expected_object_count": len(expected_keys),
        "actual_object_count": len(actual),
        "missing_object_count": len(missing),
        "orphaned_object_count": len(orphaned),
        "asset_hash_mismatch_count": len(digest_mismatches),
        "pending_billing_liabilities_preserved": pending_billing,
        "held_usage_reservations": held_reservations,
        "running_parser_child_checkpoints": child_leases,
        "held_processing_reservations": processing_reservations,
        "reconciliation_required": bool(orphaned or missing or digest_mismatches or child_leases or processing_reservations),
        "automatic_unknown_billing_retry": False,
    }
