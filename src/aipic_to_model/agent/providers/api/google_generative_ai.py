"""Native authentication wrapper for the Google Generative AI adapter."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import replace

import httpx

from ...core.errors import ProviderError
from ...core.events import CancellationToken
from ...core.models import ProviderEvent
from ..base import ModelRequest
from .adapter_provider import AdapterProvider


class GoogleGenerativeAIProvider:
    """Google AI Studio uses ``x-goog-api-key``, never a Bearer API key."""

    def __init__(
        self,
        credential_resolver: Callable[[str], str | None],
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._credential_resolver = credential_resolver
        self._client = client

    async def stream(
        self, request: ModelRequest, cancellation: CancellationToken
    ) -> AsyncIterator[ProviderEvent]:
        key = self._credential_resolver(
            request.profile.credential_ref or request.profile.provider_id
        )
        if not key:
            raise ProviderError("Google Generative AI credentials are not configured.")
        profile = replace(
            request.profile,
            headers={**request.profile.headers, "x-goog-api-key": key},
        )
        transport = AdapterProvider("google-generative-ai", lambda _ref: None, client=self._client)
        async for event in transport.stream(replace(request, profile=profile), cancellation):
            yield event
