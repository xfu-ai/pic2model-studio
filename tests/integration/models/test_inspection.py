from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from aipic_to_model.application.host_capabilities import HostCapabilityStore
from aipic_to_model.application.model_assets import (
    ModelAssetService,
    PreviewCamera,
    PreviewRegistration,
)
from aipic_to_model.composition import compose_local_app
from aipic_to_model.domain.errors import DomainErrorV1
from aipic_to_model.infrastructure.sqlite.model_repository import SqliteModelAssetRepository


def _glb(document: dict[str, object]) -> bytes:
    encoded = json.dumps(document, separators=(",", ":")).encode()
    encoded += b" " * ((-len(encoded)) % 4)
    body = len(encoded).to_bytes(4, "little") + b"JSON" + encoded
    return b"glTF" + (2).to_bytes(4, "little") + (12 + len(body)).to_bytes(4, "little") + body


def _model_bytes() -> bytes:
    return _glb(
        {
            "asset": {"version": "2.0"},
            "accessors": [
                {"count": 3, "min": [-1, -2, -3], "max": [1, 2, 3]},
                {"count": 3},
                {"count": 2, "max": [1.5]},
            ],
            "meshes": [
                {
                    "name": "body",
                    "primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}],
                }
            ],
            "materials": [
                {
                    "pbrMetallicRoughness": {
                        "baseColorTexture": {"index": 0},
                        "metallicRoughnessTexture": {"index": 0},
                    },
                    "normalTexture": {"index": 0},
                }
            ],
            "textures": [{"source": 0}],
            "skins": [{}],
            "animations": [{"name": "idle", "samplers": [{"input": 2}]}],
        }
    )


def _service(tmp_path: Path):
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    project = dependencies.projects.create(tmp_path / "project", "Model test")
    return (
        dependencies,
        project,
        ModelAssetService(dependencies.assets, SqliteModelAssetRepository()),
    )


def test_staged_glb_import_inspects_capabilities_and_persists_metadata(tmp_path: Path) -> None:
    dependencies, project, service = _service(tmp_path)
    staged = tmp_path / "input.glb"
    staged.write_bytes(_model_bytes())
    staged_id = dependencies.capabilities.issue(staged, "model3d.import_local", project.id)
    result = service.import_staged(
        tmp_path / "project", project.id, staged_id, dependencies.capabilities, "import"
    )
    asset, inspection = result["asset"], result["inspection"]
    assert isinstance(asset, dict) and asset["asset_type"] == "glb"
    assert isinstance(inspection, dict) and inspection["parseable"]
    assert inspection["vertex_count"] == 3 and inspection["triangle_count"] == 1
    assert inspection["bounds_xyz"] == [2.0, 4.0, 6.0]
    assert inspection["animations"][0]["duration_seconds"] == 1.5
    assert inspection["capabilities"]["animation"]["available"]
    assert not inspection["capabilities"]["render_preview"]["available"]
    stored = dependencies.assets.get(tmp_path / "project", project.id, str(asset["id"]))
    assert stored["metadata"]["model_inspection"] == inspection
    assert "staged_file" not in json.dumps(stored["provenance"])


def test_import_requires_staged_capability_and_rejects_bad_glb_without_asset(
    tmp_path: Path,
) -> None:
    dependencies, project, service = _service(tmp_path)
    bad = tmp_path / "bad.glb"
    bad.write_bytes(b"not a glb")
    staged_id = dependencies.capabilities.issue(bad, "model3d.import_local", project.id)
    with pytest.raises(DomainErrorV1, match="真实性"):
        service.import_staged(
            tmp_path / "project", project.id, staged_id, dependencies.capabilities, "bad-import"
        )
    assert dependencies.assets.list_by_group(tmp_path / "project", project.id, group="models") == []


def test_corrupt_managed_glb_has_complete_failure_capabilities(tmp_path: Path) -> None:
    dependencies, project, service = _service(tmp_path)
    bad = tmp_path / "bad.glb"
    bad.write_bytes(b"not a glb")
    asset = dependencies.assets.register_derived(
        tmp_path / "project", project.id, bad, "glb", "corrupt"
    )
    inspection = service.inspect(tmp_path / "project", project.id, str(asset["id"]))
    assert not inspection.parseable
    assert not inspection.capabilities.animation.available
    assert inspection.capabilities.open_containing_folder.available


def test_preview_registration_creates_new_png_asset_with_model_provenance(tmp_path: Path) -> None:
    dependencies, project, service = _service(tmp_path)
    staged = tmp_path / "input.glb"
    staged.write_bytes(_model_bytes())
    staged_id = dependencies.capabilities.issue(staged, "model3d.import_local", project.id)
    model = service.import_staged(
        tmp_path / "project", project.id, staged_id, dependencies.capabilities, "import"
    )["asset"]
    stream = io.BytesIO()
    Image.new("RGBA", (8, 8), "red").save(stream, "PNG")
    preview = service.register_preview(
        tmp_path / "project",
        project.id,
        str(model["id"]),
        stream.getvalue(),
        PreviewRegistration(
            view="front",
            camera=PreviewCamera(position=(0, 0, 2), target=(0, 0, 0), fov_degrees=35),
        ),
        "preview",
    )
    assert preview["asset_type"] == "preview"
    assert preview["parent_asset_id"] == model["id"]
    assert preview["provenance"]["parameters"]["view"] == "front"
