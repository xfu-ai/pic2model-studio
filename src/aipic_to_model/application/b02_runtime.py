"""Durable B02 Tool execution boundary.

This module deliberately schedules work only.  Provider-specific handlers are
installed by their owning B02 step, so an unavailable integration is a stable
failure rather than a fake successful result.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..domain.common import RiskLevel, canonical_json, new_id
from ..domain.errors import DomainErrorV1, ErrorCode
from ..domain.job_models import CancelCapability, JobStage, JobStatus, ResumeClass
from ..domain.tools import ToolResultV1
from .b02_tool_catalog import B02_TOOLS
from .generation_policy import effective_generation_risk
from .jobs.submission_policy import PAID_SUBMISSION_TOOLS

_APPROVED_EXTERNALLY = PAID_SUBMISSION_TOOLS

_MESHY_IMAGE_TOOLS = frozenset(
    {
        "image.transform",
        "image.generate_variants",
        "image.upscale",
        "image.remove_background",
        "image.inpaint_selection",
        "element.split",
        "element.export_transparent",
        "multiview.generate",
        "multiview.regenerate_view",
    }
)
_GEMINI_VISION_TOOLS = frozenset(
    {
        "image.analyze_content",
        "image.analyze_style",
        "image.evaluate_3d_suitability",
        "image.understand_for_agent",
        "prompt.rewrite",
        "selection.auto_suggest_boxes",
        "multiview.detect_regions",
        "multiview.validate",
    }
)
_RETRY_SOURCE_TOOL_CALL_ID = "__retry_source_tool_call_id"


def _requires_new_submission_confirmation(
    *,
    status: JobStatus,
    stage: JobStage,
    resume_class: ResumeClass,
    error: Any,
) -> bool:
    """Return whether a paid submission has durably become ambiguous.

    ``UNKNOWN_SUBMISSION`` is also used as an in-flight crash-safety marker
    while a paid create request is executing. That marker must not be exposed
    as a user recovery action until the Job has settled into the matching
    interrupted/error state.
    """

    return (
        status is JobStatus.INTERRUPTED
        and stage is JobStage.UNKNOWN_SUBMISSION
        and resume_class is ResumeClass.UNKNOWN_SUBMISSION
        and isinstance(error, dict)
        and error.get("code") == "JOB_UNKNOWN_SUBMISSION"
    )


def _collect_input_asset_ids(arguments: Any) -> list[str]:
    """Collect opaque managed-asset references without exposing other arguments."""
    found: list[str] = []

    def visit(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                visit(child_value, str(child_key))
            return
        if (key == "asset_id" or key.endswith("_asset_id")) and isinstance(value, str):
            found.append(value)
            return
        if key.endswith("_asset_ids") and isinstance(value, list):
            found.extend(str(item) for item in value if isinstance(item, str))

    visit(arguments)
    return list(dict.fromkeys(found))


def _canonical_provider_arguments(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Keep production tools on the application's configured provider policy."""

    if name in _MESHY_IMAGE_TOOLS:
        normalized = {
            **arguments,
            "provider_profile": "meshy/default",
            "channel": "meshy",
        }
        model = normalized.get("model")
        if not isinstance(model, str) or not model.strip() or model.lower().startswith("gpt-image"):
            normalized["model"] = "nano-banana"
        return normalized
    if name in _GEMINI_VISION_TOOLS:
        normalized = {**arguments, "provider_profile": "gemini/google/default"}
        model = normalized.get("model")
        if not isinstance(model, str) or not model.strip() or model.lower().startswith("gpt-"):
            normalized["model"] = "gemini-flash-lite-latest"
        return normalized
    return arguments


