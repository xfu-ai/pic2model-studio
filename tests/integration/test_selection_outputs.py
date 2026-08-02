from pathlib import Path

import pytest
from PIL import Image

from aipic_to_model.application.assets import AssetService
from aipic_to_model.application.projects import ProjectService
from aipic_to_model.application.selections import SelectionService
from aipic_to_model.domain.common import DomainErrorV1, ErrorCode


def test_b01_07_confirmed_selection_generates_pixel_exact_crop(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Demo")
    source = tmp_path / "grid.png"
    image = Image.new("RGB", (5, 4))
    image.putdata([(x * 40, y * 40, 0) for y in range(4) for x in range(5)])
    image.save(source)
    asset = AssetService().import_file(root, project.id, source, "source_image", "request")
    selections = SelectionService()
    saved = selections.save(
        root, project.id, asset["id"], [{"x": 1, "y": 1, "width": 2, "height": 2}], "target", "user"
    )
    selections.confirm(root, project.id, saved["id"], saved["revision"])
    crop = selections.crop(root, project.id, saved["id"], "crop-request")[0]
    from_path = root / "assets" / "source" / f"{asset['id']}.png"
    crop_path = next((root / "assets" / "selections").glob(f"{crop['id']}.*"))
    with Image.open(from_path) as original, Image.open(crop_path) as result:
        assert result.size == (2, 2)
        assert list(result.get_flattened_data()) == list(
            original.crop((1, 1, 3, 3)).get_flattened_data()
        )


def test_b01_07_selection_save_request_id_replays_or_conflicts(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Demo")
    source = tmp_path / "image.png"
    Image.new("RGB", (4, 4)).save(source)
    asset = AssetService().import_file(root, project.id, source, "source_image", "import")
    selections = SelectionService()
    args = (root, project.id, asset["id"], [{"x": 0, "y": 0, "width": 2, "height": 2}], "x", "user")
    first = selections.save(*args, request_id="selection")
    assert selections.save(*args, request_id="selection") == first
    with pytest.raises(DomainErrorV1) as conflict:
        selections.save(
            root,
            project.id,
            asset["id"],
            [{"x": 1, "y": 1, "width": 2, "height": 2}],
            "x",
            "user",
            request_id="selection",
        )
    assert conflict.value.code == ErrorCode.IDEMPOTENCY_CONFLICT


def test_b01_07_selection_confirm_request_id_replays_or_conflicts(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Demo")
    source = tmp_path / "image.png"
    Image.new("RGB", (4, 4)).save(source)
    asset = AssetService().import_file(root, project.id, source, "source_image", "import")
    selections = SelectionService()
    saved = selections.save(
        root,
        project.id,
        asset["id"],
        [{"x": 0, "y": 0, "width": 2, "height": 2}],
        "x",
        "user",
    )

    first = selections.confirm(
        root, project.id, saved["id"], saved["revision"], request_id="confirm"
    )
    assert (
        selections.confirm(root, project.id, saved["id"], saved["revision"], request_id="confirm")
        == first
    )
    with pytest.raises(DomainErrorV1) as conflict:
        selections.confirm(
            root, project.id, saved["id"], saved["revision"] + 1, request_id="confirm"
        )
    assert conflict.value.code == ErrorCode.IDEMPOTENCY_CONFLICT


def test_b01_07_multirect_crop_uses_one_replayable_command_id(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Demo")
    source = tmp_path / "image.png"
    Image.new("RGB", (6, 4)).save(source)
    asset = AssetService().import_file(root, project.id, source, "source_image", "import")
    selections = SelectionService()
    saved = selections.save(
        root,
        project.id,
        asset["id"],
        [
            {"rect_id": "left", "x": 0, "y": 0, "width": 2, "height": 2},
            {"rect_id": "right", "x": 3, "y": 1, "width": 2, "height": 2},
        ],
        "two",
        "user",
    )
    selections.confirm(root, project.id, saved["id"], saved["revision"])
    first = selections.crop(root, project.id, saved["id"], "crop-batch")
    assert selections.crop(root, project.id, saved["id"], "crop-batch") == first
    assert len(first) == 2


def test_b01_07_only_confirm_command_can_enter_confirmed_state(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Selection states")
    source = tmp_path / "image.png"
    Image.new("RGB", (4, 4)).save(source)
    asset = AssetService().import_file(root, project.id, source, "source_image", "import")
    selections = SelectionService()
    with pytest.raises(DomainErrorV1) as invalid:
        selections.save(
            root,
            project.id,
            asset["id"],
            [{"x": 0, "y": 0, "width": 2, "height": 2}],
            "x",
            "user",
            status="confirmed",
        )
    assert invalid.value.code == ErrorCode.INVALID_SELECTION
    draft = selections.save(
        root,
        project.id,
        asset["id"],
        [{"x": 0, "y": 0, "width": 2, "height": 2}],
        "x",
        "user",
    )
    edited = selections.save(
        root,
        project.id,
        asset["id"],
        [{"x": 1, "y": 1, "width": 2, "height": 2}],
        "x",
        "user",
        status="edited",
        selection_id=draft["id"],
        expected_revision=draft["revision"],
    )
    with pytest.raises(DomainErrorV1):
        selections.save(
            root,
            project.id,
            asset["id"],
            [{"x": 1, "y": 1, "width": 2, "height": 2}],
            "x",
            "user",
            status="draft",
            selection_id=draft["id"],
            expected_revision=edited["revision"],
        )
    confirmed = selections.confirm(root, project.id, draft["id"], edited["revision"])
    assert confirmed["status"] == "confirmed"
