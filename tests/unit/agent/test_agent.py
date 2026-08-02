from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from aipic_to_model.agent.core.agent import Agent
from aipic_to_model.agent.core.errors import AgentCoreError
from aipic_to_model.agent.core.events import AgentEventType, CancellationToken
from aipic_to_model.agent.core.models import (
    AssistantMessage,
    ProviderEvent,
    ProviderEventType,
    TextContent,
)
from aipic_to_model.agent.providers.base import ModelProfile
from aipic_to_model.agent.providers.fake import FakeProvider, ScriptedResponse


def response(message: AssistantMessage) -> ScriptedResponse:
    return ScriptedResponse(
        (
            ProviderEvent(ProviderEventType.MESSAGE_START),
            ProviderEvent(ProviderEventType.MESSAGE_END, message=message),
        )
    )


class GateProvider:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream(
        self, request, cancellation: CancellationToken
    ) -> AsyncIterator[ProviderEvent]:
        del request
        self.started.set()
        await self.release.wait()
        cancellation.raise_if_cancelled()
        yield ProviderEvent(ProviderEventType.MESSAGE_START)
        yield ProviderEvent(
            ProviderEventType.MESSAGE_END, message=AssistantMessage((TextContent("done"),))
        )


@pytest.mark.agent
@pytest.mark.asyncio
async def test_agent_rejects_concurrent_prompt_and_returns_to_idle() -> None:
    provider = GateProvider()
    agent = Agent(provider, ModelProfile("fake", "fake", "http://fake"))
    active = asyncio.create_task(agent.prompt("first"))
    await provider.started.wait()

    with pytest.raises(AgentCoreError, match="already running"):
        await agent.prompt("second")

    provider.release.set()
    await active
    await agent.wait_for_idle()
    assert not agent.state.is_running


@pytest.mark.agent
@pytest.mark.asyncio
async def test_listener_sees_projected_message_state_and_failure_cannot_stick_busy() -> None:
    provider = FakeProvider((response(AssistantMessage((TextContent("done"),))),))
    agent = Agent(provider, ModelProfile("fake", "fake", "http://fake"))
    observed: list[bool] = []

    async def listener(event) -> None:
        if event.type is AgentEventType.MESSAGE_END:
            observed.append(bool(agent.state.messages))
            raise RuntimeError("listener failure")

    agent.subscribe(listener)
    await agent.prompt("go")

    assert observed == [True]
    assert not agent.state.is_running
    assert agent.state.error is not None and "Listener failed" in agent.state.error


@pytest.mark.agent
@pytest.mark.asyncio
async def test_abort_clears_message_queues_but_preserves_next_turn_updates() -> None:
    provider = GateProvider()
    agent = Agent(provider, ModelProfile("fake", "fake", "http://fake"))
    active = asyncio.create_task(agent.prompt("go"))
    await provider.started.wait()
    agent.steer("discard steer")
    agent.follow_up("discard follow-up")
    agent.queue_next_turn(lambda _agent: None)

    agent.abort()
    with pytest.raises(AgentCoreError, match="aborted"):
        await active

    assert not agent._steering and not agent._follow_up
    assert len(agent._next_turn) == 1
    assert not agent.state.is_running
