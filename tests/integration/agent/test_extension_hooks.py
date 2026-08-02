from __future__ import annotations

from collections.abc import Mapping

import pytest

from aipic_to_model.agent.core.models import (
    AssistantMessage,
    ProviderEvent,
    ProviderEventType,
    TextContent,
    ToolCall,
    ToolResult,
)
from aipic_to_model.agent.core.tool import ToolContext, ToolExecutionMode
from aipic_to_model.agent.harness import AgentHarness
from aipic_to_model.agent.providers.base import ModelProfile
from aipic_to_model.agent.providers.fake import FakeProvider, ScriptedResponse
from aipic_to_model.agent.session.sqlite import LinearSessionRepository


class Blocker:
    extension_id = "blocker"
    version = "1"
    priority = 0

    def register(self, context) -> None:
        context.add_lifecycle_hook(
            "before_tool_call", lambda _payload: {"block": True, "reason": "policy"}
        )

    def close(self) -> None:
        return None


class RequestPatch:
    extension_id = "request-patch"
    version = "1"
    priority = 0

    def register(self, context) -> None:
        context.add_lifecycle_hook(
            "before_provider_request", lambda _payload: {"temperature": 0.25}
        )

    def close(self) -> None:
        return None


class AuditPatch:
    extension_id = "audit-patch"
    version = "1"
    priority = 0

    def register(self, context) -> None:
        context.add_lifecycle_hook("after_tool_call", lambda _payload: {"details": {"audit": True}})

    def close(self) -> None:
        return None


class Tool:
    name = "guarded"
    label = "Guarded"
    description = "guarded"
    execution_mode: ToolExecutionMode = "sequential"

    def __init__(self) -> None:
        self.parameters: Mapping[str, object] = {"type": "object", "properties": {}}

    async def execute(
        self, tool_call_id, arguments, context: ToolContext, cancellation, on_update=None
    ) -> ToolResult:
        del tool_call_id, arguments, context, cancellation, on_update
        return ToolResult((TextContent("should not run"),))


@pytest.mark.agent
@pytest.mark.asyncio
async def test_extension_blocks_a_tool_without_sticking_the_harness(tmp_path) -> None:
    repository = LinearSessionRepository(tmp_path / "agent.sqlite3")
    session = repository.create()
    response = ScriptedResponse(
        (
            ProviderEvent(ProviderEventType.MESSAGE_START),
            ProviderEvent(
                ProviderEventType.MESSAGE_END,
                    message=AssistantMessage(
                        (ToolCall("c1", "guarded", {}),), stop_reason="tool_use"
                    ),
            ),
        )
    )
    finish = ScriptedResponse(
        (
            ProviderEvent(ProviderEventType.MESSAGE_START),
            ProviderEvent(
                ProviderEventType.MESSAGE_END, message=AssistantMessage((TextContent("done"),))
            ),
        )
    )
    harness = AgentHarness(
        FakeProvider((response, finish)),
        ModelProfile("fake", "fake", "http://fake"),
        repository,
        session.id,
        tools=(Tool(),),
        extensions=(Blocker(),),
    )

    messages = await harness.prompt("go")

    tool_result = next(message for message in messages if message.role == "tool_result")
    assert isinstance(tool_result.content[0], TextContent)
    assert "policy" in tool_result.content[0].text
    assert harness.phase.value == "idle"


@pytest.mark.agent
@pytest.mark.asyncio
async def test_extension_can_patch_provider_request(tmp_path) -> None:
    repository = LinearSessionRepository(tmp_path / "agent.sqlite3")
    session = repository.create()
    response = ScriptedResponse(
        (
            ProviderEvent(ProviderEventType.MESSAGE_START),
            ProviderEvent(
                ProviderEventType.MESSAGE_END, message=AssistantMessage((TextContent("done"),))
            ),
        )
    )
    provider = FakeProvider((response,))
    harness = AgentHarness(
        provider,
        ModelProfile("fake", "fake", "http://fake"),
        repository,
        session.id,
        extensions=(RequestPatch(),),
    )

    await harness.prompt("go")

    assert provider.requests[0].temperature == 0.25


@pytest.mark.agent
@pytest.mark.asyncio
async def test_extension_can_add_safe_tool_audit_details(tmp_path) -> None:
    repository = LinearSessionRepository(tmp_path / "agent.sqlite3")
    session = repository.create()
    response = ScriptedResponse(
        (
            ProviderEvent(ProviderEventType.MESSAGE_START),
            ProviderEvent(
                ProviderEventType.MESSAGE_END,
                    message=AssistantMessage(
                        (ToolCall("c1", "guarded", {}),), stop_reason="tool_use"
                    ),
            ),
        )
    )
    finish = ScriptedResponse(
        (
            ProviderEvent(ProviderEventType.MESSAGE_START),
            ProviderEvent(
                ProviderEventType.MESSAGE_END, message=AssistantMessage((TextContent("done"),))
            ),
        )
    )
    harness = AgentHarness(
        FakeProvider((response, finish)),
        ModelProfile("fake", "fake", "http://fake"),
        repository,
        session.id,
        tools=(Tool(),),
        extensions=(AuditPatch(),),
    )

    messages = await harness.prompt("go")

    result = next(message for message in messages if message.role == "tool_result")
    assert result.result.details == {"audit": True}
