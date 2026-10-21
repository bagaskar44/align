from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

from pydantic import SecretStr
from sqlalchemy.orm import Session

from align_api.models import AnalysisRun, CheckElement, SubmissionVersion
from align_domain.providers import OpenAIResponsesProvider
from align_worker import drafting
from align_worker.executor import (
    _estimated_payload_tokens,
    _pack_embedding_batches,
    _pack_negative_scan_batches,
    _verify_absence_in_batches,
)


def _job(mode: str) -> Any:
    return type("JobStub", (), {
        "id": uuid4(),
        "input_fingerprint": "a" * 64,
        "scope_json": {
            "provider_mode": mode,
            "model_manifest_id": str(uuid4()),
            "routing_manifest_id": str(uuid4()),
            "assignment_version_id": str(uuid4()),
            "target_id": str(uuid4()),
        },
    })()


def test_mock_b2_drafting_abstains_without_invented_suggestions() -> None:
    job = _job("mock")
    output = drafting._attempt(
        job,
        {"task_type": "criteria_draft", "instruction": "Jelaskan konsep."},
        [{"source_kind": "instruction", "source_id": str(uuid4()), "source_hash": "b" * 64,
          "quote": "Jelaskan konsep."}],
        object(),
    )
    assert output["status"] == "mock_abstention"
    assert output["simulated"] is True
    assert output["suggestions"] == []
    assert output["paid_calls"] == 0


def test_configured_live_b2_drafting_stops_at_paid_call_guard(monkeypatch: Any) -> None:
    job = _job("live")
    monkeypatch.setattr(
        drafting,
        "_provider",
        lambda _settings, _mode: OpenAIResponsesProvider(
            "synthetic-test-key", {"efficient": "synthetic-model", "extract_fields": "synthetic-model"}
        ),
    )
    output = drafting._attempt(
        job,
        {"task_type": "criteria_draft", "instruction": "Jelaskan konsep."},
        [{"source_kind": "instruction", "source_id": str(uuid4()), "source_hash": "b" * 64,
          "quote": "Jelaskan konsep."}],
        type("SettingsStub", (), {"openai_api_key": SecretStr("synthetic-test-key"),
                                   "model_efficient": "synthetic-model"})(),
    )
    assert output["status"] == "paid_call_guard_blocked_before_network"
    assert output["simulated"] is False
    assert output["paid_calls"] == 0


def test_embedding_batches_split_without_omitting_text_and_fail_oversized_input_closed() -> None:
    texts = ["a" * 11_000, "b" * 11_000, "c" * 11_000]
    batches = _pack_embedding_batches(texts, "synthetic-model", token_limit=3_000)
    assert [item for batch in batches for item in batch] == texts
    assert all(_estimated_payload_tokens({"input": batch, "model": "synthetic-model"}) <= 3_000
               for batch in batches)
    assert _pack_embedding_batches(["x" * 20_000], "synthetic-model", token_limit=1_000) == []


def test_absence_scan_batches_cover_each_full_source_once_or_reject_the_scan() -> None:
    sources = [{"evidence_id": f"source-{index}", "text": "evidence " + str(index) + " " + "x" * 9_000}
               for index in range(3)]
    batches = _pack_negative_scan_batches(base_payload={"sources": sources}, base_claim="a claim", token_limit=3_000)
    flattened = [item for batch in batches for item in batch]
    assert flattened == sources
    assert len({item["evidence_id"] for item in flattened}) == len(sources)
    assert all(
        _estimated_payload_tokens({
            "task_type": "verify_claim",
            "claim_to_verify": f"Segment {index}: no compatible source in this segment supports: a claim",
            "complete_source_scan": True,
            "sources": batch,
        }) <= 3_000
        for index, batch in enumerate(batches, start=1)
    )
    assert _pack_negative_scan_batches(
        base_payload={"sources": [{"evidence_id": "huge", "text": "x" * 100_000}]},
        base_claim="a claim",
        token_limit=1_000,
    ) == []


def test_absence_executor_accepts_only_complete_test_provider_scan(monkeypatch: Any) -> None:
    sources = [{"evidence_id": f"ev-{index}", "text": "source " + "x" * 11_000}
               for index in range(5)]
    calls: list[list[dict[str, Any]]] = []

    def synthetic_verifier(_db: Any, **kwargs: Any) -> tuple[Any, str, bool, int]:
        payload = kwargs["payload"]
        calls.append(payload["sources"])
        return (
            SimpleNamespace(payload={
                "status": "supported",
                "claim": payload["claim_to_verify"],
                "evidence_ids": [],
                "explanation": "Synthetic test verifier reviewed this segment.",
            }),
            "completed",
            False,
            0,
        )

    monkeypatch.setattr("align_worker.executor._invoke_provider", synthetic_verifier)
    result, exhaustive, status = _verify_absence_in_batches(
        cast(Session, SimpleNamespace(add=lambda _value: None, flush=lambda: None)),
        provider=object(),
        claim="A required source-backed statement is absent.",
        decision_question="Does the source satisfy the obligation?",
        grounding_mode="anchored_content",
        sources=sources,
        run=cast(AnalysisRun, SimpleNamespace(id=uuid4(), job_id=uuid4())),
        source=cast(SubmissionVersion, SimpleNamespace(id=uuid4())),
        element_row=cast(CheckElement, SimpleNamespace(id=uuid4())),
        mode="synthetic_test_provider",
        limits={"max_calls": 16, "max_text_input_tokens": 40_000},
        counters={"calls": 0, "input_tokens": 0},
        model_manifest_id=uuid4(),
        routing_manifest_id=uuid4(),
        batching_manifest_id=uuid4(),
    )
    observed = [source["evidence_id"] for batch in calls for source in batch]
    assert len(calls) > 1
    assert sorted(observed) == sorted(source["evidence_id"] for source in sources)
    assert result is not None and result.status == "supported" and result.evidence_ids == []
    assert exhaustive is True
    assert status == "semantic_scan_completed"


def test_absence_executor_with_insufficient_segment_keeps_negative_gate_closed(monkeypatch: Any) -> None:
    def synthetic_insufficient(_db: Any, **kwargs: Any) -> tuple[Any, str, bool, int]:
        payload = kwargs["payload"]
        return (
            SimpleNamespace(payload={
                "status": "insufficient",
                "claim": payload["claim_to_verify"],
                "evidence_ids": [],
                "explanation": "Synthetic test segment could not be assessed.",
            }),
            "completed",
            False,
            0,
        )

    monkeypatch.setattr("align_worker.executor._invoke_provider", synthetic_insufficient)
    result, exhaustive, status = _verify_absence_in_batches(
        cast(Session, SimpleNamespace(add=lambda _value: None, flush=lambda: None)),
        provider=object(),
        claim="A required source-backed statement is absent.",
        decision_question="Does the source satisfy the obligation?",
        grounding_mode="anchored_content",
        sources=[{"evidence_id": "ev-1", "text": "synthetic text"}],
        run=cast(AnalysisRun, SimpleNamespace(id=uuid4(), job_id=uuid4())),
        source=cast(SubmissionVersion, SimpleNamespace(id=uuid4())),
        element_row=cast(CheckElement, SimpleNamespace(id=uuid4())),
        mode="synthetic_test_provider",
        limits={"max_calls": 16, "max_text_input_tokens": 40_000},
        counters={"calls": 0, "input_tokens": 0},
        model_manifest_id=uuid4(),
        routing_manifest_id=uuid4(),
        batching_manifest_id=uuid4(),
    )
    assert result is not None and result.status == "insufficient"
    assert exhaustive is False
    assert status == "semantic_scan_insufficient"
