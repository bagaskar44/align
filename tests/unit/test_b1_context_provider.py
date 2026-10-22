from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from align_api.context_provider_profiles import context_provider_payload
from align_domain.providers import (
    MockProvider,
    OpenAIResponsesProvider,
    ProviderContext,
    ProviderResult,
)
from align_worker.context_provider import propose_context_fields


def inputs() -> tuple[UUID, UUID, dict[str, Any], list[dict[str, Any]]]:
    job, context = uuid4(), uuid4()
    scope = {"context_provider": {"mode": "mock", "data_mode": "synthetic",
                                  "manifest_id": str(uuid4()), "policy_id": str(uuid4())}}
    candidates = [{"field_path": "context_description", "value": "Konteks sintetis",
                   "source_ref": {"file_id": str(uuid4()), "source_sha256": "a" * 64,
                                  "source_role": "kk", "anchor": {"format": "pdf", "page": 1}}}]
    return job, context, scope, candidates


def test_mock_context_output_is_closed_source_bound_and_explicitly_simulated() -> None:
    job, context, scope, candidates = inputs()
    proposed, outcome = propose_context_fields(job, context, scope, candidates)
    assert proposed == candidates
    assert outcome["status"] == "mocked" and outcome["simulated"]
    assert outcome["paid_calls"] == 0
    assert outcome["fixture_id"] == "mock:extract_fields:v1"


@pytest.mark.parametrize("failure", ["refused", "schema", "foreign_anchor", "foreign_scope", "unavailable"])
def test_invalid_refused_unavailable_provider_never_produces_context_fields(failure: str) -> None:
    class BrokenProvider(MockProvider):
        def extract_fields(self, call: ProviderContext, payload: dict[str, object]) -> ProviderResult:
            if failure == "unavailable":
                raise TimeoutError("synthetic provider failure")
            result = super().extract_fields(call, payload)
            if failure == "refused":
                return result.model_copy(update={"status": "refused"})
            if failure == "schema":
                return result.model_copy(update={"payload": {"schema_version": 1, "candidates": [], "extra": True}})
            if failure == "foreign_scope":
                return result.model_copy(update={"scope_ids": [uuid4()]})
            return result.model_copy(update={"payload": {"schema_version": 1, "candidates": [
                {"field_path": "language", "value": "id", "source_ref": {"file_id": str(uuid4())}},
            ]}})
    proposed, outcome = propose_context_fields(*inputs(), provider=BrokenProvider())
    assert proposed == [] and outcome["status"] == "model_response_invalid"


def test_off_mode_never_constructs_a_provider() -> None:
    class NeverCalled(MockProvider):
        def extract_fields(self, call: ProviderContext, payload: dict[str, object]) -> ProviderResult:
            pytest.fail("disabled provider must not be invoked")
    job, context, scope, candidates = inputs()
    scope["context_provider"]["mode"] = "off"
    proposed, outcome = propose_context_fields(job, context, scope, candidates, NeverCalled())
    assert proposed == [] and outcome["status"] == "provider_disabled"


def test_live_context_adapter_is_configured_but_financial_guard_stops_before_network() -> None:
    job, context, scope, candidates = inputs()
    scope["context_provider"]["mode"] = "live"
    adapter = OpenAIResponsesProvider("test-only-not-a-secret", {"extract_fields": "test-model"})
    proposed, outcome = propose_context_fields(job, context, scope, candidates, adapter)
    assert proposed == []
    assert outcome["status"] == "paid_call_guard_incomplete"
    assert outcome["paid_calls"] == 0


def test_context_output_cap_retains_manual_fallback_without_silent_truncation() -> None:
    job, context, scope, candidates = inputs()
    candidates[0]["value"] = "x" * 4000
    proposed, outcome = propose_context_fields(job, context, scope, candidates)
    assert proposed == [] and outcome["status"] == "processing_limit"


def test_context_model_profile_pins_model_and_labels_live_calls_as_ledger_guarded() -> None:
    profile = context_provider_payload(SimpleNamespace(
        provider_mode="live", data_mode="synthetic", model_efficient="synthetic-model",
    ))  # type: ignore[arg-type]
    assert profile["model"] == "synthetic-model"
    assert profile["live_calls"] == "ledger_guarded"
