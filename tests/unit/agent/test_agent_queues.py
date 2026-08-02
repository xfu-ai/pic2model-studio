from __future__ import annotations

from dataclasses import dataclass

import pytest

from aipic_to_model.agent.core.agent import Agent
from aipic_to_model.agent.core.events import AgentEventType, CancellationToken
from aipic_to_model.agent.core.models import (
    AssistantMessage,
    ProviderEvent,
    ProviderEventType,
    TextContent,
    ToolCall,
    ToolResult,
)
from aipic_to_model.agent.core.tool import ToolContext
from aipic_to_model.agent.providers.base import ModelProfile
from aipic_to_model.agent.providers.fake import FakeProvider, ScriptedResponse


def response(message: AssistantMessage) -> ScriptedResponse:
    return ScriptedResponse(
        (
            ProviderEvent(ProviderEventType.MESSAGE_START),
            ProviderEvent(ProviderEventType.MESSAGE_END, message=message),
        )
    )


@dataclass
class EchoTool:
    name: str = "echo"
    label: str = "Echo"
    description: str = "Echoes a value"
    parameters: dict[str, object] = None  # type: ignore[assignment]
    execution_mode: str = "sequential"

    def __post_init__(self) -> None:
        self.parameters = {"type": "object", "properties": {}, "additionalProperties": False}

    async def execute(
        self,
        tool_call_id,
        arguments,
        context: ToolContext,
        cancellation: CancellationToken,
        on_update=None,
    ) -> ToolResult:
        del tool_call_id, arguments, context
        cancellation.raise_if_cancelled()
        result = ToolResult((TextContent("ok"),), details={})
        if on_update is not None:
            await on_update(result)
        return result


@pytest.mark.agent
@pytest.mark.asyncio
async def test_steering_enters_after_tool_turn_in_fifo_order() -> None:
    provider = FakeProvider(
        (
            response(AssistantMessage((ToolCall("c1", "echo", {}),), stop_reason="tool_use")),
            response(AssistantMessage((TextContent("done"),))),
        )
    )
    agent = Agent(provider, ModelProfile("fake", "fake", "http://fake"), (EchoTool(),))

    async def listener(event) -> None:
        if event.type is AgentEventType.TOOL_EXECUTION_END:
            agent.steer("first steer")
            agent.steer("second steer")

    agent.subscribe(listener)
    await agent.prompt("go")

    assert [message.content for message in provider.requests[1].messages if message.role == "user"][
        -2:
    ] == ["first steer", "second steer"]


@pytest.mark.agent
@pytest.mark.asyncio
async def test_follow_up_only_runs_when_agent_would_otherwise_finish() -> None:
    provider = FakeProvider(
        (
            response(AssistantMessage((TextContent("first"),))),
            response(AssistantMessage((TextContent("second"),))),
        )
    )
    agent = Agent(provider, ModelProfile("fake", "fake", "http://fake"))

    async def listener(event) -> None:
        if event.type is AgentEventType.TURN_END and len(provider.requests) == 1:
            agent.follow_up("continue")

    agent.subscribe(listener)
    await agent.prompt("go")

    assert len(provider.requests) == 2
    assert provider.requests[1].messages[-1].content == "continue"


@pytest.mark.agent
@pytest.mark.asyncio
async def test_profile_update_is_applied_to_next_turn_not_inflight_request() -> None:
    provider = FakeProvider(
        (
            response(AssistantMessage((ToolCall("c1", "echo", {}),), stop_reason="tool_use")),
            response(AssistantMessage((TextContent("done"),))),
        )
    )
    old = ModelProfile("fake", "old", "http://old")
    new = ModelProfile("fake", "new", "http://new")
    agent = Agent(provider, old, (EchoTool(),))

    async def listener(event) -> None:
        if event.type is AgentEventType.TOOL_EXECUTION_START:
            agent.update_profile(new)

    agent.subscribe(listener)
    await agent.prompt("go")

    assert provider.requests[0].profile is old
    assert provider.requests[1].profile is new
