"""Durable local single-image TripoSR Job execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ...domain.errors import DomainErrorV1
from ...domain.job_models import JobStage, JobStatus, ResumeClass
from ...infrastructure.triposr_worker import (
    TripoSRGenerationSpec,
    TripoSROutputInvalid,
    TripoSRRuntimeConfig,
    TripoSRWorkerCancelled,
    TripoSRWorkerError,
    TripoSRWorkerOutOfMemory,
    TripoSRWorkerTimedOut,
)

TRIPOSR_PROFILE = "model3d/local/triposr"
TRIPOSR_MODEL = "stabilityai/TripoSR"


@dataclass(frozen=True)
class TripoSRLocalSettings:
    chunk_size: int = 8192
    marching_cubes_resolution: int = 512
    foreground_ratio: float = 0.85
    timeout_seconds: float = 900.0


class Model3DGenerationJobRouter:
    """Route only a pre-frozen local model Job to TripoSR."""

    def __init__(self, local: Any, remote: Any) -> None:
        self._local = local
        self._remote = remote

    def __call__(self, root: Path, project_id: str, job: Any, **kwargs: Any) -> Any:
        handler = self._local if job.provider == TRIPOSR_PROFILE else self._remote
        return handler(root, project_id, job, **kwargs)


class LocalTripoSRJobHandler:
    def __init__(
        self,
        jobs: Any,
        assets: Any,
        model_assets: Any,
        runner: Any,
        *,
        settings: TripoSRLocalSettings | None = None,
    ) -> None:
        self._jobs = jobs
        self._assets = assets
        self._model_assets = model_assets
        self._runner = runner
        self._settings = settings or TripoSRLocalSettings()

    def __call__(
        self,
        root: Path,
        project_id: str,
        job: Any,
        *,
        owner: str,
        lease_until: str,
    ) -> Any:
        database = root / "project.sqlite3"
        if not self._jobs.heartbeat(
            database,
            job_id=job.id,
            owner=owner,
            lease_until=lease_until,
        ):
            raise RuntimeError("Local TripoSR Job lease was lost")
        self._jobs.update(
            database,
            job_id=job.id,
            target=JobStatus.RUNNING,
            stage=JobStage.POSTPROCESSING,
            resume_class=ResumeClass.LOCAL_RESTARTABLE,
            progress=1,
        )
        arguments = self._jobs.retry_context(database, job_id=job.id)["arguments"]
        if (
            job.job_type != "model3d.generate"
            or job.provider != TRIPOSR_PROFILE
            or arguments.get("provider_profile") != TRIPOSR_PROFILE
            or arguments.get("model") != TRIPOSR_MODEL
            or arguments.get("mode") != "image"
            or not isinstance(arguments.get("image_asset_id"), str)
            or arguments.get("multiview_set_id") is not None
            or bool(arguments.get("view_asset_ids"))
        ):
            return self._fail(
                database,
                job.id,
                "LOCAL_3D_ROUTE_INVALID",
                "The local TripoSR Job route is invalid.",
                retryable=False,
                stage=JobStage.POSTPROCESSING,
            )

        image_asset_id = str(arguments["image_asset_id"])
        try:
            _, image_bytes, mime_type, _ = self._assets.read_content(
                root,
                project_id,
                image_asset_id,
                None,
            )

            def cancelled() -> bool:
                return self._jobs.get(database, job_id=job.id).status is JobStatus.CANCELLED

            def heartbeat() -> bool:
                return self._jobs.heartbeat(
                    database,
                    job_id=job.id,
                    owner=owner,
                    lease_until=(datetime.now(UTC) + timedelta(seconds=60))
                    .isoformat()
                    .replace("+00:00", "Z"),
                )

            output = self._runner.generate(
                f"job:{job.id}",
                TripoSRRuntimeConfig(),
                TripoSRGenerationSpec(
                    image_bytes=image_bytes,
                    mime_type=mime_type,
                    chunk_size=self._settings.chunk_size,
                    marching_cubes_resolution=self._settings.marching_cubes_resolution,
                    foreground_ratio=self._settings.foreground_ratio,
                    timeout_seconds=self._settings.timeout_seconds,
                ),
                root / "temp" / "local-inference",
                cancelled=cancelled,
                heartbeat=heartbeat,
            )
        except TripoSRWorkerCancelled:
            current = self._jobs.get(database, job_id=job.id)
            if current.status is JobStatus.CANCELLED:
                return current
            return self._jobs.update(
                database,
                job_id=job.id,
                target=JobStatus.CANCELLED,
                stage=JobStage.CANCEL_REQUESTED,
            )
        except TripoSRWorkerOutOfMemory:
            return self._fail(
                database,
                job.id,
                "LOCAL_3D_OUT_OF_MEMORY",
                "TripoSR needs more available GPU memory. Close other GPU tasks or lower local 3D quality.",
                retryable=True,
                stage=JobStage.POSTPROCESSING,
            )
        except TripoSRWorkerTimedOut:
            return self._fail(
                database,
                job.id,
                "LOCAL_3D_TIMEOUT",
                "TripoSR exceeded the local generation timeout.",
                retryable=True,
                stage=JobStage.POSTPROCESSING,
            )
        except TripoSROutputInvalid:
            return self._fail(
                database,
                job.id,
                "LOCAL_3D_OUTPUT_INVALID",
                "TripoSR produced an invalid GLB output.",
                retryable=True,
                stage=JobStage.VERIFYING,
            )
        except TripoSRWorkerError:
            return self._fail(
                database,
                job.id,
                "LOCAL_3D_EXECUTION_FAILED",
                "TripoSR local generation failed.",
                retryable=True,
                stage=JobStage.POSTPROCESSING,
            )
        except DomainErrorV1, OSError, ValueError:
            return self._fail(
                database,
                job.id,
                "LOCAL_3D_INPUT_INVALID",
                "The managed source image is not valid for local TripoSR generation.",
                retryable=False,
                stage=JobStage.POSTPROCESSING,
            )

        current = self._jobs.get(database, job_id=job.id)
        if current.status is JobStatus.CANCELLED:
            return current
        self._jobs.update(
            database,
            job_id=job.id,
            target=JobStatus.RUNNING,
            stage=JobStage.VERIFYING,
            resume_class=ResumeClass.LOCAL_RESTARTABLE,
            progress=85,
        )
        temporary = root / "temp" / f"triposr-{job.id}.glb"
        try:
            temporary.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(output.glb)
            registered = self._assets.register_derived(
                root,
                project_id,
                temporary,
                "glb",
                f"job:{job.id}:triposr",
                parent_asset_id=image_asset_id,
                input_asset_ids=[image_asset_id],
                name="triposr-model.glb",
                provenance={
                    "source_kind": "tool",
                    "tool_call_id": job.tool_call_id,
                    "provider_profile": TRIPOSR_PROFILE,
                    "model": TRIPOSR_MODEL,
                    "parameters": {
                        "source_job_id": job.id,
                        "mode": "image",
                        "chunk_size": output.chunk_size,
                        "marching_cubes_resolution": output.marching_cubes_resolution,
                        "foreground_ratio": output.foreground_ratio,
                        "model_save_format": "glb",
                        "texture_mode": "vertex_color",
                        "pbr": False,
                    },
                },
            )
            inspection = self._model_assets.inspect(root, project_id, str(registered["id"]))
            if not inspection.parseable:
                raise ValueError("Registered TripoSR GLB did not pass inspection")
            self._jobs.update(
                database,
                job_id=job.id,
                target=JobStatus.RUNNING,
                stage=JobStage.VERIFYING,
                resume_class=ResumeClass.LOCAL_RESTARTABLE,
                progress=95,
            )
        except DomainErrorV1, OSError, ValueError:
            return self._fail(
                database,
                job.id,
                "LOCAL_3D_OUTPUT_INVALID",
                "TripoSR produced an invalid GLB output.",
                retryable=True,
                stage=JobStage.VERIFYING,
            )
        finally:
            temporary.unlink(missing_ok=True)
        return self._jobs.update(
            database,
            job_id=job.id,
            target=JobStatus.SUCCEEDED,
            stage=JobStage.VERIFYING,
            progress=100,
            result_asset_ids=[str(registered["id"])],
        )

    def _fail(
        self,
        database: Path,
        job_id: str,
        code: str,
        message: str,
        *,
        retryable: bool,
        stage: JobStage,
    ) -> Any:
        return self._jobs.update(
            database,
            job_id=job_id,
            target=JobStatus.INTERRUPTED if retryable else JobStatus.FAILED,
            stage=stage,
            resume_class=(
                ResumeClass.LOCAL_RESTARTABLE if retryable else ResumeClass.MANUAL_REVIEW
            ),
            error={
                "code": code,
                "category": "local_processing",
                "user_message": message,
                "recoverable": retryable,
                "failed_object": "model",
                "failed_step": stage.value,
                "fee_incurred": False,
                "safe_to_retry": retryable,
                "recommended_action": "retry" if retryable else "fix_input",
            },
        )
