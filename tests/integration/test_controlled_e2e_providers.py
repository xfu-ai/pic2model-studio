from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image

from aipic_to_model.application.host_capabilities import HostCapabilityStore
from aipic_to_model.composition import compose_local_app
from aipic_to_model.domain.job_models import JobStatus
from aipic_to_model.domain.prompt_parser import BilingualPrompt, serialize_prompt


def test_controlled_e2e_mode_completes_tripo_without_a_real_provider(
    tmp_path: Path, monkeypatch
) -> None:
    """The desktop E2E environment must be fully useful without credentials."""

    monkeypatch.setenv("AIPIC_CONTROLLED_E2E", "1")
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    # Exercise the controlled remote Tripo fixture even when local TripoSR is
    # installed on the validation host.
    dependencies.settings.update_app(
        dependencies.app_db, {"model3d_generation_backend": "remote"}
    )
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Controlled provider project")
    source = tmp_path / "source.png"
    Image.new("RGBA", (8, 8), (30, 90, 180, 255)).save(source)
    image = dependencies.assets.import_file(root, project.id, source, "source_image", "import")

    requested = dependencies.registry.execute(
        root,
        project.id,
        "model3d.generate",
        "1.0.0",
        {
            "mode": "image",
            "image_asset_id": image["id"],
            "provider_profile": "tripo3d/default",
            "model": "v3.1-20260211",
            "parameters": {},
        },
        "controlled-tripo-request",
    )
    assert requested.status == "awaiting_ui_action"
    assert requested.ui_action is not None

    approved = dependencies.b02_runtime.decide_approval(
        root, project.id, requested.ui_action["action_id"], approved=True
    )
    assert approved.job is not None
    job_id = approved.job["job_id"]
    for _ in range(6):
        dependencies.job_worker.run_once(root, project.id, owner="controlled-e2e")
        job = dependencies.jobs.get(root / "project.sqlite3", job_id=job_id)
        if job.status is JobStatus.SUCCEEDED:
            break

    job = dependencies.jobs.get(root / "project.sqlite3", job_id=job_id)
    assert job.status is JobStatus.SUCCEEDED
    assert len(job.result_asset_ids) == 1
    model = dependencies.assets.get(root, project.id, job.result_asset_ids[0])
    assert model["asset_type"] == "glb"
    _, content, _, _ = dependencies.assets.read_content(root, project.id, model["id"], None)
    assert hashlib.sha256(content).hexdigest() == (
        "ed52f7192b8311d700ac0ce80644e3852cd01537e4d62241b9acba023da3d54e"
    )
    json_length = int.from_bytes(content[12:16], "little")
    document = json.loads(content[20 : 20 + json_length])
    assert document["meshes"][0]["primitives"]


def test_controlled_e2e_mode_generates_valid_image_candidates(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AIPIC_CONTROLLED_E2E", "1")
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    dependencies.settings.update_app(
        dependencies.app_db, {"image_generation_backend": "remote"}
    )
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Controlled image project")
    prompt_file = tmp_path / "prompt.json"
    prompt_file.write_text(serialize_prompt(BilingualPrompt(
        "受控候选图分析", "controlled candidate analysis",
        "受控候选图", "controlled candidate",
    )), encoding="utf-8")
    prompt = dependencies.assets.import_file(root, project.id, prompt_file, "prompt", "controlled-prompt")

    requested = dependencies.registry.execute(root, project.id, "image.generate", "1.0.0", {
        "prompt_asset_id": prompt["id"], "provider_profile": "image-generation/auto", "channel": "auto",
        "model": "auto", "candidate_count": 2, "aspect_ratio": "1:1", "output_format": "png",
    }, "controlled-image-request")
    approved = dependencies.b02_runtime.decide_approval(root, project.id, requested.ui_action["action_id"], approved=True)
    job_id = approved.job["job_id"]
    dependencies.job_worker.run_once(root, project.id, owner="controlled-e2e")
    job = dependencies.jobs.get(root / "project.sqlite3", job_id=job_id)
    assert job.status is JobStatus.SUCCEEDED
    assert job.provider == "tripo3d/default"
    assert len(job.result_asset_ids) == 2
    candidate = dependencies.assets.get(root, project.id, job.result_asset_ids[0])
    assert candidate["provenance"]["provider_profile"] == "tripo3d/default"
    assert candidate["provenance"]["model"] == "seedream_v5"


def test_controlled_e2e_image_to_image_uses_tripo_priority_and_records_route(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AIPIC_CONTROLLED_E2E", "1")
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Controlled image edit project")
    source_file = tmp_path / "source.png"
    Image.new("RGBA", (8, 8), (30, 90, 180, 255)).save(source_file)
    source = dependencies.assets.import_file(
        root, project.id, source_file, "source_image", "controlled-source"
    )
    prompt_file = tmp_path / "prompt.json"
    prompt_file.write_text(
        serialize_prompt(
            BilingualPrompt(
                "受控图生图分析",
                "controlled image-to-image analysis",
                "将主体改为蓝色",
                "change the subject to blue",
            )
        ),
        encoding="utf-8",
    )
    prompt = dependencies.assets.import_file(
        root, project.id, prompt_file, "prompt", "controlled-edit-prompt"
    )

    requested = dependencies.registry.execute(
        root,
        project.id,
        "image.transform",
        "1.0.0",
        {
            "prompt_asset_id": prompt["id"],
            "source_asset_id": source["id"],
            "provider_profile": "image-generation/auto",
            "channel": "auto",
            "model": "auto",
            "candidate_count": 2,
        },
        "controlled-image-edit-request",
    )
    approved = dependencies.b02_runtime.decide_approval(
        root, project.id, requested.ui_action["action_id"], approved=True
    )
    job_id = approved.job["job_id"]
    dependencies.job_worker.run_once(root, project.id, owner="controlled-e2e")

    job = dependencies.jobs.get(root / "project.sqlite3", job_id=job_id)
    assert job.status is JobStatus.SUCCEEDED
    assert len(job.result_asset_ids) == 2
    candidate = dependencies.assets.get(root, project.id, job.result_asset_ids[0])
    assert candidate["provenance"]["provider_profile"] == "tripo3d/default"
    assert candidate["provenance"]["model"] == "gemini_3.1_flash_image_preview"
    assert candidate["provenance"]["parameters"]["channel"] == "tripo"
