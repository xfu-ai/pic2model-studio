from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from aipic_to_model.agent.core.errors import AgentCoreError
from aipic_to_model.agent.core.events import CancellationToken
from aipic_to_model.agent.core.models import (
    AssistantMessage,
    ProviderEvent,
    ProviderEventType,
    TextContent,
    ToolCall,
    ToolResult,
    Usage,
    UserMessage,
)
from aipic_to_model.agent.core.tool import ToolContext, ToolExecutionMode
from aipic_to_model.agent.harness import AgentHarness, CompactionSettings, HarnessPhase
from aipic_to_model.agent.harness.context import clamp_max_output_tokens
from aipic_to_model.agent.providers.base import ModelProfile
from aipic_to_model.agent.providers.fake import FakeProvider, ScriptedResponse
from aipic_to_model.agent.session.sqlite import LinearSessionRepository


def _response(text: str, usage: int = 0) -> ScriptedResponse:
    return ScriptedResponse(
        (
            ProviderEvent(ProviderEventType.MESSAGE_START),
            ProviderEvent(
                ProviderEventType.MESSAGE_END,
                message=AssistantMessage((TextContent(text),), usage=Usage(total_tokens=usage)),
            ),
        )
    )


class GateProvider:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream(self, request, cancellation: CancellationToken):
        del request
        self.started.set()
        await self.release.wait()
        cancellation.raise_if_cancelled()
        yield ProviderEvent(ProviderEventType.MESSAGE_START)
        yield ProviderEvent(
            ProviderEventType.MESSAGE_END, message=AssistantMessage((TextContent("done"),))
        )


def test_output_budget_matches_pi_model_cap_and_remaining_context() -> None:
    messages = (AssistantMessage((TextContent("x" * 400),), usage=Usage(total_tokens=9_000)),)

    assert clamp_max_output_tokens(messages, 1_000_000, 384_000) == 384_000
    assert clamp_max_output_tokens(messages, 10_000, 384_000) == 1


def test_output_budget_accounts_for_tool_schemas() -> None:
    messages = (UserMessage("brief request"),)
    tools = (
        {
            "type": "function",
            "function": {
                "name": "large_tool",
                "description": "x" * 4_000,
                "parameters": {"type": "object"},
            },
        },
    )

    without_tools = clamp_max_output_tokens(messages, 32_768, 28_672)
    with_tools = clamp_max_output_tokens(messages, 32_768, 28_672, tools)

    assert with_tools < without_tools


@pytest.mark.agent
@pytest.mark.asyncio
async def test_harness_rejects_second_structural_operation_while_turn_is_active(tmp_path) -> None:
    repository = LinearSessionRepository(tmp_path / "agent.sqlite3")
    session = repository.create()
    provider = GateProvider()
    harness = AgentHarness(
        provider, ModelProfile("fake", "fake", "http://fake"), repository, session.id
    )
    active = asyncio.create_task(harness.prompt("first"))
    await provider.started.wait()

    with pytest.raises(AgentCoreError, match="already running"):
        await harness.compact()
    assert harness.phase is HarnessPhase.TURN

    provider.release.set()
    await active
    assert harness.phase is HarnessPhase.IDLE
    assert [message.role for message in repository.open(session.id).messages] == [
        "user",
        "assistant",
    ]


@pytest.mark.agent
@pytest.mark.asyncio
async def test_snapshot_captures_session_and_turn_configuration(tmp_path) -> None:
    repository = LinearSessionRepository(tmp_path / "agent.sqlite3")
    session = repository.create(system_prompt="be concise", active_skills=("image",))
    harness = AgentHarness(
        FakeProvider((_response("ok"),)),
        ModelProfile("fake", "fake", "http://fake"),
        repository,
        session.id,
        tool_context={"project": "p1"},
        stream_options={"temperature": 0},
    )

    snapshot = harness.snapshot()

    assert snapshot.system_prompt == "be concise"
    assert snapshot.context[0].role == "system"
    assert snapshot.skills == ("image",)
    assert snapshot.tool_context == {"project": "p1"}
    assert snapshot.stream_options == {"temperature": 0}


class CountTool:
    name = "count"
    label = "Count"
    description = "Counts calls"
    execution_mode: ToolExecutionMode = "sequential"

    def __init__(self) -> None:
        self.calls = 0
        self.parameters: Mapping[str, object] = {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }

    async def execute(
        self, tool_call_id, arguments, context: ToolContext, cancellation, on_update=None
    ):
        del tool_call_id, arguments, context, cancellation, on_update
        self.calls += 1
        return ToolResult((TextContent("counted"),))


@pytest.mark.agent
@pytest.mark.asyncio
async def test_twenty_plus_durable_turns_compact_and_still_call_registered_tool(tmp_path) -> None:
    repository = LinearSessionRepository(tmp_path / "agent.sqlite3")
    session = repository.create()
    responses = [_response(f"turn {index}", 95) for index in range(21)]
    responses[-1] = ScriptedResponse(
        (
            ProviderEvent(ProviderEventType.MESSAGE_START),
            ProviderEvent(
                    ProviderEventType.MESSAGE_END,
                    message=AssistantMessage(
                        (ToolCall("call-1", "count", {}),),
                        usage=Usage(total_tokens=95),
                        stop_reason="tool_use",
                    ),
            ),
        )
    )
    responses.append(_response("tool complete"))
    tool = CountTool()
    harness = AgentHarness(
        FakeProvider(tuple(responses)),
        ModelProfile("fake", "fake", "http://fake"),
        repository,
        session.id,
        tools=(tool,),
        context_window=100,
        compaction_settings=CompactionSettings(reserve_tokens=20, keep_recent_tokens=1),
    )

    for index in range(21):
        await harness.prompt(f"turn {index}")

    assert tool.calls == 1
    assert repository.latest_compaction(session.id) is not None
