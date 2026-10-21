"""Typed, non-authoritative B1/B2 drafting assistance outputs."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, StrictStr

from .contracts import StrictModel


class DraftSourceReference(StrictModel):
    source_kind: Literal["instruction", "criterion", "requirement"]
    source_id: UUID
    source_hash: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")
    quote: StrictStr = Field(min_length=1, max_length=2000)


class DraftSuggestion(StrictModel):
    proposal_kind: Literal["criterion", "requirement", "spec_element"]
    title: StrictStr = Field(min_length=1, max_length=240)
    description: StrictStr = Field(min_length=1, max_length=2000)
    source_refs: list[DraftSourceReference] = Field(min_length=1, max_length=20)


class DraftAssistanceOutput(StrictModel):
    schema_version: Literal[1]
    status: Literal["suggestions", "abstained"]
    suggestions: list[DraftSuggestion] = Field(max_length=100)
    limitations: list[StrictStr] = Field(max_length=100)
