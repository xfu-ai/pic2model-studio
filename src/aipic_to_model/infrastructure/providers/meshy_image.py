"""Meshy asynchronous 2D text-to-image adapter.

Meshy returns signed image URLs only while a task result is being read.  This
adapter downloads those URLs in memory and returns base64 image data, so task
and asset layers never persist provider URLs or credentials.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import httpx

from ...domain.provider_models import ProviderResult
from .config import MeshyImageSettings
from .http_errors import http_failure
from .tls import provider_http_client
from .transport_errors import transport_failure

_TERMINAL_FAILURES = {"failed", "cancelled", "canceled", "expired"}
_SUCCESS = {"succeeded", "success", "completed"}


def _request_id(response: httpx.Response) -> str | None:
    for name in ("x-request-id", "request-id", "x-meshy-request-id"):
        value = response.headers.get(name)
        if value and len(value) <= 200:
            return value
    return None


def _json_object(response: httpx.Response) -> dict[str, Any] | None:
    try:
        value = response.json()
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _retry_after(response: httpx.Response) -> int | None:
    try:
        value = int(response.headers.get("retry-after", ""))
    except ValueError:
        return None
    return value if 0 <= value <= 86_400 else None


def _failure(response: httpx.Response, operation: str, *, ambiguous: bool = False) -> ProviderResult | None:
    if 200 <= response.status_code < 300:
        return None
    body = _json_object(response) or {}
    error = body.get("error")
    code = str(error.get("code") if isinstance(error, dict) else body.get("code") or "").lower()
    return http_failure(
        operation=operation,
        status_code=response.status_code,
        request_id=_request_id(response),
        retry_after_seconds=_retry_after(response),
        submission_ambiguous=ambiguous,
        credits_exhausted=code in {"insufficient_credits", "insufficient_credit", "credits_exhausted"},
        model_unavailable=code in {"model_not_found", "model_not_available"},
    )


class MeshyTextToImageProvider:
    """Materialize Meshy text-to-image tasks as the app's image result contract."""

    def __init__(
        self,
        settings: MeshyImageSettings,
        credential: Callable[[], str | None],
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._credential = credential
        self._client = client or provider_http_client(
            timeout_seconds=settings.timeout_seconds,
            follow_redirects=False,
        )
        self._sleep = sleep

    def probe(self) -> ProviderResult:
        """Read the Meshy balance endpoint without creating a paid task."""

        secret = self._credential()
        if not secret:
            return http_failure(operation="probing", configuration_missing=True)
        try:
            response = self._client.get(
                self._settings.balance_url,
                headers={"Authorization": f"Bearer {secret}"},
                timeout=min(self._settings.timeout_seconds, 10.0),
            )
        except httpx.HTTPError as error:
            return transport_failure(error, operation="probing")
        failed = _failure(response, "probing")
        if failed is not None:
            return failed
        if _json_object(response) is None:
            return http_failure(operation="probing", request_id=_request_id(response))
        return ProviderResult(
            ok=True,
            provider_request_id=_request_id(response) or "provider-request-unavailable",
            stage="probing",
            retryable=False,
        )

    def generate(self, request: dict[str, object]) -> ProviderResult:
        secret = self._credential()
        if not secret:
            return http_failure(operation="generating", configuration_missing=True)
        mode = request.get("mode")
        if request.get("channel") != "meshy" or mode not in {"t2i", "i2i"}:
            return http_failure(operation="generating")
        count = request.get("candidate_count")
        prompt = request.get("prompt")
        model = request.get("model")
        if (
            not isinstance(count, int)
            or isinstance(count, bool)
            or not 1 <= count <= 8
            or not isinstance(prompt, str)
            or not prompt.strip()
            or not isinstance(model, str)
            or not model.strip()
        ):
            return http_failure(operation="generating")
        aspect_ratio = request.get("aspect_ratio")
        if aspect_ratio is not None and not isinstance(aspect_ratio, str):
            return http_failure(operation="generating")
        source = request.get("source_bytes")
        mime_type = request.get("source_mime")
        heartbeat = request.get("_heartbeat")
        cancelled = request.get("_cancelled")
        heartbeat_callback = heartbeat if callable(heartbeat) else lambda: None
        cancelled_callback = cancelled if callable(cancelled) else lambda: False
        if mode == "i2i" and (
            not isinstance(source, bytes)
            or not source
            or mime_type not in {"image/png", "image/jpeg"}
        ):
            return http_failure(operation="generating")

        images: list[dict[str, str]] = []
        request_id: str | None = None
        for _ in range(count):
            heartbeat_callback()
            if cancelled_callback():
                return ProviderResult(ok=False, stage="cancelled", retryable=False)
            created = self._create_and_wait(
                secret,
                model=model.strip(),
                prompt=prompt.strip(),
                aspect_ratio=aspect_ratio.strip() if isinstance(aspect_ratio, str) else None,
                source_bytes=source if isinstance(source, bytes) else None,
                source_mime=mime_type if isinstance(mime_type, str) else None,
                heartbeat=heartbeat_callback,
                cancelled=cancelled_callback,
            )
            if isinstance(created, ProviderResult):
                return created
            task_id, image_url, provider_request_id = created
            request_id = provider_request_id or request_id
            heartbeat_callback()
            if cancelled_callback():
                return ProviderResult(ok=False, stage="cancelled", retryable=False)
            image = self._download_image(secret, task_id, image_url)
            if isinstance(image, ProviderResult):
                return image
            heartbeat_callback()
            if cancelled_callback():
                return ProviderResult(ok=False, stage="cancelled", retryable=False)
            images.append({"base64": base64.b64encode(image).decode("ascii")})
        return ProviderResult(
            ok=True,
            provider_request_id=request_id or "provider-request-unavailable",
            stage="generating",
            retryable=False,
            payload={"images": images},
        )

    def _create_and_wait(
        self,
        secret: str,
        *,
        model: str,
        prompt: str,
        aspect_ratio: str | None,
        source_bytes: bytes | None,
        source_mime: str | None,
        heartbeat: Callable[[], object],
        cancelled: Callable[[], object],
    ) -> tuple[str, str, str | None] | ProviderResult:
        payload: dict[str, object] = {"ai_model": model, "prompt": prompt}
        if aspect_ratio:
            payload["aspect_ratio"] = aspect_ratio
        url = self._settings.text_to_image_url
        if source_bytes is not None and source_mime is not None:
            payload["reference_image_urls"] = [
                f"data:{source_mime};base64,{base64.b64encode(source_bytes).decode('ascii')}"
            ]
            url = self._settings.image_to_image_url
        heartbeat()
        if cancelled():
            return ProviderResult(ok=False, stage="cancelled", retryable=False)
        try:
            response = self._client.post(
                url,
                headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"},
                json=payload,
            )
        except httpx.HTTPError as error:
            return transport_failure(error, operation="creating", paid_submission=True)
        failed = _failure(response, "creating")
        if failed is not None:
            return failed
        body = _json_object(response) or {}
        task_id = body.get("result") or body.get("id")
        if isinstance(task_id, dict):
            task_id = task_id.get("id")
        if not isinstance(task_id, str) or not task_id:
            return http_failure(operation="creating", request_id=_request_id(response), submission_ambiguous=True)
        return self._wait_for_image(
            secret,
            task_id,
            _request_id(response),
            url,
            heartbeat=heartbeat,
            cancelled=cancelled,
        )

    def _wait_for_image(
        self,
        secret: str,
        task_id: str,
        request_id: str | None,
        task_url: str,
        *,
        heartbeat: Callable[[], object],
        cancelled: Callable[[], object],
    ) -> tuple[str, str, str | None] | ProviderResult:
        for attempt in range(self._settings.max_poll_attempts):
            heartbeat()
            if cancelled():
                return ProviderResult(ok=False, stage="cancelled", retryable=False)
            try:
                response = self._client.get(
                    f"{task_url}/{task_id}",
                    headers={"Authorization": f"Bearer {secret}"},
                )
            except httpx.TimeoutException:
                return http_failure(operation="remote_running", timed_out=True)
            except httpx.HTTPError:
                return http_failure(operation="remote_running", status_code=503)
            failed = _failure(response, "remote_running")
            if failed is not None:
                return failed
            body = _json_object(response) or {}
            request_id = _request_id(response) or request_id
            status = str(body.get("status") or "").lower()
            urls = body.get("image_urls")
            if status in _SUCCESS and isinstance(urls, list):
                image_url = next((item for item in urls if isinstance(item, str) and item), None)
                if image_url:
                    return task_id, image_url, request_id
                return http_failure(operation="remote_running", request_id=request_id)
            if status in _TERMINAL_FAILURES:
                return http_failure(operation="remote_running", request_id=request_id)
            if attempt + 1 < self._settings.max_poll_attempts:
                self._sleep(self._settings.poll_interval_seconds)
        return http_failure(operation="remote_running", timed_out=True)

    def _download_image(self, secret: str, task_id: str, image_url: str) -> bytes | ProviderResult:
        del task_id  # Kept only to make this boundary explicit in call sites.
        parsed = urlparse(image_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or host not in self._settings.allowed_image_hosts:
            return http_failure(operation="downloading")
        try:
            response = self._client.get(image_url, headers={"Authorization": f"Bearer {secret}"})
        except httpx.TimeoutException:
            return http_failure(operation="downloading", timed_out=True)
        except httpx.HTTPError:
            return http_failure(operation="downloading", status_code=503)
        failed = _failure(response, "downloading")
        if failed is not None:
            return failed
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type not in {"image/png", "image/jpeg", "image/webp"} or not response.content:
            return http_failure(operation="downloading", request_id=_request_id(response))
        if len(response.content) > 50 * 1024 * 1024:
            return http_failure(operation="downloading", request_id=_request_id(response))
        return response.content
