from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from aipic_to_model.application.host_capabilities import HostCapabilityStore
from aipic_to_model.application.jobs.tripo_handler import TripoLifecycleHandler
from aipic_to_model.application.jobs.worker import ProductionJobWorker
from aipic_to_model.composition import compose_local_app
from aipic_to_model.domain.job_models import JobStatus, ResumeClass
from aipic_to_model.infrastructure.providers.fake import (
    FakeFileTransferProvider,
    FakeScenario,
    FakeTripo3DProvider,
)
from aipic_to_model.infrastructure.sqlite.multiview_repository import MultiviewRepository


def test_approved_tripo_lifecycle_uploads_creates_gets_downloads_and_registers_glb_once(
    tmp_path: Path,
) -> None:
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Tripo lifecycle")
    source = tmp_path / "source.png"
    Image.new("RGB", (8, 8), "blue").save(source)
    image = dependencies.assets.import_file(root, project.id, source, "source_image", "image")

    requested = dependencies.registry.execute(
        root,
        project.id,
        "model3d.generate",
        "1.0.0",
        {
            "mode": "image",
            "image_asset_id": image["id"],
            "provider_profile": "fake-tripo",
            "model": "fake",
            "parameters": {},
        },
        "generate",
    )
    assert requested.ui_action is not None
    queued = dependencies.b02_runtime.decide_approval(
        root,
        project.id,
        requested.ui_action["action_id"],
        approved=True,
    )
    assert queued.job is not None
    job_id = queued.job["job_id"]

    transfer = FakeFileTransferProvider(
        [
            FakeScenario(
                "file.prepare",
                payload={
                    "remote_input": {
                        "provider": "fake-tripo",
                        "opaque_input_id": "upload-1",
                        "kind": "upload_token",
                    }
                },
            )
        ]
    )
    artifact = {
        "artifact_id": "artifact-1",
        "kind": "glb",
        "host_fingerprint": "host-fingerprint",
    }
    provider = FakeTripo3DProvider(
        [
            FakeScenario("tripo.create", payload={"external_task_id": "remote-1"}),
            FakeScenario("tripo.get", payload={"status": "succeeded", "artifacts": [artifact]}),
            FakeScenario("tripo.get", payload={"status": "succeeded", "artifacts": [artifact]}),
            FakeScenario("tripo.download"),
        ]
    )
    handler = TripoLifecycleHandler(
        dependencies.jobs,
        dependencies.assets,
        transfer,
        provider,
        allowed_artifact_hosts=frozenset({"artifacts.fake.example"}),
    )
    worker = ProductionJobWorker(dependencies.jobs, {"model3d.generate": handler.run})
    assert worker.run_once(root, project.id, owner="worker") == job_id
    assert (
        dependencies.jobs.get(root / "project.sqlite3", job_id=job_id).external_task_id
        == "remote-1"
    )
    assert worker.run_once(root, project.id, owner="worker") == job_id
    assert worker.run_once(root, project.id, owner="worker") == job_id

    completed = dependencies.jobs.get(root / "project.sqlite3", job_id=job_id)
    assert completed.status is JobStatus.SUCCEEDED
    assert len(completed.result_asset_ids) == 1
    model = dependencies.assets.get(root, project.id, completed.result_asset_ids[0])
    assert model["asset_type"] == "glb"
    serialized = json.dumps(model["provenance"], sort_keys=True)
    assert "artifact-1" in serialized and "host-fingerprint" in serialized
    assert "http" not in serialized and "signature" not in serialized
    assert [name for name, _ in provider.calls].count("tripo.create") == 1


