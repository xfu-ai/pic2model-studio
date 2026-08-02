from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .common import RiskLevel


@dataclass(frozen=True)
class ToolManifestV1:
    name: str
    version: str
    human_name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    risk_level: RiskLevel
    execution: str
    idempotency: bool
    supports_cancel: bool
    allowed_asset_types: list[str]
    executor_key: str
    # B02 keeps the approval/capability decision in the frozen manifest so
    # callers cannot infer it from a provider name or a UI-only convention.
    requires_approval: bool = False
    capability: str | None = None


@dataclass(frozen=True)
class UiActionV1:
    """A persisted request for a host/UI action; it never contains a path."""

    action_id: str
    type: str
    workspace_mode: str
    asset_id: str | None = None
    selection_id: str | None = None
    run_id: str | None = None


@dataclass(frozen=True)
class JobRefV1:
    job_id: str
    status: str
    job_type: str
    stage: str
    elapsed_seconds: int
    provider: str
    can_cancel: bool
    can_stop_waiting: bool
    progress: float | None = None
    estimated_seconds: int | None = None

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value
            for value in (self.job_id, self.job_type, self.stage, self.provider)
        ):
            raise ValueError("job identifiers must be non-empty")
        if self.status != "queued" or self.elapsed_seconds != 0:
            raise ValueError("B01 job references must represent a newly queued job")
        if not isinstance(self.elapsed_seconds, int) or isinstance(self.elapsed_seconds, bool):
            raise ValueError("elapsed_seconds must be an integer")  # noqa: TRY004
        if not isinstance(self.can_cancel, bool) or not isinstance(self.can_stop_waiting, bool):
            raise ValueError("job cancellation flags must be boolean")  # noqa: TRY004
        if self.progress is not None and (
            not isinstance(self.progress, (int, float))
            or isinstance(self.progress, bool)
            or not 0 <= self.progress <= 1
        ):
            raise ValueError("job progress must be between zero and one")
        if self.estimated_seconds is not None and (
            not isinstance(self.estimated_seconds, int)
            or isinstance(self.estimated_seconds, bool)
            or self.estimated_seconds < 0
        ):
            raise ValueError("estimated_seconds must be a non-negative integer")


@dataclass(frozen=True)
class ToolErrorV1:
    code: str
    category: str
    user_message: str
    recoverable: bool
    failed_object: str | None = None
    failed_step: str | None = None
    fee_incurred: bool = False
    preserved_asset_ids: list[str] | None = None
    safe_to_retry: bool = False
    recommended_action: str | None = None
    retry_after_seconds: int | None = None
    technical_message: str | None = None
    details_ref: str | None = None


