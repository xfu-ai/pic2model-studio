from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from aipic_to_model.application.host_capabilities import HostCapabilityStore
from aipic_to_model.application.prompt_service import PromptVersionService
from aipic_to_model.composition import compose_local_app
from aipic_to_model.domain.job_models import JobStatus
from aipic_to_model.domain.prompt_parser import BilingualPrompt
from aipic_to_model.infrastructure.sqlite.prompt_repository import PromptVersionRepository


def _glb() -> bytes:
    document = json.dumps(
        {"asset": {"version": "2.0"}, "meshes": [], "materials": []},
        separators=(",", ":"),
    ).encode()
    document += b" " * ((-len(document)) % 4)
    chunk = len(document).to_bytes(4, "little") + b"JSON" + document
    return b"glTF" + (2).to_bytes(4, "little") + (12 + len(chunk)).to_bytes(4, "little") + chunk


def test_external_providers_offline_do_not_disable_prompt_crop_or_glb_inspection(
    tmp_path: Path,
) -> None:
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Offline")

    prompts = PromptVersionService(dependencies.assets, PromptVersionRepository())
    content = prompts.create_bilingual(
        root,
        project.id,
        kind="content",
        bilingual=BilingualPrompt("蓝色主体", "subject", "蓝色主体", "subject"),
        request_id="content",
    )
    style = prompts.create_bilingual(
        root,
        project.id,
        kind="style",
        bilingual=BilingualPrompt("水彩风格", "style", "水彩风格", "style"),
        request_id="style",
    )
    merged = dependencies.registry.execute(
        root,
        project.id,
        "prompt.merge",
        "1.0.0",
        {
            "content_prompt_asset_id": content["asset"]["id"],
            "style_prompt_asset_id": style["asset"]["id"],
        },
        "merge",
    )
    assert merged.status == "succeeded" and len(merged.output_asset_ids) == 1
    loaded_prompt = dependencies.registry.execute(
        root,
        project.id,
        "prompt.get_current",
        "1.0.0",
        {"prompt_asset_id": merged.output_asset_ids[0]},
        "load-prompt",
    )
    prompt_payload = json.loads(loaded_prompt.summary)
    assert prompt_payload["message"] == "Prompt loaded."
    assert prompt_payload["prompt"]["zh_prompt"]
    assert prompt_payload["prompt"]["en_prompt"]

    source = tmp_path / "source.png"
    Image.new("RGB", (8, 8), "red").save(source)
    image = dependencies.assets.import_file(root, project.id, source, "source_image", "image")
    selection = dependencies.selections.save(
        root,
        project.id,
        str(image["id"]),
        [{"x": 1, "y": 1, "width": 4, "height": 4}],
        "subject",
        "user",
        request_id="selection",
    )
    dependencies.selections.confirm(
        root,
        project.id,
        str(selection["id"]),
        int(selection["revision"]),
        "confirm",
    )
    cropped = dependencies.registry.execute(
        root,
        project.id,
        "image.crop",
        "1.0.0",
        {"selection_id": selection["id"]},
        "crop",
    )
    assert cropped.status == "succeeded" and cropped.output_asset_ids

    staged = tmp_path / "model.glb"
    staged.write_bytes(_glb())
    staged_id = dependencies.capabilities.issue(staged, "model3d.import_local", project.id)
    imported = dependencies.registry.execute(
        root,
        project.id,
        "model3d.import_local",
        "1.0.0",
        {"staged_file_id": staged_id},
        "import-model",
    )
    assert imported.job is not None
    dependencies.job_worker.run_once(root, project.id, owner="offline-worker")
    imported_job = dependencies.jobs.get(root / "project.sqlite3", job_id=imported.job["job_id"])
    assert imported_job.status is JobStatus.SUCCEEDED
    model_id = imported_job.result_asset_ids[0]
    inspected = dependencies.registry.execute(
        root,
        project.id,
        "model3d.inspect",
        "1.0.0",
        {"asset_id": model_id},
        "inspect-model",
    )
    assert inspected.status == "succeeded"
    assert inspected.job is None
    assert inspected.output_asset_ids == [model_id]
    inspection_payload = json.loads(inspected.summary)
    assert inspection_payload["message"] == "3D model inspected."
    assert inspection_payload["inspection"]["format"] == "glb"
    assert inspection_payload["inspection"]["parseable"] is True

    capabilities = dependencies.registry.visible(
        {"asset_types": ["glb"], "available_provider_profiles": []}
    )
    tripo = next(item for item in capabilities if item["name"] == "model3d.generate")
    assert not tripo["available"] and tripo["unavailable_reason"] == "provider_unavailable"
