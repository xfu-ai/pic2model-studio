"""Strict transport DTOs for every B01 write boundary.

The frozen OpenAPI snapshot publishes these class identities.  Focused modules
remain available for non-transport helpers, but relocating a Pydantic model
would itself alter the `/v1` contract component identity.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateProjectRequest(StrictRequest):
    name: str = Field(min_length=1, max_length=200)
    create_capability_id: str = Field(min_length=1)


class OpenProjectRequest(StrictRequest):
    open_capability_id: str = Field(min_length=1)


class HostCapabilityIssueRequest(StrictRequest):
    """Host-only local IPC payload; this schema is never called by the renderer."""

    path: str = Field(min_length=1)
    operation: Literal[
        "create", "open", "import", "export", "diagnostic_export",
        "model3d.import_local",
    ]
    project_id: str | None = None
    request_id: str = Field(min_length=1)


class HostRecentCapabilityRequest(StrictRequest):
    """Host-only request for a previously trusted project root."""

    recent_project_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)


class RenameProjectRequest(StrictRequest):
    name: str = Field(min_length=1, max_length=200)
    request_id: str = Field(min_length=1)


class UpdateWorkspaceStateRequest(StrictRequest):
    """B04-owned UI state only; the service rejects unrecognised or sensitive values."""

    state: dict[str, Any] = Field(min_length=1)
    request_id: str = Field(min_length=1)


class ExportProjectRequest(StrictRequest):
    export_capability_id: str
    format: Literal["project_v1"]
    request_id: str


class ImportAssetRequest(StrictRequest):
    file_capability_id: str
    asset_type: Literal["source_image", "glb", "prompt"]
    request_id: str
    name: str | None = None
    parent_asset_id: str | None = None


class ProjectRequest(StrictRequest):
    project_id: str
    request_id: str


class SetCurrentRequest(ProjectRequest):
    decision_source: Literal["user", "agent", "import", "system"]
    reason: str | None = None


class TrashRequest(ProjectRequest):
    impact_token: str | None = None


class AssetActionRequest(ProjectRequest):
    """Shared project/request identity for reversible asset commands."""


class CompareAssetsRequest(ProjectRequest):
    left_id: str = Field(min_length=1)
    right_id: str = Field(min_length=1)


class SelectionConfirmRequest(ProjectRequest):
    expected_revision: int


class SelectionSaveRequest(ProjectRequest):
    rects: list[dict[str, Any]] = Field(min_length=1)
    label: str = ""
    confidence: float | None = Field(default=None, ge=0, le=1)
    source: Literal["user", "agent"] = "user"
    status: Literal["draft", "edited"] = "draft"
    selection_id: str | None = None
    expected_revision: int | None = None


class SelectionUpdateRequest(ProjectRequest):
    rects: list[dict[str, Any]] = Field(min_length=1)
    expected_revision: int
    label: str | None = None
    source: Literal["user", "agent"] | None = None
    status: Literal["draft", "edited"] = "edited"


class ToolInvokeRequest(StrictRequest):
    project_id: str
    run_id: str | None = None
    round_index: int = Field(ge=0)
    tool_name: str
    tool_version: str
    arguments: dict[str, Any]
    request_id: str
    provider_profile: str | None = None


class SavePromptVersionRequest(StrictRequest):
    zh_prompt: str = Field(min_length=1, max_length=20_000)
    en_prompt: str = Field(min_length=1, max_length=20_000)
    kind: Literal["content", "style", "merged", "image", "multiview", "element", "boxsplit"] = "merged"
    parent_asset_id: str | None = None
    request_id: str = Field(min_length=1)


class UpdateSettingsRequest(StrictRequest):
    scope: Literal["app", "project"]
    project_id: str | None = None
    patch: dict[str, Any]
    request_id: str


class SetSecretRequest(StrictRequest):
    provider_profile: str
    secret: str
    request_id: str


class ProbeProviderRequest(StrictRequest):
    provider_profile: str
    request_id: str


class CancelSelectionStepRequest(StrictRequest):
    project_id: str
    selection_id: str | None = None
    action_id: str
    run_id: str | None = None


class DiagnosticPreviewRequest(StrictRequest):
    project_id: str


class DiagnosticExportRequest(StrictRequest):
    project_id: str
    export_capability_id: str
    confirmed_manifest_hash: str
    request_id: str