@dataclass(frozen=True)
class ToolResultV1:
    ok: bool
    status: str
    tool_call_id: str
    output_asset_ids: list[str]
    summary: str
    warnings: list[str]
    expected_action: dict[str, Any] | None = None
    ui_action: dict[str, Any] | None = None
    job: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    reused: bool = False

    def __post_init__(self) -> None:
        """Keep all four frozen response branches mutually exclusive."""
        if (
            not isinstance(self.ok, bool)
            or not isinstance(self.tool_call_id, str)
            or not isinstance(self.output_asset_ids, list)
            or not all(isinstance(asset_id, str) for asset_id in self.output_asset_ids)
            or not isinstance(self.summary, str)
            or not isinstance(self.warnings, list)
            or not all(isinstance(warning, str) for warning in self.warnings)
            or not isinstance(self.reused, bool)
        ):
            raise ValueError("invalid ToolResultV1 field type")
        if self.job is not None:
            allowed_job = {
                "job_id",
                "status",
                "job_type",
                "stage",
                "elapsed_seconds",
                "provider",
                "can_cancel",
                "can_stop_waiting",
                "progress",
                "estimated_seconds",
            }
            required_job = allowed_job - {"progress", "estimated_seconds"}
            if set(self.job) - allowed_job or not required_job <= set(self.job):
                raise ValueError("invalid JobRefV1")
            JobRefV1(**self.job)
        if self.expected_action is not None and set(self.expected_action) != {"type"}:
            raise ValueError("invalid expected action")
        if self.expected_action is not None and (
            not isinstance(self.expected_action["type"], str) or not self.expected_action["type"]
        ):
            raise ValueError("invalid expected action")
        if self.ui_action is not None:
            allowed_action = {
                "action_id",
                "type",
                "workspace_mode",
                "asset_id",
                "selection_id",
                "run_id",
            }
            required_action = {"action_id", "type", "workspace_mode"}
            if set(self.ui_action) - allowed_action or not required_action <= set(self.ui_action):
                raise ValueError("invalid UiActionV1")
            UiActionV1(**self.ui_action)
            if any(
                not isinstance(self.ui_action[key], str) or not self.ui_action[key]
                for key in required_action
            ):
                raise ValueError("invalid UiActionV1")
            if any(
                key in self.ui_action
                and self.ui_action[key] is not None
                and not isinstance(self.ui_action[key], str)
                for key in ("asset_id", "selection_id", "run_id")
            ):
                raise ValueError("invalid UiActionV1")
        if self.error is not None:
            allowed_error = {
                "code",
                "category",
                "user_message",
                "recoverable",
                "failed_object",
                "failed_step",
                "fee_incurred",
                "preserved_asset_ids",
                "safe_to_retry",
                "recommended_action",
                "retry_after_seconds",
                "technical_message",
                "details_ref",
            }
            required_error = {"code", "category", "user_message", "recoverable"}
            if (
                set(self.error) - allowed_error
                or not required_error <= set(self.error)
                or not isinstance(self.error.get("code"), str)
                or not self.error["code"]
                or not isinstance(self.error.get("category"), str)
                or not self.error["category"]
                or not isinstance(self.error.get("user_message"), str)
                or not self.error["user_message"]
            ):
                raise ValueError("invalid ToolErrorV1")
            if any(
                key in self.error
                and self.error[key] is not None
                and not isinstance(self.error[key], expected)
                for key, expected in {
                    "category": str,
                    "user_message": str,
                    "recoverable": bool,
                    "failed_object": str,
                    "failed_step": str,
                    "fee_incurred": bool,
                    "safe_to_retry": bool,
                    "recommended_action": str,
                    "retry_after_seconds": int,
                    "technical_message": str,
                    "details_ref": str,
                }.items()
            ) or (
                "preserved_asset_ids" in self.error
                and self.error["preserved_asset_ids"] is not None
                and (
                    not isinstance(self.error["preserved_asset_ids"], list)
                    or not all(
                        isinstance(value, str) for value in self.error["preserved_asset_ids"]
                    )
                )
            ):
                raise ValueError("invalid ToolErrorV1")
            if self.error.get("retry_after_seconds") is not None and (
                not isinstance(self.error["retry_after_seconds"], int)
                or isinstance(self.error["retry_after_seconds"], bool)
                or self.error["retry_after_seconds"] < 0
            ):
                raise ValueError("invalid ToolErrorV1")
        if self.status not in {"succeeded", "queued", "awaiting_ui_action", "failed"}:
            raise ValueError("unsupported tool result status")
        if self.status == "succeeded":
            if not self.ok or any(
                value is not None
                for value in (self.expected_action, self.ui_action, self.job, self.error)
            ):
                raise ValueError("succeeded result contains an incompatible field")
        elif self.status == "queued":
            if (
                not self.ok
                or self.job is None
                or any(
                    value is not None
                    for value in (self.expected_action, self.ui_action, self.error)
                )
            ):
                raise ValueError("queued result requires only job")
        elif self.status == "awaiting_ui_action":
            if (
                not self.ok
                or self.expected_action is None
                or self.ui_action is None
                or any(value is not None for value in (self.job, self.error))
            ):
                raise ValueError("awaiting_ui_action result requires an action only")
        elif (
            self.ok
            or self.error is None
            or any(value is not None for value in (self.expected_action, self.ui_action, self.job))
        ):
            raise ValueError("failed result requires only error and ok=false")
