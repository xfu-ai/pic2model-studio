from pathlib import Path

from PIL import Image

from aipic_to_model.application.host_capabilities import HostCapabilityStore
from aipic_to_model.composition import compose_local_app
from aipic_to_model.infrastructure.sqlite.multiview_repository import MultiviewRepository


def test_desktop_manual_views_create_confirmed_managed_multiview_set(tmp_path: Path) -> None:
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Manual views")
    members: dict[str, str] = {}
    for view in ("front", "side", "back"):
        source = tmp_path / f"{view}.png"
        Image.new("RGB", (24, 16), "blue").save(source)
        asset = dependencies.assets.import_file(root, project.id, source, "source_image", "image")
        members[view] = str(asset["id"])

    set_id = dependencies.multiview.create_from_existing_views(
        root, project.id, source_asset_id=members["front"], members=members, request_id="manual-views"
    )
    repository = MultiviewRepository()
    regions = repository.region_selection_ids(root / "project.sqlite3", set_id=set_id)
    assert set(regions) == {"front", "side", "back"}
    for selection_id in regions.values():
        assert dependencies.selections.get(root, project.id, selection_id)["status"] == "confirmed"
