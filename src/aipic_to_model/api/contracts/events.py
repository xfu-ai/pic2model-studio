"""Closed B01 event payload contract used before event persistence."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ...domain.common import DomainErrorV1, ErrorCode


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectMetadataChanged(_Payload):
    changed_fields: list[str]
    request_id: str


class AssetCreated(_Payload):
    asset_id: str
    asset_type: str
    asset_group: str | None
    parent_asset_id: str | None


class AssetCurrentChanged(_Payload):
    previous_asset_id: str | None
    asset_id: str | None
    decision_id: str
    decision_source: Literal["user", "agent", "import", "system"]


class AssetVisibilityChanged(_Payload):
    asset_id: str
    is_hidden: bool
    trashed_at: str | None


class SelectionChanged(_Payload):
    selection_id: str
    asset_id: str
    revision: int = Field(ge=1)
    status: Literal["draft", "edited", "confirmed"]


class SelectionCancelled(_Payload):
    action_id: str
    selection_id: str | None
    run_id: str | None


class ToolCallStatusChanged(_Payload):
    tool_call_id: str
    status: str
    output_asset_ids: list[str]
    job_id: str | None


class WorkspaceActionRequested(_Payload):
    action_id: str
    type: str
    workspace_mode: str
    asset_id: str | None = None
    selection_id: str | None = None
    run_id: str | None = None


PAYLOAD_MODELS: dict[str, type[_Payload]] = {
    "project.metadata.changed": ProjectMetadataChanged,
    "asset.created": AssetCreated,
    "asset.current_changed": AssetCurrentChanged,
    "asset.visibility.changed": AssetVisibilityChanged,
    "selection.changed": SelectionChanged,
    "selection.cancelled": SelectionCancelled,
    "tool_call.status.changed": ToolCallStatusChanged,
    "workspace.action.requested": WorkspaceActionRequested,
}


def validate_event_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    model = PAYLOAD_MODELS.get(event_type)
    if model is None:
        raise DomainErrorV1(ErrorCode.SCHEMA_VALIDATION_FAILED, "unsupported event type")
    try:
        return model.model_validate(payload).model_dump(mode="json")
    except ValidationError as error:
        raise DomainErrorV1(ErrorCode.SCHEMA_VALIDATION_FAILED, "invalid event payload") from error
