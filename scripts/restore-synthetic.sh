#!/usr/bin/env bash
set -euo pipefail
umask 077

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
: "${ALIGN_RESTORE_PROJECT:?Set an isolated project name such as align-b7-restore-check-01}"
: "${ALIGN_RESTORE_INPUT:?Set the encrypted CMS backup path}"
: "${ALIGN_RESTORE_CERT:?Set the matching recipient certificate}"
: "${ALIGN_RESTORE_KEY:?Set the matching recipient private key}"
if [[ ! "$ALIGN_RESTORE_PROJECT" =~ ^align-b7-restore-[a-z0-9-]+$ ]]; then
  echo "Restore refused: project name must start with align-b7-restore-." >&2
  exit 2
fi
if [[ ! -r "$ALIGN_RESTORE_INPUT" || ! -r "$ALIGN_RESTORE_CERT" || ! -r "$ALIGN_RESTORE_KEY" || ! -f "$repo_root/.env" ]]; then
  echo "Encrypted backup, certificate, private key, or repository .env is unavailable." >&2
  exit 2
fi
if docker volume ls -q --filter "label=com.docker.compose.project=$ALIGN_RESTORE_PROJECT" | grep -q .; then
  echo "Restore refused: target Compose project already has volumes; use a new isolated name." >&2
  exit 2
fi

temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/align-b7-restore.XXXXXX")"
trap 'rm -rf "$temp_dir"' EXIT
openssl cms -decrypt -binary -inform DER -in "$ALIGN_RESTORE_INPUT" \
  -recip "$ALIGN_RESTORE_CERT" -inkey "$ALIGN_RESTORE_KEY" -out "$temp_dir/payload.tar"
members="$(tar -tf "$temp_dir/payload.tar")"
if [[ "$members" != $'database.dump\nobjects.tar.gz\nsnapshot.json' ]]; then
  echo "Restore refused: backup bundle inventory is unexpected." >&2
  exit 2
fi
tar -xf "$temp_dir/payload.tar" -C "$temp_dir"
python3 - "$temp_dir/snapshot.json" <<'PY'
import json
import sys
from pathlib import Path

snapshot = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if snapshot.get("schema_version") != 1 or snapshot.get("data_mode") != "synthetic":
    raise SystemExit("Restore refused: snapshot is not a supported synthetic backup.")
PY

compose=(docker compose --project-name "$ALIGN_RESTORE_PROJECT" --env-file "$repo_root/.env")
"${compose[@]}" up -d --wait --wait-timeout 90 postgres object-storage
"${compose[@]}" cp "$temp_dir/database.dump" postgres:/tmp/align-b7-database.dump
"${compose[@]}" exec -T postgres sh -lc \
  'pg_restore --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" /tmp/align-b7-database.dump'
"${compose[@]}" run --rm --no-deps --volume "$temp_dir:/restore:ro" api \
  uv run --no-sync --package align-api align backup objects-import --input /restore/objects.tar.gz
"${compose[@]}" run --rm --no-deps api \
  uv run --no-sync --package align-api align backup reconcile
"${compose[@]}" up -d
echo "Synthetic restore reconciled and services started in project $ALIGN_RESTORE_PROJECT"
