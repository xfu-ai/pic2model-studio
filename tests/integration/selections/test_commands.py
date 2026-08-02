from __future__ import annotations

from pathlib import Path

import pytest

from aipic_to_model.infrastructure.sqlite.connection import connect, migrate
from aipic_to_model.infrastructure.sqlite.selection_history import SelectionHistoryRepository


def _database(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    database = root / "project.sqlite3"
    migrate(database, root / "recovery")
    connection = connect(database)
    try:
        connection.execute(
            "INSERT INTO projects(id,name,created_at,updated_at) VALUES('project-1','test','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')"
        )
        connection.execute(
            """INSERT INTO assets(id,project_id,asset_family_id,asset_type,name,version_no,relative_path,
            mime_type,size_bytes,sha256,metadata_json,created_at) VALUES(
            'asset-1','project-1','family-1','source_image','source',1,'assets/source/a.png','image/png',1,
            'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','{"width":100,"height":100}','2026-01-01T00:00:00Z')"""
        )
        connection.execute(
            """INSERT INTO selections(id,project_id,asset_id,selection_type,geometry_json,label,source,status,
            confirmed_by_user,revision,created_at,updated_at) VALUES(
            'selection-1','project-1','asset-1','rect','{"rects":[{"x":10,"y":10,"width":30,"height":30}]}',
            'target','user','draft',0,1,'2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')"""
        )
    finally:
        connection.close()
    return database


def _geometry(x: int) -> dict[str, object]:
    return {"rects": [{"x": x, "y": 10, "width": 30, "height": 30}]}


def test_selection_undo_redo_survives_new_repository_instance(tmp_path: Path) -> None:
    database = _database(tmp_path)
    history = SelectionHistoryRepository()
    revision = history.apply(
        database,
        project_id="project-1",
        selection_id="selection-1",
        expected_revision=1,
        command_type="move",
        geometry=_geometry(20),
    )
    assert revision == 2
    # New instance simulates restart: all replay data is SQLite-backed.
    revision = SelectionHistoryRepository().undo(
        database, project_id="project-1", selection_id="selection-1", expected_revision=2
    )
    assert revision == 3
    revision = SelectionHistoryRepository().redo(
        database, project_id="project-1", selection_id="selection-1", expected_revision=3
    )
    assert revision == 4
    connection = connect(database, read_only=True)
    try:
        assert (
            connection.execute(
                "SELECT geometry_json FROM selections WHERE id='selection-1'"
            ).fetchone()[0]
            == '{"rects":[{"height":30,"width":30,"x":20,"y":10}]}'
        )
        assert [
            row[0]
            for row in connection.execute(
                "SELECT command_type FROM selection_revisions ORDER BY revision"
            )
        ] == ["move", "undo", "redo"]
    finally:
        connection.close()


def test_selection_commands_require_current_revision(tmp_path: Path) -> None:
    database = _database(tmp_path)
    with pytest.raises(ValueError, match="revision conflict"):
        SelectionHistoryRepository().apply(
            database,
            project_id="project-1",
            selection_id="selection-1",
            expected_revision=8,
            command_type="numeric",
            geometry=_geometry(10),
        )


def test_clear_and_confirm_are_durable_selection_state_transitions(tmp_path: Path) -> None:
    database = _database(tmp_path)
    history = SelectionHistoryRepository()
    assert (
        history.apply(
            database,
            project_id="project-1",
            selection_id="selection-1",
            expected_revision=1,
            command_type="clear",
        )
        == 2
    )
    assert (
        SelectionHistoryRepository().undo(
            database, project_id="project-1", selection_id="selection-1", expected_revision=2
        )
        == 3
    )
    assert (
        SelectionHistoryRepository().apply(
            database,
            project_id="project-1",
            selection_id="selection-1",
            expected_revision=3,
            command_type="confirm",
        )
        == 4
    )
    connection = connect(database, read_only=True)
    try:
        state = connection.execute(
            "SELECT status,confirmed_by_user,visual_state FROM selections WHERE id='selection-1'"
        ).fetchone()
        assert tuple(state) == ("confirmed", 1, "user_confirmed")
    finally:
        connection.close()
