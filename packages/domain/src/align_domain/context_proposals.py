"""Closed context-field output schema; ownership and source membership stay server-owned."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from .contracts import StrictModel


class ContextFieldProposal(StrictModel):
    field_path: Literal["course_code", "course_title", "context_description", "number_locale", "language"]
    value: str = Field(min_length=1, max_length=4000)
    source_ref: dict[str, Any]


class ContextFieldOutput(StrictModel):
    schema_version: Literal[1]
    candidates: list[ContextFieldProposal] = Field(max_length=100)
