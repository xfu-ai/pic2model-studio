from __future__ import annotations

import asyncio

import pytest

from aipic_to_model.agent.core.errors import AgentCancelledError, EventStreamClosedError
from aipic_to_model.agent.core.events import CancellationToken
from aipic_to_model.agent.core.stream import EventStream


@pytest.mark.agent
@pytest.mark.asyncio
async def test_stream_preserves_publish_order_and_returns_final_result() -> None:
    stream: EventStream[int, str] = EventStream()
    stream.publish(1)
    stream.publish(2)
    stream.close("complete")

    assert [event async for event in stream] == [1, 2]
    assert await stream.result() == "complete"


@pytest.mark.agent
@pytest.mark.asyncio
async def test_closed_stream_rejects_further_writes() -> None:
    stream: EventStream[int, None] = EventStream()
    stream.close()

    with pytest.raises(EventStreamClosedError):
        stream.publish(1)


@pytest.mark.agent
@pytest.mark.asyncio
async def test_producer_failure_reaches_consumer_and_result() -> None:
    stream: EventStream[str, None] = EventStream()
    stream.publish("before-error")
    stream.fail(RuntimeError("provider disconnected"))

    assert await anext(stream) == "before-error"
    with pytest.raises(RuntimeError, match="provider disconnected"):
        await anext(stream)
    with pytest.raises(RuntimeError, match="provider disconnected"):
        await stream.result()


@pytest.mark.agent
@pytest.mark.asyncio
async def test_cancellation_stops_stream_and_registered_provider_and_tool_tasks() -> None:
    cancellation = CancellationToken()
    stream: EventStream[str, None] = EventStream(cancellation)
    provider_cancelled = asyncio.Event()
    tool_cancelled = asyncio.Event()

    async def provider() -> None:
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            provider_cancelled.set()
            raise

    async def tool() -> None:
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            tool_cancelled.set()
            raise

    provider_task = cancellation.create_task(provider())
    tool_task = cancellation.create_task(tool())
    await asyncio.sleep(0)
    assert cancellation.cancel("request aborted")

    with pytest.raises(AgentCancelledError, match="request aborted"):
        await anext(stream)
    await asyncio.gather(provider_task, tool_task, return_exceptions=True)
    assert provider_cancelled.is_set()
    assert tool_cancelled.is_set()


@pytest.mark.agent
@pytest.mark.asyncio
async def test_wait_for_cancels_in_flight_operation() -> None:
    cancellation = CancellationToken()

    async def never_finishes() -> None:
        await asyncio.Future()

    pending = asyncio.create_task(cancellation.wait_for(never_finishes()))
    await asyncio.sleep(0)
    cancellation.cancel()

    with pytest.raises(AgentCancelledError):
        await pending
