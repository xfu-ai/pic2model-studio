"""Sequential, provider-agnostic Agent loop for native tool calling."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from ..providers.base import AgentModelProvider, ModelProfile, ModelRequest
from .errors import AgentCancelledError, AgentCoreError, ToolExecutionError
from .events import AgentEvent, AgentEventType, CancellationToken
from .models import (
    AssistantMessage,
    Message,
    SystemMessage,
    TextContent,
    ToolCall,
    ToolResult,
    ToolResultMessage,
    json_dumps,
)
from .tool import AgentTool, ToolContext, ToolRegistry

EventListener = Callable[[AgentEvent], Awaitable[None] | None]
MessageSupplier = Callable[[], Awaitable[tuple[Message, ...]] | tuple[Message, ...]]

_ASYNC_WAIT_TOOL_NAMES = frozenset({"job.get_status", "model3d.get_status", "asset.list"})
_ASYNC_WAIT_REPEAT_MESSAGE = (
    "This async task was already checked in this Agent turn. Do not poll it again or "
    "use asset.list to check for its output. Wait for the desktop completion event, "
    "then give the user a concise waiting summary."
)


@dataclass(frozen=True)
class BeforeToolCallResult:
    block: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class BeforeToolCallContext:
    assistant_message: AssistantMessage
    tool_call: ToolCall
    arguments: dict[str, object]
    messages: tuple[Message, ...]


@dataclass(frozen=True)
class AfterToolCallContext:
    assistant_message: AssistantMessage
    tool_call: ToolCall
    arguments: dict[str, object]
    result: ToolResult
    is_error: bool
    messages: tuple[Message, ...]


BeforeToolCallHook = Callable[
    [BeforeToolCallContext, CancellationToken],
    Awaitable[BeforeToolCallResult | None] | BeforeToolCallResult | None,
]
BeforeProviderRequestHook = Callable[
    [ModelRequest, CancellationToken], Awaitable[ModelRequest | None] | ModelRequest | None
]
AfterProviderResponseHook = Callable[
    [AssistantMessage, CancellationToken],
    Awaitable[AssistantMessage | None] | AssistantMessage | None,
]
AfterToolCallHook = Callable[
    [AfterToolCallContext, CancellationToken], Awaitable[ToolResult | None] | ToolResult | None
]


@dataclass(frozen=True)
class AgentLoopConfig:
    deadline_seconds: float | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
    before_tool_call: BeforeToolCallHook | None = None
    after_tool_call: AfterToolCallHook | None = None
    before_provider_request: BeforeProviderRequestHook | None = None
    after_provider_response: AfterProviderResponseHook | None = None
    get_steering_messages: MessageSupplier | None = None
    get_follow_up_messages: MessageSupplier | None = None
    prepare_next_turn: (
        Callable[[tuple[Message, ...]], Awaitable[AgentLoopRuntime] | AgentLoopRuntime] | None
    ) = None


@dataclass(frozen=True)
class AgentLoopRuntime:
    profile: ModelProfile
    tools: ToolRegistry


class AgentLoop:
    """Executes model turns and tool calls in assistant source order only."""

    def __init__(
        self,
        provider: AgentModelProvider,
        profile: ModelProfile,
        tools: ToolRegistry,
        config: AgentLoopConfig | None = None,
        listener: EventListener | None = None,
    ) -> None:
        self._provider = provider
        self._profile = profile
        self._tools = tools
        self._config = config or AgentLoopConfig()
        self._listener = listener
        self.events: list[AgentEvent] = []

    async def run(
        self, messages: tuple[Message, ...], cancellation: CancellationToken
    ) -> tuple[Message, ...]:
        transcript = list(messages)
        runtime = AgentLoopRuntime(self._profile, self._tools)
        started_at = time.monotonic()
        async_wait_calls: set[str] = set()
        format_corrections = 0
        await self._emit(AgentEventType.AGENT_START)
        try:
            turn = 0
            while True:
                turn += 1
                self._check_deadline(started_at)
                cancellation.raise_if_cancelled()
                await self._emit(AgentEventType.TURN_START, turn=turn)
                assistant = await self._run_provider_turn(
                    tuple(transcript),
                    cancellation,
                    started_at,
                    runtime,
                    require_native_tool_call=format_corrections == 1,
                )
                if _looks_like_text_tool_json(assistant, runtime.tools):
                    transcript.append(assistant)
                    if format_corrections == 0:
                        format_corrections = 1
                        transcript.append(
                            SystemMessage(
                                "Use the native Tool Call channel now. Do not output Tool JSON as text. "
                                "Reuse the same requested Tool name and parameters."
                            )
                        )
                        continue
                    stable = AssistantMessage(
                        (TextContent("The requested tool call could not be formatted safely. Please try again."),)
                    )
                    transcript.pop()  # malformed second text envelope
                    transcript.pop()  # ephemeral correction instruction
                    transcript.append(stable)
                    await self._emit(AgentEventType.TURN_END, turn=turn)
                    return tuple(transcript)
                transcript.append(assistant)
                if assistant.stop_reason in {"error", "aborted"}:
                    await self._emit(AgentEventType.TURN_END, turn=turn)
                    return tuple(transcript)

                calls = tuple(block for block in assistant.content if isinstance(block, ToolCall))
                results: list[ToolResultMessage] = []
                suspended = False
                for call in calls:
                    self._check_deadline(started_at)
                    cancellation.raise_if_cancelled()
                    key = f"{call.name}:{json_dumps(call.arguments)}"
                    async_wait_key = _async_wait_key(call, key)
                    if async_wait_key is not None and async_wait_key in async_wait_calls:
                        result = await self._blocked_tool_result(call, _ASYNC_WAIT_REPEAT_MESSAGE)
                        transcript.append(result)
                        results.append(result)
                        continue
                    if async_wait_key is not None:
                        async_wait_calls.add(async_wait_key)
                    result = await self._execute_tool(
                        assistant, call, tuple(transcript), cancellation, started_at, runtime.tools
                    )
                    if _is_awaiting_ui_action(result):
                        # This is a desktop control event. The original Tool
                        # Call remains open until approval resolves it.
                        suspended = True
                        continue
                    transcript.append(result)
                    results.append(result)
                await self._emit(
                    AgentEventType.TURN_END,
                    turn=turn,
                    tool_result_ids=[result.id for result in results],
                )
                if suspended:
                    return tuple(transcript)
                additions = await _maybe_await(self._config.get_steering_messages)
                if not calls and not additions:
                    additions = await _maybe_await(self._config.get_follow_up_messages)
                if additions:
                    for message in additions:
                        transcript.append(message)
                        await self._emit(AgentEventType.MESSAGE_START, message=message.to_dict())
                        await self._emit(AgentEventType.MESSAGE_END, message=message.to_dict())
                elif not calls:
                    return tuple(transcript)
                update = await _maybe_await(self._config.prepare_next_turn, tuple(transcript))
                if update is not None:
                    runtime = update
        finally:
            await self._emit(AgentEventType.AGENT_END)

    async def _run_provider_turn(
        self,
        transcript: tuple[Message, ...],
        cancellation: CancellationToken,
        started_at: float,
        runtime: AgentLoopRuntime,
        *,
        require_native_tool_call: bool = False,
    ) -> AssistantMessage:
        final_message: AssistantMessage | None = None
        request = ModelRequest(
            profile=runtime.profile,
            messages=transcript,
            tools=tuple(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": dict(tool.parameters),
                    },
                }
                for tool in runtime.tools.all()
            ),
            temperature=self._config.temperature,
            max_output_tokens=self._config.max_output_tokens,
            tool_choice="required" if require_native_tool_call else None,
        )
        request_override = await self._await_controlled(
            _maybe_await(self._config.before_provider_request, request, cancellation),
            cancellation,
            started_at,
        )
        if request_override is not None:
            request = request_override
        events = self._provider.stream(request, cancellation)
        try:
            while True:
                try:
                    provider_event = await self._await_controlled(
                        anext(events), cancellation, started_at
                    )
                except StopAsyncIteration:
                    break
                if provider_event.type.value == "provider_error":
                    raise AgentCoreError(
                        provider_event.error_message or "Provider failed.", "provider_error"
                    )
                if provider_event.type.value == "message_start":
                    await self._emit(
                        AgentEventType.MESSAGE_START, provider_event=provider_event.to_dict()
                    )
                elif (
                    provider_event.type.value == "message_end"
                    and (message := provider_event.message) is not None
                ):
                    _require_visible_terminal_response(message)
                    final_message = message
                else:
                    await self._emit(
                        AgentEventType.MESSAGE_UPDATE, provider_event=provider_event.to_dict()
                    )
        finally:
            aclose = getattr(events, "aclose", None)
            if aclose is not None:
                await aclose()
        if final_message is None:
            raise AgentCoreError(
                "Provider stream ended without an assistant message.", "provider_protocol_error"
            )
        assert final_message is not None
        response_override = await self._await_controlled(
            _maybe_await(self._config.after_provider_response, final_message, cancellation),
            cancellation,
            started_at,
        )
        if response_override is not None:
            final_message = response_override
        await self._emit(AgentEventType.MESSAGE_END, message=final_message.to_dict())
        return cast(AssistantMessage, final_message)

    async def _execute_tool(
        self,
        assistant: AssistantMessage,
        call: ToolCall,
        messages: tuple[Message, ...],
        cancellation: CancellationToken,
        started_at: float,
        tools: ToolRegistry,
    ) -> ToolResultMessage:
        await self._emit(
            AgentEventType.TOOL_EXECUTION_START,
            tool_call_id=call.id,
            tool_name=call.name,
            arguments=call.arguments,
        )
        try:
            tool, arguments = tools.validate(call)
            before_context = BeforeToolCallContext(assistant, call, arguments, messages)
            before = await self._await_controlled(
                _maybe_await(self._config.before_tool_call, before_context, cancellation),
                cancellation,
                started_at,
            )
            if before is not None and before.block:
                result = _error_result(before.reason or "Tool execution was blocked.")
            else:
                result = await self._invoke_tool(
                    tool, call, arguments, messages, cancellation, started_at
                )
            is_error = result.is_error
        except AgentCancelledError:
            raise
        except AgentCoreError as error:
            result = _error_result(error.message)
            is_error = True
        except Exception as error:  # noqa: BLE001 - Tool exceptions become model-visible results.
            result = _error_result(str(error) or "Tool execution failed.")
            is_error = True

        after_context = AfterToolCallContext(
            assistant, call, dict(call.arguments), result, is_error, messages
        )
        try:
            override = await self._await_controlled(
                _maybe_await(self._config.after_tool_call, after_context, cancellation),
                cancellation,
                started_at,
            )
            if override is not None:
                result = override
                is_error = result.is_error
        except AgentCancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - Hook failures become model-visible results.
            result = _error_result(f"After-tool hook failed: {error}")
            is_error = True

        await self._emit(
            AgentEventType.TOOL_EXECUTION_END,
            tool_call_id=call.id,
            tool_name=call.name,
            is_error=is_error,
            result=result.to_dict(),
        )
        message = ToolResultMessage(call.id, call.name, result)
        if not _is_awaiting_ui_action(message):
            await self._emit(AgentEventType.MESSAGE_START, message=message.to_dict())
            await self._emit(AgentEventType.MESSAGE_END, message=message.to_dict())
        return message

    async def _blocked_tool_result(self, call: ToolCall, reason: str) -> ToolResultMessage:
        """Persist a protocol-complete failure for a call blocked before execution.

        A provider has already emitted the assistant tool call by the time loop
        limits are evaluated.  Omitting this paired result leaves an invalid
        provider transcript, which makes recovery requests fail before they can
        produce a useful response.
        """

        result = _error_result(reason)
        await self._emit(
            AgentEventType.TOOL_EXECUTION_START,
            tool_call_id=call.id,
            tool_name=call.name,
            arguments=call.arguments,
        )
        await self._emit(
            AgentEventType.TOOL_EXECUTION_END,
            tool_call_id=call.id,
            tool_name=call.name,
            is_error=True,
            result=result.to_dict(),
        )
        message = ToolResultMessage(call.id, call.name, result)
        await self._emit(AgentEventType.MESSAGE_START, message=message.to_dict())
        await self._emit(AgentEventType.MESSAGE_END, message=message.to_dict())
        return message

    async def _invoke_tool(
        self,
        tool: AgentTool,
        call: ToolCall,
        arguments: dict[str, object],
        messages: tuple[Message, ...],
        cancellation: CancellationToken,
        started_at: float,
    ) -> ToolResult:
        async def on_update(partial: ToolResult) -> None:
            await self._emit(
                AgentEventType.TOOL_EXECUTION_UPDATE,
                tool_call_id=call.id,
                tool_name=call.name,
                is_error=partial.is_error,
            )

        try:
            return await self._await_controlled(
                tool.execute(call.id, arguments, ToolContext(messages), cancellation, on_update),
                cancellation,
                started_at,
            )
        except AgentCancelledError:
            raise
        except Exception as error:
            raise ToolExecutionError(call.name, str(error) or "Tool execution failed.") from error

    def _check_deadline(self, started_at: float) -> None:
        if (
            self._config.deadline_seconds is not None
            and time.monotonic() - started_at >= self._config.deadline_seconds
        ):
            raise AgentCoreError("Agent loop deadline exceeded.", "deadline_exceeded")

    async def _await_controlled(
        self,
        awaitable: Awaitable[Any],
        cancellation: CancellationToken,
        started_at: float,
    ) -> Any:
        """Await provider/tool work while enforcing cooperative cancellation and deadline."""

        self._check_deadline(started_at)
        remaining: float | None = None
        if self._config.deadline_seconds is not None:
            remaining = self._config.deadline_seconds - (time.monotonic() - started_at)
        try:
            controlled = cancellation.wait_for(awaitable)
            if remaining is None:
                return await controlled
            return await asyncio.wait_for(controlled, timeout=remaining)
        except TimeoutError as error:
            raise AgentCoreError("Agent loop deadline exceeded.", "deadline_exceeded") from error

    async def _emit(self, event_type: AgentEventType, **payload: Any) -> None:
        event = AgentEvent(event_type, payload)
        self.events.append(event)
        if self._listener is not None:
            result = self._listener(event)
            if result is not None:
                await result


async def _maybe_await(hook: Callable[..., Awaitable[Any] | Any] | None, *arguments: Any) -> Any:
    if hook is None:
        return None
    result = hook(*arguments)
    if asyncio.iscoroutine(result):
        return await result
    return result


def _error_result(message: str) -> ToolResult:
    from .models import TextContent

    return ToolResult((TextContent(message),), details={}, is_error=True)


def _async_wait_key(call: ToolCall, fallback: str) -> str | None:
    """Deduplicate status aliases by job, while retaining exact asset-list requests."""

    if call.name in {"job.get_status", "model3d.get_status"}:
        job_id = call.arguments.get("job_id")
        return f"job:{job_id}" if isinstance(job_id, str) and job_id else fallback
    return fallback if call.name in _ASYNC_WAIT_TOOL_NAMES else None


def _is_awaiting_ui_action(message: ToolResultMessage) -> bool:
    details = message.result.details
    return isinstance(details, dict) and details.get("status") == "awaiting_ui_action"


def _looks_like_text_tool_json(message: AssistantMessage, tools: ToolRegistry) -> bool:
    """Recognize a likely Tool envelope but never execute text as a Tool Call."""

    if any(isinstance(block, ToolCall) for block in message.content):
        return False
    text = "".join(block.text for block in message.content if isinstance(block, TextContent)).strip()
    if not text.startswith("{") or not text.endswith("}"):
        return False
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    name = payload.get("name") or payload.get("tool_name")
    arguments = payload.get("arguments")
    return isinstance(name, str) and name in {tool.name for tool in tools.all()} and isinstance(arguments, dict)


def _require_visible_terminal_response(message: AssistantMessage) -> None:
    """Reject a successful terminal turn that gives the user nothing to read."""

    if message.stop_reason != "stop":
        return
    if any(
        isinstance(block, TextContent) and bool(block.text.strip())
        for block in message.content
    ):
        return
    raise AgentCoreError(
        "Provider returned an empty final assistant response.", "empty_final_response"
    )
