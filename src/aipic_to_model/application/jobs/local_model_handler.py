"""Job adapter for B02 local model import, inspection, conversion and packaging."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ...domain.errors import DomainErrorV1
from ...domain.job_models import JobStage, JobStatus, ResumeClass
from ...infrastructure.logging import append_log


class LocalModelJobHandler:
    def __init__(
        self,
        jobs: Any,
        model_assets: Any,
        capabilities: Any,
        conversion: Any,
        optimization: Any,
        packaging: Any,
    ) -> None:
        self._jobs = jobs
        self._model_assets = model_assets
        self._capabilities = capabilities
        self._conversion = conversion
        self._optimization = optimization
        self._packaging = packaging

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
            if job.job_type == "model3d.import_local":
                result = self._model_assets.import_staged(
                    root,
                    project_id,
                    str(arguments["staged_file_id"]),
                    self._capabilities,
                    f"job:{job.id}",
                )
                asset_ids = [str(result["asset"]["id"])]
            elif job.job_type == "model3d.inspect":
                asset_id = str(arguments["asset_id"])
                self._model_assets.inspect(root, project_id, asset_id)
                asset_ids = [asset_id]
            elif job.job_type == "model3d.convert":
                converted, attempts = self._conversion.convert(
                    root,
                    project_id,
                    str(arguments["asset_id"]),
                    target_format=str(arguments["target_format"]),
                    request_id=f"job:{job.id}",
                )
                if converted is None:
                    return self._jobs.update(
                        database,
                        job_id=job.id,
                        target=JobStatus.FAILED,
                        stage=JobStage.POSTPROCESSING,
                        resume_class=ResumeClass.LOCAL_RESTARTABLE,
                        error={
                            "code": "MODEL_CONVERSION_FAILED",
                            "category": "format_unsupported",
                            "user_message": "No approved converter produced a valid FBX copy.",
                            "recoverable": True,
                            "failed_object": "model",
                            "failed_step": "conversion",
                            "safe_to_retry": True,
                            "recommended_action": "open_details",
                            "attempted_backends": [
                                {"backend": item.backend, "status": item.status}
                                for item in attempts
                            ],
                        },
                    )
                asset_ids = [str(converted["id"])]
            elif job.job_type == "model3d.optimize":
                append_log(
                    root,
                    "model-optimization",
                    "Local model optimization started.",
                    project_id=project_id,
                    job_id=job.id,
                    target_triangles=arguments.get("target_triangles"),
                )

                def progress(stage: str, percent: int, details: dict[str, int]) -> None:
                    renewed = (datetime.now(UTC) + timedelta(seconds=60)).isoformat().replace(
                        "+00:00", "Z"
                    )
                    if not self._jobs.heartbeat(
                        database, job_id=job.id, owner=owner, lease_until=renewed
                    ):
                        raise RuntimeError("Local model optimization Job lease was lost.")
                    self._jobs.update(
                        database,
                        job_id=job.id,
                        target=JobStatus.RUNNING,
                        stage=JobStage.POSTPROCESSING,
                        progress=percent,
                    )
                    append_log(
                        root,
                        "model-optimization",
                        "Local model optimization progress.",
                        project_id=project_id,
                        job_id=job.id,
                        stage=stage,
                        progress_percent=percent,
                        **details,
                    )

                optimized = self._optimization.optimize(
                    root,
                    project_id,
                    str(arguments["asset_id"]),
                    target_triangles=arguments.get("target_triangles"),
                    max_texture_bytes=arguments.get("max_texture_bytes"),
                    request_id=f"job:{job.id}",
                    on_progress=progress,
                )
                asset_ids = [str(optimized["id"])]
                append_log(
                    root,
                    "model-optimization",
                    "Local model optimization completed.",
                    project_id=project_id,
                    job_id=job.id,
                )
            elif job.job_type == "model3d.package":
                package = self._packaging.package(
                    root,
                    project_id,
                    list(arguments["asset_ids"]),
                    request_id=f"job:{job.id}",
                )
                asset_ids = [str(package["id"])]
            else:
                return self._jobs.update(
                    database,
                    job_id=job.id,
                    target=JobStatus.FAILED,
                    stage=JobStage.POSTPROCESSING,
                    resume_class=ResumeClass.MANUAL_REVIEW,
                    error=self._error("TOOL_NOT_AVAILABLE", "The local capability is unavailable."),
                )
        except DomainErrorV1 as error:
            if job.job_type == "model3d.optimize":
                append_log(
                    root,
                    "model-optimization",
                    "Local model optimization failed.",
                    level="ERROR",
                    project_id=project_id,
                    job_id=job.id,
                    error_code=str(error.code),
                )
            return self._jobs.update(
                database,
                job_id=job.id,
                target=JobStatus.FAILED,
                stage=JobStage.POSTPROCESSING,
                resume_class=ResumeClass.LOCAL_RESTARTABLE,
                error=self._error(str(error.code), error.user_message),
            )
        self._jobs.heartbeat(database, job_id=job.id, owner=owner, lease_until=lease_until)
        return self._jobs.update(
            database,
            job_id=job.id,
            target=JobStatus.SUCCEEDED,
            stage=JobStage.VERIFYING,
            result_asset_ids=asset_ids,
        )

    @staticmethod
    def _error(code: str, message: str) -> dict[str, object]:
        return {
            "code": code,
            "category": "format_unsupported",
            "user_message": message,
            "recoverable": True,
            "failed_object": "model",
            "failed_step": "postprocessing",
            "safe_to_retry": True,
            "recommended_action": "open_details",
        }
