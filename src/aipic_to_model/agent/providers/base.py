from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol

from ..core.events import CancellationToken
from ..core.models import Message, ProviderEvent


@dataclass(frozen=True)
class ModelCapabilities:
    context_window: int
    max_output_tokens: int
    input_modalities: tuple[str, ...] = ("text",)
    tool_calling: bool = False
    reasoning: bool = False
    cache: bool = False
    transport: tuple[str, ...] = ("sse",)


@dataclass(frozen=True)
class ModelProfile:
    provider_id: str
    model: str
    base_url: str
    credential_ref: str | None = None
    timeout_seconds: float = 60.0
    max_output_tokens: int | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelRequest:
    profile: ModelProfile
    messages: tuple[Message, ...]
    tools: tuple[dict[str, object], ...] = ()
    temperature: float | None = None
    max_output_tokens: int | None = None


class AgentModelProvider(Protocol):
    def stream(
        self, request: ModelRequest, cancellation: CancellationToken
    ) -> AsyncIterator[ProviderEvent]: ...
