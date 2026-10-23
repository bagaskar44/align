#!/usr/bin/env python3
"""Close a local paid-call cap, then stop its isolated Compose project."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE_CONFIG = (ROOT / "config" / "live-local").resolve()
SAFE_PROJECT = re.compile(r"^align-livecheck-[a-z0-9][a-z0-9-]{0,60}$")


def stop(metadata_value: str) -> int:
    metadata_path = Path(metadata_value).resolve()
    if LIVE_CONFIG not in metadata_path.parents or metadata_path.name != "local-run.json":
        raise SystemExit("metadata must be a generated local-run.json under config/live-local/")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit("local-run metadata is missing or invalid") from exc
    if not isinstance(metadata, dict):
        raise SystemExit("local-run metadata must be a JSON object")
    project = metadata.get("project_name")
    if not isinstance(project, str) or not SAFE_PROJECT.fullmatch(project):
        raise SystemExit("metadata does not identify an isolated ALIGN livecheck Compose project")
    release_label = metadata.get("release_version_label")
    if not isinstance(release_label, str) or not release_label.strip():
        raise SystemExit("metadata has no live release label; inspect this project before stopping")

    passthrough = {"PATH", "HOME", "TMPDIR", "SSH_AUTH_SOCK", "LANG", "LC_ALL"}
    env = {
        key: value for key, value in os.environ.items()
        if key in passthrough or key.startswith("DOCKER_")
    }
    env["ALIGN_HOST_PORT"] = str(metadata.get("host_port", 3311))
    env["APP_ORIGIN"] = str(metadata.get("app_origin", "http://localhost:3311"))
    project_args = ["docker", "compose", "--project-name", project]
    close = [
        *project_args, "--profile", "operator", "run", "--rm", "operator",
        "live", "close-current",
        "--release-version-label", release_label,
        "--reason", "operator stopped the isolated local livecheck session",
    ]
    result = subprocess.run(
        close, cwd=ROOT, env=env, check=False, capture_output=True, text=True,
    )  # noqa: S603 -- fixed executable and argument vector; no shell
    if result.returncode != 0:
        print("Spend run could not be closed; Compose stack was left running with its cap in force.", file=sys.stderr)
        return result.returncode
    close_result = None
    for line in reversed(result.stdout.splitlines()):
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and candidate.get("state") in {"closed", "none"}:
            close_result = candidate
            break
    if close_result is None:
        print("Operator close returned no recognized state; isolated Compose stack was left running.", file=sys.stderr)
        return 2
    metadata["state"] = "closed" if close_result["state"] == "closed" else "stopped_no_run"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    down = subprocess.run(
        [*project_args, "down"], cwd=ROOT, env=env, check=False,
    )  # noqa: S603 -- fixed executable and argument vector; no shell
    if down.returncode != 0:
        print("Spend run is closed; isolated containers remain for inspection. Volumes were not removed.", file=sys.stderr)
        return down.returncode
    print(json.dumps({
        "status": "stopped",
        "compose_project": project,
        "spend_run_close_result": close_result["state"],
        "no_active_paid_run": True,
        "volumes_removed": False,
        "provider_calls_performed": 0,
    }, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", required=True, help="local-run.json emitted by prepare-live-local.py")
    return stop(parser.parse_args().metadata)


if __name__ == "__main__":
    raise SystemExit(main())
