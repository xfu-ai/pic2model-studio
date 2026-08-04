from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from aipic_to_model.application.host_capabilities import HostCapabilityStore
from aipic_to_model.application.jobs.configured_tripo import ConfiguredTripoJobHandler
from aipic_to_model.application.jobs.triposr_handler import TRIPOSR_MODEL, TRIPOSR_PROFILE
from aipic_to_model.application.model_assets import ModelAssetService
from aipic_to_model.composition import compose_local_app
from aipic_to_model.domain.job_models import JobStatus, ResumeClass
from aipic_to_model.infrastructure.sqlite.connection import connect
from aipic_to_model.infrastructure.sqlite.model_repository import SqliteModelAssetRepository
from aipic_to_model.infrastructure.triposr_worker import (
    TripoSRGenerationOutput,
    TripoSROutputInvalid,
    TripoSRWorkerCancelled,
    TripoSRWorkerOutOfMemory,
    TripoSRWorkerRunner,
    TripoSRWorkerTimedOut,
)


def _glb() -> bytes:
    document = json.dumps(
        {
            "asset": {"version": "2.0"},
            "scene": 0,
            "scenes": [{"nodes": [0]}],
            "nodes": [{"mesh": 0}],
            "meshes": [{"name": "triposr", "primitives": []}],
        },
        separators=(",", ":"),
    ).encode("utf-8")
    document += b" " * (-len(document) % 4)
    length = 12 + 8 + len(document)
    return (
        b"glTF"
        + struct.pack("<II", 2, length)
        + struct.pack("<II", len(document), 0x4E4F534A)
        + document
    )


def _output() -> TripoSRGenerationOutput:
    return TripoSRGenerationOutput(
        glb=_glb(),
        chunk_size=8192,
        marching_cubes_resolution=256,
        foreground_ratio=0.85,
    )


