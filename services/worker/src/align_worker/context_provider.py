"""B1 simulated drafting through the shared adapter, with fail-closed validation."""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid5

from pydantic import ValidationError

from align_api.idempotency import canonical_hash
from align_domain.context_proposals import ContextFieldOutput
from align_domain.providers import MockProvider, ModelProvider, ProviderContext


def propose_context_fields(
    job_id: UUID, context_id: UUID, scope: dict[str, Any], candidates: list[dict[str, Any]],
    provider: ModelProvider | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """No live adapter is constructed here; missing/disabled/invalid output is manual-only."""
    config = scope.get("context_provider", {})
    if config.get("mode") != "mock" or config.get("data_mode") != "synthetic":
        return [], {"status": "provider_disabled", "simulated": False}
    try:
        # Parser-only checkpoints may contain an extra anchor. The provider sees
        # the source_ref containing that anchor, with no authority to alter it.
        bound = [{key: item[key] for key in ("field_path", "value", "source_ref")} for item in candidates]
        validated = ContextFieldOutput.model_validate({"schema_version": 1, "candidates": bound})
        payload = {"task_type": "context_fields", **validated.model_dump(mode="json")}
        # Conservative UTF-8 byte caps keep this deterministic simulation within
        # the 20k/4k token ceilings without claiming a model tokenizer was run.
        if len(json.dumps(payload, ensure_ascii=False).encode()) > 20_000:
            return [], {"status": "processing_limit", "simulated": True}
        call = ProviderContext(
            call_id=uuid5(job_id, canonical_hash(payload)), job_id=job_id,
            manifest_id=UUID(config["manifest_id"]), policy_id=UUID(config["policy_id"]),
            scope_ids=[context_id], max_input_tokens=20_000, max_output_tokens=4_000,
        )
        result = (provider or MockProvider()).extract_fields(call, payload)
        if (result.call_id != call.call_id or result.job_id != call.job_id
                or result.manifest_id != call.manifest_id or result.policy_id != call.policy_id
                or result.scope_ids != call.scope_ids or result.limits != {
                    "max_input_tokens": 20_000, "max_output_tokens": 4_000}
                or not result.simulated or result.status != "mocked"
                or result.usage != {"input_tokens": 0, "output_tokens": 0}):
            return [], {"status": "model_response_invalid", "simulated": True}
        output = ContextFieldOutput.model_validate(result.payload)
        if len(json.dumps(result.payload, ensure_ascii=False).encode()) > 4_000:
            return [], {"status": "processing_limit", "simulated": True}
        allowed = {canonical_hash(item) for item in bound}
        proposed = output.model_dump(mode="json")["candidates"]
        if any(canonical_hash(item) not in allowed for item in proposed):
            return [], {"status": "model_response_invalid", "simulated": True}
        return proposed, {"status": "mocked", "simulated": True, "fixture_id": result.fixture_id,
                          "call_id": str(call.call_id), "manifest_id": str(call.manifest_id),
                          "policy_id": str(call.policy_id), "input_sha256": canonical_hash(payload),
                          "output_sha256": canonical_hash(result.payload), "paid_calls": 0}
    except (KeyError, ValueError, ValidationError, OSError, RuntimeError, TypeError):
        return [], {"status": "model_response_invalid", "simulated": True}
