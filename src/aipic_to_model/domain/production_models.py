"""Frozen production DTOs shared by B02 application services and adapters."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .job_models import JobView
from .provider_models import ErrorDetail


class SelectionRect(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class CandidateItemDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: str = Field(min_length=1)
    ordinal: int = Field(ge=1, le=8)
    version_no: int = Field(ge=1)
    created_at: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    parameters: dict[str, Any]
    evaluation_status: Literal["evaluated", "not_evaluated", "failed"]
    short_evaluation: str | None = None
    anomalies: list[str] = Field(default_factory=list)


class CandidateGroupDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    id: str = Field(min_length=1)
    source_asset_id: str | None = None
    prompt_asset_id: str = Field(min_length=1)
    requested_count: int = Field(ge=1, le=8)
    status: Literal["created", "ready", "partial_ready", "selected", "cancelled"]
    items: list[CandidateItemDTO] = Field(min_length=1, max_length=8)
    selected_asset_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SelectionDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    id: str = Field(min_length=1)
    asset_id: str = Field(min_length=1)
    selection_type: Literal["rect", "multi_rect"]
    rects: list[SelectionRect] = Field(min_length=1)
    label: str = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    source: Literal["user", "agent"]
    status: Literal["draft", "edited", "confirmed"]
    confirmed_by_user: bool
    revision: int = Field(gt=0)
    visual_state: Literal["agent_suggested", "user_draft", "user_edited", "user_confirmed"]


class SelectionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    selection_id: str = Field(min_length=1)
    expected_revision: int = Field(ge=0)
    command_type: Literal[
        "create",
        "move",
        "resize_n",
        "resize_ne",
        "resize_e",
        "resize_se",
        "resize_s",
        "resize_sw",
        "resize_w",
        "resize_nw",
        "numeric",
        "clear",
        "undo",
        "redo",
        "confirm",
    ]
    rect: SelectionRect | None = None
    label: str | None = None
    event_id: str = Field(min_length=1)


class TripoParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_version: Literal["v3.1-20260211", "v3.0-20250812", "v2.5-20250123", "P1-20260311"] = (
        "v3.1-20260211"
    )
    texture_quality: Literal["standard", "detailed", "extreme"] = "standard"
    geometry_quality: Literal["standard", "detailed"] = "standard"
    texture_alignment: Literal["original_image", "geometry"] = "original_image"
    texture: bool = True
    pbr: bool = True
    quad: bool = False
    face_limit: int = Field(default=100_000, ge=1)
    auto_size: bool = False
    orientation: Literal["default", "align_image"] = "default"
    smart_low_poly: bool = False
    generate_parts: bool = False
    compress: Literal["", "geometry"] = ""
    enable_image_autofix: bool = False
    model_seed: int = 0
    texture_seed: int = 0


class TripoGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    mode: Literal["image", "multiview"]
    provider_profile: str = Field(min_length=1)
    model: str = Field(min_length=1)
    image_asset_id: str | None = None
    multiview_set_id: str | None = None
    view_asset_ids: dict[Literal["front", "side", "back"], str] = Field(default_factory=dict)
    parameters: TripoParameters

    @model_validator(mode="after")
    def _has_exactly_the_managed_inputs_for_its_mode(self) -> TripoGenerationRequest:
        if self.mode == "image" and (
            not self.image_asset_id or self.multiview_set_id is not None or self.view_asset_ids
        ):
            raise ValueError("image mode requires one image asset and no multiview assets")
        if self.mode == "multiview" and (
            self.image_asset_id is not None
            or not self.multiview_set_id
            or set(self.view_asset_ids) != {"front", "side", "back"}
        ):
            raise ValueError(
                "multiview mode requires a confirmed multiview set and front, side, and back managed assets"
            )
        return self


class Capability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    available: bool
    reason: str | None = None
    tool_name: str | None = None


class MeshSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    vertex_count: int = Field(ge=0)
    triangle_count: int = Field(ge=0)
    material_count: int = Field(ge=0)


class AnimationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    duration_seconds: float = Field(ge=0)


class ModelCapabilitySet(BaseModel):
    """Every B04 control gets an explicit availability decision from B02."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    standard_views: Capability
    camera_modes: Capability
    environment: Capability
    background: Capability
    wireframe: Capability
    material_channels: Capability
    animation: Capability
    inspect: Capability
    render_preview: Capability
    optimize: Capability
    regenerate: Capability
    open_containing_folder: Capability


