#!/bin/bash
# Local manual/mock runtime; heavy parser startup and verification are explicit.
set -euo pipefail
script_dir=$(cd "$(dirname "$0")" && pwd)
cd "$script_dir/.."
source "$script_dir/macos-env.sh"
project=${ALIGN_COMPOSE_PROJECT:-align}
docker_bin=${ALIGN_DOCKER_BIN:-docker}
compose() { "$docker_bin" compose --project-name "$project" "$@"; }
db_query() {
  compose exec -T postgres sh -c 'exec psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "$1"' sh "$1"
}
case "${1:-help}" in
  bootstrap)
    python3 "$script_dir/bootstrap.py" --port "${ALIGN_HOST_PORT:-3000}"
    ;;
  install)
    uv sync --frozen --all-packages --group dev
    corepack pnpm install --frozen-lockfile --store-dir "$ALIGN_CACHE_DIR/pnpm-store"
    ;;
  up)
    compose config --quiet
    # Start only infrastructure before role inspection/migration. Fresh credentials stay private.
    compose up --detach --wait postgres redis object-storage
    roles=$(db_query "SELECT count(*) FROM pg_roles WHERE rolname IN ('align_migration','align_api','align_worker','align_operator')")
    case "$roles" in
      0) echo 'Initializing the four database roles with the repository SQL.'
         compose exec -T postgres bash /docker-entrypoint-initdb.d/001-roles.sh ;;
      4) echo 'All four database roles already exist; no credential rotation.' ;;
      *) echo 'Partial role initialization. Stop and inspect PostgreSQL; no automatic reset.' >&2; exit 1 ;;
    esac
    compose run --build --rm --no-deps migrate
    compose up --build --detach --wait api worker outbox-dispatcher scheduler web proxy
    ;;
  seed)
    compose exec -T api align seed
    ;;
  verify)
    python3 "$script_dir/smoke-local.py" --project "$project" --docker "$docker_bin"
    ;;
  release-baseline)
    # Only an empty DB, the recognized default B0, or this exact B5 baseline
    # may proceed. Custom/live releases are protected before any registration.
    active_json=$(db_query "SELECT COALESCE(json_agg(json_build_object('id', id::text, 'version_label', version_label, 'config_hash', config_hash, 'provider_mode', manifest_payload->>'provider_mode', 'data_mode', manifest_payload->>'data_mode', 'capabilities_available', manifest_payload->'capabilities_available')), '[]'::json)::text FROM release_manifests WHERE state='active'")
    plan=$(printf '%s' "$active_json" | python3 "$script_dir/release_guard.py" --target config/release.baseline.json)
    action=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["action"])')
    release_json=$(compose run --rm -T --no-deps api align release register --file config/release.baseline.json)
    baseline_id=$(printf '%s' "$release_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["release_id"])')
    target_hash=$(printf '%s' "$release_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["config_hash"])')
    target_state=$(printf '%s' "$release_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["state"])')
    if [ "$action" = verify_target ]; then
      active_id=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["active_release_id"])')
      active_hash=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["active_config_hash"])')
      if [ "$target_state" != active ] || [ "$baseline_id" != "$active_id" ] || [ "$target_hash" != "$active_hash" ]; then
        echo 'Active B5 release hash differs from the shipped target; it was preserved.' >&2
        exit 1
      fi
      echo 'The shipped synthetic B5 baseline is active with the same hash; no activation was repeated.'
    else
      if [ "$target_state" = retired ]; then
        echo 'The target B5 release is retired and cannot be reactivated; register a new version first.' >&2
        exit 1
      fi
      compose exec -T api align release activate "$baseline_id"
    fi
    ;;
  integration)
    restore_integration_workers() {
      status=$?
      trap - EXIT
      compose --profile test stop test-parser-worker test-outbox-dispatcher >/dev/null 2>&1 || true
      compose start outbox-dispatcher >/dev/null 2>&1 || true
      exit "$status"
    }
    # Both dispatchers read the shared database outbox. Pause the development
    # dispatcher so test events reach the isolated align-test queues.
    compose stop outbox-dispatcher
    trap restore_integration_workers EXIT
    compose --profile test up --build --detach --wait test-parser-worker test-outbox-dispatcher
    compose --profile test run --build --rm --no-deps test-runner
    ;;
  parser-up)
    compose up --build --detach parser-worker
    ;;
  parser-integration)
    # The project dispatcher and the test dispatcher share the same database
    # outbox but target different Redis queue prefixes. Keep the normal
    # dispatcher from claiming test events during this isolated test window.
    restore_dispatcher() {
      status=$?
      trap - EXIT
      compose --profile test stop test-parser-worker test-outbox-dispatcher >/dev/null 2>&1 || true
      compose start outbox-dispatcher >/dev/null 2>&1 || true
      exit "$status"
    }
    compose stop outbox-dispatcher
    trap restore_dispatcher EXIT
    compose --profile test up --build --detach --wait test-parser-worker test-outbox-dispatcher
    compose --profile test run --build --rm --no-deps --env ALIGN_RUN_PARSER_INTEGRATION=true test-runner uv run --no-sync pytest tests/integration/test_b1_parser_runtime.py -v
    ;;
  parser-benchmark)
    reports_dir="$(pwd)/docs/handover/b8-reports"
    mkdir -p "$reports_dir"
    compose --profile test run --build --rm --no-deps \
      --volume "$(pwd)/tests/fixtures/b1:/fixtures:ro" \
      --volume "$reports_dir:/reports" \
      test-parser-worker \
      uv run --no-sync --package align-api align parser benchmark \
      --profile docling_native_v1 --fixtures /fixtures \
      --output /reports/parser-capacity-current.json
    ;;
  stop)
    compose stop
    ;;
  *) echo 'Usage: bash scripts/dev-macos.sh {bootstrap|install|up|seed|verify|release-baseline|integration|parser-up|parser-integration|parser-benchmark|stop}'
     echo 'Select isolation with ALIGN_COMPOSE_PROJECT and ALIGN_HOST_PORT (before bootstrap).'
     exit 1 ;;
esac
