"""Durable Job adapter for bundled offline image inference."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ...domain.errors import DomainErrorV1
from ...domain.job_models import JobStage, JobStatus, ResumeClass


class _LocalImageJobCancelled(Exception):
    pass


class LocalImageJobHandler:
    def __init__(self, jobs: Any, images: Any) -> None:
        self._jobs = jobs
        self._images = images

    def __call__(
        self,
        root: Path,
        project_id: str,
        job: Any,
        *,
        owner: str,
        lease_until: str,
    ) -> object:
        database = root / "project.sqlite3"
        context = self._jobs.retry_context(database, job_id=job.id)
        arguments = context["arguments"]
        self._jobs.heartbeat(database, job_id=job.id, owner=owner, lease_until=lease_until)
        try:
            if job.job_type != "image.upscale_local":
                return self._failed(
                    database,
                    job.id,
                    "TOOL_NOT_AVAILABLE",
                    "The local image Job is not available.",
                    recoverable=False,
                )

            last_progress = -5

            def progress(completed: int, total: int) -> None:
                nonlocal last_progress
                if self._jobs.get(database, job_id=job.id).status is JobStatus.CANCELLED:
                    raise _LocalImageJobCancelled
                renewed = (datetime.now(UTC) + timedelta(seconds=60)).isoformat().replace(
                    "+00:00", "Z"
                )
                if not self._jobs.heartbeat(
                    database,
                    job_id=job.id,
                    owner=owner,
                    lease_until=renewed,
                ):
                    raise RuntimeError("Local image Job lease was lost.")
                percent = round(completed / max(total, 1) * 100)
                if percent >= last_progress + 5 or completed == total:
                    self._jobs.update(
                        database,
                        job_id=job.id,
                        target=JobStatus.RUNNING,
                        stage=JobStage.POSTPROCESSING,
                        progress=percent,
                    )
                    last_progress = percent

            result = self._images.upscale_asset(
                root,
                project_id,
                str(arguments["source_asset_id"]),
                scale=int(arguments["scale"]),
                request_id=f"job:{job.id}",
                on_progress=progress,
            )
        except _LocalImageJobCancelled:
            return self._jobs.get(database, job_id=job.id)
        except (DomainErrorV1, RuntimeError, ValueError) as error:
            message = error.user_message if isinstance(error, DomainErrorV1) else str(error)
            return self._failed(
                database,
                job.id,
                "LOCAL_IMAGE_PROCESSING_FAILED",
                message or "Local image processing failed.",
                recoverable=True,
            )

        self._jobs.heartbeat(database, job_id=job.id, owner=owner, lease_until=lease_until)
        return self._jobs.update(
            database,
            job_id=job.id,
            target=JobStatus.SUCCEEDED,
            stage=JobStage.VERIFYING,
            result_asset_ids=[str(result["id"])],
        )

    def _failed(
        self,
        database: Path,
        job_id: str,
        code: str,
        message: str,
        *,
        recoverable: bool,
    ) -> object:
        return self._jobs.update(
            database,
            job_id=job_id,
            target=JobStatus.FAILED,
            stage=JobStage.POSTPROCESSING,
            resume_class=(
                ResumeClass.LOCAL_RESTARTABLE if recoverable else ResumeClass.MANUAL_REVIEW
            ),
            error={
                "code": code,
                "category": "local_processing",
                "user_message": message,
                "recoverable": recoverable,
                "failed_object": "image",
                "failed_step": "local_inference",
                "safe_to_retry": recoverable,
                "recommended_action": "retry" if recoverable else "open_details",
            },
        )