class ModelInspection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    parseable: bool
    format: Literal["glb"]
    size_bytes: int = Field(ge=0)
    vertex_count: int | None = Field(default=None, ge=0)
    triangle_count: int | None = Field(default=None, ge=0)
    meshes: list[MeshSummary] = Field(default_factory=list)
    material_count: int | None = Field(default=None, ge=0)
    texture_count: int | None = Field(default=None, ge=0)
    bounds_xyz: tuple[float, float, float] | None = None
    bounds_unit: str | None = None
    skeleton_count: int | None = Field(default=None, ge=0)
    animations: list[AnimationSummary] = Field(default_factory=list)
    material_channels: dict[Literal["base_color", "normal", "roughness", "metalness"], Capability]
    capabilities: ModelCapabilitySet
    source_job_id: str | None = None
    local_relative_path: str = Field(min_length=1)
    diagnostics_ref: str | None = None

    @field_validator("local_relative_path")
    @classmethod
    def _relative_path_is_safe(cls, value: str) -> str:
        if value.startswith(("/", "\\")) or ":" in value or ".." in value.split("/"):
            raise ValueError("local_relative_path must be a safe relative path")
        return value


class ToolResult(BaseModel):
    """B02 view of the already-issued B01 ToolResultV1 one-of contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    ok: bool
    status: Literal["succeeded", "queued", "awaiting_ui_action", "failed"]
    tool_call_id: str = Field(min_length=1)
    output_asset_ids: list[str] = Field(default_factory=list)
    summary: str
    warnings: list[str] = Field(default_factory=list)
    expected_action: str | None = None
    ui_action: dict[str, Any] | None = None
    job: JobView | None = None
    error: ErrorDetail | None = None
    reused: bool = False

    def model_post_init(self, __context: Any, /) -> None:
        if self.status == "succeeded" and (
            not self.ok or any((self.expected_action, self.ui_action, self.job, self.error))
        ):
            raise ValueError("succeeded result may not carry action, job, or error")
        if self.status == "queued" and (
            not self.ok
            or self.job is None
            or any((self.expected_action, self.ui_action, self.error))
        ):
            raise ValueError("queued result requires exactly a job")
        if self.status == "awaiting_ui_action" and (
            not self.ok
            or not self.expected_action
            or self.ui_action is None
            or any((self.job, self.error))
        ):
            raise ValueError("awaiting_ui_action result requires persisted action")
        if self.status == "failed" and (
            self.ok or self.error is None or any((self.expected_action, self.ui_action, self.job))
        ):
            raise ValueError("failed result requires exactly an error")


class Issue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    issue_id: str = Field(min_length=1)
    code: Literal[
        "MV_SUBJECT_SCALE",
        "MV_DIRECTION",
        "MV_KEY_ACCESSORY",
        "MV_TRUNCATION",
        "MV_BACKGROUND",
        "MV_RESOLUTION",
        "MV_REGION_MISSING",
        "MV_REGION_OUT_OF_BOUNDS",
        "MV_REGION_OVERLAP",
        "MV_REGION_TOO_SMALL",
    ]
    view: Literal["front", "side", "back", "set"]
    check_status: Literal["passed", "warning", "blocking", "not_run"]
    explanation: str
    evidence_summary: str
    recommended_action: str


class MultiviewValidation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    severity: Literal["info", "warning", "blocking"]
    checks_run: list[
        Literal[
            "subject_scale", "direction", "key_accessory", "truncation", "background", "resolution"
        ]
    ]
    issues: list[Issue] = Field(default_factory=list)
    can_continue: bool
    rules_version: Literal[1] = 1
