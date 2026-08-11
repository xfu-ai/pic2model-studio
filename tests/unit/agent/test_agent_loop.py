from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest

from aipic_to_model.agent.core.agent_loop import (
    AfterToolCallContext,
    AgentLoop,
    AgentLoopConfig,
    BeforeToolCallContext,
    BeforeToolCallResult,
)
from aipic_to_model.agent.core.errors import AgentCancelledError, AgentCoreError
from aipic_to_model.agent.core.events import AgentEventType, CancellationToken
from aipic_to_model.agent.core.models import (
    AssistantMessage,
    ProviderEvent,
    ProviderEventType,
    TextContent,
    ToolCall,
    ToolResult,
    UserMessage,
)
from aipic_to_model.agent.core.tool import ToolContext, ToolRegistry
from aipic_to_model.agent.providers.base import ModelProfile
from aipic_to_model.agent.providers.fake import FakeProvider, ScriptedResponse


@dataclass
class RecordingTool:
    name: str
    order: list[str]
    parameters: dict[str, object]
    label: str = "Recording"
    description: str = "Records invocations"
    execution_mode: str = "sequential"
    raises: bool = False

    async def execute(
        self, tool_call_id, arguments, context: ToolContext, cancellation, on_update=None
    ):
        del tool_call_id, context
        cancellation.raise_if_cancelled()
        value = arguments.get("value", arguments.get("job_id"))
        self.order.append(f"{self.name}:{value}")
        if self.raises:
            raise RuntimeError("tool exploded")
        result = ToolResult((TextContent(str(value)),), details={"ok": True})
        if on_update is not None:
            await on_update(result)
        return result


@dataclass
class AwaitApprovalTool(RecordingTool):
    async def execute(
        self, tool_call_id, arguments, context: ToolContext, cancellation, on_update=None
    ):
        del tool_call_id, arguments, context, cancellation, on_update
        return ToolResult((TextContent("Approval required."),), details={"status": "awaiting_ui_action"})


def assistant(*content, stop_reason="stop") -> AssistantMessage:
    return AssistantMessage(tuple(content), stop_reason=stop_reason)


def response(message: AssistantMessage) -> ScriptedResponse:
    return ScriptedResponse(
        (
            ProviderEvent(ProviderEventType.MESSAGE_START),
            ProviderEvent(ProviderEventType.MESSAGE_END, message=message),
        )
    )


def make_loop(responses, tools=(), **config):
    return AgentLoop(
        FakeProvider(tuple(response(item) for item in responses)),
        ModelProfile("fake", "fake", "http://fake"),
        ToolRegistry(tuple(tools)),
        AgentLoopConfig(**config),
    )


@pytest.mark.agent
@pytest.mark.asyncio
async def test_plain_text_turn_finishes_with_agent_end() -> None:
    loop = make_loop([assistant(TextContent("done"))])

    transcript = await loop.run((UserMessage("hello"),), CancellationToken())

    assert isinstance(transcript[-1], AssistantMessage)
    assert [event.type for event in loop.events][-1] is AgentEventType.AGENT_END


@pytest.mark.agent
@pytest.mark.asyncio
async def test_provider_response_transform_is_the_persisted_message_end() -> None:
    async def transform(_message: AssistantMessage, _cancellation: CancellationToken):
        return AssistantMessage((TextContent("grounded final"),))

    loop = make_loop(
        [assistant(TextContent("unsupported claim"))],
        after_provider_response=transform,
    )

    transcript = await loop.run((UserMessage("go"),), CancellationToken())

    assert transcript[-1].content[0].text == "grounded final"
    message_end = next(
        event
        for event in loop.events
        if event.type is AgentEventType.MESSAGE_END
        and isinstance(event.payload.get("message"), dict)
        and event.payload["message"].get("role") == "assistant"
    )
    assert message_end.payload["message"]["content"][0]["text"] == "grounded final"


