#!/bin/bash
# Test HEAD plus the candidate setup scripts in an empty snapshot; never copy ignored installs.
set -euo pipefail
# Snapshot the current non-ignored source tree, including B8 work in progress,
# while keeping machine-local IDE state out. Source files must retain normal
# readable modes for the non-root Docker runtime.
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
git ls-files --deleted -z | while IFS= read -r -d '' deleted; do rm -f "$checkout/$deleted"; done
git ls-files --cached --others --exclude-standard \
  | rg -v '^(\.vscode/|\.DS_Store$)|/\.DS_Store$' > "$test_root/checkout-source-files.txt"
test -s "$test_root/checkout-source-files.txt"
tar -cf - -T "$test_root/checkout-source-files.txt" | tar -xf - -C "$checkout"
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
# Retained test/development networks may occupy previously hard-coded CIDRs.
# Inspect existing networks and select an unused RFC1918 pair without pruning them.
python3 - "$docker_bin" "$test_root/compose-network.yml" "$test_root/compose-network.txt" <<'PY'
import ipaddress
import json
import subprocess
import sys
from pathlib import Path

docker_bin, compose_file, evidence_file = sys.argv[1:]
network_ids = subprocess.run(
    [docker_bin, "network", "ls", "-q"], capture_output=True, text=True, check=True
).stdout.splitlines()
used = []
for network_id in network_ids:
    output = subprocess.run(
        [docker_bin, "network", "inspect", network_id], capture_output=True, text=True, check=True
    ).stdout
    networks = json.loads(output)
    for network in networks:
        for item in network.get("IPAM", {}).get("Config") or []:
            try:
                used.append(ipaddress.ip_network(item["Subnet"], strict=False))
            except (KeyError, ValueError):
                continue

pair = None
for subnet_octet in range(16, 254, 2):
    edge = ipaddress.ip_network(f"10.{subnet_octet}.0.0/24")
    private = ipaddress.ip_network(f"10.{subnet_octet + 1}.0.0/20")
    if any(edge.overlaps(item) or private.overlaps(item) for item in used):
        continue
    pair = edge, private
    break
if pair is None:
    raise SystemExit("no unused 10.x.x.x test network pair; choose an unused RFC1918 pair manually")
edge, private = pair
Path(compose_file).write_text(
    "services: {}\nnetworks:\n"
    "  edge:\n    ipam:\n      config:\n        - subnet: " + str(edge) + "\n"
    "  private:\n    ipam:\n      config:\n        - subnet: " + str(private) + "\n",
    encoding="utf-8",
)
Path(evidence_file).write_text(f"edge={edge}\nprivate={private}\n", encoding="utf-8")
PY
export COMPOSE_FILE="$checkout/docker-compose.yml:$test_root/compose-network.yml"
echo "Evidence directory: $test_root"
echo "Isolated Compose project: $ALIGN_COMPOSE_PROJECT; port: $ALIGN_HOST_PORT"
printf '%s\n' "$ALIGN_COMPOSE_PROJECT" > "$test_root/compose-project.txt"
cleanup_failed_project() {
  status=$?
  if [ "$status" -ne 0 ] && [ -f "$checkout/.env" ] && [ -f "$test_root/compose-network.yml" ]; then
    /usr/bin/python3 - "$docker_bin" "$ALIGN_COMPOSE_PROJECT" "$checkout/.env" \
      "$checkout/docker-compose.yml" "$test_root/compose-network.yml" "$test_root/cleanup.log" <<'PY'
import subprocess
import sys
from pathlib import Path

docker_bin, project, env_file, base, network, log_file = sys.argv[1:]
command = [docker_bin, "compose", "--project-name", project, "--env-file", env_file,
           "-f", base, "-f", network, "down", "--volumes", "--remove-orphans"]
try:
    result = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
    Path(log_file).write_text(
        f"exit={result.returncode}\n{result.stdout}{result.stderr}", encoding="utf-8"
    )
except subprocess.TimeoutExpired as exc:
    Path(log_file).write_text(
        f"cleanup timed out after 20 seconds\n{exc.stdout or ''}{exc.stderr or ''}",
        encoding="utf-8",
    )
PY
    echo "Test project cleanup attempted; inspect $test_root/cleanup.log" >&2
  fi
  exit "$status"
}
trap cleanup_failed_project EXIT
cd "$checkout"
bash scripts/macos-toolchain.sh
source scripts/macos-env.sh
shasum -a 256 uv.lock pnpm-lock.yaml > "$test_root/lockfiles-before.txt"
bash scripts/dev-macos.sh bootstrap
bash scripts/dev-macos.sh install
uv run --no-sync ruff check packages services tests migrations scripts
uv run --no-sync pytest -q --import-mode=importlib
corepack pnpm --dir apps/web lint
corepack pnpm --dir apps/web typecheck
# Avoid depending on pre-existing application image layers or the running development database.
"$docker_bin" compose --project-name "$ALIGN_COMPOSE_PROJECT" build --no-cache api worker web
bash scripts/dev-macos.sh up
bash scripts/dev-macos.sh seed
bash scripts/dev-macos.sh release-baseline
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
