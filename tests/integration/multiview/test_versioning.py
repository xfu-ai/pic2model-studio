from __future__ import annotations

from pathlib import Path

from aipic_to_model.infrastructure.sqlite.connection import connect, migrate
from aipic_to_model.infrastructure.sqlite.multiview_repository import MultiviewRepository


def test_regenerating_side_keeps_front_and_back_ids(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    database = root / "project.sqlite3"
    migrate(database, root / "recovery")
    connection = connect(database)
    try:
        connection.execute(
            "INSERT INTO projects(id,name,created_at,updated_at) VALUES('project','test','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')"
        )
        for number, asset_id in enumerate(("source", "front", "side", "back", "side2"), start=1):
            connection.execute(
                "INSERT INTO assets(id,project_id,asset_family_id,asset_type,name,version_no,relative_path,mime_type,size_bytes,sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    asset_id,
                    "project",
                    asset_id,
                    "source_image",
                    asset_id,
                    number,
                    f"assets/{asset_id}.png",
                    "image/png",
                    1,
                    asset_id.ljust(64, "0"),
                    "2026-01-01T00:00:00Z",
                ),
            )
    finally:
        connection.close()
    repository = MultiviewRepository()
    set_id = repository.create_set(
        database,
        project_id="project",
        source_asset_id="source",
        members={"front": "front", "side": "side", "back": "back"},
    )
    assert (
        repository.regenerate_view(database, set_id=set_id, view_name="side", asset_id="side2") == 2
    )
    assert repository.current_assets(database, set_id) == {
        "front": "front",
        "side": "side2",
        "back": "back",
    }
