#!/bin/bash
# Test HEAD plus the candidate setup scripts in an empty snapshot; never copy ignored installs.
set -euo pipefail
# Source files must retain normal readable modes for the non-root Docker runtime.
# Secret environments are created later with explicit mode 0600 by bootstrap.py.
umask 022
script_dir=$(cd "$(dirname "$0")" && pwd)
repo_dir=$(cd "$script_dir/.." && pwd)
docker_bin=${ALIGN_DOCKER_BIN:-$(command -v docker)}
test_root=$(mktemp -d /private/tmp/align-macos-clean.XXXXXX)
checkout="$test_root/checkout"
mkdir "$checkout"
cd "$repo_dir"
git rev-parse HEAD > "$test_root/baseline-revision.txt"
git archive HEAD | tar -xf - -C "$checkout"
# Include only reviewable source changes, including new scripts that are not committed yet.
git ls-files --cached --others --exclude-standard -- scripts README.md docs/handover/macos-local-setup.md > "$test_root/setup-source-files.txt"
tar -cf - -T "$test_root/setup-source-files.txt" | tar -xf - -C "$checkout"
for absent in .env .venv node_modules .codex-tools .corepack-cache .playwright-browsers; do
  test ! -e "$checkout/$absent" || { echo "Snapshot unexpectedly contains $absent" >&2; exit 1; }
done
export ALIGN_DOCKER_BIN="$docker_bin"
export ALIGN_TOOLCHAIN_DIR="$test_root/toolchain"
export ALIGN_CACHE_DIR="$test_root/cache"
export ALIGN_COMPOSE_PROJECT="align-macos-clean-$(date +%s)"
export ALIGN_HOST_PORT=${ALIGN_REPRO_PORT:-3101}
# Remove inherited host/project toolchain selection. HOME/Docker login remain untouched.
export PATH="$(dirname "$docker_bin"):/usr/bin:/bin:/usr/sbin:/sbin"
unset VIRTUAL_ENV PYTHONPATH NODE_PATH UV_PROJECT UV_PROJECT_ENVIRONMENT COMPOSE_FILE COMPOSE_PROJECT_NAME
echo "Evidence directory: $test_root"
echo "Isolated Compose project: $ALIGN_COMPOSE_PROJECT; port: $ALIGN_HOST_PORT"
printf '%s\n' "$ALIGN_COMPOSE_PROJECT" > "$test_root/compose-project.txt"
cd "$checkout"
bash scripts/macos-toolchain.sh
source scripts/macos-env.sh
shasum -a 256 uv.lock pnpm-lock.yaml > "$test_root/lockfiles-before.txt"
bash scripts/dev-macos.sh bootstrap
bash scripts/dev-macos.sh install
uv run --no-sync ruff check packages services tests migrations scripts
uv run --no-sync pytest -q
corepack pnpm --dir apps/web lint
corepack pnpm --dir apps/web typecheck
# Avoid depending on pre-existing application image layers or the running development database.
"$docker_bin" compose --project-name "$ALIGN_COMPOSE_PROJECT" build --no-cache api worker web
bash scripts/dev-macos.sh up
bash scripts/dev-macos.sh seed
bash scripts/dev-macos.sh verify
bash scripts/dev-macos.sh integration
shasum -a 256 uv.lock pnpm-lock.yaml > "$test_root/lockfiles-after.txt"
cmp "$test_root/lockfiles-before.txt" "$test_root/lockfiles-after.txt"
"$docker_bin" compose --project-name "$ALIGN_COMPOSE_PROJECT" ps --all
if [ "${ALIGN_KEEP_TEST_RUNNING:-0}" != 1 ]; then
  bash scripts/dev-macos.sh stop
fi
echo "Clean setup verification passed. Evidence, toolchain and test volumes retained: $test_root"
echo 'Parser runtime coverage is documented separately in docs/handover/B1.md.'