@pytest.mark.agent
@pytest.mark.asyncio
async def test_text_tool_json_is_not_executed_and_one_native_format_correction_is_requested() -> None:
    order: list[str] = []
    tool = RecordingTool(
        "echo",
        order,
        {"type": "object", "required": ["value"], "properties": {"value": {"type": "integer"}}},
    )
    loop = make_loop(
        [
            assistant(TextContent('{"name":"echo","arguments":{"value":7}}')),
            assistant(ToolCall("native-call", "echo", {"value": 7}), stop_reason="tool_use"),
            assistant(TextContent("done")),
        ],
        [tool],
    )

    await loop.run((UserMessage("go"),), CancellationToken())

    fake = loop._provider
    assert isinstance(fake, FakeProvider)
    assert order == ["echo:7"]
    assert fake.requests[1].tool_choice == "required"


@pytest.mark.agent
@pytest.mark.asyncio
async def test_repeated_text_tool_json_returns_a_stable_error_without_executing_it() -> None:
    order: list[str] = []
    tool = RecordingTool(
        "echo",
        order,
        {"type": "object", "required": ["value"], "properties": {"value": {"type": "integer"}}},
    )
    loop = make_loop(
        [
            assistant(TextContent('{"name":"echo","arguments":{"value":7}}')),
            assistant(TextContent('{"name":"echo","arguments":{"value":7}}')),
        ],
        [tool],
    )

    transcript = await loop.run((UserMessage("go"),), CancellationToken())

    assert order == []
    assert isinstance(transcript[-1], AssistantMessage)
    assert "could not be formatted safely" in transcript[-1].content[0].text


@pytest.mark.agent
@pytest.mark.asyncio
async def test_five_tool_turns_feed_results_back_in_order() -> None:
    order: list[str] = []
    tool = RecordingTool(
        "echo",
        order,
        {"type": "object", "required": ["value"], "properties": {"value": {"type": "integer"}}},
    )
    responses = [
        assistant(ToolCall(f"call-{index}", "echo", {"value": index}), stop_reason="tool_use")
        for index in range(5)
    ] + [assistant(TextContent("finished"))]
    loop = make_loop(responses, [tool])

    transcript = await loop.run((UserMessage("go"),), CancellationToken())

    assert order == ["echo:0", "echo:1", "echo:2", "echo:3", "echo:4"]
    assert sum(1 for message in transcript if message.role == "tool_result") == 5
    fake = loop._provider
    assert isinstance(fake, FakeProvider)
    assert all(request.tools[0]["function"]["name"] == "echo" for request in fake.requests)


@pytest.mark.agent
@pytest.mark.asyncio
async def test_two_calls_from_one_assistant_message_run_in_source_order() -> None:
    order: list[str] = []
    schema = {"type": "object", "required": ["value"], "properties": {"value": {"type": "integer"}}}
    one, two = RecordingTool("one", order, schema), RecordingTool("two", order, schema)
    loop = make_loop(
        [
            assistant(
                ToolCall("a", "one", {"value": 1}),
                ToolCall("b", "two", {"value": 2}),
                stop_reason="tool_use",
            ),
            assistant(TextContent("ok")),
        ],
        [one, two],
    )

    await loop.run((UserMessage("go"),), CancellationToken())

    assert order == ["one:1", "two:2"]


@pytest.mark.agent
@pytest.mark.asyncio
async def test_approval_sideband_leaves_the_original_tool_call_open_without_a_tool_result_message() -> None:
    tool = AwaitApprovalTool("paid", [], {"type": "object", "properties": {}})
    loop = make_loop([assistant(ToolCall("approval-call", "paid", {}), stop_reason="tool_use")], [tool])

    transcript = await loop.run((UserMessage("go"),), CancellationToken())

    assert [message.role for message in transcript] == ["user", "assistant"]
    assert not any(
        event.type is AgentEventType.MESSAGE_END
        and isinstance(event.payload.get("message"), dict)
        and event.payload["message"].get("role") == "tool_result"
        for event in loop.events
    )


@pytest.mark.agent
@pytest.mark.asyncio
async def test_invalid_unknown_and_throwing_tools_return_error_results() -> None:
    order: list[str] = []
    schema = {"type": "object", "required": ["value"], "properties": {"value": {"type": "integer"}}}
    exploding = RecordingTool("explode", order, schema, raises=True)
    loop = make_loop(
        [
            assistant(
                ToolCall("invalid", "explode", {"value": "not-an-int"}),
                ToolCall("unknown", "missing", {}),
                ToolCall("throws", "explode", {"value": 1}),
                stop_reason="tool_use",
            ),
            assistant(TextContent("recovered")),
        ],
        [exploding],
    )

    transcript = await loop.run((UserMessage("go"),), CancellationToken())

    tool_results = [message for message in transcript if message.role == "tool_result"]
    assert len(tool_results) == 3
    assert all(message.is_error for message in tool_results)


