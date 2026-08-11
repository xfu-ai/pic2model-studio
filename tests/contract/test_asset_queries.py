from pathlib import Path

from PIL import Image

from aipic_to_model.application.assets import AssetService
from aipic_to_model.application.projects import ProjectService


def test_b01_05_asset_groups_usage_lineage_and_sibling_comparison_are_stable(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Queries")
    image = tmp_path / "image.png"
    Image.new("RGB", (4, 4)).save(image)
    service = AssetService()
    source = service.import_file(root, project.id, image, "source_image", "import")
    first = service.register_derived(
        root, project.id, image, "generated_image", "generated", input_asset_ids=[source["id"]]
    )
    second = service.register_derived(
        root,
        project.id,
        image,
        "generated_image",
        "next",
        parent_asset_id=first["id"],
        input_asset_ids=[source["id"]],
        lineage_mode="new_version",
    )
    for asset_type in ("annotation", "crop", "multiview", "texture", "export"):
        service.register_derived(root, project.id, image, asset_type, f"{asset_type}-request")
    groups = {item["group"] for item in service.list_by_group(root, project.id) if item["group"]}
    assert {
        "input_images",
        "generated_images",
        "split_elements",
        "multiview_and_crops",
        "models",
        "exports",
    } <= groups
    default_source = next(item for item in service.list_by_group(root, project.id) if item["id"] == source["id"])
    assert "visual_fingerprint" not in default_source
    visual_source = next(
        item
        for item in service.list_by_group(root, project.id, include_visual_identities=True)
        if item["id"] == source["id"]
    )
    assert len(visual_source["visual_fingerprint"]) == 128
    assert visual_source["visual_aspect_ratio"] == 1
    lineage = service.lineage(root, project.id, first["id"])
    assert lineage["children"] == [second["id"]]
    assert service.compare_siblings(root, project.id, first["id"], second["id"])["same_family"]
    usage = service.usage_summary(root, project.id, source["id"])
    assert usage["input_link_count"] >= 2
