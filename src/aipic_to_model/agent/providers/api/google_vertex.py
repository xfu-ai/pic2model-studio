"""Google Vertex transport with API-key, ADC, and service-account auth."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import replace
from typing import Protocol

import httpx

from ...core.errors import ProviderError
from ...core.events import CancellationToken
from ...core.models import ProviderEvent
from ..base import ModelRequest
from .adapter_provider import AdapterProvider
from .google_credentials import GoogleAccessToken, GoogleCredentials


class GoogleTokenSource(Protocol):
    async def access_token(self) -> GoogleAccessToken | None: ...


class GoogleVertexProvider:
    def __init__(
        self,
        credential_resolver: Callable[[str], str | None],
        *,
        environment: Mapping[str, str] | None = None,
        credentials: GoogleTokenSource | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._credential_resolver = credential_resolver
        self._environment = dict(environment) if environment is not None else dict(os.environ)
        self._credentials = credentials or GoogleCredentials(self._environment)
        self._client = client

    async def stream(
        self, request: ModelRequest, cancellation: CancellationToken
    ) -> AsyncIterator[ProviderEvent]:
        cancellation.raise_if_cancelled()
        headers = dict(request.profile.headers)
        api_key = self._credential_resolver(
            request.profile.credential_ref or request.profile.provider_id
        )
        if api_key:
            headers["x-goog-api-key"] = api_key
        else:
            token = await self._credentials.access_token()
            if token is None:
                raise ProviderError("Google Vertex credentials are not configured.")
            headers["authorization"] = f"Bearer {token.token}"
        profile = replace(
            request.profile,
            base_url=_vertex_base_url(request.profile.base_url, self._environment),
            headers=headers,
        )
        transport = AdapterProvider("google-vertex", lambda _ref: None, client=self._client)
        async for event in transport.stream(replace(request, profile=profile), cancellation):
            yield event


def _vertex_base_url(base_url: str, environment: Mapping[str, str]) -> str:
    project = environment.get("GOOGLE_CLOUD_PROJECT") or environment.get("GCLOUD_PROJECT")
    location = environment.get("GOOGLE_CLOUD_LOCATION")
    if not project or not location:
        raise ProviderError(
            "Google Vertex requires GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_LOCATION."
        )
    if base_url and "{location}" not in base_url:
        return base_url.rstrip("/")
    return f"https://{location}-aiplatform.googleapis.com/v1/projects/{project}/locations/{location}/publishers/google"
