from __future__ import annotations

import base64
import json
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from aipic_to_model.application.host_capabilities import HostCapabilityStore
from aipic_to_model.application.image_provider_routing import PrioritizedImageGenerationProvider
from aipic_to_model.composition import compose_local_app
from aipic_to_model.domain.job_models import JobStatus, ResumeClass
from aipic_to_model.domain.prompt_parser import BilingualPrompt, serialize_prompt
from aipic_to_model.domain.provider_models import ProviderResult
from aipic_to_model.infrastructure.providers.z_image_turbo import (
    Z_IMAGE_MODEL,
    Z_IMAGE_PROFILE,
    ZImageTurboProvider,
)
from aipic_to_model.infrastructure.sqlite.connection import connect


def _png(color: str = "teal") -> bytes:
    stream = BytesIO()
    Image.new("RGB", (512, 512), color).save(stream, format="PNG")
    return stream.getvalue()


def _success(*, seed: int = 77, steps: int = 8, encoded: str | None = None) -> ProviderResult:
    return ProviderResult(
        ok=True,
        stage="generated",
        retryable=False,
        payload={
            "images": [
                {
                    "base64": encoded or base64.b64encode(_png()).decode("ascii"),
                    "evaluation_status": "not_evaluated",
                    "short_evaluation": None,
                    "anomalies": [],
                }
            ],
            "routing": {
                "provider_profile": Z_IMAGE_PROFILE,
                "channel": "z_image",
                "model": Z_IMAGE_MODEL,
            },
            "parameters": {
                "seed": seed,
                "steps": steps,
                "width": 512,
                "height": 512,
            },
        },
    )


