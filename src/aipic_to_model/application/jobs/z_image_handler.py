"""Durable local text-to-image Job handler for Z-Image-Turbo."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from ...domain.job_models import JobStage, JobStatus, ResumeClass
from ...domain.provider_models import GenerationRequest, ProviderResult
from ...infrastructure.providers.z_image_turbo import Z_IMAGE_MODEL, Z_IMAGE_PROFILE
from ..candidate_service import CandidateService, ProviderGenerationError

_ASPECT_DIMENSIONS = {
    "1:1": (1024, 1024),
    "16:9": (1024, 576),
    "9:16": (576, 1024),
    "4:3": (1024, 768),
    "3:4": (768, 1024),
}


class ImageGenerationJobRouter:
    """Route a frozen local Provider Job without changing it after creation."""

    def __init__(self, local: Any, remote: Any) -> None:
        self._local = local
        self._remote = remote

    def __call__(self, root: Path, project_id: str, job: Any, **kwargs: Any) -> Any:
        handler = self._local if job.provider == Z_IMAGE_PROFILE else self._remote
        return handler(root, project_id, job, **kwargs)


class LocalZImageJobHandler:
    def __init__(
        self,
        jobs: Any,
        assets: Any,
        candidates: CandidateService,
        provider: Any,
    ) -> None:
        self._jobs = jobs
        self._assets = assets
        self._candidates = candidates
        self._provider = provider

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
            raise RuntimeError("Local image Job lease was lost")
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
            job.job_type != "image.generate"
            or job.provider != Z_IMAGE_PROFILE
            or arguments.get("provider_profile") != Z_IMAGE_PROFILE
            or arguments.get("model") != Z_IMAGE_MODEL
        ):
            return self._fail(
                database,
                job.id,
                ProviderResult(ok=False, stage="routing", retryable=False),
                code="LOCAL_IMAGE_ROUTE_INVALID",
            )
        if arguments.get("output_format") not in {None, "png"}:
            return self._fail(
                database,
                job.id,
                ProviderResult(ok=False, stage="validating", retryable=False),
                code="LOCAL_IMAGE_FORMAT_UNSUPPORTED",
            )

        prompt_asset_id = str(arguments["prompt_asset_id"])
        try:
            prompt = self._prompt(root, project_id, prompt_asset_id)
            width, height = _dimensions(arguments.get("size"), arguments.get("aspect_ratio"))
            candidate_count = int(arguments.get("candidate_count", 1))
            seed = _seed(arguments.get("seed"), job.id)
            steps = int(arguments.get("steps", 8))
            timeout_seconds = float(arguments.get("timeout_seconds", 900))
        except KeyError, TypeError, ValueError:
            return self._fail(
                database,
                job.id,
                ProviderResult(ok=False, stage="validating", retryable=False),
                code="LOCAL_IMAGE_INPUT_INVALID",
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

        result = self._provider.generate(
            owner=f"job:{job.id}",
            temporary_root=root / "temp" / "local-inference",
            prompt=prompt,
            width=width,
            height=height,
            candidate_count=candidate_count,
            seed=seed,
            steps=steps,
            timeout_seconds=timeout_seconds,
            cancelled=cancelled,
            heartbeat=heartbeat,
        )
        current = self._jobs.get(database, job_id=job.id)
        if current.status is JobStatus.CANCELLED or result.stage == "cancelled":
            if current.status is JobStatus.CANCELLED:
                return current
            return self._jobs.update(
                database,
                job_id=job.id,
                target=JobStatus.CANCELLED,
                stage=JobStage.CANCEL_REQUESTED,
            )
        if not result.ok:
            return self._fail(database, job.id, result)
        self._jobs.update(
            database,
            job_id=job.id,
            target=JobStatus.RUNNING,
            stage=JobStage.VERIFYING,
            resume_class=ResumeClass.LOCAL_RESTARTABLE,
            progress=85,
        )

        request = GenerationRequest(
            prompt_asset_id=prompt_asset_id,
            provider_profile=Z_IMAGE_PROFILE,
            channel="z_image",
            mode="t2i",
            model=Z_IMAGE_MODEL,
            candidate_count=candidate_count,
            aspect_ratio=arguments.get("aspect_ratio"),
            size=f"{width}x{height}",
            output_format="png",
            seed=seed,
            steps=steps,
        )
        try:
            created = self._candidates.materialize_group(
                root,
                project_id,
                request,
                result,
                request_id=f"job:{job.id}",
                tool_call_id=job.tool_call_id,
            )
        except ProviderGenerationError, TypeError, ValueError:
            return self._fail(
                database,
                job.id,
                ProviderResult(ok=False, stage="verifying", retryable=True),
                code="LOCAL_IMAGE_OUTPUT_INVALID",
            )
        return self._jobs.update(
            database,
            job_id=job.id,
            target=JobStatus.SUCCEEDED,
            stage=JobStage.VERIFYING,
            progress=100,
            result_asset_ids=[str(value) for value in cast(list[object], created["asset_ids"])],
        )

    def _prompt(self, root: Path, project_id: str, asset_id: str) -> str:
        _, content, mime_type, _ = self._assets.read_content(root, project_id, asset_id, None)
        if mime_type not in {"application/json", "text/plain"}:
            raise ValueError("Prompt asset must contain text")
        from ...domain.prompt_parser import parse_bilingual

        return parse_bilingual(content.decode("utf-8")).en_prompt

    def _fail(
        self,
        database: Path,
        job_id: str,
        result: ProviderResult,
        *,
        code: str = "LOCAL_IMAGE_GENERATION_FAILED",
    ) -> Any:
        detail = result.error.model_dump(mode="json") if result.error is not None else {}
        retryable = result.retryable
        return self._jobs.update(
            database,
            job_id=job_id,
            target=JobStatus.INTERRUPTED if retryable else JobStatus.FAILED,
            stage=JobStage.VERIFYING if result.stage == "verifying" else JobStage.POSTPROCESSING,
            resume_class=(
                ResumeClass.LOCAL_RESTARTABLE if retryable else ResumeClass.MANUAL_REVIEW
            ),
            error=detail
            or {
                "code": code,
                "category": "local_processing",
                "user_message": "Local Z-Image-Turbo generation could not be completed.",
                "recoverable": retryable,
                "failed_object": "provider",
                "failed_step": result.stage,
                "fee_incurred": False,
                "safe_to_retry": retryable,
                "recommended_action": "retry" if retryable else "open_details",
            },
        )


def _dimensions(size: object, aspect_ratio: object) -> tuple[int, int]:
    if isinstance(size, str) and "x" in size.lower():
        width_text, height_text = size.lower().split("x", 1)
        width, height = int(width_text), int(height_text)
    elif isinstance(aspect_ratio, str) and aspect_ratio in _ASPECT_DIMENSIONS:
        width, height = _ASPECT_DIMENSIONS[aspect_ratio]
    else:
        width, height = _ASPECT_DIMENSIONS["1:1"]
    if (
        not 512 <= width <= 1536
        or not 512 <= height <= 1536
        or width % 64
        or height % 64
        or width * height > 1_572_864
    ):
        raise ValueError("Unsupported local image dimensions")
    return width, height


def _seed(value: object, job_id: str) -> int:
    if value is None:
        return int.from_bytes(hashlib.sha256(job_id.encode()).digest()[:4], "big") & 0x7FFF_FFFF
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2_147_483_647:
        raise ValueError("Invalid local image seed")
    return value
