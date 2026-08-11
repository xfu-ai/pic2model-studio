"""Complete an approval-suspended Agent Tool call from the durable Job repository."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.models import TextContent, ToolResult, ToolResultMessage
from ..session.sqlite import LinearSessionRepository


class ApprovedToolJobWait:
    """Own the one final Tool Result for an approval-suspended Tool Call.

    This service never calls a model, posts a natural-language continuation,
    cancels a Job, or discovers assets through a workspace list.
    """

    def __init__(self, repository: LinearSessionRepository, broker: Any) -> None:
        self._repository = repository
        self._broker = broker

    async def wait_and_complete(
        self,
        wait: dict[str, object],
        *,
        project_root: Path,
        timeout_seconds: float = 180.0,
    ) -> str | None:
        job_id = wait.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            return None
        terminal = await self._broker.wait_for_terminal(
            project_root / "project.sqlite3", job_id, timeout_seconds=timeout_seconds
        )
        session_id, tool_call_id, tool_name = (
            str(wait["session_id"]),
            str(wait["tool_call_id"]),
            str(wait["tool_name"]),
        )
        if terminal is None:
            self._repository.complete_job_wait(session_id, tool_call_id, "waiting_external")
            # Keep the original Tool Call open. A non-terminal Tool Result would
            # close it permanently and make a later terminal result a duplicate.
            return "waiting_external"
        if self._repository.complete_job_wait(session_id, tool_call_id, "terminal_returned"):
            self._repository.append_or_replace_waiting_tool_result(
                session_id,
                ToolResultMessage(tool_call_id, tool_name, _terminal_result(terminal)),
            )
        return "terminal_returned"

    def complete_declined(self, wait: dict[str, object]) -> None:
        session_id, tool_call_id, tool_name = (
            str(wait["session_id"]),
            str(wait["tool_call_id"]),
            str(wait["tool_name"]),
        )
        if self._repository.complete_job_wait(session_id, tool_call_id, "declined"):
            self._repository.append_message(
                session_id, ToolResultMessage(tool_call_id, tool_name, _declined_result())
            )

    def complete_superseded(self, wait: dict[str, object]) -> None:
        """Close an unapproved call when a newer user instruction replaces it."""

        session_id, tool_call_id, tool_name = (
            str(wait["session_id"]),
            str(wait["tool_call_id"]),
            str(wait["tool_name"]),
        )
        if self._repository.complete_job_wait(session_id, tool_call_id, "declined"):
            self._repository.append_message(
                session_id,
                ToolResultMessage(tool_call_id, tool_name, _superseded_result()),
            )


def _terminal_result(job: Any) -> ToolResult:
    status = getattr(getattr(job, "status", None), "value", getattr(job, "status", "failed"))
    output_asset_refs = list(getattr(job, "result_asset_ids", []))
    job_ref = getattr(job, "id", None)
    succeeded = status == "succeeded"
    error = getattr(job, "error", None) or ({"code": "JOB_NOT_SUCCEEDED"} if not succeeded else None)
    unknown_submission = bool(
        status == "interrupted"
        and isinstance(error, dict)
        and error.get("code") == "JOB_UNKNOWN_SUBMISSION"
    )
    if succeeded:
        summary = "The Job completed successfully."
    elif unknown_submission:
        summary = (
            "The Job was interrupted with an unknown submission state and produced no model. "
            "Do not claim completion or retry automatically. Ask the user to explicitly confirm "
            "a new paid submission, then use job.confirm_new_submission with job_ref."
        )
    else:
        summary = f"The Job ended with status {status} and did not complete the requested work."
    details: dict[str, object] = {
        "status": status,
        "output_asset_ids": output_asset_refs,
        "output_asset_refs": output_asset_refs,
        "output_count": len(output_asset_refs),
    }
    if isinstance(job_ref, str) and job_ref:
        details["job_ref"] = job_ref
    if succeeded and str(getattr(job, "job_type", "")) == "model3d.generate":
        local = str(getattr(job, "provider", "")) == "model3d/local/triposr"
        details["artifact_facts"] = {
            "file_created": bool(output_asset_refs),
            "semantic_match": "not_verified",
            "pbr": False if local else "backend_report_required",
            "material_mode": "vertex_color" if local else "backend_report_required",
        }
        details["warnings"] = [
            (
                "File creation succeeded, but subject identity, style, and localized image "
                "edits have not been visually verified."
            )
        ]
    if not succeeded:
        details["error"] = error
        details["safe_to_retry"] = bool(
            isinstance(error, dict) and error.get("safe_to_retry") is True
        )
        if status == "interrupted":
            details["requires_user_confirmation"] = True
            details["recommended_action"] = (
                "job.confirm_new_submission" if unknown_submission else "job.retry"
            )
    return ToolResult(
        (TextContent(f"{summary}\nFacade result: {json.dumps({'status': status, 'output_asset_refs': output_asset_refs}, separators=(',', ':'))}"),),
        details=details,
        is_error=not succeeded,
    )


def _declined_result() -> ToolResult:
    return ToolResult(
        (TextContent("The external operation was declined."),),
        details={"status": "declined", "output_asset_ids": [], "output_asset_refs": []},
        is_error=True,
    )


def _superseded_result() -> ToolResult:
    return ToolResult(
        (TextContent("The pending approval was superseded by a newer user instruction."),),
        details={
            "status": "superseded",
            "output_asset_ids": [],
            "output_asset_refs": [],
        },
        is_error=True,
    )
