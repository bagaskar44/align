#!/usr/bin/env python3
"""Measure synthetic local API list/save latency with independent sessions.

This is a bounded software-capacity smoke, not a general load generator. It
creates isolated draft contexts in the synthetic IF101 course and makes no
provider calls. The seed password is read from the repository .env and never
printed or passed as a command-line argument.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def percentile95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", help="local synthetic app origin (defaults to APP_ORIGIN)")
    parser.add_argument("--sessions", type=int, default=20)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--target-p95-ms", type=float, default=1000.0)
    args = parser.parse_args()
    if not 1 <= args.sessions <= 20 or not 1 <= args.rounds <= 20:
        raise SystemExit("sessions and rounds must be between 1 and 20")

    env = read_env(Path(__file__).resolve().parents[1] / ".env")
    if env.get("PROVIDER_MODE") != "mock" or env.get("DATA_MODE") != "synthetic":
        raise SystemExit("Refusing capacity run outside mock/synthetic mode")
    password = env.get("ALIGN_SEED_PASSWORD")
    if not password:
        raise SystemExit("ALIGN_SEED_PASSWORD is required in the local .env")
    base_url = (args.base_url or env.get("APP_ORIGIN", "")).rstrip("/")
    parsed = urlsplit(base_url)
    if (parsed.scheme != "http" or parsed.hostname != "localhost" or parsed.port is None
            or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment):
        raise SystemExit("Capacity run is restricted to an explicit local http://localhost origin")

    clients = [httpx.AsyncClient(base_url=base_url, timeout=20.0) for _ in range(args.sessions)]
    samples: dict[str, list[float]] = {"course_list_ms": [], "context_list_ms": [], "draft_save_ms": []}
    try:
        async def login(client: httpx.AsyncClient) -> str:
            response = await client.post(
                "/api/v1/auth/login",
                headers={"Origin": base_url},
                json={"email": "dosen@align.local", "password": password},
            )
            if response.status_code != 200:
                raise RuntimeError(f"synthetic lecturer login returned HTTP {response.status_code}")
            csrf = response.json().get("csrf_token")
            if not isinstance(csrf, str) or not csrf:
                raise RuntimeError("login did not return a CSRF token")
            return csrf

        csrf_tokens = await asyncio.gather(*(login(client) for client in clients))
        course_response = await clients[0].get("/api/v1/courses")
        if course_response.status_code != 200:
            raise RuntimeError(f"course metadata lookup returned HTTP {course_response.status_code}")
        courses = course_response.json().get("items", [])
        course = next((item for item in courses if item.get("code") == "IF101"), None)
        if not isinstance(course, dict) or not isinstance(course.get("id"), str):
            raise RuntimeError("synthetic IF101 seed course is unavailable")
        course_id = course["id"]

        async def make_draft(index: int, client: httpx.AsyncClient, csrf: str) -> tuple[str, int]:
            response = await client.post(
                f"/api/v1/courses/{course_id}/context-drafts",
                headers={"Origin": base_url, "X-CSRF-Token": csrf, "Idempotency-Key": str(uuid4())},
                json={"manual_fields": {"context_description": f"Konteks kapasitas sintetis {index}"}},
            )
            if response.status_code != 201:
                raise RuntimeError(f"synthetic draft setup returned HTTP {response.status_code}")
            context = response.json().get("context", {})
            return str(context["id"]), int(context["revision"])

        contexts = await asyncio.gather(*(
            make_draft(index, client, csrf)
            for index, (client, csrf) in enumerate(zip(clients, csrf_tokens, strict=True), start=1)
        ))

        async def measured(name: str, operation: Any) -> None:
            started = time.perf_counter()
            response = await operation
            elapsed_ms = (time.perf_counter() - started) * 1000
            if response.status_code not in {200, 201}:
                raise RuntimeError(f"{name} returned HTTP {response.status_code}")
            samples[name].append(elapsed_ms)

        async def session_round(index: int, client: httpx.AsyncClient, csrf: str) -> None:
            context_id, revision = contexts[index]
            await measured("course_list_ms", client.get("/api/v1/courses"))
            await measured("context_list_ms", client.get(f"/api/v1/courses/{course_id}/context-versions"))
            await measured("draft_save_ms", client.put(
                f"/api/v1/context-versions/{context_id}",
                headers={"Origin": base_url, "X-CSRF-Token": csrf},
                json={"expected_revision": revision,
                      "fields": {"context_description": f"Konteks kapasitas sintetis {index} putaran {revision + 1}"}},
            ))
            contexts[index] = (context_id, revision + 1)

        for _round in range(args.rounds):
            await asyncio.gather(*(
                session_round(index, client, csrf)
                for index, (client, csrf) in enumerate(zip(clients, csrf_tokens, strict=True))
            ))

        actions = {
            name: {
                "samples": len(values),
                "median_ms": round(statistics.median(values), 2),
                "p95_ms": round(percentile95(values), 2),
                "max_ms": round(max(values), 2),
                "target_p95_ms": args.target_p95_ms,
                "target_met": percentile95(values) < args.target_p95_ms,
            }
            for name, values in samples.items()
        }
        print(json.dumps({
            "status": "measured",
            "data_mode": "synthetic",
            "provider_mode": "mock",
            "sessions": args.sessions,
            "rounds": args.rounds,
            "independent_login_sessions": len(csrf_tokens),
            "actions": actions,
            "limitations": [
                "Single local Compose host and seeded lecturer account; not a production capacity claim.",
                "No submission files, parser jobs, paid provider calls, or external network transfer were included.",
            ],
        }, sort_keys=True, indent=2))
        if any(not result["target_met"] for result in actions.values()):
            raise SystemExit(1)
    finally:
        await asyncio.gather(*(client.aclose() for client in clients))


if __name__ == "__main__":
    asyncio.run(main())
