from __future__ import annotations

from pathlib import Path

import trimesh
from PIL import Image

from aipic_to_model.application.host_capabilities import HostCapabilityStore
from aipic_to_model.application.model_assets import ModelAssetService
from aipic_to_model.application.model_inspection import inspect_glb
from aipic_to_model.composition import compose_local_app
from aipic_to_model.domain.job_models import JobStatus
from aipic_to_model.infrastructure.keyring_store import OSKeyringStore
from aipic_to_model.infrastructure.sqlite.model_repository import SqliteModelAssetRepository


def test_production_composition_registers_external_handlers_and_missing_config_is_structured(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("TRIPO_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(OSKeyringStore, "get", lambda _self, _profile: None)
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    assert {
        "image.analyze_content",
        "image.generate",
        "element.split",
        "multiview.generate",
        "model3d.generate",
        "model3d.download",
    } <= dependencies.job_worker.job_types

    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Production composition")
    source_path = tmp_path / "source.png"
    Image.new("RGB", (8, 8), "blue").save(source_path)
    source = dependencies.assets.import_file(
        root, project.id, source_path, "source_image", "source"
    )
    proposed = dependencies.registry.execute(
        root,
        project.id,
        "model3d.generate",
        "1.0.0",
        {
            "mode": "image",
            "image_asset_id": source["id"],
            "provider_profile": "tripo3d/default",
            "model": "tripo",
            "parameters": {},
        },
        "generate",
    )
    queued = dependencies.b02_runtime.decide_approval(
        root,
        project.id,
        proposed.ui_action["action_id"],
        approved=True,
    )
    job_id = queued.job["job_id"]
    assert dependencies.job_worker.run_once(root, project.id, owner="test-worker") == job_id
    failed = dependencies.jobs.get(root / "project.sqlite3", job_id=job_id)
    assert failed.status is JobStatus.FAILED
    assert failed.error is not None and failed.error["code"] == "PROVIDER_NOT_CONFIGURED"


def test_background_runner_starts_and_stops_without_open_projects(tmp_path: Path) -> None:
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    assert dependencies.job_runner is not None
    dependencies.job_runner.start()
    assert dependencies.job_runner.running
    dependencies.job_runner.stop()
    assert not dependencies.job_runner.running


def test_production_composition_optimizes_managed_glb_with_a_durable_job(tmp_path: Path) -> None:
    capabilities = HostCapabilityStore()
    dependencies = compose_local_app(capabilities, tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Optimize")
    source = tmp_path / "source.glb"
    source.write_bytes(trimesh.Scene(trimesh.creation.icosphere(subdivisions=3)).export(file_type="glb"))
    model = ModelAssetService(dependencies.assets, SqliteModelAssetRepository()).import_staged(
        root,
        project.id,
        capabilities.issue(source, "model3d.import_local", project.id),
        capabilities,
        "import-model",
    )["asset"]
    queued = dependencies.registry.execute(
        root,
        project.id,
        "model3d.optimize",
        "1.0.0",
        {"asset_id": model["id"], "target_triangles": 100},
        "optimize-model",
    )
    assert queued.status == "queued" and queued.job is not None
    job_id = queued.job["job_id"]
    assert dependencies.job_worker.run_once(root, project.id, owner="test-worker") == job_id
    complete = dependencies.jobs.get(root / "project.sqlite3", job_id=job_id)
    assert complete.status is JobStatus.SUCCEEDED and len(complete.result_asset_ids) == 1
    _, content, _, _ = dependencies.assets.read_content(
        root, project.id, complete.result_asset_ids[0], None
    )
    assert inspect_glb(content, local_relative_path="assets/models/optimized.glb").triangle_count <= 100
