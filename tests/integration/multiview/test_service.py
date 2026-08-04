from __future__ import annotations

import base64
from io import BytesIO

from PIL import Image

from aipic_to_model.application.host_capabilities import HostCapabilityStore
from aipic_to_model.application.multiview import MultiviewService
from aipic_to_model.composition import compose_local_app
from aipic_to_model.infrastructure.sqlite.multiview_repository import MultiviewRepository


def _png(colour: str) -> str:
    output = BytesIO()
    Image.new("RGB", (20, 16), colour).save(output, "PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


def test_three_view_regions_and_confirmed_crops_are_persistent(tmp_path) -> None:
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Multiview")
    source_file = tmp_path / "source.png"
    source_file.write_bytes(base64.b64decode(_png("grey")))
    source = dependencies.assets.import_file(
        root, project.id, source_file, "source_image", "source"
    )
    repository = MultiviewRepository()
    service = MultiviewService(dependencies.assets, dependencies.selections, repository)
    set_id = service.create_from_base64_views(
        root,
        project.id,
        source_asset_id=str(source["id"]),
        views={"front": _png("red"), "side": _png("green"), "back": _png("blue")},
        request_id="views",
    )
    original = repository.current_assets(root / "project.sqlite3", set_id)
    service.confirm_regions(root, project.id, set_id=set_id, request_id="confirm")
    cropped = service.crop_confirmed_views(root, project.id, set_id=set_id, request_id="crop")
    assert set(cropped) == {"front", "side", "back"}
    assert all(cropped[view] != original[view] for view in cropped)
    assert repository.is_ready_for_submission(
        root / "project.sqlite3", set_id=set_id, members=cropped
    )
    report = service.validate(
        root,
        set_id=set_id,
        checks={
            "subject_scale": "passed",
            "direction": "passed",
            "key_accessory": "passed",
            "truncation": "passed",
            "background": "passed",
            "resolution": "passed",
        },
    )
    assert report.can_continue


def test_optional_quality_review_does_not_replace_crop_confirmation(tmp_path) -> None:
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Manual quality")
    source_file = tmp_path / "source.png"
    source_file.write_bytes(base64.b64decode(_png("grey")))
    source = dependencies.assets.import_file(
        root, project.id, source_file, "source_image", "source"
    )
    repository = MultiviewRepository()
    service = MultiviewService(dependencies.assets, dependencies.selections, repository)
    set_id = service.create_from_base64_views(
        root,
        project.id,
        source_asset_id=str(source["id"]),
        views={"front": _png("red"), "side": _png("green"), "back": _png("blue")},
        request_id="views",
    )
    action = dependencies.registry.execute(
        root,
        project.id,
        "multiview.request_quality_confirmation",
        "1.0.0",
        {"multiview_set_id": set_id},
        "request-quality",
    )
    assert action.status == "awaiting_ui_action"
    assert action.ui_action and action.ui_action["type"] == "confirm_multiview_quality"
    confirmed = dependencies.registry.execute(
        root,
        project.id,
        "multiview.set_quality_checks",
        "1.0.0",
        {
            "multiview_set_id": set_id,
            "checks": {
                "subject_scale": "passed",
                "direction": "warning",
                "key_accessory": "passed",
                "truncation": "passed",
                "background": "passed",
                "resolution": "passed",
            },
        },
        "set-quality",
    )
    assert confirmed.status == "succeeded"
    members = repository.current_assets(root / "project.sqlite3", set_id)
    assert not repository.is_ready_for_submission(
        root / "project.sqlite3", set_id=set_id, members=members
    )
    service.confirm_regions(root, project.id, set_id=set_id, request_id="confirm-regions")
    members = service.crop_confirmed_views(
        root, project.id, set_id=set_id, request_id="confirm-crops"
    )
    assert repository.is_ready_for_submission(
        root / "project.sqlite3", set_id=set_id, members=members
    )
    repository.regenerate_view(
        root / "project.sqlite3", set_id=set_id, view_name="side", asset_id=members["side"]
    )
    assert not repository.is_ready_for_submission(
        root / "project.sqlite3", set_id=set_id, members=members
    )