def _project_and_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def remote_must_not_run(self: object, request: object) -> ProviderResult:
        del self, request
        raise AssertionError("a frozen local image Job reached the remote Provider router")

    monkeypatch.setattr(PrioritizedImageGenerationProvider, "generate", remote_must_not_run)
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Local Z-Image lifecycle")
    prompt_path = tmp_path / "prompt.json"
    prompt_path.write_text(
        serialize_prompt(
            BilingualPrompt(
                "本地生图分析",
                "local generation analysis",
                "陶瓷机器人产品照",
                "a studio product photograph of a ceramic robot",
            )
        ),
        encoding="utf-8",
    )
    prompt = dependencies.assets.import_file(
        root,
        project.id,
        prompt_path,
        "prompt",
        "local-z-image-prompt",
    )
    arguments = {
        "prompt_asset_id": prompt["id"],
        "provider_profile": Z_IMAGE_PROFILE,
        "channel": "z_image",
        "model": Z_IMAGE_MODEL,
        "candidate_count": 1,
        "aspect_ratio": "1:1",
        "output_format": "png",
        "seed": 77,
        "steps": 8,
    }
    database = root / "project.sqlite3"
    call_id = "local-z-image-call"
    connection = connect(database)
    try:
        connection.execute(
            """INSERT INTO tool_calls(id,round_index,tool_name,tool_version,arguments_json,
            arguments_hash,idempotency_key,provider_profile,risk_level,status)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                call_id,
                0,
                "image.generate",
                "1.0.0",
                json.dumps(arguments),
                call_id,
                call_id,
                Z_IMAGE_PROFILE,
                "local_reversible",
                "queued",
            ),
        )
    finally:
        connection.close()
    job_id = "local-z-image-job"
    dependencies.jobs.create(
        database,
        job_id=job_id,
        tool_call_id=call_id,
        job_type="image.generate",
        provider=Z_IMAGE_PROFILE,
        resume_class=ResumeClass.LOCAL_RESTARTABLE,
    )
    return dependencies, root, project.id, job_id


def test_local_z_image_job_materializes_candidate_with_frozen_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    def local_generate(self: object, **kwargs: Any) -> ProviderResult:
        del self
        calls.append(kwargs)
        return _success(seed=kwargs["seed"], steps=kwargs["steps"])

    monkeypatch.setattr(ZImageTurboProvider, "generate", local_generate)
    dependencies, root, project_id, job_id = _project_and_job(tmp_path, monkeypatch)

    assert dependencies.job_worker.run_once(root, project_id, owner="local-worker") == job_id
    job = dependencies.jobs.get(root / "project.sqlite3", job_id=job_id)
    assert job.status is JobStatus.SUCCEEDED
    assert job.provider == Z_IMAGE_PROFILE
    assert len(job.result_asset_ids) == 1
    assert len(calls) == 1 and calls[0]["seed"] == 77 and calls[0]["steps"] == 8
    assert calls[0]["width"] == 1024 and calls[0]["height"] == 1024
    candidate = dependencies.assets.get(root, project_id, job.result_asset_ids[0])
    assert candidate["provenance"]["provider_profile"] == Z_IMAGE_PROFILE
    assert candidate["provenance"]["model"] == Z_IMAGE_MODEL
    parameters = candidate["provenance"]["parameters"]
    assert parameters["seed"] == 77 and parameters["steps"] == 8
    assert parameters["size"] == "1024x1024" and parameters["channel"] == "z_image"


def test_local_z_image_job_can_cancel_without_materializing_an_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dependencies, root, project_id, job_id = _project_and_job(tmp_path, monkeypatch)

    def cancel_during_generation(self: object, **kwargs: Any) -> ProviderResult:
        del self
        dependencies.jobs.request_cancel(
            root / "project.sqlite3",
            job_id=job_id,
            mode="local",
        )
        assert kwargs["cancelled"]()
        return ProviderResult(ok=False, stage="cancelled", retryable=False)

    monkeypatch.setattr(ZImageTurboProvider, "generate", cancel_during_generation)
    dependencies.job_worker.run_once(root, project_id, owner="local-worker")
    job = dependencies.jobs.get(root / "project.sqlite3", job_id=job_id)
    assert job.status is JobStatus.CANCELLED
    assert not dependencies.assets.list_by_group(root, project_id, group="generated_images")


def test_retryable_local_failure_resumes_without_switching_provider_or_duplicating_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def fail_once(self: object, **_kwargs: Any) -> ProviderResult:
        nonlocal calls
        del self
        calls += 1
        if calls == 1:
            return ProviderResult(ok=False, stage="generating", retryable=True)
        return _success()

    monkeypatch.setattr(ZImageTurboProvider, "generate", fail_once)
    dependencies, root, project_id, job_id = _project_and_job(tmp_path, monkeypatch)

    dependencies.job_worker.run_once(root, project_id, owner="local-worker")
    interrupted = dependencies.jobs.get(root / "project.sqlite3", job_id=job_id)
    assert interrupted.status is JobStatus.INTERRUPTED
    assert interrupted.resume_class is ResumeClass.LOCAL_RESTARTABLE
    assert interrupted.provider == Z_IMAGE_PROFILE

    dependencies.job_worker.run_once(root, project_id, owner="local-worker")
    completed = dependencies.jobs.get(root / "project.sqlite3", job_id=job_id)
    assert completed.status is JobStatus.SUCCEEDED
    assert completed.provider == Z_IMAGE_PROFILE and calls == 2
    assert len(dependencies.assets.list_by_group(root, project_id, group="generated_images")) == 1


def test_malformed_local_output_registers_no_asset_and_is_restartable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ZImageTurboProvider,
        "generate",
        lambda _self, **_kwargs: _success(encoded="not-valid-base64"),
    )
    dependencies, root, project_id, job_id = _project_and_job(tmp_path, monkeypatch)

    dependencies.job_worker.run_once(root, project_id, owner="local-worker")
    job = dependencies.jobs.get(root / "project.sqlite3", job_id=job_id)
    assert job.status is JobStatus.INTERRUPTED
    assert job.resume_class is ResumeClass.LOCAL_RESTARTABLE
    assert job.error is not None and job.error["code"] == "LOCAL_IMAGE_OUTPUT_INVALID"
    assert job.error["fee_incurred"] is False
    assert not dependencies.assets.list_by_group(root, project_id, group="generated_images")
