"""Provider-neutral B02 request, result, and error contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class ErrorCategory(StrEnum):
    INPUT_INVALID = "input_invalid"
    API_NOT_CONFIGURED = "api_not_configured"
    SERVICE_REJECTED = "service_rejected"
    TIMEOUT = "timeout"
    FILE_MISSING = "file_missing"
    FORMAT_UNSUPPORTED = "format_unsupported"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class RecommendedAction(StrEnum):
    FIX_INPUT = "fix_input"
    CONFIGURE_PROVIDER = "configure_provider"
    RETRY = "retry"
    RESUME = "resume"
    QUERY_REMOTE = "query_remote"
    CONFIRM_NEW_SUBMISSION = "confirm_new_submission"
    STOP_WAITING = "stop_waiting"
    OPEN_DETAILS = "open_details"
    NONE = "none"


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    code: str = Field(min_length=1)
    category: ErrorCategory
    user_message: str = Field(min_length=1)
    recoverable: bool
    retry_after_seconds: int | None = Field(default=None, ge=0)
    technical_message: str | None = None
    details_ref: str | None = None
    failed_object: Literal["tool_call", "job", "asset", "selection", "provider", "model"]
    failed_step: str = Field(min_length=1)
    fee_incurred: bool | None = None
    preserved_asset_ids: list[str] = Field(default_factory=list)
    safe_to_retry: bool
    recommended_action: RecommendedAction


class ProviderResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    ok: bool
    provider_request_id: str | None = None
    stage: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    retryable: bool
    error: ErrorDetail | None = None


class RemoteInputRef(BaseModel):
    """Opaque reference only; signed URLs never leave a provider adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1)
    opaque_input_id: str = Field(min_length=1)
    kind: Literal["upload_token", "provider_file", "signed_reference"]
    expires_at: str | None = None


class RemoteArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str = Field(min_length=1)
    kind: Literal["glb", "render", "image"]
    host_fingerprint: str = Field(min_length=1)
    expected_size: int | None = Field(default=None, ge=0)
    etag_hash: str | None = None


class RemoteTaskState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    external_task_id: str = Field(min_length=1)
    status: Literal["queued", "running", "succeeded", "failed", "cancelled", "unknown"]
    progress: int | None = Field(default=None, ge=0, le=100)
    provider_eta_seconds: int | None = Field(default=None, ge=0)
    artifacts: list[RemoteArtifactRef] = Field(default_factory=list)
    fee_incurred: bool | None = None


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    asset_id: str = Field(min_length=1)
    provider_profile: str = Field(min_length=1)
    model: str = Field(min_length=1)
    mode: Literal["content", "style", "3d_suitability"]


class AnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    mode: Literal["content", "style", "3d_suitability"]
    zh_text: str | None = None
    en_text: str | None = None
    zh_prompt: str | None = None
    en_prompt: str | None = None
    preserve: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    dimensions: dict[str, str] = Field(default_factory=dict)
    suitability_issues: list[str] = Field(default_factory=list)
    raw_response: str | None = None
    parse_error: str | None = None
    provider_request_id: str = Field(min_length=1)
    model: str = Field(min_length=1)


class GenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    prompt_asset_id: str = Field(min_length=1)
    source_asset_id: str | None = None
    provider_profile: str = Field(min_length=1)
    channel: Literal["banana", "gpt_image", "meshy", "tripo"]
    mode: Literal["t2i", "i2i"]
    model: str = Field(min_length=1)
    candidate_count: int = Field(ge=1, le=8)
    aspect_ratio: str | None = None
    size: str | None = None
    quality: str | None = None
    output_format: Literal["png", "jpg", "webp"] | None = None
    structure_strength: float | None = Field(default=None, ge=0, le=1)


class UnsafeRemoteUrl(BaseModel):
    """Fixture-only value used to prove the downloader rejects bad URLs.

    Production adapters do not persist or expose this field.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    url: HttpUrl
