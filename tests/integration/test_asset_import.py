from pathlib import Path

import pytest
from PIL import Image

from aipic_to_model.application.assets import AssetService
from aipic_to_model.application.projects import ProjectService
from aipic_to_model.domain.common import DomainErrorV1, ErrorCode


def test_b01_04_import_records_required_metadata(tmp_path: Path):
    project = ProjectService().create(tmp_path / "project", "Demo")
    source = tmp_path / "source.png"
    Image.new("RGBA", (12, 8), (1, 2, 3, 255)).save(source)
    before = source.read_bytes()
    asset = AssetService().import_file(
        tmp_path / "project", project.id, source, "source_image", "request-1"
    )
    assert (
        asset["metadata"] == {"width": 12, "height": 8, "format": "PNG"}
        and source.read_bytes() == before
        and "relative_path" not in asset
    )


def test_b01_04_import_request_id_replays_and_parent_is_enforced(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Demo")
    image = tmp_path / "image.png"
    Image.new("RGBA", (3, 3), (1, 2, 3, 4)).save(image)
    assets = AssetService()
    parent = assets.import_file(root, project.id, image, "source_image", "parent")
    child = assets.import_file(
        root, project.id, image, "source_image", "child", parent_asset_id=parent["id"]
    )
    replay = assets.import_file(
        root, project.id, image, "source_image", "child", parent_asset_id=parent["id"]
    )
    assert replay["id"] == child["id"]
    with pytest.raises(DomainErrorV1) as conflict:
        assets.import_file(root, project.id, image, "source_image", "child")
    assert conflict.value.code == ErrorCode.IDEMPOTENCY_CONFLICT
