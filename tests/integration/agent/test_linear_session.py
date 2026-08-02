from __future__ import annotations

import json

import pytest

from aipic_to_model.agent.core.events import AgentEvent, AgentEventType
from aipic_to_model.agent.core.models import (
    AssistantMessage,
    TextContent,
    ToolResult,
    ToolResultMessage,
    UserMessage,
)
from aipic_to_model.agent.session.sqlite import LinearSessionRepository


@pytest.mark.agent
@pytest.mark.asyncio
async def test_linear_session_persists_complete_messages_and_tool_linkage(tmp_path) -> None:
    repository = LinearSessionRepository(tmp_path / "agent.sqlite3")
    session = repository.create(
        system_prompt="system",
        profile={"provider": "fake", "model": "fake"},
        active_tools=("calculator.add",),
        active_skills=("math",),
    )
    operation = repository.start_operation(session.id)
    user = UserMessage("add")
    assistant = AssistantMessage((TextContent("calling"),))
    result = ToolResultMessage(
        "call-1", "calculator.add", ToolResult((TextContent("42"),), details={})
    )
    await repository.listener(
        session.id, operation, AgentEvent(AgentEventType.MESSAGE_END, {"message": user.to_dict()})
    )
    await repository.listener(
        session.id,
        operation,
        AgentEvent(AgentEventType.MESSAGE_END, {"message": assistant.to_dict()}),
    )
    await repository.listener(
        session.id,
        operation,
        AgentEvent(
            AgentEventType.TOOL_EXECUTION_START,
            {"tool_call_id": "call-1", "tool_name": "calculator.add"},
        ),
    )
    await repository.listener(
        session.id,
        operation,
        AgentEvent(
            AgentEventType.TOOL_EXECUTION_END, {"tool_call_id": "call-1", "result": {"ok": True}}
        ),
    )
    await repository.listener(
        session.id, operation, AgentEvent(AgentEventType.MESSAGE_END, {"message": result.to_dict()})
    )
    await repository.listener(session.id, operation, AgentEvent(AgentEventType.AGENT_END))

    reopened = repository.open(session.id)
    assert [message.id for message in reopened.messages] == [user.id, assistant.id, result.id]
    assert reopened.active_tools == ("calculator.add",) and reopened.active_skills == ("math",)
    with repository._connect() as connection:
        tool = connection.execute("SELECT state,result_json FROM agent_tool_operations").fetchone()
        assert tool[0] == "completed" and json.loads(tool[1]) == {"ok": True}
        assert connection.execute("SELECT state FROM agent_operations").fetchone()[0] == "completed"


@pytest.mark.agent
def test_message_delta_events_are_never_persisted(tmp_path) -> None:
    repository = LinearSessionRepository(tmp_path / "agent.sqlite3")
    session = repository.create()
    operation = repository.start_operation(session.id)

    # Only message_end is durable; a stream delta has no complete Message artifact.
    assert repository.open(session.id).messages == ()
    repository.finish_operation(operation)
