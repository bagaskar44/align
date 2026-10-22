#!/usr/bin/env bash
set -euo pipefail
umask 077

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
: "${ALIGN_COMPOSE_PROJECT:?Set the exact Compose project name to back up}"
: "${ALIGN_BACKUP_OUTPUT:?Set the destination encrypted archive path}"
: "${ALIGN_BACKUP_CERT:?Set the path to the backup recipient public certificate}"
: "${ALIGN_BACKUP_QUIESCED:?Set to 1 only after API/worker writers and parser children are drained}"
if [[ ! "$ALIGN_COMPOSE_PROJECT" =~ ^align-b7-[a-z0-9-]+$ ]]; then
  echo "Backup refused: project name must start with align-b7-." >&2
  exit 2
fi
if [[ "$ALIGN_BACKUP_QUIESCED" != "1" ]]; then
  echo "Backup refused: stop new writes and drain workers/children first." >&2
  exit 2
fi
if [[ ! -r "$ALIGN_BACKUP_CERT" || ! -f "$repo_root/.env" ]]; then
  echo "Backup certificate or repository .env is unavailable." >&2
  exit 2
fi
if [[ -e "$ALIGN_BACKUP_OUTPUT" ]]; then
  echo "Backup refused: output already exists; choose a new path." >&2
  exit 2
fi

backup_parent="$(dirname "$ALIGN_BACKUP_OUTPUT")"
mkdir -p "$backup_parent"
temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/align-b7-backup.XXXXXX")"
encrypted_temp="$ALIGN_BACKUP_OUTPUT.tmp.$$"
trap 'rm -rf "$temp_dir"; rm -f "$encrypted_temp"' EXIT
compose=(docker compose --project-name "$ALIGN_COMPOSE_PROJECT" --env-file "$repo_root/.env")

# BuildKit progress is not JSON and some Compose versions write it to stdout.
# Prebuild the one-off CLI image with output isolated before capturing reports.
"${compose[@]}" build api > "$temp_dir/api-build.log" 2>&1
"${compose[@]}" run --rm --no-deps --volume "$temp_dir:/backup" api \
  uv run --no-sync --package align-api align backup reconcile > "$temp_dir/reconcile.json"
"${compose[@]}" exec -T postgres sh -lc \
  'pg_dump --format=custom --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"' \
  > "$temp_dir/database.dump"
"${compose[@]}" run --rm --no-deps --volume "$temp_dir:/backup" api \
  uv run --no-sync --package align-api align backup objects-export --output /backup/objects.tar.gz \
  > "$temp_dir/storage-export.json"
cat > "$temp_dir/snapshot.json" <<EOF
{"schema_version":1,"data_mode":"synthetic","compose_project":"$ALIGN_COMPOSE_PROJECT","created_at":"$(date -u +%Y-%m-%dT%H:%M:%SZ)","reconciliation":$(cat "$temp_dir/reconcile.json"),"storage_export":$(cat "$temp_dir/storage-export.json")}
EOF
tar -cf "$temp_dir/payload.tar" -C "$temp_dir" database.dump objects.tar.gz snapshot.json
openssl cms -encrypt -binary -aes-256-cbc -in "$temp_dir/payload.tar" \
  -out "$encrypted_temp" -outform DER "$ALIGN_BACKUP_CERT"
mv "$encrypted_temp" "$ALIGN_BACKUP_OUTPUT"
echo "Encrypted synthetic backup written: $ALIGN_BACKUP_OUTPUT"
