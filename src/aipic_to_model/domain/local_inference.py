"""Public, secret-free contracts for locally hosted inference engines."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    TypeAdapter,
    model_validator,
)

LocalHealthReason = Literal[
    "not_checked",
    "runtime_not_configured",
    "runtime_unavailable",
    "model_not_installed",
    "response_invalid",
]

_HTTP_URL_ADAPTER = TypeAdapter(HttpUrl)


def _http_url(value: str) -> HttpUrl:
    return _HTTP_URL_ADAPTER.validate_python(value)


class LocalEngineKind(StrEnum):
    OLLAMA = "ollama"
    STABLE_DIFFUSION_CPP = "stable_diffusion_cpp"
    TRIPOSR = "triposr"


class LocalTransport(StrEnum):
    OPENAI_COMPATIBLE = "openai_compatible"
    CONTROLLED_PROCESS = "controlled_process"
    WORKER_STDIO = "worker_stdio"


class LocalCapability(StrEnum):
    AGENT_CHAT = "agent_chat"
    IMAGE_ANALYSIS = "image_analysis"
    TOOL_CALLING = "tool_calling"
    TEXT_TO_IMAGE = "text_to_image"
    IMAGE_TO_3D = "image_to_3d"


class LocalLicenseMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    identifier: str = Field(min_length=1, max_length=80)
    source_url: HttpUrl
    notice: str = Field(min_length=1, max_length=500)


class LocalProviderProfile(BaseModel):
    """A local model descriptor containing no filesystem path or credential."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    profile_id: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=120)
    engine: LocalEngineKind
    transport: LocalTransport
    model_id: str = Field(min_length=1, max_length=200)
    capabilities: tuple[LocalCapability, ...] = Field(min_length=1)
    endpoint: str | None = Field(default=None, max_length=300)
    runtime_capability_id: str | None = Field(default=None, min_length=1, max_length=200)
    license: LocalLicenseMetadata

    @model_validator(mode="after")
    def _transport_has_one_runtime_location(self) -> LocalProviderProfile:
        if self.transport is LocalTransport.OPENAI_COMPATIBLE:
            if not self.endpoint or self.runtime_capability_id is not None:
                raise ValueError("OpenAI-compatible local profiles require only an endpoint")
        elif not self.runtime_capability_id or self.endpoint is not None:
            raise ValueError("Local process profiles require only a runtime capability ID")
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("Local profile capabilities must be unique")
        return self


class LocalProviderHealth(BaseModel):
    """Redacted status safe for API responses, logs, and Agent runtime context."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    profile_id: str = Field(min_length=1)
    engine: LocalEngineKind
    model_id: str = Field(min_length=1)
    configured: bool
    available: bool
    reason: LocalHealthReason | None = None
    engine_version: str | None = Field(default=None, max_length=120)
    capabilities: tuple[LocalCapability, ...] = ()

    @model_validator(mode="after")
    def _available_status_has_no_reason(self) -> LocalProviderHealth:
        if self.available and (not self.configured or self.reason is not None):
            raise ValueError("Available local providers must be configured without a reason")
        if not self.available and self.reason is None:
            raise ValueError("Unavailable local providers require a reason")
        return self


def default_local_provider_profiles() -> tuple[LocalProviderProfile, ...]:
    return (
        LocalProviderProfile(
            profile_id="agent/ollama/qwen3-vl",
            label="Qwen3-VL (Ollama)",
            engine=LocalEngineKind.OLLAMA,
            transport=LocalTransport.OPENAI_COMPATIBLE,
            model_id="qwen3-vl:8b",
            endpoint="http://127.0.0.1:11434/v1",
            capabilities=(
                LocalCapability.AGENT_CHAT,
                LocalCapability.IMAGE_ANALYSIS,
                LocalCapability.TOOL_CALLING,
            ),
            license=LocalLicenseMetadata(
                identifier="Apache-2.0",
                source_url=_http_url("https://github.com/QwenLM/Qwen3-VL"),
                notice="Qwen3-VL code and published model repository declare Apache-2.0.",
            ),
        ),
        LocalProviderProfile(
            profile_id="image/local/z-image-turbo",
            label="Z-Image-Turbo",
            engine=LocalEngineKind.STABLE_DIFFUSION_CPP,
            transport=LocalTransport.CONTROLLED_PROCESS,
            model_id="Z-Image-Turbo",
            runtime_capability_id="local-runtime/stable-diffusion-cpp",
            capabilities=(LocalCapability.TEXT_TO_IMAGE,),
            license=LocalLicenseMetadata(
                identifier="Apache-2.0",
                source_url=_http_url("https://github.com/Tongyi-MAI/Z-Image"),
                notice="Z-Image-Turbo is used only for text-to-image in the first release.",
            ),
        ),
        LocalProviderProfile(
            profile_id="model3d/local/triposr",
            label="TripoSR",
            engine=LocalEngineKind.TRIPOSR,
            transport=LocalTransport.CONTROLLED_PROCESS,
            model_id="stabilityai/TripoSR",
            runtime_capability_id="local-runtime/triposr-worker",
            capabilities=(LocalCapability.IMAGE_TO_3D,),
            license=LocalLicenseMetadata(
                identifier="MIT",
                source_url=_http_url("https://github.com/VAST-AI-Research/TripoSR"),
                notice="TripoSR code and pretrained model are released under the MIT license.",
            ),
        ),
    )
