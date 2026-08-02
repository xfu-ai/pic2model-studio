"""A closeable async event stream with explicit normal, error, and cancel endings."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator

from .errors import AgentCancelledError, EventStreamClosedError
from .events import CancellationToken

_UNSET = object()


class EventStream[T, R](AsyncIterator[T]):
    """Queue ordered events for async consumers until explicitly finalized.

    Publishing after a terminal state is a programmer error.  A producer failure
    is re-raised to consumers after already queued events, rather than being
    silently converted into normal completion.
    """

    def __init__(self, cancellation: CancellationToken | None = None) -> None:
        self._events: deque[T] = deque()
        self._waiters: list[asyncio.Future[None]] = []
        self._closed = False
        self._failure: BaseException | None = None
        self._result: R | None | object = _UNSET
        self._cancellation_task: asyncio.Task[None] | None = None
        if cancellation is not None:
            self.attach_cancellation(cancellation)

    @property
    def closed(self) -> bool:
        return self._closed

    def publish(self, event: T) -> None:
        if self._closed:
            raise EventStreamClosedError()
        self._events.append(event)
        self._wake_waiters()

    push = publish

    def close(self, result: R | None = None) -> None:
        if self._closed:
            return
        self._closed = True
        self._result = result
        self._stop_cancellation_task()
        self._wake_waiters()

    end = close
    aclose = close

    def fail(self, error: BaseException) -> None:
        if self._closed:
            raise EventStreamClosedError()
        self._closed = True
        self._failure = error
        self._stop_cancellation_task()
        self._wake_waiters()

    def cancel(self, message: str = "Event stream cancelled.") -> None:
        self.fail(AgentCancelledError(message))

    def attach_cancellation(self, cancellation: CancellationToken) -> None:
        if self._closed:
            return
        if cancellation.cancelled:
            self.cancel((cancellation.reason or AgentCancelledError()).message)
            return
        if self._cancellation_task is not None:
            raise RuntimeError("A cancellation token is already attached to this stream.")

        async def observe_cancellation() -> None:
            reason = await cancellation.wait()
            if not self._closed:
                self.cancel(reason.message)

        self._cancellation_task = asyncio.create_task(observe_cancellation())

    async def result(self) -> R | None:
        while not self._closed:
            waiter = asyncio.get_running_loop().create_future()
            self._waiters.append(waiter)
            try:
                await waiter
            finally:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)
        if self._failure is not None:
            raise self._failure
        return None if self._result is _UNSET else self._result  # type: ignore[return-value]

    def __aiter__(self) -> EventStream[T, R]:
        return self

    async def __anext__(self) -> T:
        while not self._events:
            if self._closed:
                if self._failure is not None:
                    raise self._failure
                raise StopAsyncIteration
            waiter = asyncio.get_running_loop().create_future()
            self._waiters.append(waiter)
            try:
                await waiter
            finally:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)
        return self._events.popleft()

    def _wake_waiters(self) -> None:
        for waiter in tuple(self._waiters):
            if not waiter.done():
                waiter.set_result(None)

    def _stop_cancellation_task(self) -> None:
        task = self._cancellation_task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
