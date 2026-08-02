"""Agent-level events and cooperative cancellation primitives."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeVar

from .errors import AgentCancelledError
from .models import JsonModel, JsonValue, new_id, utc_timestamp_ms

T = TypeVar("T")
CancellationCallback = Callable[[AgentCancelledError], None]


class AgentEventType(StrEnum):
    AGENT_START = "agent_start"
    AGENT_END = "agent_end"
    TURN_START = "turn_start"
    TURN_END = "turn_end"
    MESSAGE_START = "message_start"
    MESSAGE_UPDATE = "message_update"
    MESSAGE_END = "message_end"
    TOOL_EXECUTION_START = "tool_execution_start"
    TOOL_EXECUTION_UPDATE = "tool_execution_update"
    TOOL_EXECUTION_END = "tool_execution_end"
    QUEUE_UPDATE = "queue_update"
    COMPACTION_START = "compaction_start"
    COMPACTION_END = "compaction_end"
    CONTEXT_COMPACTED = "context_compacted"
    RETRY_SCHEDULED = "retry_scheduled"
    ATTEMPT_START = "attempt_start"
    ATTEMPT_FINISHED = "attempt_finished"
    EXTENSION_ERROR = "extension_error"


@dataclass(frozen=True)
class AgentEvent(JsonModel):
    type: AgentEventType
    payload: dict[str, JsonValue] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: new_id("aev"))
    timestamp: int = field(default_factory=utc_timestamp_ms)


class CancellationToken:
    """Cooperatively cancel async work and any tasks explicitly registered with it."""

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._error: AgentCancelledError | None = None
        self._callbacks: dict[int, CancellationCallback] = {}
        self._next_callback_id = 0

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> AgentCancelledError | None:
        return self._error

    def cancel(self, message: str = "Operation cancelled.") -> bool:
        if self.cancelled:
            return False
        self._error = AgentCancelledError(message)
        self._event.set()
        for callback in tuple(self._callbacks.values()):
            callback(self._error)
        return True

    async def wait(self) -> AgentCancelledError:
        await self._event.wait()
        return self._error or AgentCancelledError()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise self._error or AgentCancelledError()

    def add_callback(self, callback: CancellationCallback) -> Callable[[], None]:
        if self._error is not None:
            callback(self._error)
            return lambda: None
        callback_id = self._next_callback_id
        self._next_callback_id += 1
        self._callbacks[callback_id] = callback

        def remove() -> None:
            self._callbacks.pop(callback_id, None)

        return remove

    def child(self) -> CancellationToken:
        child = CancellationToken()

        def cancel_child(error: AgentCancelledError) -> None:
            child.cancel(error.message)

        self.add_callback(cancel_child)
        return child

    def register_task(self, task: asyncio.Task[Any]) -> Callable[[], None]:
        """Cancel *task* when this token is cancelled; unregister on completion."""

        def cancel_task(_error: AgentCancelledError) -> None:
            task.cancel()

        remove = self.add_callback(cancel_task)
        task.add_done_callback(lambda _task: remove())
        return remove

    def create_task(
        self, coroutine: Coroutine[Any, Any, T], *, name: str | None = None
    ) -> asyncio.Task[T]:
        task = asyncio.create_task(coroutine, name=name)
        self.register_task(task)
        return task

    async def wait_for(self, awaitable: Awaitable[T]) -> T:
        """Await work or cancel it promptly when this token is cancelled."""

        self.raise_if_cancelled()
        work = asyncio.ensure_future(awaitable)
        cancellation_wait = asyncio.create_task(self.wait())
        try:
            done, _pending = await asyncio.wait(
                {work, cancellation_wait}, return_when=asyncio.FIRST_COMPLETED
            )
            if work in done:
                return await work
            raise await cancellation_wait
        finally:
            # An enclosing deadline may cancel this coroutine.  Always settle both
            # child tasks before returning so an async provider generator can be
            # closed safely and no background tool work outlives the Agent run.
            if not work.done():
                work.cancel()
            if not cancellation_wait.done():
                cancellation_wait.cancel()
            await asyncio.gather(work, cancellation_wait, return_exceptions=True)
