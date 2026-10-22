"""Versioned, local-only context drafting configuration."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from align_api.config import Settings


def context_provider_payload(settings: Settings) -> dict[str, Any]:
    paths = ["packages/domain/src/align_domain/context_proposals.py",
             "packages/domain/src/align_domain/providers.py",
             "services/worker/src/align_worker/context_provider.py",
             "fixtures/synthetic/provider-responses.json"]
    return {"schema_version": 1, "purpose": "context_fields", "version_no": 1,
            "mode": settings.provider_mode, "data_mode": settings.data_mode, "role": "efficient",
            "model": settings.model_efficient,
            "simulated": settings.provider_mode == "mock",
            "live_calls": "ledger_guarded" if settings.provider_mode == "live" else "blocked_before_network",
            "implementation": {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in paths},
            "input_byte_cap": 20_000, "output_byte_cap": 4_000, "max_simulated_calls": 1}
