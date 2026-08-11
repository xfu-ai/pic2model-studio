from __future__ import annotations

import pytest

from aipic_to_model.agent.core.models import UserMessage
from aipic_to_model.agent.integrations.progressive_tools import PERMANENT_TOOL_NAMES
from aipic_to_model.agent.integrations.runtime import AgentRuntime
from aipic_to_model.agent.session.sqlite import LinearSessionRepository
from aipic_to_model.application.host_capabilities import HostCapabilityStore
from aipic_to_model.application.projects import ProjectService
from aipic_to_model.composition import compose_local_app


@pytest.mark.agent
def test_reopen_marks_running_operation_and_tool_interrupted_without_replay(tmp_path) -> None:
    repository = LinearSessionRepository(tmp_path / "agent.sqlite3")
    session = repository.create()
    operation = repository.start_operation(session.id)
    repository.append_message(session.id, UserMessage("before crash"))
    with repository._connect() as connection:
        connection.execute(
            "INSERT INTO agent_tool_operations VALUES(?,?,?,?,?,?)",
            (session.id, "call-1", operation, "write", "running", None),
        )

    reopened = repository.open(session.id)

    assert [message.role for message in reopened.messages] == ["user"]
    with repository._connect() as connection:
        assert (
            connection.execute("SELECT state FROM agent_operations").fetchone()[0] == "interrupted"
        )
        assert (
            connection.execute("SELECT state FROM agent_tool_operations").fetchone()[0]
            == "interrupted"
        )
        assert connection.execute("SELECT count(*) FROM agent_messages").fetchone()[0] == 1


@pytest.mark.agent
def test_session_migration_is_repeatable_and_has_no_tree_tables(tmp_path) -> None:
    repository = LinearSessionRepository(tmp_path / "agent.sqlite3")
    repository.migrate()
    repository.migrate()

    with repository._connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM agent_schema_migrations WHERE version=3"
            ).fetchone()[0]
            == 1
        )
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert not {"branches", "leaves", "tree_nodes"} & names


@pytest.mark.agent
def test_agent_migration_coexists_with_a_b01_project_database(tmp_path) -> None:
    root = tmp_path / "project"
    ProjectService().create(root, "Session")
    repository = LinearSessionRepository(root / "project.sqlite3")

    repository.migrate()
    repository.migrate()

    with repository._connect() as connection:
        assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 11
        assert connection.execute("SELECT count(*) FROM agent_schema_migrations").fetchone()[0] == 4


@pytest.mark.agent
def test_recovery_restores_append_order_and_pins_an_approval_wait_tool(tmp_path) -> None:
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Progressive Tool recovery")
    dependencies.roots[project.id] = root
    first = AgentRuntime(dependencies.registry, dependencies.root_for)
    conversation_id = str(first.create(project.id)["id"])
    repository = LinearSessionRepository(root / "agent.sqlite3")
    repository.update_config(
        conversation_id,
        active_tools_json='["read","write","edit","bash","toolbox.status",'
        '"toolbox.load","project.get_state","image.understand_for_agent",'
        '"image.remove_background_local","model3d.generate_from_image",'
        '"image.split_grid"]',
    )
    repository.register_job_wait(
        conversation_id,
        project_id=project.id,
        run_id=conversation_id,
        tool_call_id="approval-call",
        tool_name="model3d.generate_from_multiview",
    )

    recovered = AgentRuntime(dependencies.registry, dependencies.root_for)
    recovered.status(project.id, conversation_id)
    conversation = recovered._conversations[(project.id, conversation_id)]

    assert conversation.harness.active_tool_names == (
        *PERMANENT_TOOL_NAMES,
        "image.split_grid",
        "model3d.generate_from_multiview",
    )
    assert repository.open(conversation_id).active_tools == conversation.harness.active_tool_names
