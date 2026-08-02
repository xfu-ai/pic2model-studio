"""Frozen B01 provenance persistence contract."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProvenanceV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    source_kind: Literal["import", "tool", "user_edit", "conversion"]
    input_asset_ids: list[str] = Field(default_factory=list)
    prompt_asset_id: str | None = None
    selection_ids: list[str] = Field(default_factory=list)
    tool_call_id: str | None = None
    provider_profile: str | None = None
    model: str | None = None
    parameters: dict[str, object] = Field(default_factory=dict)
    original_filename: str | None = None
    created_at: str
