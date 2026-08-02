from pathlib import Path

import pytest

from aipic_to_model.application.events import EventService, NewEvent
from aipic_to_model.application.projects import ProjectService
from aipic_to_model.infrastructure.sqlite.connection import connect, transaction
from aipic_to_model.infrastructure.sqlite.repositories import EventRepository


def test_b01_11_event_replay_is_ordered_and_ack_never_rewinds(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Demo")
    repository = EventRepository()
    events = EventService(repository)
    for index in range(5):
        repository.append_named_committed(
            root / "project.sqlite3",
            project.id,
            "project.metadata.changed",
            {"changed_fields": [str(index)], "request_id": str(index)},
        )
    first = events.replay_project(root, project.id, None, 2)
    second = events.replay_project(root, project.id, first["next_cursor"], 10)
    assert [item["sequence_no"] for item in first["items"] + second["items"]] == [1, 2, 3, 4, 5]
    repository.ack_committed(root / "project.sqlite3", project.id, "test", 5)
    repository.ack_committed(root / "project.sqlite3", project.id, "test", 2)
    connection = connect(root / "project.sqlite3")
    assert (
        connection.execute(
            "SELECT last_sequence_no FROM event_consumers WHERE project_id=? AND consumer_id='test'",
            (project.id,),
        ).fetchone()[0]
        == 5
    )
    connection.close()


def test_b01_11_event_service_exposes_append_replay_and_ack(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Event service")
    events = EventService(EventRepository())
    connection = connect(root / "project.sqlite3")
    try:
        with transaction(connection):
            appended = events.append_in_tx(
                NewEvent(
                    connection,
                    project.id,
                    "project.metadata.changed",
                    {"changed_fields": ["name"], "request_id": "event-request"},
                )
            )
    finally:
        connection.close()
    page = events.replay(root, project.id, None, 10)
    events.ack(root, project.id, "consumer", appended.sequence_no)
    events.ack(root, project.id, "consumer", 0)
    connection = connect(root / "project.sqlite3")
    try:
        assert page["items"][0]["event_id"] == appended.event_id
        assert (
            connection.execute(
                "SELECT last_sequence_no FROM event_consumers "
                "WHERE project_id=? AND consumer_id='consumer'",
                (project.id,),
            ).fetchone()[0]
            == appended.sequence_no
        )
    finally:
        connection.close()


def test_b01_11_append_in_tx_rolls_back_with_the_business_transaction(
    tmp_path: Path,
):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Event rollback")
    events = EventService(EventRepository())
    connection = connect(root / "project.sqlite3")
    try:
        with (
            pytest.raises(RuntimeError, match="abort business change"),
            transaction(connection),
        ):
            connection.execute(
                "UPDATE projects SET name='must rollback' WHERE id=?",
                (project.id,),
            )
            events.append_in_tx(
                NewEvent(
                    connection,
                    project.id,
                    "project.metadata.changed",
                    {
                        "changed_fields": ["name"],
                        "request_id": "rolled-back-event",
                    },
                )
            )
            raise RuntimeError("abort business change")
        assert (
            connection.execute("SELECT name FROM projects WHERE id=?", (project.id,)).fetchone()[0]
            == "Event rollback"
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM events WHERE payload_json LIKE '%rolled-back-event%'"
            ).fetchone()[0]
            == 0
        )
    finally:
        connection.close()
