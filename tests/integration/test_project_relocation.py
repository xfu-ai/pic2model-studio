import shutil
from pathlib import Path

from PIL import Image

from aipic_to_model.application.assets import AssetService
from aipic_to_model.application.projects import ProjectService
from aipic_to_model.infrastructure.sqlite.connection import connect


def test_b01_02_moved_project_opens_without_rewriting_relative_asset_paths(tmp_path: Path):
    original = tmp_path / "original"
    project = ProjectService().create(original, "Moved")
    source = tmp_path / "image.png"
    Image.new("RGB", (2, 2)).save(source)
    asset = AssetService().import_file(original, project.id, source, "source_image", "import")
    moved = tmp_path / "elsewhere" / "moved"
    moved.parent.mkdir()
    shutil.move(str(original), moved)
    reopened = ProjectService().open(moved)
    assert reopened.id == project.id
    connection = connect(moved / "project.sqlite3")
    row = connection.execute(
        "SELECT root_path,relative_path FROM projects JOIN assets ON assets.project_id=projects.id WHERE assets.id=?",
        (asset["id"],),
    ).fetchone()
    connection.close()
    assert row["root_path"] == "." and (moved / row["relative_path"]).is_file()
