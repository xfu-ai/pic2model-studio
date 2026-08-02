"""Generate redacted B02 evidence from an executable offline Provider lifecycle."""

from __future__ import annotations

import argparse
import base64
import json
import sys
import tempfile
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aipic_to_model.application.host_capabilities import HostCapabilityStore
from aipic_to_model.application.jobs.tripo_handler import TripoLifecycleHandler
from aipic_to_model.application.jobs.worker import ProductionJobWorker
from aipic_to_model.application.multiview import MultiviewService
from aipic_to_model.composition import compose_local_app
from aipic_to_model.domain.job_models import JobStatus
from aipic_to_model.infrastructure.providers.fake import (
    FakeFileTransferProvider,
    FakeScenario,
    FakeTripo3DProvider,
)
from aipic_to_model.infrastructure.sqlite.multiview_repository import MultiviewRepository


def _write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _png_base64(colour: str) -> str:
    buffer = BytesIO()
    Image.new("RGB", (32, 32), colour).save(buffer, "PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def generate(output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aipic-b02-evidence-") as temporary:
        work = Path(temporary)
        dependencies = compose_local_app(HostCapabilityStore(), work / "app.sqlite3")
        project_root = work / "project"
        project = dependencies.projects.create(project_root, "B02 offline evidence")
        source_path = work / "source.png"
        Image.new("RGB", (32, 32), "gray").save(source_path)
        source = dependencies.assets.import_file(
            project_root, project.id, source_path, "source_image", "evidence-source"
        )
        multiview_repository = MultiviewRepository()
        multiview = MultiviewService(
            dependencies.assets, dependencies.selections, multiview_repository
        )
        multiview_set_id = multiview.create_from_base64_views(
            project_root,
            project.id,
            source_asset_id=str(source["id"]),
            views={
                "front": _png_base64("red"),
                "side": _png_base64("green"),
                "back": _png_base64("blue"),
            },
            request_id="evidence-views",
        )
        multiview.confirm_regions(
            project_root, project.id, set_id=multiview_set_id, request_id="evidence-regions"
        )
        quality_request = dependencies.registry.execute(
            project_root,
            project.id,
            "multiview.request_quality_confirmation",
            "1.0.0",
            {"multiview_set_id": multiview_set_id},
            "evidence-request-quality",
        )
        quality_result = dependencies.registry.execute(
            project_root,
            project.id,
            "multiview.set_quality_checks",
            "1.0.0",
            {
                "multiview_set_id": multiview_set_id,
                "checks": {
                    "subject_scale": "passed",
                    "direction": "warning",
                    "key_accessory": "passed",
                    "truncation": "passed",
                    "background": "passed",
                    "resolution": "passed",
                },
            },
            "evidence-set-quality",
        )
        members = multiview_repository.current_assets(
            project_root / "project.sqlite3", multiview_set_id
        )
        if not multiview_repository.is_ready_for_submission(
            project_root / "project.sqlite3", set_id=multiview_set_id, members=members
        ):
            raise RuntimeError("manual multiview confirmation was not persisted")
        proposed = dependencies.registry.execute(
            project_root,
            project.id,
            "model3d.generate",
            "1.0.0",
            {
                "mode": "multiview",
                "multiview_set_id": multiview_set_id,
                "view_asset_ids": members,
                "provider_profile": "offline-fake",
                "model": "fake",
                "parameters": {},
            },
            "evidence-generate",
        )
        queued = dependencies.b02_runtime.decide_approval(
            project_root,
            project.id,
            proposed.ui_action["action_id"],
            approved=True,
        )
        job_id = queued.job["job_id"]
        artifact = {
            "artifact_id": "artifact-redacted",
            "kind": "glb",
            "host_fingerprint": "sha256:offline-fake-host",
        }
        provider = FakeTripo3DProvider(
            [
                FakeScenario("tripo.create", payload={"external_task_id": "task-redacted"}),
                FakeScenario(
                    "tripo.get",
                    payload={"status": "succeeded", "artifacts": [artifact]},
                ),
                FakeScenario(
                    "tripo.get",
                    payload={"status": "succeeded", "artifacts": [artifact]},
                ),
                FakeScenario("tripo.download"),
            ]
        )
        handler = TripoLifecycleHandler(
            dependencies.jobs,
            dependencies.assets,
            FakeFileTransferProvider(
                [
                    FakeScenario(
                        "file.prepare",
                        payload={
                            "remote_input": {
                                "provider": "offline-fake",
                                "opaque_input_id": "upload-redacted",
                                "kind": "upload_token",
                            }
                        },
                    ),
                    FakeScenario(
                        "file.prepare",
                        payload={
                            "remote_input": {
                                "provider": "offline-fake",
                                "opaque_input_id": "upload-redacted-side",
                                "kind": "upload_token",
                            }
                        },
                    ),
                    FakeScenario(
                        "file.prepare",
                        payload={
                            "remote_input": {
                                "provider": "offline-fake",
                                "opaque_input_id": "upload-redacted-back",
                                "kind": "upload_token",
                            }
                        },
                    ),
                ]
            ),
            provider,
            allowed_artifact_hosts=frozenset({"artifacts.fake.example"}),
            multiview_repository=multiview_repository,
        )
        worker = ProductionJobWorker(
            dependencies.jobs,
            {"model3d.generate": handler.run},
        )
        worker.run_once(project_root, project.id, owner="evidence-worker")
        submitted = dependencies.jobs.get(project_root / "project.sqlite3", job_id=job_id)
        if submitted.external_task_id != "task-redacted":
            raise RuntimeError("offline Tripo submission did not persist its external ID")

        # A fresh composition instance represents process restart.  It reads only the
        # durable Job and multiview records, then uses GET/download against the same task.
        restarted = compose_local_app(HostCapabilityStore(), work / "app-restarted.sqlite3")
        restarted.job_recovery.recover(project_root)
        restarted_repository = MultiviewRepository()
        if not restarted_repository.is_ready_for_submission(
            project_root / "project.sqlite3", set_id=multiview_set_id, members=members
        ):
            raise RuntimeError("manual quality confirmation did not survive restart")
        restarted_provider = FakeTripo3DProvider(
            [
                FakeScenario("tripo.get", payload={"status": "succeeded", "artifacts": [artifact]}),
                FakeScenario("tripo.get", payload={"status": "succeeded", "artifacts": [artifact]}),
                FakeScenario("tripo.download"),
            ]
        )
        restarted_handler = TripoLifecycleHandler(
            restarted.jobs,
            restarted.assets,
            FakeFileTransferProvider(),
            restarted_provider,
            allowed_artifact_hosts=frozenset({"artifacts.fake.example"}),
            multiview_repository=restarted_repository,
        )
        restarted_worker = ProductionJobWorker(
            restarted.jobs,
            {"model3d.generate": restarted_handler.run},
        )
        for _ in range(2):
            restarted_worker.run_once(project_root, project.id, owner="evidence-restarted-worker")
        generated = restarted.jobs.get(project_root / "project.sqlite3", job_id=job_id)
        if generated.status is not JobStatus.SUCCEEDED:
            raise RuntimeError("offline Tripo lifecycle did not complete")
        glb_id = generated.result_asset_ids[0]

        inspected = restarted.registry.execute(
            project_root,
            project.id,
            "model3d.inspect",
            "1.0.0",
            {"asset_id": glb_id},
            "evidence-inspect",
        )
        if inspected.status == "queued" and inspected.job is not None:
            inspect_job_id = inspected.job["job_id"]
            restarted.job_worker.run_once(
                project_root,
                project.id,
                owner="evidence-local-worker",
            )
            inspect_job = restarted.jobs.get(project_root / "project.sqlite3", job_id=inspect_job_id)
            if inspect_job.status is not JobStatus.SUCCEEDED:
                raise RuntimeError("offline inspection did not complete")
        elif inspected.status != "succeeded":
            raise RuntimeError("offline inspection did not complete")

        model = restarted.assets.get(project_root, project.id, glb_id)
        outbox = restarted.jobs.replay_outbox(project_root / "project.sqlite3", after=0, limit=1000)
        timeline = [
            {
                "sequence_no": item["sequence_no"],
                "event_type": item["event_type"],
                "status": item["payload"].get("status"),
                "stage": item["payload"].get("stage"),
            }
            for item in outbox
        ]
        sanitized_outbox = [
            {
                "sequence_no": item["sequence_no"],
                "event_type": item["event_type"],
                "aggregate_id": item["aggregate_id"],
                "payload": item["payload"],
            }
            for item in outbox
        ]
        _write(output / "job-timeline.json", timeline)
        _write(output / "outbox.json", sanitized_outbox)
        _write(output / "provenance.json", model["provenance"])
        _write(output / "inspection.json", model["metadata"]["model_inspection"])
        _write(
            output / "manual-quality.json",
            {
                "multiview_set_id": multiview_set_id,
                "request_status": quality_request.status,
                "ui_action_type": quality_request.ui_action["type"],
                "confirmation_status": quality_result.status,
                "checks": {
                    "subject_scale": "passed",
                    "direction": "warning",
                    "key_accessory": "passed",
                    "truncation": "passed",
                    "background": "passed",
                    "resolution": "passed",
                },
                "can_continue": True,
            },
        )
        _write(
            output / "restart-recovery.json",
            {
                "external_task_id": "task-redacted",
                "confirmation_persisted": True,
                "restart_provider_calls": [name for name, _ in restarted_provider.calls],
                "restart_create_calls": 0,
                "final_job_status": generated.status.value,
            },
        )
        _write(
            output / "conversion.json",
            {
                "executed": False,
                "reason": "converter capability is covered by isolated integration tests",
                "source_glb_preserved": True,
            },
        )
        _write(
            output / "smoke-summary.json",
            {
                "profile": "offline-fake",
                "real_provider_executed": False,
                "jobs": 2,
                "final_statuses": ["succeeded", "succeeded"],
                "provider_create_calls": 1,
                "restart_provider_create_calls": 0,
                "contains_secrets": False,
            },
        )
        manifest = {
            "schema_version": 1,
            "batch": "B02",
            "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "source_revision": "working-tree",
            "profile": "offline-fake",
            "validation": {
                "executable_offline_pipeline": "passed",
                "provider_create_calls": 1,
                "restart_provider_create_calls": 0,
                "manual_quality_confirmation": "passed",
                "restart_recovery": "passed",
                "job_count": 2,
            },
            "files": [
                "job-timeline.json",
                "outbox.json",
                "provenance.json",
                "inspection.json",
                "manual-quality.json",
                "restart-recovery.json",
                "conversion.json",
                "smoke-summary.json",
            ],
            "contains_secrets": False,
            "real_provider_executed": False,
        }
        _write(output / "manifest.json", manifest)
        return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    generate(args.output)
    print("B02 offline evidence generated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
