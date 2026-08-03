import shutil
from pathlib import Path

from PIL import Image

from aipic_to_model.application.archive_import import ProjectPackageService
from aipic_to_model.application.assets import AssetService
from aipic_to_model.application.projects import ProjectService
from aipic_to_model.application.selections import SelectionService


def test_b01_12_local_lifecycle_e2e_without_network_or_provider(tmp_path: Path):
    root = tmp_path / "project"
    source = tmp_path / "source.png"
    Image.new("RGBA", (12, 8), (20, 40, 60, 255)).save(source)
    projects, assets, selections = ProjectService(), AssetService(), SelectionService()
    project = projects.create(root, "E2E")
    imported = assets.import_file(root, project.id, source, "source_image", "import")
    assets.set_current(root, project.id, imported["id"], "user", "current")
    derived = assets.register_derived(
        root,
        project.id,
        source,
        "generated_image",
        "derived",
        parent_asset_id=None,
        input_asset_ids=[imported["id"]],
        name="candidate.png",
        provenance={"schema_version": 1, "source_kind": "tool", "parameters": {}},
    )
    selection = selections.save(
        root,
        project.id,
        imported["id"],
        [{"rect_id": "r", "label": "front", "x": 1, "y": 1, "width": 4, "height": 3}],
        "front",
        "user",
    )
    selections.confirm(root, project.id, selection["id"], selection["revision"])
    assert selections.crop(root, project.id, selection["id"], "crop")
    assert selections.render_annotation(root, project.id, selection["id"], "annotation")
    assets.hide(root, project.id, derived["id"], True)
    trashed = assets.trash(root, project.id, derived["id"], None, "trash")
    assert trashed["trashed_at"]
    restored = assets.restore_from_trash(root, project.id, derived["id"], "restore")
    assert restored["trashed_at"] is None
    package = tmp_path / "e2e.pic2model"
    exported = ProjectPackageService().export_v1(root, package)
    imported_package = ProjectPackageService().import_v1(package, tmp_path / "imported")
    assert exported["asset_count"] == imported_package["asset_count"]
    moved = tmp_path / "moved"
    shutil.move(str(root), str(moved))
    reopened = projects.open(moved)
    assert reopened.id == project.id
