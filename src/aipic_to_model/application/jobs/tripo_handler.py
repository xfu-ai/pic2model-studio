"""B02 Tripo submission recovery decisions, independent of HTTP adapters."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ...domain.ids import idempotency_key
from ...domain.job_models import JobStage, JobStatus, ResumeClass
from ...domain.production_models import TripoGenerationRequest
from ...domain.provider_models import ProviderResult, RemoteTaskState
from ...infrastructure.providers.tripo_payloads import build_tripo_payload
from ..assets import AssetService
from .secure_download import UntrustedDownload, download_glb_to_part


@dataclass(frozen=True)
class TripoSubmissionDecision:
    external_task_id: str | None
    resume_class: ResumeClass
    requires_manual_review: bool


def generation_idempotency_key(
    request: TripoGenerationRequest, *, asset_hashes: dict[str, str]
) -> str:
    """Derive the paid-operation key from managed inputs, not paths or URLs."""
    required = (
        [request.image_asset_id]
        if request.mode == "image"
        else [request.view_asset_ids[view] for view in ("front", "side", "back")]
    )
    if any(asset_id is None or not asset_hashes.get(asset_id) for asset_id in required):
        raise ValueError("every Tripo input requires a managed content hash")
    return idempotency_key(
        "model3d.generate",
        "1.0.0",
        request.model_dump(mode="json"),
        [asset_hashes[asset_id] for asset_id in required if asset_id is not None],
        request.provider_profile,
    )


def handle_submission_result(result: ProviderResult) -> TripoSubmissionDecision:
    """No external ID means no safe automatic create retry, even after timeout."""
    if result.ok:
        external_task_id = result.payload.get("external_task_id")
        if isinstance(external_task_id, str) and external_task_id:
            return TripoSubmissionDecision(external_task_id, ResumeClass.REMOTE_POLL, False)
    if result.error is not None and result.error.code != "JOB_UNKNOWN_SUBMISSION":
        return TripoSubmissionDecision(None, ResumeClass.MANUAL_REVIEW, False)
    return TripoSubmissionDecision(None, ResumeClass.UNKNOWN_SUBMISSION, True)


class TripoJobStore(Protocol):
    def bind_external_task(
        self,
        database: Path,
        *,
        job_id: str,
        provider: str,
        external_task_id: str,
        submission_summary: dict[str, object],
    ) -> object: ...

    def request_cancel(self, database: Path, *, job_id: str, mode: str) -> object: ...

    def update(
        self,
        database: Path,
        *,
        job_id: str,
        target: JobStatus,
        stage: JobStage,
        resume_class: ResumeClass | None = None,
        progress: int | None = None,
        error: dict[str, object] | None = None,
        result_asset_ids: list[str] | None = None,
        resume: dict[str, object] | None = None,
    ) -> object: ...


def persist_submission_result(
    store: TripoJobStore,
    database: Path,
    *,
    job_id: str,
    provider: str,
    result: ProviderResult,
    submission_summary: dict[str, object],
) -> TripoSubmissionDecision:
    """Persist an acknowledged external ID before any later work can occur."""
    decision = handle_submission_result(result)
    if decision.external_task_id:
        store.bind_external_task(
            database,
            job_id=job_id,
            provider=provider,
            external_task_id=decision.external_task_id,
            submission_summary=submission_summary,
        )
        return decision
    if result.error is not None and result.error.code != "JOB_UNKNOWN_SUBMISSION":
        store.update(
            database,
            job_id=job_id,
            target=JobStatus.INTERRUPTED if result.retryable else JobStatus.FAILED,
            stage=JobStage.CREATING,
            resume_class=ResumeClass.MANUAL_REVIEW,
            error=result.error.model_dump(mode="json"),
        )
        return decision
    # A lost create response is ambiguous even when the transport calls it retryable.
    store.update(
        database,
        job_id=job_id,
        target=JobStatus.INTERRUPTED,
        stage=JobStage.UNKNOWN_SUBMISSION,
        resume_class=ResumeClass.UNKNOWN_SUBMISSION,
        error={"code": "JOB_UNKNOWN_SUBMISSION", "safe_to_retry": False},
    )
    return decision


def apply_remote_state(
    store: TripoJobStore,
    database: Path,
    *,
    job_id: str,
    state: RemoteTaskState,
) -> ResumeClass:
    """Map a GET-only remote result; this function never creates a task."""
    if state.status == "queued":
        store.update(
            database,
            job_id=job_id,
            target=JobStatus.WAITING,
            stage=JobStage.REMOTE_QUEUED,
            resume_class=ResumeClass.REMOTE_POLL,
            progress=state.progress,
        )
        return ResumeClass.REMOTE_POLL
    if state.status == "running":
        store.update(
            database,
            job_id=job_id,
            target=JobStatus.WAITING,
            stage=JobStage.REMOTE_RUNNING,
            resume_class=ResumeClass.REMOTE_POLL,
            progress=state.progress,
        )
        return ResumeClass.REMOTE_POLL
    if state.status == "succeeded":
        # Queue only the local download phase.  The external creation boundary
        # is now permanently closed for this job.
        store.update(
            database,
            job_id=job_id,
            target=JobStatus.INTERRUPTED,
            stage=JobStage.DOWNLOADING,
            resume_class=ResumeClass.DOWNLOAD_RETRY,
            progress=state.progress,
            resume={
                "artifacts": [artifact.model_dump(mode="json") for artifact in state.artifacts]
            },
        )
        return ResumeClass.DOWNLOAD_RETRY
    if state.status == "cancelled":
        store.update(
            database,
            job_id=job_id,
            target=JobStatus.CANCELLED,
            stage=JobStage.CANCEL_REQUESTED,
        )
        return ResumeClass.STOP_WAITING
    store.update(
        database,
        job_id=job_id,
        target=JobStatus.FAILED,
        stage=JobStage.REMOTE_RUNNING,
        error={"code": "PROVIDER_UNAVAILABLE", "safe_to_retry": False},
    )
    return ResumeClass.MANUAL_REVIEW


def apply_remote_cancel_result(
    store: TripoJobStore, database: Path, *, job_id: str, result: ProviderResult
) -> ResumeClass:
    """Represent unsupported cancellation as stop-waiting, never as success.

    Remote completion is still allowed to win later: the job stays in the
    cancellable waiting state until a subsequent GET returns its terminal
    status.
    """
    store.request_cancel(database, job_id=job_id, mode="remote")
    if result.ok:
        return ResumeClass.REMOTE_POLL
    if result.error and result.error.recommended_action.value == "stop_waiting":
        store.request_cancel(database, job_id=job_id, mode="stop_waiting")
        return ResumeClass.STOP_WAITING
    return ResumeClass.REMOTE_POLL


class TripoLifecycleHandler:
    """Execute the approved upload/create/GET/cancel/download state machine."""

    def __init__(
        self,
        jobs: Any,
        assets: AssetService,
        transfer: Any,
        provider: Any,
        *,
        allowed_artifact_hosts: frozenset[str],
        multiview_repository: Any | None = None,
        maximum_glb_bytes: int = 500 * 1024 * 1024,
    ) -> None:
        self._jobs = jobs
        self._assets = assets
        self._transfer = transfer
        self._provider = provider
        self._allowed_artifact_hosts = allowed_artifact_hosts
        self._multiview_repository = multiview_repository
        self._maximum_glb_bytes = maximum_glb_bytes

    def run(
        self,
        root: Path,
        project_id: str,
        job: Any,
        *,
        owner: str,
        lease_until: str,
    ) -> Any:
        """Advance one claimed Job through one or more safe boundaries."""
        database = root / "project.sqlite3"
        if job.external_task_id is None:
            return self._submit(
                root, project_id, database, job, owner=owner, lease_until=lease_until
            )
        if job.stage is JobStage.DOWNLOADING or job.resume_class is ResumeClass.DOWNLOAD_RETRY:
            return self._download(
                root, project_id, database, job, owner=owner, lease_until=lease_until
            )
        return self._poll_or_cancel(database, job, owner=owner, lease_until=lease_until)

    def _submit(
        self,
        root: Path,
        project_id: str,
        database: Path,
        job: Any,
        *,
        owner: str,
        lease_until: str,
    ) -> Any:
        context = self._jobs.retry_context(database, job_id=job.id)
        request = TripoGenerationRequest.model_validate(context["arguments"])
        if request.mode == "multiview":
            members = {name: request.view_asset_ids[name] for name in ("front", "side", "back")}
            if (
                self._multiview_repository is None
                or not self._multiview_repository.is_ready_for_submission(
                    database, set_id=request.multiview_set_id or "", members=members
                )
            ):
                return self._jobs.update(
                    database,
                    job_id=job.id,
                    target=JobStatus.FAILED,
                    stage=JobStage.POSTPROCESSING,
                    resume_class=ResumeClass.MANUAL_REVIEW,
                    error={
                        "code": "MULTIVIEW_CROP_CONFIRMATION_REQUIRED",
                        "category": "input_invalid",
                        "user_message": "请先在三视图制作页确认裁切框，再提交 3D 生成。",
                        "recoverable": True,
                        "failed_object": "multiview_set",
                        "failed_step": "crop_confirmation",
                        "safe_to_retry": False,
                        "recommended_action": "fix_input",
                    },
                )
        asset_ids = (
            [request.image_asset_id]
            if request.mode == "image"
            else [request.view_asset_ids[name] for name in ("front", "side", "back")]
        )
        hashes: dict[str, str] = {}
        remote_inputs: dict[str, str] = {}
        for asset_id in asset_ids:
            if asset_id is None:
                raise ValueError("missing managed Tripo input")
            asset = self._assets.get(root, project_id, asset_id)
            _, content, mime, _ = self._assets.read_content(root, project_id, asset_id, None)
            digest = str(asset["sha256"])
            hashes[asset_id] = digest
            self._heartbeat(database, job.id, owner, lease_until)
            uploaded = self._transfer.upload(
                asset_id=asset_id,
                content_sha256=digest,
                size_bytes=len(content),
                mime_type=mime,
            )
            self._heartbeat(database, job.id, owner, lease_until)
            remote = uploaded.payload.get("remote_input") if uploaded.ok else None
            opaque_id = remote.get("opaque_input_id") if isinstance(remote, dict) else None
            if not isinstance(opaque_id, str) or not opaque_id:
                return self._provider_failure(database, job.id, uploaded, JobStage.UPLOADING)
            remote_inputs[asset_id] = opaque_id
        key = generation_idempotency_key(request, asset_hashes=hashes)
        payload = build_tripo_payload(request, remote_inputs)
        self._heartbeat(database, job.id, owner, lease_until)
        result = self._provider.create(payload, idempotency_key=key)
        self._heartbeat(database, job.id, owner, lease_until)
        return persist_submission_result(
            self._jobs,
            database,
            job_id=job.id,
            provider=job.provider or "tripo3d",
            result=result,
            submission_summary={
                "idempotency_hash": hashlib.sha256(key.encode()).hexdigest(),
                "input_hashes": sorted(hashes.values()),
            },
        )

    def _poll_or_cancel(
        self,
        database: Path,
        job: Any,
        *,
        owner: str,
        lease_until: str,
    ) -> Any:
        self._heartbeat(database, job.id, owner, lease_until)
        state = self._provider.get(job.external_task_id)
        self._heartbeat(database, job.id, owner, lease_until)
        if isinstance(state, ProviderResult):
            return self._provider_failure(database, job.id, state, JobStage.REMOTE_RUNNING)
        if job.stage is JobStage.CANCEL_REQUESTED and state.status not in {
            "succeeded",
            "failed",
            "cancelled",
        }:
            self._heartbeat(database, job.id, owner, lease_until)
            result = self._provider.cancel(job.external_task_id)
            self._heartbeat(database, job.id, owner, lease_until)
            return apply_remote_cancel_result(self._jobs, database, job_id=job.id, result=result)
        return apply_remote_state(self._jobs, database, job_id=job.id, state=state)

    def _download(
        self,
        root: Path,
        project_id: str,
        database: Path,
        job: Any,
        *,
        owner: str,
        lease_until: str,
    ) -> Any:
        self._heartbeat(database, job.id, owner, lease_until)
        state = self._provider.get(job.external_task_id)
        self._heartbeat(database, job.id, owner, lease_until)
        if isinstance(state, ProviderResult):
            return self._provider_failure(database, job.id, state, JobStage.DOWNLOADING)
        artifact = next((item for item in state.artifacts if item.kind == "glb"), None)
        if artifact is None:
            return self._jobs.update(
                database,
                job_id=job.id,
                target=JobStatus.FAILED,
                stage=JobStage.DOWNLOADING,
                error={
                    "code": "REMOTE_ARTIFACT_NOT_FOUND",
                    "safe_to_retry": True,
                    "failed_step": "downloading",
                },
            )
        part = root / "temp" / f"tripo-{job.id}.glb.part"
        offset = part.stat().st_size if part.exists() else 0
        try:
            response = self._provider.open_artifact(
                external_task_id=job.external_task_id,
                artifact=artifact,
                offset=offset,
            )
            receipt = download_glb_to_part(
                response,
                part_path=part,
                part_root=root / "temp",
                allowed_hosts=self._allowed_artifact_hosts,
                maximum_bytes=self._maximum_glb_bytes,
                expected_size=artifact.expected_size,
            )
            self._heartbeat(database, job.id, owner, lease_until)
            registered = self._assets.register_derived(
                root,
                project_id,
                part,
                "glb",
                f"tripo-download:{job.id}",
                name="model.glb",
                provenance={
                    "source_kind": "tool",
                    "provider_profile": job.provider or "tripo3d",
                    "tool_call_id": job.tool_call_id,
                    "parameters": {
                        "source_job_id": job.id,
                        "artifact_id": artifact.artifact_id,
                        "host_fingerprint": artifact.host_fingerprint,
                        "content_type": receipt.content_type,
                        "sha256": receipt.sha256,
                        "size_bytes": receipt.size_bytes,
                    },
                },
            )
            part.unlink(missing_ok=True)
            return self._jobs.update(
                database,
                job_id=job.id,
                target=JobStatus.SUCCEEDED,
                stage=JobStage.VERIFYING,
                result_asset_ids=[str(registered["id"])],
                resume={
                    "artifact_id": artifact.artifact_id,
                    "host_fingerprint": artifact.host_fingerprint,
                    "content_type": receipt.content_type,
                    "sha256": receipt.sha256,
                    "size_bytes": receipt.size_bytes,
                },
            )
        except OSError, UntrustedDownload:
            return self._jobs.update(
                database,
                job_id=job.id,
                target=JobStatus.INTERRUPTED,
                stage=JobStage.DOWNLOADING,
                resume_class=ResumeClass.DOWNLOAD_RETRY,
                error={
                    "code": "DOWNLOAD_INTERRUPTED",
                    "safe_to_retry": True,
                    "failed_step": "downloading",
                },
            )

    def _provider_failure(
        self, database: Path, job_id: str, result: ProviderResult, stage: JobStage
    ) -> Any:
        detail = result.error.model_dump(mode="json") if result.error is not None else {}
        target = JobStatus.INTERRUPTED if result.retryable else JobStatus.FAILED
        resume_class = (
            ResumeClass.DOWNLOAD_RETRY
            if stage is JobStage.DOWNLOADING and result.retryable
            else ResumeClass.REMOTE_POLL
            if stage is JobStage.REMOTE_RUNNING and result.retryable
            else ResumeClass.MANUAL_REVIEW
        )
        return self._jobs.update(
            database,
            job_id=job_id,
            target=target,
            stage=stage,
            resume_class=resume_class,
            error=detail
            or {
                "code": "PROVIDER_UNAVAILABLE",
                "safe_to_retry": result.retryable,
                "failed_step": stage.value,
            },
        )

    def _heartbeat(self, database: Path, job_id: str, owner: str, lease_until: str) -> None:
        if not self._jobs.heartbeat(database, job_id=job_id, owner=owner, lease_until=lease_until):
            raise RuntimeError("Job lease was lost")
