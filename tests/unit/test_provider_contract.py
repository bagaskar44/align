from __future__ import annotations

from decimal import Decimal
from typing import Any
from urllib.error import URLError
from uuid import uuid4

import pytest
from pydantic import ValidationError

from align_domain.contracts import JobContract, JobLimits, JobScope, JobStage, JobState
from align_domain.providers import (
    MockProvider,
    OpenAIResponsesProvider,
    PaidCallBlocked,
    ProviderContext,
    assert_live_call_guard,
)


def context() -> ProviderContext:
    return ProviderContext(
        call_id=uuid4(),
        job_id=uuid4(),
        manifest_id=uuid4(),
        policy_id=uuid4(),
        scope_ids=[uuid4()],
        max_input_tokens=500,
        max_output_tokens=200,
    )


def test_mock_provider_uses_labeled_fixtures_and_preserves_context() -> None:
    provider = MockProvider()
    call = context()
    result = provider.interpret_evidence(call, {"evidence": "synthetic"})
    assert result.status == "mocked"
    assert result.simulated is True
    assert result.fixture_id == "mock:interpret_evidence:v1"
    assert result.call_id == call.call_id
    assert result.job_id == call.job_id
    assert result.manifest_id == call.manifest_id
    assert result.policy_id == call.policy_id
    assert result.scope_ids == call.scope_ids
    assert result.payload["fixture_result"] == "interpret_evidence"


def test_embedding_fixture_has_exactly_1536_dimensions() -> None:
    result = MockProvider().embed(context(), {"text": "sintetis"})
    vector = result.payload["embedding"]
    assert result.simulated is True
    assert result.payload["dimensions"] == 1536
    assert isinstance(vector, list)
    assert len(vector) == 1536


def test_live_embedding_adapter_validates_and_orders_every_vector(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OpenAIResponsesProvider("test-only-not-a-secret", {"embedding": "test-model"})
    call = context()
    first, second = [1.0] * 1536, [2.0] * 1536

    def fake_post(_context: ProviderContext, path: str, body: dict[str, Any]) -> dict[str, Any]:
        assert path == "embeddings"
        assert body["input"] == ["satu", "dua"]
        return {
            "data": [
                {"index": 1, "embedding": second},
                {"index": 0, "embedding": first},
            ],
            "usage": {"prompt_tokens": 2},
        }

    monkeypatch.setattr(provider, "_post", fake_post)
    result = provider.embed(call, {"model": "test-model", "input": ["satu", "dua"]})
    assert result.status == "completed" and result.simulated is False
    assert result.payload["embeddings"] == [first, second]
    assert result.payload["dimensions"] == 1536


def test_live_embedding_adapter_rejects_missing_or_nonfinite_vectors(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OpenAIResponsesProvider("test-only-not-a-secret", {"embedding": "test-model"})
    monkeypatch.setattr(
        provider,
        "_post",
        lambda *_args: {"data": [{"index": 0, "embedding": [float("nan")] * 1536}]},
    )
    with pytest.raises(RuntimeError, match="provider_embedding_dimension_invalid"):
        provider.embed(context(), {"input": "satu"})


def test_live_provider_guard_fails_before_any_network_adapter_exists() -> None:
    with pytest.raises(PaidCallBlocked, match="PAID_CALL_GUARD_INCOMPLETE"):
        assert_live_call_guard(context())


def test_paid_adapter_reserves_before_network_and_settles_actual_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    call = context().model_copy(update={
        "rate_manifest_id": uuid4(),
        "paid_calls_enabled": True,
        "billing_bounds": {
            "input_tokens": 400, "output_tokens": 200, "embedding_tokens": 0,
            "vision_calls": 0, "image_count": 0, "active_ms": 60_000,
        },
    })
    events: list[str] = []

    class FakeLedger:
        def reserve(self, _context: ProviderContext, model_id: str, bounds: dict[str, int]) -> None:
            assert model_id == "test-model"
            assert bounds["input_tokens"] <= 400
            events.append("reserved")

        def settle(self, _call_id: Any, *, provider_request_id: str | None, usage: dict[str, int]) -> None:
            assert provider_request_id == "req_test"
            assert usage["input_tokens"] == 10 and usage["output_tokens"] == 3
            events.append("settled")

        def unknown(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("successful provider response must settle")

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"id":"req_test","usage":{"input_tokens":10,"output_tokens":3}}'

    def fake_urlopen(*_args: Any, **_kwargs: Any) -> FakeResponse:
        assert events == ["reserved"]
        events.append("network")
        return FakeResponse()

    monkeypatch.setattr("align_domain.providers.urllib.request.urlopen", fake_urlopen)
    provider = OpenAIResponsesProvider(
        "test-only-not-a-secret", {"complex": "test-model"}, paid_call_ledger=FakeLedger()
    )
    result = provider._post(call, "responses", {"model": "test-model", "input": "small"})
    assert result["id"] == "req_test"
    assert events == ["reserved", "network", "settled"]


def test_paid_adapter_keeps_unknown_transport_attempt_in_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    call = context().model_copy(update={
        "rate_manifest_id": uuid4(),
        "paid_calls_enabled": True,
        "billing_bounds": {
            "input_tokens": 400, "output_tokens": 200, "embedding_tokens": 0,
            "vision_calls": 0, "image_count": 0, "active_ms": 60_000,
        },
    })
    events: list[str] = []

    class FakeLedger:
        def reserve(self, *_args: Any, **_kwargs: Any) -> None:
            events.append("reserved")

        def settle(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("transport failure cannot settle as known")

        def unknown(self, _call_id: Any, *, provider_request_id: str | None, reason: str) -> None:
            assert provider_request_id is None
            assert reason == "transport_or_response_error"
            events.append("unknown")

    def failed_urlopen(*_args: Any, **_kwargs: Any) -> None:
        events.append("network")
        raise URLError("synthetic transport failure")

    monkeypatch.setattr("align_domain.providers.urllib.request.urlopen", failed_urlopen)
    provider = OpenAIResponsesProvider(
        "test-only-not-a-secret", {"complex": "test-model"}, paid_call_ledger=FakeLedger()
    )
    with pytest.raises(RuntimeError, match="provider_transport_or_response_error"):
        provider._post(call, "responses", {"model": "test-model", "input": "small"})
    assert events == ["reserved", "network", "unknown"]


def test_job_contract_rejects_extra_fields_and_invalid_fingerprints() -> None:
    payload = {
        "id": uuid4(),
        "job_type": "analysis",
        "subject_id": uuid4(),
        "scope": JobScope(),
        "input_fingerprint": "f" * 64,
        "state": JobState.QUEUED,
        "stage": JobStage.VALIDATE_MANIFEST,
        "limits": JobLimits(
            max_calls=16,
            max_active_seconds=600,
            max_text_input_tokens=40_000,
            max_text_output_tokens=8_000,
            budget_usd=Decimal("0.20"),
        ),
    }
    assert JobContract.model_validate(payload).stage == JobStage.VALIDATE_MANIFEST
    with pytest.raises(ValidationError):
        JobContract.model_validate({**payload, "unknown": True})
    with pytest.raises(ValidationError):
        JobContract.model_validate({**payload, "input_fingerprint": "not-a-hash"})

