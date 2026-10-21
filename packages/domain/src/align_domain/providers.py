from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID

from pydantic import Field

from .contracts import StrictModel


class ProviderContext(StrictModel):
    call_id: UUID
    job_id: UUID
    manifest_id: UUID
    policy_id: UUID
    scope_ids: list[UUID]
    max_input_tokens: int = Field(ge=0)
    max_output_tokens: int = Field(ge=0)


class ProviderResult(StrictModel):
    call_id: UUID
    job_id: UUID
    manifest_id: UUID
    policy_id: UUID
    scope_ids: list[UUID]
    limits: dict[str, int]
    status: str
    payload: dict[str, object]
    usage: dict[str, int]
    simulated: bool
    fixture_id: str | None = None


class ModelProvider(Protocol):
    def extract_fields(self, context: ProviderContext, payload: dict[str, object]) -> ProviderResult: ...

    def rerank(self, context: ProviderContext, payload: dict[str, object]) -> ProviderResult: ...

    def interpret_evidence(self, context: ProviderContext, payload: dict[str, object]) -> ProviderResult: ...

    def verify_claim(self, context: ProviderContext, payload: dict[str, object]) -> ProviderResult: ...

    def draft_feedback(self, context: ProviderContext, payload: dict[str, object]) -> ProviderResult: ...

    def interpret_region(self, context: ProviderContext, payload: dict[str, object]) -> ProviderResult: ...

    def embed(self, context: ProviderContext, payload: dict[str, object]) -> ProviderResult: ...


class PaidCallBlocked(RuntimeError):
    pass


def assert_live_call_guard(context: ProviderContext) -> None:
    """Fail before a live adapter can perform network I/O until B0's finance guard is complete."""
    if not context.call_id or not context.job_id or not context.manifest_id or not context.policy_id:
        raise PaidCallBlocked("PAID_CALL_GUARD_INCOMPLETE")
    raise PaidCallBlocked("PAID_CALL_GUARD_INCOMPLETE")


class MockProvider:
    """Deterministic mock implementation; every result is explicitly marked simulated."""

    @staticmethod
    @lru_cache(maxsize=1)
    def _fixture() -> dict[str, object]:
        path = Path.cwd() / "fixtures" / "synthetic" / "provider-responses.json"
        fixture = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(fixture, dict):
            raise RuntimeError("synthetic provider fixture must contain a JSON object")
        if fixture.get("schema_version") != 1 or fixture.get("label") != "SIMULASI • data sintetis":
            raise RuntimeError("synthetic provider fixture is missing its required simulation label")
        return cast(dict[str, object], fixture)

    @staticmethod
    def _result(name: str, context: ProviderContext, payload: dict[str, object]) -> ProviderResult:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        digest = hashlib.sha256(f"{name}:{canonical}".encode()).hexdigest()
        fixtures = MockProvider._fixture()
        responses = fixtures.get("responses")
        if not isinstance(responses, dict) or name not in responses:
            raise RuntimeError(f"synthetic fixture does not define {name}")
        fixture_response = responses[name]
        if not isinstance(fixture_response, dict):
            raise RuntimeError(f"synthetic fixture entry {name} is invalid")
        result_payload = dict(fixture_response.get("payload", {}))
        if name == "extract_fields" and payload.get("task_type") == "context_fields":
            # A simulation echoes only source-bound, labeled parser candidates.
            # It does not pretend to infer unlabelled semantics from documents.
            result_payload = {"schema_version": 1, "candidates": payload.get("candidates", [])}
        else:
            result_payload["payload_hash"] = digest
        if name == "embed":
            dimensions = int(result_payload["dimensions"])
            if dimensions != 1536:
                raise RuntimeError("mock embedding fixture dimension must be 1536")
            result_payload["embedding"] = [float(result_payload.pop("vector_fill"))] * dimensions
        return ProviderResult(
            call_id=context.call_id,
            job_id=context.job_id,
            manifest_id=context.manifest_id,
            policy_id=context.policy_id,
            scope_ids=context.scope_ids,
            limits={"max_input_tokens": context.max_input_tokens, "max_output_tokens": context.max_output_tokens},
            status=str(fixture_response["status"]),
            payload=result_payload,
            usage={"input_tokens": 0, "output_tokens": 0},
            simulated=True,
            fixture_id=f"mock:{name}:v1",
        )

    def extract_fields(self, context: ProviderContext, payload: dict[str, object]) -> ProviderResult:
        return self._result("extract_fields", context, payload)

    def rerank(self, context: ProviderContext, payload: dict[str, object]) -> ProviderResult:
        return self._result("rerank", context, payload)

    def interpret_evidence(self, context: ProviderContext, payload: dict[str, object]) -> ProviderResult:
        return self._result("interpret_evidence", context, payload)

    def verify_claim(self, context: ProviderContext, payload: dict[str, object]) -> ProviderResult:
        return self._result("verify_claim", context, payload)

    def draft_feedback(self, context: ProviderContext, payload: dict[str, object]) -> ProviderResult:
        return self._result("draft_feedback", context, payload)

    def interpret_region(self, context: ProviderContext, payload: dict[str, object]) -> ProviderResult:
        return self._result("interpret_region", context, payload)

    def embed(self, context: ProviderContext, payload: dict[str, object]) -> ProviderResult:
        return self._result("embed", context, payload)
