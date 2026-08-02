"""Strict, deliberately small transport DTOs for the Agent sidecar API."""

from typing import Annotated, Literal

from pydantic import Field

from . import StrictRequest


class CreateConversationRequest(StrictRequest):
    project_id: str = Field(min_length=1)
    system_prompt: str = Field(default="", max_length=20_000)
    model: str | None = Field(default=None, max_length=200)


class SendAgentMessageRequest(StrictRequest):
    project_id: str = Field(min_length=1)
    content: str = Field(min_length=1, max_length=100_000)
    asset_refs: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=8
    )
    request_id: str = Field(min_length=1)
    wait: bool = False


class AgentConversationRequest(StrictRequest):
    project_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)


class AgentQueueMessageRequest(AgentConversationRequest):
    content: str = Field(min_length=1, max_length=100_000)
    kind: Literal["steer", "follow_up"]


class ActivateSkillRequest(AgentConversationRequest):
    name: str = Field(min_length=1, max_length=200)
