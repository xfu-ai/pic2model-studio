"""Stateful facade over the low-level AgentLoop."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace

from ..providers.base import AgentModelProvider, ModelProfile
from .agent_loop import AgentLoop, AgentLoopConfig, AgentLoopRuntime
from .errors import AgentCoreError
from .events import AgentEvent, AgentEventType, CancellationToken
from .models import AssistantMessage, Message, UserMessage, message_from_dict
from .tool import AgentTool, ToolRegistry

AgentListener = Callable[[AgentEvent], Awaitable[None] | None]
NextTurnUpdate = Callable[["Agent"], None]


@dataclass
class AgentState:
    system_prompt: str
    profile: ModelProfile
    tools: tuple[AgentTool, ...]
    messages: list[Message] = field(default_factory=list)
    thinking_level: str = "off"
    streaming_message: AssistantMessage | None = None
    pending_tool_calls: set[str] = field(default_factory=set)
    error: str | None = None
    is_running: bool = False


class Agent:
    """Owns a transcript, queue policy, state projection, and one active AgentLoop."""

    def __init__(
        self,
        provider: AgentModelProvider,
        profile: ModelProfile,
        tools: tuple[AgentTool, ...] = (),
        *,
        system_prompt: str = "",
        loop_config: AgentLoopConfig | None = None,
    ) -> None:
        self._provider = provider
        self._loop_config = loop_config or AgentLoopConfig()
        self.state = AgentState(system_prompt, profile, tuple(tools))
        self._listeners: list[AgentListener] = []
        self._steering: deque[Message] = deque()
        self._follow_up: deque[Message] = deque()
        self._next_turn: deque[NextTurnUpdate] = deque()
        self._cancellation: CancellationToken | None = None
        self._idle = asyncio.Event()
        self._idle.set()

    def subscribe(self, listener: AgentListener) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def steer(self, message: Message | str) -> None:
        self._steering.append(_as_message(message))

    def follow_up(self, message: Message | str) -> None:
        self._follow_up.append(_as_message(message))

    def queue_next_turn(self, update: NextTurnUpdate) -> None:
        self._next_turn.append(update)

    def update_profile(self, profile: ModelProfile) -> None:
        if self.state.is_running:
            self.queue_next_turn(lambda agent: setattr(agent.state, "profile", profile))
        else:
            self.state.profile = profile

    def update_tools(self, tools: tuple[AgentTool, ...]) -> None:
        if self.state.is_running:
            self.queue_next_turn(lambda agent: setattr(agent.state, "tools", tuple(tools)))
        else:
            self.state.tools = tuple(tools)

    async def prompt(self, message: Message | str) -> tuple[Message, ...]:
        return await self._run((_as_message(message),))

    async def continue_run(self) -> tuple[Message, ...]:
        if not self.state.messages or self.state.messages[-1].role == "assistant":
            raise AgentCoreError(
                "Cannot continue from an assistant message.", "invalid_continuation"
            )
        return await self._run(())

    def abort(self) -> None:
        self._steering.clear()
        self._follow_up.clear()
        if self._cancellation is not None:
            self._cancellation.cancel("Agent run aborted.")

    async def wait_for_idle(self) -> None:
        await self._idle.wait()

    async def _run(self, initial: tuple[Message, ...]) -> tuple[Message, ...]:
        if self.state.is_running:
            raise AgentCoreError("Agent is already running.", "agent_busy")
        self.state.is_running = True
        self.state.error = None
        self._idle.clear()
        self._cancellation = CancellationToken()
        self.state.messages.extend(initial)
        config = replace(
            self._loop_config,
            get_steering_messages=self._drain_steering,
            get_follow_up_messages=self._drain_follow_up,
            prepare_next_turn=self._apply_next_turn,
        )
        loop = AgentLoop(
            self._provider,
            self.state.profile,
            ToolRegistry(self.state.tools),
            config,
            self._process_event,
        )
        try:
            return await loop.run(tuple(self.state.messages), self._cancellation)
        except Exception as error:
            self.state.error = str(error)
            raise
        finally:
            self.state.is_running = False
            self.state.streaming_message = None
            self.state.pending_tool_calls.clear()
            self._cancellation = None
            self._idle.set()

    async def _drain_steering(self) -> tuple[Message, ...]:
        return _drain(self._steering)

    async def _drain_follow_up(self) -> tuple[Message, ...]:
        return _drain(self._follow_up)

    async def _apply_next_turn(self, _messages: tuple[Message, ...]) -> AgentLoopRuntime:
        while self._next_turn:
            self._next_turn.popleft()(self)
        return AgentLoopRuntime(self.state.profile, ToolRegistry(self.state.tools))

    async def _process_event(self, event: AgentEvent) -> None:
        self._project_event(event)
        for listener in tuple(self._listeners):
            try:
                result = listener(event)
                if result is not None:
                    await result
            except Exception as error:  # noqa: BLE001 - listeners must not hold the Agent busy.
                self.state.error = f"Listener failed: {error}"

    def _project_event(self, event: AgentEvent) -> None:
        payload = event.payload
        if event.type is AgentEventType.MESSAGE_START and "message" not in payload:
            self.state.streaming_message = AssistantMessage(())
        elif event.type is AgentEventType.MESSAGE_END:
            raw = payload.get("message")
            if isinstance(raw, dict):
                message = message_from_dict(raw)
                if not self.state.messages or self.state.messages[-1].id != message.id:
                    self.state.messages.append(message)
                if isinstance(message, AssistantMessage) and message.error_message:
                    self.state.error = message.error_message
            self.state.streaming_message = None
        elif event.type is AgentEventType.TOOL_EXECUTION_START:
            self.state.pending_tool_calls.add(str(payload["tool_call_id"]))
        elif event.type is AgentEventType.TOOL_EXECUTION_END:
            self.state.pending_tool_calls.discard(str(payload["tool_call_id"]))
        elif event.type is AgentEventType.AGENT_END:
            self.state.streaming_message = None


def _as_message(message: Message | str) -> Message:
    return UserMessage(message) if isinstance(message, str) else message


def _drain(queue: deque[Message]) -> tuple[Message, ...]:
    result = tuple(queue)
    queue.clear()
    return result
