from pathlib import Path

from PIL import Image

from aipic_to_model.application.assets import AssetService
from aipic_to_model.application.projects import ProjectService
from aipic_to_model.infrastructure.sqlite.connection import connect


def test_b01_05_current_decision_and_event_are_persisted(tmp_path: Path):
    root = tmp_path / "project"
    p = ProjectService().create(root, "Demo")
    image = tmp_path / "a.png"
    Image.new("RGB", (3, 3)).save(image)
    service = AssetService()
    a = service.import_file(root, p.id, image, "source_image", "r")
    service.set_current(root, p.id, a["id"], "user", "current")
    c = connect(root / "project.sqlite3")
    assert c.execute("select count(*) from asset_decisions").fetchone()[0] == 1
    assert (
        c.execute(
            "select count(*) from events where event_type='asset.current_changed'"
        ).fetchone()[0]
        == 1
    )
    c.close()


def test_b01_06_trash_with_confirmed_impact_and_restore(tmp_path: Path):
    root = tmp_path / "project"
    p = ProjectService().create(root, "Demo")
    image = tmp_path / "a.png"
    Image.new("RGB", (3, 3)).save(image)
    service = AssetService()
    a = service.import_file(root, p.id, image, "source_image", "r")
    impact = service.impact(root, p.id, a["id"])
    trashed = service.trash(root, p.id, a["id"], impact["impact_token"])
    assert trashed["trashed_at"]
    assert service.restore_from_trash(root, p.id, a["id"], "restore")["trashed_at"] is None


def test_b01_05_06_asset_commands_replay_without_duplicate_events(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Demo")
    image = tmp_path / "asset.png"
    Image.new("RGB", (3, 3)).save(image)
    service = AssetService()
    asset = service.import_file(root, project.id, image, "source_image", "import")

    assert service.set_current(
        root, project.id, asset["id"], "user", "current"
    ) == service.set_current(root, project.id, asset["id"], "user", "current")
    assert service.hide(root, project.id, asset["id"], True, "hide") == service.hide(
        root, project.id, asset["id"], True, "hide"
    )
    impact = service.impact(root, project.id, asset["id"])
    assert service.trash(
        root, project.id, asset["id"], impact["impact_token"], "trash"
    ) == service.trash(root, project.id, asset["id"], impact["impact_token"], "trash")
    assert service.restore_from_trash(
        root, project.id, asset["id"], "restore"
    ) == service.restore_from_trash(root, project.id, asset["id"], "restore")
    connection = connect(root / "project.sqlite3")
    try:
        assert connection.execute("SELECT count(*) FROM asset_decisions").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM events").fetchone()[0] == 6
    finally:
        connection.close()