class PersistentB02ToolRuntime:
    """Uses repository-shaped ports supplied by the composition root."""

    def __init__(
        self,
        jobs: Any,
        approvals: Any,
        sync_dispatcher: Callable[[str, Path, str, dict[str, Any], str], ToolResultV1]
        | None = None,
        local_capability: Callable[[str], bool] | None = None,
    ) -> None:
        self._jobs = jobs
        self._approvals = approvals
        self._sync_dispatcher = sync_dispatcher
        self._local_capability = local_capability or (lambda _name: False)

    def invoke(
        self,
        name: str,
        risk_level: RiskLevel,
        execution: str,
        root: Path,
        project_id: str,
        arguments: dict[str, Any],
        call_id: str,
    ) -> ToolResultV1:
        database = root / "project.sqlite3"
        arguments = _canonical_provider_arguments(name, arguments)
        risk_level = effective_generation_risk(name, risk_level, arguments)
        if name in {"job.get_status", "model3d.get_status"}:
            return self._status(database, call_id, str(arguments["job_id"]))
        if name in {"job.cancel", "model3d.cancel"}:
            return self._cancel(database, call_id, str(arguments["job_id"]))
        if name == "model3d.download":
            return self._download_request(database, call_id, str(arguments["job_id"]))
        if name == "job.retry":
            return self._retry(database, project_id, call_id, str(arguments["job_id"]))
        if name == "job.confirm_new_submission":
            return self._confirm_new_submission(
                database, project_id, call_id, str(arguments["job_id"])
            )
        if name == "model3d.render_preview":
            return ToolResultV1(
                True,
                "awaiting_ui_action",
                call_id,
                [],
                "Open the managed 3D preview and explicitly capture a preview image.",
                [],
                {"type": "capture_model_preview"},
                {
                    "action_id": call_id,
                    "type": "capture_model_preview",
                    "workspace_mode": "model3d",
                },
            )
        if name == "model3d.optimize":
            if self._local_capability(name):
                return self._schedule(database, call_id, name, risk_level, arguments)
            return ToolResultV1(
                False,
                "failed",
                call_id,
                [],
                "Model optimization is not configured in this desktop build.",
                [],
                error={
                    "code": "TOOL_NOT_AVAILABLE",
                    "category": "api_not_configured",
                    "user_message": "No approved model optimization provider is configured.",
                    "recoverable": True,
                    "failed_object": "tool_call",
                    "failed_step": "dispatch",
                    "safe_to_retry": True,
                    "recommended_action": "configure_provider",
                },
            )
        if risk_level in {RiskLevel.EXTERNAL, RiskLevel.EXTERNAL_PAID} and name in _APPROVED_EXTERNALLY:
            return self._request_approval(database, project_id, call_id, name, arguments)
        if execution == "job":
            return self._schedule(database, call_id, name, risk_level, arguments)
        if self._sync_dispatcher is not None:
            return self._sync_dispatcher(name, root, project_id, arguments, call_id)
        return ToolResultV1(
            False,
            "failed",
            call_id,
            [],
            "This production operation has not been connected yet.",
            [],
            error={
                "code": "TOOL_NOT_AVAILABLE",
                "category": "api_not_configured",
                "user_message": "The requested production capability is not configured.",
                "recoverable": True,
                "failed_object": "tool_call",
                "failed_step": "dispatch",
                "safe_to_retry": True,
                "recommended_action": "configure_provider",
            },
        )

    def decide_approval(
        self,
        root: Path,
        project_id: str,
        approval_id: str,
        *,
        approved: bool,
    ) -> ToolResultV1:
        """Apply one parameter-bound decision and schedule at most one Job."""
        database = root / "project.sqlite3"
        approval = self._approvals.get(database, approval_id=approval_id)
        if approval.project_id != project_id:
            return self._job_error(
                approval.tool_call_id,
                "APPROVAL_SCOPE_MISMATCH",
                "The approval belongs to a different project.",
            )
        existing = self._jobs.get_by_tool_call(database, tool_call_id=approval.tool_call_id)
        if approval.decision == "consumed" and existing is not None:
            return self._queued(approval.tool_call_id, existing, reused=True)
        try:
            decided = self._approvals.decide(database, approval_id=approval_id, approved=approved)
        except ValueError:
            return self._job_error(
                approval.tool_call_id,
                "APPROVAL_ALREADY_DECIDED",
                "The approval decision can no longer be changed.",
            )
        if decided.decision == "denied":
            return ToolResultV1(
                False,
                "failed",
                approval.tool_call_id,
                [],
                "The external operation was not approved.",
                [],
                error={
                    "code": "APPROVAL_DENIED",
                    "category": "cancelled",
                    "user_message": "The external operation was not approved.",
                    "recoverable": False,
                    "failed_object": "tool_call",
                    "failed_step": "approval",
                    "safe_to_retry": False,
                    "recommended_action": "none",
                },
            )
        summaries = self._approvals.summaries(database, approval_id=approval_id)
        arguments = dict(summaries["arguments_summary"])
        if approval.provider_profile:
            arguments["provider_profile"] = approval.provider_profile
        retry_source_tool_call_id = arguments.get(_RETRY_SOURCE_TOOL_CALL_ID)
        arguments_hash, scope_hash = self._approval_hashes(
            approval.tool_name, approval.provider_profile, arguments
        )
        try:
            self._approvals.consume(
                database,
                approval_id=approval_id,
                tool_call_id=approval.tool_call_id,
                provider_profile=approval.provider_profile,
                arguments_hash=arguments_hash,
                scope_hash=scope_hash,
            )
        except ValueError:
            existing = self._jobs.get_by_tool_call(database, tool_call_id=approval.tool_call_id)
            if existing is not None:
                return self._queued(approval.tool_call_id, existing, reused=True)
            return self._job_error(
                approval.tool_call_id,
                "APPROVAL_SCOPE_MISMATCH",
                "The approved parameters no longer match the requested operation.",
            )
        risk, execution = self._tool_policy(approval.tool_name)
        if execution != "job":
            return self._job_error(
                approval.tool_call_id,
                "TOOL_NOT_AVAILABLE",
                "The approved operation has no Job execution boundary.",
            )
        arguments.pop(_RETRY_SOURCE_TOOL_CALL_ID, None)
        return self._schedule(
            database,
            approval.tool_call_id,
            approval.tool_name,
            risk,
            arguments,
            source_tool_call_id=(
                retry_source_tool_call_id
                if isinstance(retry_source_tool_call_id, str)
                else None
            ),
        )

    def job_view(self, root: Path, job_id: str) -> dict[str, Any]:
        try:
            job = self._jobs.get(root / "project.sqlite3", job_id=job_id)
        except KeyError as error:
            raise DomainErrorV1(ErrorCode.TOOL_ARGUMENT_INVALID, "Job does not exist.") from error
        capability = self._cancel_capability(job)
        arguments = self._jobs.retry_context(root / "project.sqlite3", job_id=job_id).get("arguments", {})
        input_asset_ids = _collect_input_asset_ids(arguments)
        completed_at = (
            job.updated_at
            if job.status
            in {
                JobStatus.SUCCEEDED,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
                JobStatus.INTERRUPTED,
            }
            else None
        )
        recovery_actions: list[str] = []
        if _requires_new_submission_confirmation(
            status=job.status,
            stage=job.stage,
            resume_class=job.resume_class,
            error=job.error,
        ):
            if job.external_task_id:
                recovery_actions.append("query_remote")
            recovery_actions.append("confirm_new_submission")
        return {
            "schema_version": 1,
            "id": job.id,
            "job_type": job.job_type,
            "status": job.status.value,
            "stage": job.stage.value,
            "progress": job.progress,
            "provider": job.provider,
            "resume_class": job.resume_class.value,
            "recovery_actions": recovery_actions,
            "cancel_capability": capability.value,
            "can_cancel": capability
            in {CancelCapability.CANCEL_LOCAL, CancelCapability.CANCEL_REMOTE},
            "can_stop_waiting": capability is CancelCapability.STOP_WAITING,
            "output_asset_ids": job.result_asset_ids,
            "input_asset_ids": input_asset_ids,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "completed_at": completed_at,
            "error": job.error,
        }

    def job_views(self, root: Path, *, include_terminal: bool = False) -> list[dict[str, Any]]:
        """Expose durable Jobs to the desktop task center without duplicating state."""
        database = root / "project.sqlite3"
        jobs = self._jobs.list_recent(database) if include_terminal else self._jobs.list_nonterminal(database)
        return [self.job_view(root, job.id) for job in jobs]

    def _request_approval(
        self,
        database: Path,
        project_id: str,
        call_id: str,
        name: str,
        arguments: dict[str, Any],
    ) -> ToolResultV1:
        provider = str(arguments.get("provider_profile", ""))
        arguments_hash, scope_hash = self._approval_hashes(name, provider, arguments)
        approval = self._approvals.request(
            database,
            project_id=project_id,
            tool_call_id=call_id,
            tool_name=name,
            provider_profile=provider,
            arguments_hash=arguments_hash,
            scope_hash=scope_hash,
            input_asset_summary=self._asset_ids(arguments),
            cost_summary={"status": "unknown"},
            arguments_summary={
                key: value for key, value in arguments.items() if key != "provider_profile"
            },
        )
        return ToolResultV1(
            True,
            "awaiting_ui_action",
            call_id,
            [],
            "Approval is required before external work can be scheduled.",
            [],
            {"type": "approval_required"},
            {"action_id": approval.id, "type": "approval_required", "workspace_mode": "working"},
        )

    def _status(self, database: Path, call_id: str, job_id: str) -> ToolResultV1:
        try:
            job = self._jobs.get(database, job_id=job_id)
        except KeyError:
            return self._job_error(call_id, "JOB_NOT_FOUND", "The requested Job does not exist.")
        status = job.status.value
        stage = job.stage.value
        progress = f", progress={job.progress}%" if job.progress is not None else ""
        error_code = str(job.error.get("code")) if job.error else ""
        error_detail = f" Error code: {error_code}." if error_code else ""
        if job.status not in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
            summary = (
                f"Job {job.id} is {status} at stage {stage}{progress}. Do not poll this job "
                "again in this Agent turn and do not use asset.list to look for its output. "
                "Tell the user it is waiting; the desktop will send a completion event when ready."
                f"{error_detail}"
            )
        elif job.status is JobStatus.SUCCEEDED:
            assets = ", ".join(job.result_asset_ids) or "none"
            summary = (
                f"Job {job.id} succeeded at stage {stage}. "
                f"Output asset IDs: {assets}."
            )
        else:
            summary = (
                f"Job {job.id} ended with status {status} at stage {stage}."
                f"{error_detail}"
            )
        return ToolResultV1(
            True,
            "succeeded",
            call_id,
            job.result_asset_ids,
            summary,
            [],
        )

    def _cancel(self, database: Path, call_id: str, job_id: str) -> ToolResultV1:
        try:
            job = self._jobs.get(database, job_id=job_id)
            capability = self._cancel_capability(job)
            if capability is CancelCapability.NOT_CANCELLABLE:
                return self._job_error(
                    call_id, "JOB_NOT_CANCELLABLE", "This Job can no longer be cancelled."
                )
            mode = {
                CancelCapability.CANCEL_LOCAL: "local",
                CancelCapability.CANCEL_REMOTE: "remote",
                CancelCapability.STOP_WAITING: "stop_waiting",
            }[capability]
            result = self._jobs.request_cancel(database, job_id=job_id, mode=mode)
        except KeyError:
            return self._job_error(call_id, "JOB_NOT_FOUND", "The requested Job does not exist.")
        return ToolResultV1(
            True, "succeeded", call_id, result.result_asset_ids, "Cancellation recorded.", []
        )

    def _download_request(self, database: Path, call_id: str, job_id: str) -> ToolResultV1:
        try:
            job = self._jobs.get(database, job_id=job_id)
            if not job.external_task_id:
                return self._job_error(
                    call_id, "REMOTE_TASK_NOT_FOUND", "The Job has no known remote task."
                )
            if job.status is JobStatus.SUCCEEDED:
                return ToolResultV1(
                    True,
                    "succeeded",
                    call_id,
                    job.result_asset_ids,
                    "The verified model asset is already available.",
                    [],
                    reused=True,
                )
            if job.status in {JobStatus.FAILED, JobStatus.CANCELLED}:
                return self._job_error(
                    call_id, "JOB_NOT_RETRYABLE", "The Job cannot enter the download phase."
                )
            if not (
                job.stage is JobStage.DOWNLOADING and job.resume_class is ResumeClass.DOWNLOAD_RETRY
            ):
                job = self._jobs.update(
                    database,
                    job_id=job_id,
                    target=JobStatus.INTERRUPTED,
                    stage=JobStage.DOWNLOADING,
                    resume_class=ResumeClass.DOWNLOAD_RETRY,
                )
            return self._queued(call_id, job)
        except KeyError:
            return self._job_error(call_id, "JOB_NOT_FOUND", "The requested Job does not exist.")

    def _retry(self, database: Path, project_id: str, call_id: str, job_id: str) -> ToolResultV1:
        try:
            context = self._jobs.retry_context(database, job_id=job_id)
        except KeyError:
            return self._job_error(call_id, "JOB_NOT_FOUND", "The requested Job does not exist.")
        error = context["error"] if isinstance(context["error"], dict) else {}
        safe_interrupted = context["status"] == JobStatus.INTERRUPTED.value and context[
            "resume_class"
        ] in {ResumeClass.LOCAL_RESTARTABLE.value, ResumeClass.DOWNLOAD_RETRY.value}
        if not (bool(error.get("safe_to_retry")) or safe_interrupted):
            return self._job_error(
                call_id, "JOB_NOT_RETRYABLE", "The requested Job is not safe to retry."
            )
        arguments = dict(context["arguments"])
        provider = context["provider_profile"]
        if provider and "provider_profile" not in arguments:
            arguments["provider_profile"] = provider
        risk = RiskLevel(context["risk_level"])
        name = str(context["tool_name"])
        source_tool_call_id = str(context["source_tool_call_id"])
        arguments = _canonical_provider_arguments(name, arguments)
        if risk is RiskLevel.EXTERNAL_PAID:
            arguments[_RETRY_SOURCE_TOOL_CALL_ID] = source_tool_call_id
            return self._request_approval(database, project_id, call_id, name, arguments)
        return self._schedule(
            database,
            call_id,
            name,
            risk,
            arguments,
            source_tool_call_id=source_tool_call_id,
        )

    def _confirm_new_submission(
        self,
        database: Path,
        project_id: str,
        call_id: str,
        job_id: str,
    ) -> ToolResultV1:
        """Prepare a new paid submission while preserving the unknown Job.

        This is intentionally separate from ordinary retry. The original
        idempotency reservation remains in ``unknown_submission`` for audit
        and duplicate-charge protection; approval creates a new Tool Call and
        Job linked through ``source_tool_call_id``.
        """

        try:
            job = self._jobs.get(database, job_id=job_id)
            context = self._jobs.retry_context(database, job_id=job_id)
        except KeyError:
            return self._job_error(call_id, "JOB_NOT_FOUND", "The requested Job does not exist.")
        if not _requires_new_submission_confirmation(
            status=job.status,
            stage=job.stage,
            resume_class=job.resume_class,
            error=job.error,
        ):
            return self._job_error(
                call_id,
                "JOB_CONFIRMATION_NOT_REQUIRED",
                "The requested Job does not require a new-submission confirmation.",
            )
        arguments = dict(context["arguments"])
        provider = context["provider_profile"]
        if provider and "provider_profile" not in arguments:
            arguments["provider_profile"] = provider
        name = str(context["tool_name"])
        arguments = _canonical_provider_arguments(name, arguments)
        arguments[_RETRY_SOURCE_TOOL_CALL_ID] = str(context["source_tool_call_id"])
        return self._request_approval(database, project_id, call_id, name, arguments)

    @staticmethod
    def _queued(call_id: str, job: Any, *, reused: bool = False) -> ToolResultV1:
        return ToolResultV1(
            True,
            "queued",
            call_id,
            [],
            "Job queued.",
            [],
            job={
                "job_id": job.id,
                "status": "queued",
                "job_type": job.job_type,
                "stage": job.stage.value,
                "elapsed_seconds": 0,
                "provider": job.provider or "local",
                "can_cancel": True,
                "can_stop_waiting": False,
            },
            reused=reused,
        )

    def _schedule(
        self,
        database: Path,
        call_id: str,
        name: str,
        risk_level: RiskLevel,
        arguments: dict[str, Any],
        *,
        source_tool_call_id: str | None = None,
    ) -> ToolResultV1:
        job = self._jobs.create(
            database,
            job_id=new_id(),
            tool_call_id=call_id,
            job_type=name,
            provider=(
                str(arguments.get("provider_profile")) if "provider_profile" in arguments else None
            ),
            resume_class=(
                ResumeClass.FRESH
                if risk_level in {RiskLevel.EXTERNAL, RiskLevel.EXTERNAL_PAID}
                else ResumeClass.LOCAL_RESTARTABLE
            ),
            resume=(
                {"source_tool_call_id": source_tool_call_id}
                if source_tool_call_id is not None
                else None
            ),
        )
        return self._queued(call_id, job)

    @staticmethod
    def _approval_hashes(name: str, provider: str, arguments: dict[str, Any]) -> tuple[str, str]:
        arguments_hash = hashlib.sha256(canonical_json(arguments).encode()).hexdigest()
        scope_hash = hashlib.sha256(
            canonical_json(
                {"name": name, "provider_profile": provider, "arguments": arguments}
            ).encode()
        ).hexdigest()
        return arguments_hash, scope_hash

    @staticmethod
    def _tool_policy(name: str) -> tuple[RiskLevel, str]:
        for tool_name, risk, execution, _approval, _capability in B02_TOOLS:
            if tool_name == name:
                return risk, execution
        raise ValueError(f"unknown B02 tool: {name}")

    @staticmethod
    def _job_error(call_id: str, code: str, message: str) -> ToolResultV1:
        return ToolResultV1(
            False,
            "failed",
            call_id,
            [],
            message,
            [],
            error={
                "code": code,
                "category": "job",
                "user_message": message,
                "recoverable": False,
            },
        )

    @staticmethod
    def _cancel_capability(job: Any) -> CancelCapability:
        if job.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
            return CancelCapability.NOT_CANCELLABLE
        if job.resume_class is ResumeClass.STOP_WAITING:
            return CancelCapability.NOT_CANCELLABLE
        if job.external_task_id:
            return CancelCapability.CANCEL_REMOTE
        if job.status is JobStatus.WAITING:
            return CancelCapability.STOP_WAITING
        return CancelCapability.CANCEL_LOCAL

    @staticmethod
    def _asset_ids(arguments: dict[str, Any]) -> list[str]:
        found: list[str] = []

        def visit(value: Any, key: str | None = None) -> None:
            if isinstance(value, dict):
                for child_key, child in value.items():
                    visit(child, str(child_key))
            elif isinstance(value, list):
                for child in value:
                    visit(child, key)
            elif (
                isinstance(value, str) and key and (key == "asset_id" or key.endswith("_asset_id"))
            ):
                found.append(value)

        visit(arguments)
        return list(dict.fromkeys(found))
