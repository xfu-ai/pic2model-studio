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


def _minimal_glb() -> bytes:
    document = json.dumps({"asset": {"version": "2.0"}}, separators=(",", ":")).encode()
    document += b" " * ((-len(document)) % 4)
    body = len(document).to_bytes(4, "little") + b"JSON" + document
    return b"glTF" + (2).to_bytes(4, "little") + (12 + len(body)).to_bytes(4, "little") + body


def test_preview_registration_accepts_only_valid_png_and_preserves_glb(tmp_path: Path) -> None:
    capabilities = HostCapabilityStore()
    dependencies = compose_local_app(capabilities, tmp_path / "app.sqlite3")
    project = dependencies.projects.create(tmp_path / "project", "Preview test")
    service = ModelAssetService(dependencies.assets, SqliteModelAssetRepository())
    staged = tmp_path / "model.glb"
    staged.write_bytes(_minimal_glb())
    model = service.import_staged(
        tmp_path / "project",
        project.id,
        capabilities.issue(staged, "model3d.import_local", project.id),
        capabilities,
        "import",
    )["asset"]
    original = dependencies.assets.get(tmp_path / "project", project.id, str(model["id"]))
    with pytest.raises(DomainErrorV1):
        service.register_preview(
            tmp_path / "project",
            project.id,
            str(model["id"]),
            b"not-png",
            PreviewRegistration(
                view="front",
                camera=PreviewCamera(position=(0, 0, 1), target=(0, 0, 0), fov_degrees=45),
            ),
            "bad-preview",
        )
    content = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(content, "PNG")
    preview = service.register_preview(
        tmp_path / "project",
        project.id,
        str(model["id"]),
        content.getvalue(),
        PreviewRegistration(
            view="top",
            camera=PreviewCamera(position=(0, 2, 0), target=(0, 0, 0), fov_degrees=45),
        ),
        "good-preview",
    )
    assert preview["asset_type"] == "preview"
    assert (
        dependencies.assets.get(tmp_path / "project", project.id, str(model["id"]))["sha256"]
        == original["sha256"]
    )
