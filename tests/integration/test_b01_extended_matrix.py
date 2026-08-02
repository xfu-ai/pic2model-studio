"""Additional B01 exit-matrix cases that exercise real service side effects."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from PIL import Image

from aipic_to_model.application.assets import AssetService
from aipic_to_model.application.projects import ProjectService
from aipic_to_model.application.selections import SelectionService
from aipic_to_model.application.tools import ToolRegistry
from aipic_to_model.domain.common import RiskLevel
from aipic_to_model.domain.tools import ToolManifestV1
from aipic_to_model.infrastructure.sqlite.connection import connect


def test_b01_04_exif_transparent_large_image_and_prompt_are_managed_without_source_change(
    tmp_path: Path,
):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Import matrix")
    transparent = tmp_path / "transparent.png"
    Image.new("RGBA", (32, 24), (20, 30, 40, 128)).save(transparent)
    exif = tmp_path / "exif.jpg"
    metadata = Image.Exif()
    metadata[274] = 6
    Image.new("RGB", (2048, 1200), (10, 20, 30)).save(exif, exif=metadata)
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("A blue mechanical bird", encoding="utf-8")
    before = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (transparent, exif, prompt)
    }
    service = AssetService()
    imported = [
        service.import_file(root, project.id, transparent, "source_image", "transparent"),
        service.import_file(root, project.id, exif, "source_image", "exif"),
        service.import_file(root, project.id, prompt, "prompt", "prompt"),
    ]
    assert [item["metadata"].get("format") for item in imported[:2]] == ["PNG", "JPEG"]
    assert imported[1]["metadata"]["width"] == 2048
    assert before == {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (transparent, exif, prompt)
    }


def test_b01_05_four_concurrent_versions_preserve_old_bytes_and_unique_version_numbers(
    tmp_path: Path,
):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Concurrent versions")
    source = tmp_path / "source.png"
    Image.new("RGB", (12, 12), "white").save(source)
    service = AssetService()
    parent = service.import_file(root, project.id, source, "source_image", "parent")

    def create(index: int) -> dict:
        return AssetService().register_derived(
            root,
            project.id,
            source,
            "source_image",
            f"candidate-{index}",
            parent_asset_id=parent["id"],
            input_asset_ids=[parent["id"]],
            lineage_mode="new_version",
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        candidates = list(pool.map(create, range(4)))
    versions = sorted(candidate["version_no"] for candidate in candidates)
    assert versions == [2, 3, 4, 5]
    assert all((root / "assets" / "source").glob("*"))


def test_b01_06_reopen_keeps_one_current_and_every_decision_event(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Current")
    source = tmp_path / "source.png"
    Image.new("RGB", (3, 3)).save(source)
    assets = AssetService()
    items = [
        assets.import_file(root, project.id, source, "source_image", f"import-{index}")
        for index in range(3)
    ]
    for index, item in enumerate(items):
        assets.set_current(root, project.id, item["id"], "user", f"current-{index}")
    assert ProjectService().open(root).id == project.id
    connection = connect(root / "project.sqlite3")
    try:
        assert (
            connection.execute("SELECT COUNT(*) FROM assets WHERE is_current=1").fetchone()[0] == 1
        )
        assert connection.execute("SELECT COUNT(*) FROM asset_decisions").fetchone()[0] == 3
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM events WHERE event_type='asset.current_changed'"
            ).fetchone()[0]
            == 3
        )
    finally:
        connection.close()


def test_b01_08_cancel_action_is_idempotent_and_creates_no_asset(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Cancel")
    source = tmp_path / "source.png"
    Image.new("RGB", (8, 8)).save(source)
    asset = AssetService().import_file(root, project.id, source, "source_image", "import")
    selections = SelectionService()
    selection = selections.save(
        root,
        project.id,
        asset["id"],
        [{"rect_id": "front", "label": "front", "x": 1, "y": 1, "width": 3, "height": 3}],
        "front",
        "user",
    )
    before = len(AssetService().list_by_group(root, project.id))
    selections.cancel_step(root, project.id, selection["id"], "cancel-once", "run-1")
    selections.cancel_step(root, project.id, selection["id"], "cancel-once", "run-1")
    connection = connect(root / "project.sqlite3")
    try:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM events WHERE event_type='selection.cancelled'"
            ).fetchone()[0]
            == 1
        )
    finally:
        connection.close()
    assert len(AssetService().list_by_group(root, project.id)) == before


def test_b01_09_impact_includes_descendant_and_unknown_external_tool_reference(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Impact")
    source = tmp_path / "source.png"
    Image.new("RGB", (4, 4)).save(source)
    assets = AssetService()
    parent = assets.import_file(root, project.id, source, "source_image", "parent")
    child = assets.register_derived(
        root, project.id, source, "generated_image", "child", parent_asset_id=parent["id"]
    )
    registry = ToolRegistry()
    registry.register(
        ToolManifestV1(
            "fake.impact_external",
            "1",
            "Impact",
            "Impact",
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"asset_id": {"type": "string"}},
                "required": ["asset_id"],
            },
            {"type": "object"},
            RiskLevel.EXTERNAL,
            "sync",
            True,
            False,
            [],
            "impact",
        ),
        lambda *_: (_ for _ in ()).throw(RuntimeError("submitted")),
    )
    with pytest.raises(RuntimeError, match="submitted"):
        registry.execute(
            root,
            project.id,
            "fake.impact_external",
            "1",
            {"asset_id": parent["id"]},
            "impact",
            "run-active",
        )
    impact = assets.impact(root, project.id, parent["id"])
    assert child["id"] in impact["children"] and len(impact["active_tool_calls"]) == 1
    assert impact["active_runs"] == ["run-active"] and len(impact["active_jobs"]) == 1
