import time

from aipic_to_model.agent.core.events import AgentEvent, AgentEventType
from aipic_to_model.agent.core.models import AssistantMessage, TextContent, UserMessage
from aipic_to_model.agent.harness.context import project_context
from aipic_to_model.agent.providers.model_catalog import load_frozen_catalog
from aipic_to_model.agent.session.sqlite import LinearSessionRepository


def test_agent_runtime_local_performance_baseline(tmp_path):
    """Generous release regression limits for the local-only Agent hot paths."""
    repository = LinearSessionRepository(tmp_path / "agent.sqlite3")
    session = repository.create()
    started = time.perf_counter()
    for index in range(500):
        repository.append_message(session.id, UserMessage(f"input {index}"))
        repository.append_message(session.id, AssistantMessage((TextContent("output"),)))
    assert time.perf_counter() - started < 5

    loaded = repository.open(session.id).messages
    started = time.perf_counter()
    projection = project_context(loaded, summary="summary", first_kept_sequence=501)
    assert projection.messages and time.perf_counter() - started < 0.2

    started = time.perf_counter()
    catalog = load_frozen_catalog()
    assert len(catalog.models) >= 1_100 and time.perf_counter() - started < 1

    started = time.perf_counter()
    events = [AgentEvent(AgentEventType.MESSAGE_UPDATE, {"delta": "x"}) for _ in range(1_000)]
    assert len(events) == 1_000 and time.perf_counter() - started < 0.2
