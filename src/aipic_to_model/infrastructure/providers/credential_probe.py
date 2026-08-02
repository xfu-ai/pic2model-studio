"""Read-only Provider credential probes used by the desktop settings UI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from ...domain.provider_models import ProviderResult
from .config import GeminiSettings
from .http_errors import http_failure
from .tls import provider_http_client
from .transport_errors import transport_failure


def _request_id(response: httpx.Response) -> str | None:
    for name in ("x-request-id", "x-goog-request-id", "request-id"):
        value = response.headers.get(name)
        if value and len(value) <= 200:
            return value
    return None


def _probe_result(response: httpx.Response) -> ProviderResult:
    if not 200 <= response.status_code < 300:
        return http_failure(
            operation="probing",
            status_code=response.status_code,
            request_id=_request_id(response),
            retry_after_seconds=(
                _retry_after(response) if response.status_code == 429 else None
            ),
        )
    return ProviderResult(
        ok=True,
        provider_request_id=_request_id(response) or "provider-request-unavailable",
        stage="probing",
        retryable=False,
    )


def _retry_after(response: httpx.Response) -> int | None:
    try:
        value = int(response.headers.get("retry-after", ""))
    except ValueError:
        return None
    return value if 0 <= value <= 3600 else None


class GeminiCredentialProbe:
    """Validate a Gemini key by reading public metadata for the configured model."""

    def __init__(
        self,
        settings: GeminiSettings,
        credential: Callable[[], str | None],
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self._settings = settings
        self._credential = credential
        self._client = client or provider_http_client(
            timeout_seconds=min(settings.timeout_seconds, 10.0),
            follow_redirects=False,
        )

    def probe(self) -> ProviderResult:
        secret = self._credential()
        if not secret:
            return http_failure(operation="probing", configuration_missing=True)
        try:
            response = self._client.get(
                f"{self._settings.base_url}/models/{self._settings.text_model}",
                headers={"x-goog-api-key": secret},
                timeout=min(self._settings.timeout_seconds, 10.0),
            )
        except httpx.HTTPError as error:
            return transport_failure(error, operation="probing")
        return _probe_result(response)


@dataclass(frozen=True)
class DeepSeekProbeSettings:
    base_url: str = "https://api.deepseek.com"
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("DeepSeek base URL must be a plain HTTPS URL")
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))
        if not 1 <= self.timeout_seconds <= 30:
            raise ValueError("DeepSeek probe timeout is outside the approved range")

    @property
    def models_url(self) -> str:
        return f"{self.base_url}/models"


class DeepSeekCredentialProbe:
    """Validate a DeepSeek key through the non-generating models endpoint."""

    def __init__(
        self,
        settings: DeepSeekProbeSettings,
        credential: Callable[[], str | None],
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self._settings = settings
        self._credential = credential
        self._client = client or provider_http_client(
            timeout_seconds=settings.timeout_seconds,
            follow_redirects=False,
        )

    def probe(self) -> ProviderResult:
        secret = self._credential()
        if not secret:
            return http_failure(operation="probing", configuration_missing=True)
        try:
            response = self._client.get(
                self._settings.models_url,
                headers={"Authorization": f"Bearer {secret}"},
                timeout=self._settings.timeout_seconds,
            )
        except httpx.HTTPError as error:
            return transport_failure(error, operation="probing")
        return _probe_result(response)
