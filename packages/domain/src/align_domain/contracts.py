from __future__ import annotations

from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    READY_FOR_REVIEW = "ready_for_review"
    BLOCKED = "blocked"
    FAILED = "failed"


class JobStage(StrEnum):
    VALIDATE_MANIFEST = "validate_manifest"
    PARSE = "parse"
    BUILD_EVIDENCE = "build_evidence"
    INDEX = "index"
    EXECUTE_CHECKS = "execute_checks"
    VERIFY = "verify"
    READY_FOR_REVIEW = "ready_for_review"


class JobScope(StrictModel):
    course_id: UUID | None = None
    assignment_version_id: UUID | None = None
    submission_version_id: UUID | None = None


class JobLimits(StrictModel):
    max_calls: int = Field(ge=0)
    max_active_seconds: int = Field(ge=1)
    max_text_input_tokens: int = Field(ge=0)
    max_text_output_tokens: int = Field(ge=0)
    budget_usd: Decimal = Field(ge=Decimal("0"), decimal_places=8)

    @field_validator("budget_usd", mode="before")
    @classmethod
    def parse_budget_string(cls, value: object) -> Decimal:
        if isinstance(value, Decimal):
            return value
        if isinstance(value, str):
            try:
                return Decimal(value)
            except InvalidOperation as exc:
                raise ValueError("budget_usd must be a decimal string") from exc
        raise ValueError("budget_usd must be a decimal string")


class JobContract(StrictModel):
    id: UUID
    job_type: Literal["context_draft", "specification_draft", "analysis", "feedback_draft"]
    subject_id: UUID
    scope: JobScope
    input_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: JobState
    stage: JobStage | None
    limits: JobLimits

