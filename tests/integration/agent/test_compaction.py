from __future__ import annotations

import pytest

from aipic_to_model.agent.core.models import AssistantMessage, TextContent, UserMessage
from aipic_to_model.agent.harness import AgentHarness, CompactionSettings
from aipic_to_model.agent.providers.base import ModelProfile
from aipic_to_model.agent.providers.fake import FakeProvider
from aipic_to_model.agent.session.sqlite import LinearSessionRepository


@pytest.mark.agent
@pytest.mark.asyncio
async def test_manual_compaction_persists_record_and_reopens_projection(tmp_path) -> None:
    repository = LinearSessionRepository(tmp_path / "agent.sqlite3")
    session = repository.create(system_prompt="system")
    for number in range(3):
        repository.append_message(session.id, UserMessage(f"request {number}"))
        repository.append_message(session.id, AssistantMessage((TextContent(f"answer {number}"),)))
    harness = AgentHarness(
        FakeProvider(()),
        ModelProfile("fake", "fake", "http://fake"),
        repository,
        session.id,
        compaction_settings=CompactionSettings(keep_recent_tokens=1),
    )

    assert await harness.compact()
    record = repository.latest_compaction(session.id)
    assert record is not None and record.reason == "manual"
    assert record.summary is not None and "## Goal" in record.summary
    assert record.first_kept_sequence == 5
    assert len(repository.open(session.id).messages) == 6
    assert harness.snapshot().context[0].role == "system"
    with repository._connect() as connection:
        assert (
            connection.execute("SELECT state FROM agent_compactions").fetchone()[0] == "committed"
        )


@pytest.mark.agent
@pytest.mark.asyncio
async def test_before_compact_hook_can_cancel_without_changing_raw_context(tmp_path) -> None:
    repository = LinearSessionRepository(tmp_path / "agent.sqlite3")
    session = repository.create()
    repository.append_message(session.id, UserMessage("old"))
    repository.append_message(session.id, AssistantMessage((TextContent("answer"),)))
    harness = AgentHarness(
        FakeProvider(()),
        ModelProfile("fake", "fake", "http://fake"),
        repository,
        session.id,
        session_before_compact=lambda _input: False,
    )

    assert not await harness.compact()
    assert repository.latest_compaction(session.id) is None
    assert [message.role for message in repository.open(session.id).messages] == [
        "user",
        "assistant",
    ]


@pytest.mark.agent
@pytest.mark.asyncio
async def test_oversized_single_turn_uses_prefix_summary_and_retains_suffix(tmp_path) -> None:
    repository = LinearSessionRepository(tmp_path / "agent.sqlite3")
    session = repository.create()
    user = UserMessage("describe the asset")
    assistant = AssistantMessage((TextContent("x" * 20_000),))
    repository.append_message(session.id, user)
    repository.append_message(session.id, assistant)
    harness = AgentHarness(
        FakeProvider(()),
        ModelProfile("fake", "fake", "http://fake"),
        repository,
        session.id,
        compaction_settings=CompactionSettings(keep_recent_tokens=1),
    )

    assert await harness.compact()
    record = repository.latest_compaction(session.id)
    assert record is not None and record.first_kept_sequence == 2
    assert record.retained_tail == (assistant,)
    assert "describe the asset" in record.summary


@pytest.mark.agent
def test_started_compaction_is_interrupted_on_reopen_and_raw_messages_survive(tmp_path) -> None:
    repository = LinearSessionRepository(tmp_path / "agent.sqlite3")
    session = repository.create()
    repository.append_message(session.id, UserMessage("keep me"))
    repository.start_compaction(
        session.id,
        reason="manual",
        tokens_before=1,
        provider_id="fake",
        model="fake",
        previous_compaction_id=None,
    )

    reopened = repository.open(session.id)

    assert [message.role for message in reopened.messages] == ["user"]
    with repository._connect() as connection:
        assert (
            connection.execute("SELECT state FROM agent_compactions").fetchone()[0] == "interrupted"
        )
