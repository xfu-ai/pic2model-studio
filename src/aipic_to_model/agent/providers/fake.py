from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from ..core.errors import ProviderError
from ..core.events import CancellationToken
from ..core.models import ProviderEvent
from .base import ModelRequest


@dataclass(frozen=True)
class ScriptedResponse:
    events: tuple[ProviderEvent, ...]


class FakeProvider:
    """Deterministic provider used by every Agent contract test."""

    def __init__(self, responses: tuple[ScriptedResponse, ...]) -> None:
        self._responses = list(responses)
        self.requests: list[ModelRequest] = []

    async def stream(
        self, request: ModelRequest, cancellation: CancellationToken
    ) -> AsyncIterator[ProviderEvent]:
        cancellation.raise_if_cancelled()
        self.requests.append(request)
        if not self._responses:
            raise ProviderError("Fake provider has no scripted response.")
        response = self._responses.pop(0)
        for event in response.events:
            cancellation.raise_if_cancelled()
            if event.error_message is not None and event.type.value == "provider_error":
                yield event
                return
            yield event
