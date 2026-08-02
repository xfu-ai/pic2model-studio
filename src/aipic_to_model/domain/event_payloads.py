"""Closed event payload shapes shared by application and API layers."""

from typing import Any

from .common import DomainErrorV1, ErrorCode

_REQUIRED: dict[str, set[str]] = {
    "project.metadata.changed": {"changed_fields", "request_id"},
    "asset.created": {"asset_id", "asset_type", "asset_group", "parent_asset_id"},
    "asset.current_changed": {"previous_asset_id", "asset_id", "decision_id", "decision_source"},
    "asset.visibility.changed": {"asset_id", "is_hidden", "trashed_at"},
    "selection.changed": {"selection_id", "asset_id", "revision", "status"},
    "selection.cancelled": {"action_id", "selection_id", "run_id"},
    "tool_call.status.changed": {"tool_call_id", "status", "output_asset_ids", "job_id"},
    "workspace.action.requested": {"action_id", "type", "workspace_mode"},
}
_OPTIONAL: dict[str, set[str]] = {
    "workspace.action.requested": {"asset_id", "selection_id", "run_id"},
    "job.created": {
        "progress",
        "provider",
        "elapsed_seconds",
        "estimated_seconds",
        "error",
        "output_asset_ids",
    },
    "job.started": {
        "progress",
        "provider",
        "elapsed_seconds",
        "estimated_seconds",
        "error",
        "output_asset_ids",
    },
    "job.progressed": {
        "progress",
        "provider",
        "elapsed_seconds",
        "estimated_seconds",
        "error",
        "output_asset_ids",
    },
    "job.waiting": {
        "progress",
        "provider",
        "elapsed_seconds",
        "estimated_seconds",
        "error",
        "output_asset_ids",
    },
    "job.completed": {
        "progress",
        "provider",
        "elapsed_seconds",
        "estimated_seconds",
        "error",
        "output_asset_ids",
    },
    "job.failed": {
        "progress",
        "provider",
        "elapsed_seconds",
        "estimated_seconds",
        "error",
        "output_asset_ids",
    },
    "job.cancelled": {
        "progress",
        "provider",
        "elapsed_seconds",
        "estimated_seconds",
        "error",
        "output_asset_ids",
    },
    "job.interrupted": {
        "progress",
        "provider",
        "elapsed_seconds",
        "estimated_seconds",
        "error",
        "output_asset_ids",
    },
    "job.result_ready": {
        "progress",
        "provider",
        "elapsed_seconds",
        "estimated_seconds",
        "error",
        "focus_policy",
        "output_asset_ids",
    },
    "candidate.created": {"selected_asset_ids", "selection_mode"},
    "candidate.selected": {"asset_ids"},
    "selection.saved": set(),
    "selection.confirmed": set(),
}

for _job_event in (
    "job.created",
    "job.started",
    "job.progressed",
    "job.waiting",
    "job.completed",
    "job.failed",
    "job.cancelled",
    "job.interrupted",
    "job.result_ready",
):
    _REQUIRED[_job_event] = {"status", "stage", "can_cancel", "can_stop_waiting"}
_REQUIRED["job.result_ready"].add("focus_policy")
_REQUIRED["candidate.created"] = {"candidate_group_id", "asset_ids"}
_REQUIRED["candidate.selected"] = {
    "candidate_group_id",
    "selected_asset_ids",
    "selection_mode",
}
for _selection_event in ("selection.saved", "selection.confirmed", "selection.cancelled"):
    _REQUIRED[_selection_event] = {"selection_ids", "asset_id", "action"}
_REQUIRED["multiview.validated"] = {
    "multiview_set_id",
    "severity",
    "checks_run",
    "issues",
    "overridden_issue_ids",
    "can_continue",
}


def validate_event_payload(event_type: str, payload: dict[str, Any]) -> None:
    if event_type == "selection.cancelled":
        previous_shape = {"action_id", "selection_id", "run_id"}
        b02 = {"selection_ids", "asset_id", "action"}
        if frozenset(payload) not in {frozenset(previous_shape), frozenset(b02)}:
            raise DomainErrorV1(ErrorCode.SCHEMA_VALIDATION_FAILED, "invalid event payload")
        return
    required = _REQUIRED.get(event_type)
    allowed = required | _OPTIONAL.get(event_type, set()) if required is not None else set()
    if required is None or not required <= set(payload) or set(payload) - allowed:
        raise DomainErrorV1(ErrorCode.SCHEMA_VALIDATION_FAILED, "invalid event payload")
    if not all(isinstance(key, str) for key in payload):
        raise DomainErrorV1(ErrorCode.SCHEMA_VALIDATION_FAILED, "invalid event payload")
