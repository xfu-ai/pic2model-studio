from pathlib import Path

import pytest
from PIL import Image

from aipic_to_model.application.assets import AssetService
from aipic_to_model.application.projects import ProjectService
from aipic_to_model.domain.common import DomainErrorV1


def test_b01_05_new_versions_lineage_and_group_usage_are_stable(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Demo")
    source = tmp_path / "source.png"
    Image.new("RGB", (4, 4), "white").save(source)
    service = AssetService()
    original = service.import_file(root, project.id, source, "source_image", "import")
    derived_file = tmp_path / "derived.png"
    Image.new("RGB", (4, 4), "black").save(derived_file)
    version2 = service.register_derived(
        root,
        project.id,
        derived_file,
        "source_image",
        "v2",
        parent_asset_id=original["id"],
        input_asset_ids=[original["id"]],
        lineage_mode="new_version",
    )
    version3 = service.register_derived(
        root,
        project.id,
        derived_file,
        "source_image",
        "v3",
        parent_asset_id=version2["id"],
        input_asset_ids=[version2["id"]],
        lineage_mode="new_version",
    )
    assert [original["version_no"], version2["version_no"], version3["version_no"]] == [1, 2, 3]
    assert original["asset_family_id"] == version2["asset_family_id"] == version3["asset_family_id"]
    lineage = service.lineage(root, project.id, original["id"])
    assert version2["id"] in lineage["children"]
    assert {item["asset_id"] for item in lineage["descendants"]} >= {
        version2["id"],
        version3["id"],
    }
    listed = service.list_by_group(root, project.id, group="input_images")
    assert {item["id"] for item in listed} >= {original["id"], version2["id"], version3["id"]}
    assert all("usage" in item for item in listed)


def test_b01_05_compare_rejects_unrelated_families(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Demo")
    source = tmp_path / "source.png"
    Image.new("RGB", (2, 2)).save(source)
    service = AssetService()
    left = service.import_file(root, project.id, source, "source_image", "one")
    right = service.import_file(root, project.id, source, "source_image", "two")
    with pytest.raises(DomainErrorV1):
        service.compare_siblings(root, project.id, left["id"], right["id"])
