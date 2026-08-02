from __future__ import annotations

from pathlib import Path

import pytest

from aipic_to_model.application.host_capabilities import HostCapabilityStore
from aipic_to_model.composition import compose_local_app
from aipic_to_model.domain.job_models import JobStatus
from aipic_to_model.domain.prompt_parser import BilingualPrompt, parse_bilingual


def test_controlled_prompt_rewrite_job_creates_bilingual_managed_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIPIC_CONTROLLED_E2E", "1")
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Prompt rewrite")
    prompt = dependencies.prompt_versions.create_bilingual(
        root,
        project.id,
        kind="content",
        bilingual=BilingualPrompt(
            "灰色立方体",
            "gray cube",
            "灰色立方体",
            "gray cube",
        ),
        request_id="prompt-source",
    )["asset"]

    queued = dependencies.registry.execute(
        root,
        project.id,
        "prompt.rewrite",
        "1.0.0",
        {
            "prompt_asset_id": prompt["id"],
            "instruction": "Place it on a white background.",
            "provider_profile": "gemini/google/default",
            "model": "gemini-flash-lite-latest",
        },
        "prompt-rewrite",
    )
    assert queued.status == "queued"
    assert queued.job is not None
    job_id = queued.job["job_id"]
    assert dependencies.job_worker.run_once(root, project.id, owner="test-worker") == job_id

    complete = dependencies.jobs.get(root / "project.sqlite3", job_id=job_id)
    assert complete.status is JobStatus.SUCCEEDED
    assert len(complete.result_asset_ids) == 1
    view = dependencies.b02_runtime.job_view(root, job_id)
    assert view["input_asset_ids"] == [prompt["id"]]
    assert view["created_at"]
    assert view["updated_at"]
    assert view["completed_at"] == view["updated_at"]
    _, content, mime_type, _ = dependencies.assets.read_content(
        root,
        project.id,
        complete.result_asset_ids[0],
        None,
    )
    assert mime_type == "application/json"
    rewritten = parse_bilingual(content.decode("utf-8"))
    assert rewritten.zh_prompt == "受控重写提示词"
    assert rewritten.en_prompt == "controlled rewritten prompt"
