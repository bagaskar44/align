"""B1 simulated drafting through the shared adapter, with fail-closed validation."""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid5

from pydantic import ValidationError

from align_api.config import get_settings
from align_api.idempotency import canonical_hash
from align_domain.context_proposals import ContextFieldOutput
from align_domain.providers import (
    MockProvider,
    ModelProvider,
    OpenAIResponsesProvider,
    PaidCallBlocked,
    ProviderContext,
)


def propose_context_fields(
    job_id: UUID, context_id: UUID, scope: dict[str, Any], candidates: list[dict[str, Any]],
    provider: ModelProvider | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Use the configured adapter; every candidate remains source-bound and lecturer-reviewed."""
    config = scope.get("context_provider", {})
    mode = config.get("mode")
    if mode not in {"mock", "live"} or (mode == "mock" and config.get("data_mode") != "synthetic"):
        return [], {"status": "provider_disabled", "simulated": False}
    try:
        # Parser-only checkpoints may contain an extra anchor. The provider sees
        # the source_ref containing that anchor, with no authority to alter it.
        bound = [{key: item[key] for key in ("field_path", "value", "source_ref")} for item in candidates]
        validated = ContextFieldOutput.model_validate({"schema_version": 1, "candidates": bound})
        provider_candidates = [
            {
                **item.model_dump(mode="json", exclude={"source_ref"}),
                "source_ref": json.dumps(item.source_ref, ensure_ascii=False, sort_keys=True),
            }
            for item in validated.candidates
        ]
        payload = {
            "task_type": "context_fields",
            "schema_version": 1,
            "candidates": provider_candidates,
            "json_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["schema_version", "candidates"],
                "properties": {
                    "schema_version": {"type": "integer", "enum": [1]},
                    "candidates": {
                        "type": "array",
                        "maxItems": 100,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["field_path", "value", "source_ref"],
                            "properties": {
                                "field_path": {"type": "string", "enum": [
                                    "course_code", "course_title", "context_description", "number_locale", "language"
                                ]},
                                "value": {"type": "string", "minLength": 1, "maxLength": 4000},
                                "source_ref": {"type": "string", "maxLength": 4000},
                            },
                        },
                    },
                },
            },
        }
        # Conservative UTF-8 byte caps keep this deterministic simulation within
        # the 20k/4k token ceilings without claiming a model tokenizer was run.
        if len(json.dumps(payload, ensure_ascii=False).encode()) > 20_000:
            return [], {"status": "processing_limit", "simulated": True}
        call = ProviderContext(
            call_id=uuid5(job_id, canonical_hash(payload)), job_id=job_id,
            manifest_id=UUID(config["manifest_id"]), policy_id=UUID(config["policy_id"]),
            scope_ids=[context_id], max_input_tokens=20_000, max_output_tokens=4_000,
        )
        selected_provider = provider
        if selected_provider is None:
            if mode == "mock":
                selected_provider = MockProvider()
            else:
                settings = get_settings()
                if not settings.openai_api_key:
                    return [], {"status": "provider_disabled", "simulated": False}
                selected_provider = OpenAIResponsesProvider(
                    settings.openai_api_key.get_secret_value(),
                    {
                        "efficient": settings.model_efficient or "",
                        "extract_fields": settings.model_efficient or "",
                    },
                )
        result = selected_provider.extract_fields(call, payload)
        expected_simulated = mode == "mock"
        if (result.call_id != call.call_id or result.job_id != call.job_id
                or result.manifest_id != call.manifest_id or result.policy_id != call.policy_id
                or result.scope_ids != call.scope_ids or result.limits != {
                    "max_input_tokens": 20_000, "max_output_tokens": 4_000}
                or result.simulated != expected_simulated
                or result.status != ("mocked" if expected_simulated else "completed")
                or result.usage.get("input_tokens", 0) > 20_000
                or result.usage.get("output_tokens", 0) > 4_000):
            return [], {"status": "model_response_invalid", "simulated": expected_simulated}
        raw_payload = dict(result.payload)
        raw_candidates = raw_payload.get("candidates")
        if isinstance(raw_candidates, list):
            parsed_candidates = []
            for candidate in raw_candidates:
                if not isinstance(candidate, dict) or not isinstance(candidate.get("source_ref"), str):
                    return [], {"status": "model_response_invalid", "simulated": expected_simulated}
                parsed_candidates.append({
                    **candidate,
                    "source_ref": json.loads(candidate["source_ref"]),
                })
            raw_payload["candidates"] = parsed_candidates
        output = ContextFieldOutput.model_validate(raw_payload)
        if len(json.dumps(result.payload, ensure_ascii=False).encode()) > 4_000:
            return [], {"status": "processing_limit", "simulated": True}
        allowed = {canonical_hash(item) for item in bound}
        proposed = output.model_dump(mode="json")["candidates"]
        if any(canonical_hash(item) not in allowed for item in proposed):
            return [], {"status": "model_response_invalid", "simulated": True}
        return proposed, {"status": "mocked" if expected_simulated else "completed",
                          "simulated": expected_simulated, "fixture_id": result.fixture_id,
                          "call_id": str(call.call_id), "manifest_id": str(call.manifest_id),
                          "policy_id": str(call.policy_id), "input_sha256": canonical_hash(payload),
                          "output_sha256": canonical_hash(result.payload), "paid_calls": 0,
                          "usage": result.usage}
    except PaidCallBlocked:
        return [], {"status": "paid_call_guard_incomplete", "simulated": False, "paid_calls": 0}
    except (KeyError, ValueError, ValidationError, OSError, RuntimeError, TypeError):
        return [], {"status": "model_response_invalid", "simulated": True}