def test_multiview_tripo_submission_requires_confirmed_crops_before_upload(
    tmp_path: Path,
) -> None:
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Tripo quality gate")
    source = tmp_path / "source.png"
    Image.new("RGB", (8, 8), "blue").save(source)
    image = dependencies.assets.import_file(root, project.id, source, "source_image", "image")
    repository = MultiviewRepository()
    set_id = repository.create_set(
        root / "project.sqlite3",
        project_id=project.id,
        source_asset_id=str(image["id"]),
        members={"front": str(image["id"]), "side": str(image["id"]), "back": str(image["id"])},
    )
    requested = dependencies.registry.execute(
        root,
        project.id,
        "model3d.generate",
        "1.0.0",
        {
            "mode": "multiview",
            "multiview_set_id": set_id,
            "view_asset_ids": {"front": image["id"], "side": image["id"], "back": image["id"]},
            "provider_profile": "fake-tripo",
            "model": "fake",
            "parameters": {},
        },
        "generate-multiview",
    )
    assert requested.ui_action is not None
    queued = dependencies.b02_runtime.decide_approval(
        root, project.id, requested.ui_action["action_id"], approved=True
    )
    assert queued.job is not None
    transfer = FakeFileTransferProvider()
    provider = FakeTripo3DProvider()
    handler = TripoLifecycleHandler(
        dependencies.jobs,
        dependencies.assets,
        transfer,
        provider,
        allowed_artifact_hosts=frozenset({"artifacts.fake.example"}),
        multiview_repository=repository,
    )
    worker = ProductionJobWorker(dependencies.jobs, {"model3d.generate": handler.run})
    assert worker.run_once(root, project.id, owner="worker") == queued.job["job_id"]
    job = dependencies.jobs.get(root / "project.sqlite3", job_id=queued.job["job_id"])
    assert job.status is JobStatus.FAILED
    assert job.resume_class is ResumeClass.MANUAL_REVIEW
    assert job.error["code"] == "MULTIVIEW_CROP_CONFIRMATION_REQUIRED"
    assert job.error["safe_to_retry"] is False
    assert not transfer.calls and not provider.calls
    assert worker.run_once(root, project.id, owner="worker") is None


def test_multiview_tripo_submission_accepts_confirmed_final_crops_without_quality_gate(
    tmp_path: Path,
) -> None:
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Tripo confirmed crops")
    source_path = tmp_path / "source.png"
    Image.new("RGB", (12, 8), "blue").save(source_path)
    source = dependencies.assets.import_file(
        root, project.id, source_path, "source_image", "source"
    )
    set_id = dependencies.multiview.create_from_existing_views(
        root,
        project.id,
        source_asset_id=str(source["id"]),
        members={
            "front": str(source["id"]),
            "side": str(source["id"]),
            "back": str(source["id"]),
        },
        request_id="create-set",
    )
    crops = dependencies.multiview.crop_confirmed_views(
        root, project.id, set_id=set_id, request_id="crop-final-views"
    )
    requested = dependencies.registry.execute(
        root,
        project.id,
        "model3d.generate",
        "1.0.0",
        {
            "mode": "multiview",
            # Agent callers used to confuse the sheet asset with the persisted set.
            # The exact confirmed crop triple remains authoritative and repairs this ref.
            "multiview_set_id": str(source["id"]),
            "view_asset_ids": crops,
            "provider_profile": "fake-tripo",
            "model": "fake",
            "parameters": {},
        },
        "generate-confirmed-multiview",
    )
    assert requested.ui_action is not None
    queued = dependencies.b02_runtime.decide_approval(
        root, project.id, requested.ui_action["action_id"], approved=True
    )
    assert queued.job is not None
    transfer = FakeFileTransferProvider(
        [
            FakeScenario(
                "file.prepare",
                payload={
                    "remote_input": {
                        "provider": "fake-tripo",
                        "opaque_input_id": f"upload-{index}",
                        "kind": "upload_token",
                    }
                },
            )
            for index in range(3)
        ]
    )
    provider = FakeTripo3DProvider(
        [FakeScenario("tripo.create", payload={"external_task_id": "remote-multiview"})]
    )
    handler = TripoLifecycleHandler(
        dependencies.jobs,
        dependencies.assets,
        transfer,
        provider,
        allowed_artifact_hosts=frozenset({"artifacts.fake.example"}),
        multiview_repository=MultiviewRepository(),
    )
    worker = ProductionJobWorker(dependencies.jobs, {"model3d.generate": handler.run})
    assert worker.run_once(root, project.id, owner="worker") == queued.job["job_id"]
    job = dependencies.jobs.get(root / "project.sqlite3", job_id=queued.job["job_id"])
    assert job.external_task_id == "remote-multiview"
    assert [name for name, _ in transfer.calls] == ["file.prepare"] * 3
    assert [name for name, _ in provider.calls] == ["tripo.create"]