@pytest.mark.agent
@pytest.mark.asyncio
async def test_limits_cancellation_and_hooks() -> None:
    order: list[str] = []
    tool = RecordingTool(
        "echo",
        order,
        {"type": "object", "required": ["value"], "properties": {"value": {"type": "integer"}}},
    )
    calls = [
        assistant(ToolCall("a", "echo", {"value": 1}), stop_reason="tool_use"),
        assistant(TextContent("end")),
    ]

    async def before(context: BeforeToolCallContext, cancellation: CancellationToken):
        del context, cancellation
        return BeforeToolCallResult(block=True, reason="blocked by test")

    async def after(context: AfterToolCallContext, cancellation: CancellationToken):
        del cancellation
        return ToolResult(
            (TextContent("hooked"),), details=context.result.details, is_error=context.is_error
        )

    loop = make_loop(calls, [tool], before_tool_call=before, after_tool_call=after)
    transcript = await loop.run((UserMessage("go"),), CancellationToken())
    assert transcript[-2].content[0].text == "hooked"
    assert order == []

    cancellation = CancellationToken()
    cancellation.cancel()
    with pytest.raises(AgentCancelledError):
        await make_loop([assistant(TextContent("nope"))]).run((UserMessage("go"),), cancellation)


@pytest.mark.agent
@pytest.mark.asyncio
async def test_generic_repeated_tool_calls_are_not_hard_limited() -> None:
    order: list[str] = []
    tool = RecordingTool(
        "echo",
        order,
        {"type": "object", "required": ["value"], "properties": {"value": {"type": "integer"}}},
    )
    repeated_calls = [
        assistant(ToolCall(f"call-{index}", "echo", {"value": 1}), stop_reason="tool_use")
        for index in range(6)
    ] + [assistant(TextContent("finished"))]
    loop = make_loop(repeated_calls, [tool])

    transcript = await loop.run((UserMessage("go"),), CancellationToken())

    assert order == ["echo:1"] * 6
    assert sum(message.role == "tool_result" for message in transcript) == 6
    assert isinstance(transcript[-1], AssistantMessage)


@pytest.mark.agent
@pytest.mark.asyncio
async def test_async_status_is_checked_once_then_model_can_send_waiting_summary() -> None:
    order: list[str] = []
    status = RecordingTool(
        "job.get_status",
        order,
        {
            "type": "object",
            "required": ["job_id"],
            "properties": {"job_id": {"type": "string"}},
        },
    )
    loop = make_loop(
        [
            assistant(ToolCall("status-1", "job.get_status", {"job_id": "job-1"}), stop_reason="tool_use"),
            assistant(ToolCall("status-2", "job.get_status", {"job_id": "job-1"}), stop_reason="tool_use"),
            assistant(TextContent("The job is still running. I will continue when the desktop reports completion.")),
        ],
        [status],
    )

    transcript = await loop.run((UserMessage("check the job"),), CancellationToken())

    results = [message for message in transcript if message.role == "tool_result"]
    assert order == ["job.get_status:job-1"]
    assert len(results) == 2
    assert results[-1].tool_call_id == "status-2"
    assert results[-1].is_error is True
    assert "Do not poll" in results[-1].content[0].text
    assert isinstance(transcript[-1], AssistantMessage)


@pytest.mark.agent
@pytest.mark.asyncio
async def test_async_status_aliases_are_deduplicated_by_job_id() -> None:
    order: list[str] = []
    schema = {
        "type": "object",
        "required": ["job_id"],
        "properties": {"job_id": {"type": "string"}},
    }
    status, model_status = (
        RecordingTool("job.get_status", order, schema),
        RecordingTool("model3d.get_status", order, schema),
    )
    loop = make_loop(
        [
            assistant(ToolCall("status-1", "job.get_status", {"job_id": "job-1"}), stop_reason="tool_use"),
            assistant(ToolCall("status-2", "model3d.get_status", {"job_id": "job-1"}), stop_reason="tool_use"),
            assistant(TextContent("I am waiting for the job completion event.")),
        ],
        [status, model_status],
    )

    transcript = await loop.run((UserMessage("check the job"),), CancellationToken())

    results = [message for message in transcript if message.role == "tool_result"]
    assert order == ["job.get_status:job-1"]
    assert results[-1].tool_call_id == "status-2"
    assert results[-1].is_error is True


