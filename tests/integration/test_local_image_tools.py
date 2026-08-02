from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from aipic_to_model.application.host_capabilities import HostCapabilityStore
from aipic_to_model.composition import compose_local_app
from aipic_to_model.domain.job_models import JobStatus


def _project_with_image(tmp_path: Path) -> tuple[object, Path, object, dict[str, object]]:
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Local image tools")
    source = Image.new("RGBA", (16, 16), (0, 255, 0, 255))
    ImageDraw.Draw(source).rectangle((4, 3, 11, 12), fill=(220, 20, 20, 255))
    path = tmp_path / "source.png"
    source.save(path)
    asset = dependencies.assets.import_file(
        root,
        project.id,
        path,
        "source_image",
        "import-local-image",
    )
    return dependencies, root, project, asset


def test_local_sync_image_tools_create_managed_derived_assets(tmp_path: Path) -> None:
    dependencies, root, project, source = _project_with_image(tmp_path)

    removed = dependencies.registry.execute(
        root,
        project.id,
        "image.remove_background_local",
        "1.0.0",
        {
            "source_asset_id": source["id"],
            "method": "color_key",
            "target_color": [0, 255, 0],
            "tolerance": 8,
        },
        "remove-background-local",
    )
    trimmed = dependencies.registry.execute(
        root,
        project.id,
        "image.trim_transparent",
        "1.0.0",
        {"source_asset_id": removed.output_asset_ids[0], "padding": 1},
        "trim-local",
    )
    normalized = dependencies.registry.execute(
        root,
        project.id,
        "image.normalize",
        "1.0.0",
        {
            "source_asset_id": trimmed.output_asset_ids[0],
            "target_width": 20,
            "output_format": "webp",
            "quality": 80,
        },
        "normalize-local",
    )
    split = dependencies.registry.execute(
        root,
        project.id,
        "image.split_local",
        "1.0.0",
        {
            "source_asset_id": removed.output_asset_ids[0],
            "mode": "alpha_components",
            "min_area": 4,
            "max_outputs": 4,
        },
        "split-local",
    )

    assert removed.status == trimmed.status == normalized.status == split.status == "succeeded"
    assert len(split.output_asset_ids) == 1
    for asset_id in (
        removed.output_asset_ids
        + trimmed.output_asset_ids
        + normalized.output_asset_ids
        + split.output_asset_ids
    ):
        asset = dependencies.assets.get(root, project.id, asset_id)
        assert asset["parent_asset_id"]
        assert asset["provenance"]["parameters"]["operation"]


def test_local_upscale_is_a_durable_offline_job(tmp_path: Path) -> None:
    dependencies, root, project, source = _project_with_image(tmp_path)

    queued = dependencies.registry.execute(
        root,
        project.id,
        "image.upscale_local",
        "1.0.0",
        {"source_asset_id": source["id"], "scale": 2},
        "upscale-local",
    )

    assert queued.status == "queued"
    assert queued.job is not None
    dependencies.job_worker.run_once(root, project.id, owner="local-image-worker")
    job = dependencies.jobs.get(root / "project.sqlite3", job_id=queued.job["job_id"])
    assert job.status is JobStatus.SUCCEEDED
    assert len(job.result_asset_ids) == 1
    result = dependencies.assets.get(root, project.id, job.result_asset_ids[0])
    assert result["metadata"]["width"] == 32
    assert result["metadata"]["height"] == 32
    assert result["provenance"]["parameters"]["model"] == "realesrgan-x4"
