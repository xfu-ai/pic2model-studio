from __future__ import annotations

import pytest

from aipic_to_model.agent.core.models import (
    AssistantMessage,
    ProviderEvent,
    ProviderEventType,
    TextContent,
    Usage,
    UserMessage,
)
from aipic_to_model.agent.harness import AgentHarness, CompactionSettings
from aipic_to_model.agent.providers.base import ModelProfile
from aipic_to_model.agent.providers.fake import FakeProvider, ScriptedResponse
from aipic_to_model.agent.session.sqlite import LinearSessionRepository


def _answer(text: str, usage: int = 0) -> ScriptedResponse:
    return ScriptedResponse(
        (
            ProviderEvent(ProviderEventType.MESSAGE_START),
            ProviderEvent(
                ProviderEventType.MESSAGE_END,
                message=AssistantMessage((TextContent(text),), usage=Usage(total_tokens=usage)),
            ),
        )
    )


@pytest.mark.agent
@pytest.mark.asyncio
async def test_threshold_compaction_runs_before_next_turn_at_a_save_point(tmp_path) -> None:
    repository = LinearSessionRepository(tmp_path / "agent.sqlite3")
    session = repository.create()
    for number in range(3):
        repository.append_message(session.id, UserMessage(f"old {number}"))
        repository.append_message(
            session.id, AssistantMessage((TextContent("old"),), usage=Usage(total_tokens=95))
        )
    harness = AgentHarness(
        FakeProvider((_answer("new"),)),
        ModelProfile("fake", "fake", "http://fake"),
        repository,
        session.id,
        context_window=100,
        compaction_settings=CompactionSettings(reserve_tokens=20, keep_recent_tokens=1),
    )

    await harness.prompt("continue")

    record = repository.latest_compaction(session.id)
    assert record is not None and record.reason == "threshold"
    assert [event.type.value for event in harness.events].count("context_compacted") == 1


@pytest.mark.agent
@pytest.mark.asyncio
async def test_overflow_compacts_and_retries_the_original_turn_once(tmp_path) -> None:
    repository = LinearSessionRepository(tmp_path / "agent.sqlite3")
    session = repository.create()
    for number in range(3):
        repository.append_message(session.id, UserMessage(f"old {number}"))
        repository.append_message(session.id, AssistantMessage((TextContent("old"),)))
    overflow = ScriptedResponse(
        (ProviderEvent(ProviderEventType.PROVIDER_ERROR, error_message="context overflow"),)
    )
    provider = FakeProvider((overflow, _answer("recovered")))
    harness = AgentHarness(
        provider,
        ModelProfile("fake", "fake", "http://fake"),
        repository,
        session.id,
        compaction_settings=CompactionSettings(keep_recent_tokens=1),
    )

    result = await harness.prompt("retry me")

    assert result[-1].role == "assistant"
    assert len(provider.requests) == 2
    assert repository.latest_compaction(session.id).reason == "overflow"  # type: ignore[union-attr]
    assert [event.type.value for event in harness.events].count("retry_scheduled") == 1