@pytest.mark.agent
@pytest.mark.asyncio
async def test_tool_events_include_update_end_and_tool_result_message_lifecycle() -> None:
    order: list[str] = []
    tool = RecordingTool(
        "echo",
        order,
        {"type": "object", "required": ["value"], "properties": {"value": {"type": "integer"}}},
    )
    loop = make_loop(
        [
            assistant(ToolCall("call-1", "echo", {"value": 7}), stop_reason="tool_use"),
            assistant(TextContent("done")),
        ],
        [tool],
    )

    await loop.run((UserMessage("go"),), CancellationToken())

    types = [event.type for event in loop.events]
    start = types.index(AgentEventType.TOOL_EXECUTION_START)
    update = types.index(AgentEventType.TOOL_EXECUTION_UPDATE)
    end = types.index(AgentEventType.TOOL_EXECUTION_END)
    assert start < update < end
    assert types[end + 1 : end + 3] == [AgentEventType.MESSAGE_START, AgentEventType.MESSAGE_END]
    assert loop.events[end + 1].payload["message"]["role"] == "tool_result"
    assert loop.events[-1].type is AgentEventType.AGENT_END


class BlockingProvider:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def stream(
        self, request, cancellation: CancellationToken
    ) -> AsyncIterator[ProviderEvent]:
        del request, cancellation
        self.started.set()
        await asyncio.Event().wait()
        if False:  # pragma: no cover - preserves the async-generator protocol.
            yield ProviderEvent(ProviderEventType.MESSAGE_END)


@dataclass
class BlockingTool:
    started: asyncio.Event
    name: str = "wait"
    label: str = "Wait"
    description: str = "Waits until cancelled"
    parameters: dict[str, object] | None = None
    execution_mode: str = "sequential"

    def __post_init__(self) -> None:
        if self.parameters is None:
            self.parameters = {"type": "object", "additionalProperties": False}

    async def execute(
        self, tool_call_id, arguments, context, cancellation, on_update=None
    ) -> ToolResult:
        del tool_call_id, arguments, context, cancellation, on_update
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


@pytest.mark.agent
@pytest.mark.asyncio
async def test_provider_and_tool_work_are_cancelled_while_waiting() -> None:
    provider = BlockingProvider()
    loop = AgentLoop(
        provider,
        ModelProfile("fake", "fake", "http://fake"),
        ToolRegistry(),
    )
    cancellation = CancellationToken()
    provider_run = asyncio.create_task(loop.run((UserMessage("go"),), cancellation))
    await provider.started.wait()
    cancellation.cancel("provider stop")
    with pytest.raises(AgentCancelledError, match="provider stop"):
        await provider_run
    assert loop.events[-1].type is AgentEventType.AGENT_END

    tool_started = asyncio.Event()
    tool = BlockingTool(tool_started)
    tool_loop = make_loop(
        [assistant(ToolCall("wait-1", "wait", {}), stop_reason="tool_use")], [tool]
    )
    tool_cancellation = CancellationToken()
    tool_run = asyncio.create_task(tool_loop.run((UserMessage("go"),), tool_cancellation))
    await tool_started.wait()
    tool_cancellation.cancel("tool stop")
    with pytest.raises(AgentCancelledError, match="tool stop"):
        await tool_run
    assert tool_loop.events[-1].type is AgentEventType.AGENT_END


@pytest.mark.agent
@pytest.mark.asyncio
async def test_deadline_interrupts_blocked_provider_and_emits_agent_end() -> None:
    provider = BlockingProvider()
    loop = AgentLoop(
        provider,
        ModelProfile("fake", "fake", "http://fake"),
        ToolRegistry(),
        AgentLoopConfig(deadline_seconds=0.01),
    )

    with pytest.raises(AgentCoreError, match="deadline"):
        await loop.run((UserMessage("go"),), CancellationToken())

    assert loop.events[-1].type is AgentEventType.AGENT_END
