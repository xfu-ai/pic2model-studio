from pathlib import Path

import pytest
from PIL import Image

from aipic_to_model.application import assets as assets_module
from aipic_to_model.application.assets import AssetService
from aipic_to_model.application.projects import ProjectService
from aipic_to_model.domain.common import DomainErrorV1
from aipic_to_model.infrastructure.sqlite.connection import connect


def test_b01_06_trash_requires_current_impact_confirmation_and_restores(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Demo")
    image = tmp_path / "image.png"
    Image.new("RGB", (3, 3)).save(image)
    assets = AssetService()
    asset = assets.import_file(root, project.id, image, "source_image", "import")
    assets.set_current(root, project.id, asset["id"], "user", "current")
    with pytest.raises(DomainErrorV1) as error:
        assets.trash(root, project.id, asset["id"], None)
    impact = error.value.details
    assert impact and impact["is_current"]
    trashed = assets.trash(root, project.id, asset["id"], impact["impact_token"])
    assert trashed["trashed_at"] is not None
    connection = connect(root / "project.sqlite3")
    trashed_path = connection.execute(
        "SELECT relative_path FROM assets WHERE id=?", (asset["id"],)
    ).fetchone()[0]
    connection.close()
    assert (root / trashed_path).is_file()
    restored = assets.restore_from_trash(root, project.id, asset["id"], "restore")
    assert restored["trashed_at"] is None
    connection = connect(root / "project.sqlite3")
    restored_path = connection.execute(
        "SELECT relative_path FROM assets WHERE id=?", (asset["id"],)
    ).fetchone()[0]
    connection.close()
    assert (root / restored_path).is_file()


def test_b01_06_trash_rejects_expired_impact_confirmation(tmp_path: Path, monkeypatch):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Demo")
    image = tmp_path / "image.png"
    Image.new("RGB", (3, 3)).save(image)
    service = AssetService()
    asset = service.import_file(root, project.id, image, "source_image", "import")
    service.set_current(root, project.id, asset["id"], "user", "current")
    monkeypatch.setattr(assets_module.time, "time", lambda: 1_000)
    impact = service.impact(root, project.id, asset["id"])
    monkeypatch.setattr(assets_module.time, "time", lambda: 1_061)
    with pytest.raises(DomainErrorV1) as error:
        service.trash(root, project.id, asset["id"], impact["impact_token"])
    assert error.value.code == "ASSET_REFERENCED"
    assert error.value.details and error.value.details["expires_at"] == 1_121
