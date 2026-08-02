from __future__ import annotations

import json
from pathlib import Path

from aipic_to_model.application.host_capabilities import HostCapabilityStore
from aipic_to_model.application.jobs.model_conversion import (
    BackendAttempt,
    ModelConversionService,
)
from aipic_to_model.application.model_assets import ModelAssetService
from aipic_to_model.composition import compose_local_app
from aipic_to_model.infrastructure.sqlite.model_repository import SqliteModelAssetRepository


def _glb() -> bytes:
    json_chunk = json.dumps({"asset": {"version": "2.0"}}, separators=(",", ":")).encode()
    json_chunk += b" " * ((-len(json_chunk)) % 4)
    body = len(json_chunk).to_bytes(4, "little") + b"JSON" + json_chunk
    return b"glTF" + (2).to_bytes(4, "little") + (12 + len(body)).to_bytes(4, "little") + body


class Backend:
    def __init__(self, name: str, outcome: str) -> None:
        self.name, self.outcome = name, outcome

    def convert(self, _source: Path, destination: Path, *, timeout_seconds: int) -> BackendAttempt:
        if self.outcome == "success":
            destination.write_bytes(b"Kaydara FBX Binary  \x00\x1a\x00fixture")
            return BackendAttempt(self.name, "succeeded", "completed")
        if self.outcome == "invalid":
            destination.write_bytes(b"not fbx")
            return BackendAttempt(self.name, "succeeded", "completed")
        return BackendAttempt(
            self.name, "failed", "timed out" if self.outcome == "timeout" else "unavailable"
        )


def _model(tmp_path: Path):
    capabilities = HostCapabilityStore()
    dependencies = compose_local_app(capabilities, tmp_path / "app.sqlite3")
    project = dependencies.projects.create(tmp_path / "project", "Conversion test")
    source = tmp_path / "model.glb"
    source.write_bytes(_glb())
    asset = ModelAssetService(dependencies.assets, SqliteModelAssetRepository()).import_staged(
        tmp_path / "project",
        project.id,
        capabilities.issue(source, "model3d.import_local", project.id),
        capabilities,
        "import",
    )["asset"]
    return dependencies, project, asset


def test_conversion_falls_back_in_fixed_order_and_never_changes_glb(tmp_path: Path) -> None:
    dependencies, project, model = _model(tmp_path)
    original = dependencies.assets.get(tmp_path / "project", project.id, str(model["id"]))["sha256"]
    service = ModelConversionService(
        dependencies.assets,
        [Backend("blender", "success"), Backend("geometry_fbx", "fail")],
    )
    fbx, attempts = service.convert(
        tmp_path / "project",
        project.id,
        str(model["id"]),
        target_format="fbx",
        request_id="convert",
    )
    assert fbx is not None and fbx["asset_type"] == "fbx"
    assert [attempt.backend for attempt in attempts] == ["blender"]
    assert (
        dependencies.assets.get(tmp_path / "project", project.id, str(model["id"]))["sha256"]
        == original
    )


def test_invalid_output_and_timeout_leave_no_fbx_or_workdir(tmp_path: Path) -> None:
    dependencies, project, model = _model(tmp_path)
    service = ModelConversionService(
        dependencies.assets,
        [Backend("blender", "invalid"), Backend("geometry_fbx", "fail")],
    )
    result, attempts = service.convert(
        tmp_path / "project",
        project.id,
        str(model["id"]),
        target_format="fbx",
        request_id="convert-fail",
    )
    assert result is None and attempts[0].status == "failed"
    models = dependencies.assets.list_by_group(tmp_path / "project", project.id, group="models")
    assert len(models) == 1 and models[0]["id"] == model["id"] and models[0]["asset_type"] == "glb"
    assert not list((tmp_path / "project" / "temp").glob("conversion-*"))
