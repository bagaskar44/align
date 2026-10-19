from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from align_domain.contracts import JobContract, JobLimits, JobScope, JobStage, JobState
from align_domain.providers import (
    MockProvider,
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


def test_live_provider_guard_fails_before_any_network_adapter_exists() -> None:
    with pytest.raises(PaidCallBlocked, match="PAID_CALL_GUARD_INCOMPLETE"):
        assert_live_call_guard(context())


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
            budget_usd="0.20",
        ),
    }
    assert JobContract.model_validate(payload).stage == JobStage.VALIDATE_MANIFEST
    with pytest.raises(ValidationError):
        JobContract.model_validate({**payload, "unknown": True})
    with pytest.raises(ValidationError):
        JobContract.model_validate({**payload, "input_fingerprint": "not-a-hash"})