def _project_and_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def remote_must_not_run(self: object, *args: object, **kwargs: object) -> object:
        del self, args, kwargs
        raise AssertionError("a frozen local TripoSR Job reached the paid Tripo handler")

    monkeypatch.setattr(ConfiguredTripoJobHandler, "run", remote_must_not_run)
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Local TripoSR lifecycle")
    source_path = tmp_path / "source.png"
    Image.new("RGBA", (64, 64), (40, 100, 180, 255)).save(source_path)
    source = dependencies.assets.import_file(
        root,
        project.id,
        source_path,
        "source_image",
        "triposr-source",
    )
    arguments = {
        "mode": "image",
        "image_asset_id": source["id"],
        "provider_profile": TRIPOSR_PROFILE,
        "model": TRIPOSR_MODEL,
        "parameters": {},
    }
    database = root / "project.sqlite3"
    call_id = "local-triposr-call"
    connection = connect(database)
    try:
        connection.execute(
            """INSERT INTO tool_calls(id,round_index,tool_name,tool_version,arguments_json,
            arguments_hash,idempotency_key,provider_profile,risk_level,status)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                call_id,
                0,
                "model3d.generate",
                "1.0.0",
                json.dumps(arguments),
                call_id,
                call_id,
                TRIPOSR_PROFILE,
                "local_reversible",
                "queued",
            ),
        )
    finally:
        connection.close()
    job_id = "local-triposr-job"
    dependencies.jobs.create(
        database,
        job_id=job_id,
        tool_call_id=call_id,
        job_type="model3d.generate",
        provider=TRIPOSR_PROFILE,
        resume_class=ResumeClass.LOCAL_RESTARTABLE,
    )
    return dependencies, root, project.id, str(source["id"]), job_id


def test_local_triposr_job_registers_inspects_and_exposes_previewable_glb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    def generate(self: object, *args: object, **kwargs: Any) -> TripoSRGenerationOutput:
        del self
        calls.append({"args": args, **kwargs})
        return _output()

    monkeypatch.setattr(TripoSRWorkerRunner, "generate", generate)
    dependencies, root, project_id, source_id, job_id = _project_and_job(tmp_path, monkeypatch)

    assert dependencies.job_worker.run_once(root, project_id, owner="local-worker") == job_id
    job = dependencies.jobs.get(root / "project.sqlite3", job_id=job_id)
    assert job.status is JobStatus.SUCCEEDED and job.provider == TRIPOSR_PROFILE
    assert len(job.result_asset_ids) == 1 and len(calls) == 1
    model = dependencies.assets.get(root, project_id, job.result_asset_ids[0])
    assert model["asset_type"] == "glb" and model["parent_asset_id"] == source_id
    provenance = model["provenance"]
    assert provenance["provider_profile"] == TRIPOSR_PROFILE
    assert provenance["model"] == TRIPOSR_MODEL
    assert provenance["parameters"]["source_job_id"] == job_id
    assert provenance["parameters"]["model_save_format"] == "glb"
    assert provenance["parameters"]["texture_mode"] == "vertex_color"
    assert provenance["parameters"]["pbr"] is False
    inspection = ModelAssetService(
        dependencies.assets,
        SqliteModelAssetRepository(),
    ).inspect(root, project_id, job.result_asset_ids[0])
    assert inspection.parseable and inspection.source_job_id == job_id
    assert inspection.capabilities.standard_views.available
    assert inspection.capabilities.render_preview.tool_name == "model3d.render_preview"


def test_local_triposr_cancellation_keeps_asset_store_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dependencies, root, project_id, _source_id, job_id = _project_and_job(tmp_path, monkeypatch)

    def cancel(self: object, *args: object, **kwargs: Any) -> TripoSRGenerationOutput:
        del self, args
        dependencies.jobs.request_cancel(
            root / "project.sqlite3",
            job_id=job_id,
            mode="local",
        )
        assert kwargs["cancelled"]()
        raise TripoSRWorkerCancelled("cancelled")

    monkeypatch.setattr(TripoSRWorkerRunner, "generate", cancel)
    dependencies.job_worker.run_once(root, project_id, owner="local-worker")
    job = dependencies.jobs.get(root / "project.sqlite3", job_id=job_id)
    assert job.status is JobStatus.CANCELLED
    assert not dependencies.assets.list_by_group(root, project_id, group="models")


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (TripoSRWorkerOutOfMemory("secret path"), "LOCAL_3D_OUT_OF_MEMORY"),
        (TripoSROutputInvalid("secret glb"), "LOCAL_3D_OUTPUT_INVALID"),
    ],
)
def test_local_triposr_retryable_failures_are_fee_free_and_register_no_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    code: str,
) -> None:
    def fail(self: object, *args: object, **kwargs: object) -> TripoSRGenerationOutput:
        del self, args, kwargs
        raise failure

    monkeypatch.setattr(TripoSRWorkerRunner, "generate", fail)
    dependencies, root, project_id, _source_id, job_id = _project_and_job(tmp_path, monkeypatch)
    dependencies.job_worker.run_once(root, project_id, owner="local-worker")
    job = dependencies.jobs.get(root / "project.sqlite3", job_id=job_id)
    assert job.status is JobStatus.INTERRUPTED
    assert job.resume_class is ResumeClass.LOCAL_RESTARTABLE
    assert job.error is not None and job.error["code"] == code
    assert job.error["fee_incurred"] is False and "secret" not in str(job.error).lower()
    assert not dependencies.assets.list_by_group(root, project_id, group="models")


def test_local_triposr_retry_resumes_same_frozen_job_without_duplicate_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def fail_once(self: object, *args: object, **kwargs: object) -> TripoSRGenerationOutput:
        nonlocal calls
        del self, args, kwargs
        calls += 1
        if calls == 1:
            raise TripoSRWorkerTimedOut("first attempt")
        return _output()

    monkeypatch.setattr(TripoSRWorkerRunner, "generate", fail_once)
    dependencies, root, project_id, _source_id, job_id = _project_and_job(tmp_path, monkeypatch)

    dependencies.job_worker.run_once(root, project_id, owner="local-worker")
    interrupted = dependencies.jobs.get(root / "project.sqlite3", job_id=job_id)
    assert interrupted.status is JobStatus.INTERRUPTED
    assert interrupted.resume_class is ResumeClass.LOCAL_RESTARTABLE
    assert interrupted.provider == TRIPOSR_PROFILE

    dependencies.job_worker.run_once(root, project_id, owner="local-worker")
    completed = dependencies.jobs.get(root / "project.sqlite3", job_id=job_id)
    assert completed.status is JobStatus.SUCCEEDED and completed.provider == TRIPOSR_PROFILE
    assert calls == 2
    assert len(dependencies.assets.list_by_group(root, project_id, group="models")) == 1
