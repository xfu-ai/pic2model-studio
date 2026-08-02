"""Tripo asynchronous text/image-to-image adapter.

Only managed image bytes cross this boundary. Signed result URLs stay in
memory and are validated before they are downloaded.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import httpx

from ...domain.provider_models import ProviderResult
from .config import TripoImageSettings
from .http_errors import http_failure
from .tls import provider_http_client
from .transport_errors import transport_failure

_SUCCESS = {"success", "succeeded", "completed"}
_TERMINAL_FAILURES = {"failed", "cancelled", "canceled", "expired"}
_MAX_PROMPT_CHARACTERS = 1024


def _bounded_prompt(value: str) -> str:
    prompt = value.strip()
    if len(prompt) <= _MAX_PROMPT_CHARACTERS:
        return prompt
    clipped = prompt[:_MAX_PROMPT_CHARACTERS]
    boundary = clipped.rfind(" ")
    return clipped[:boundary].rstrip() if boundary > 0 else clipped.rstrip()


def _safe_host_token(host: str) -> str:
    if 0 < len(host) <= 253 and all(
        character.isascii() and (character.isalnum() or character in ".-")
        for character in host
    ):
        return host
    return "invalid"


def _has_supported_image_signature(content: bytes) -> bool:
    return (
        content.startswith(b"\x89PNG\r\n\x1a\n")
        or content.startswith(b"\xff\xd8\xff")
        or (len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP")
    )


def _request_id(response: httpx.Response) -> str | None:
    for name in ("x-request-id", "request-id", "x-tripo-request-id"):
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


def _nested(value: dict[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        current: Any = value
        for key in path:
            if not isinstance(current, dict) or key not in current:
                current = None
                break
            current = current[key]
        if current is not None:
            return current
    return None


def _failure(
    response: httpx.Response,
    operation: str,
    *,
    fee_incurred: bool = False,
) -> ProviderResult | None:
    if 200 <= response.status_code < 300:
        return None
    body = _json_object(response) or {}
    vendor_code = str(
        _nested(body, ("error", "code"), ("code",), ("data", "code")) or ""
    ).lower()
    return http_failure(
        operation=operation,
        status_code=response.status_code,
        request_id=_request_id(response),
        fee_incurred=fee_incurred,
        credits_exhausted=vendor_code
        in {"2010", "insufficient_credits", "credits_exhausted"},
        model_unavailable=vendor_code in {"model_not_found", "model_not_available"},
    )


class TripoTextToImageProvider:
    """Generate one Tripo image task per requested candidate.

    Text-only requests use Tripo's documented v2 ``text_to_image`` task.
    Reference-image and edit requests use the v2 ``generate_image`` task:
    the managed image is uploaded once, then referenced only by its opaque
    token while candidate tasks are created.
    """

    def __init__(
        self,
        settings: TripoImageSettings,
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
        """Use Tripo's account read endpoint; this does not create a paid task."""

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
        if request.get("channel") != "tripo" or mode not in {"t2i", "i2i"}:
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

        uploaded_input: tuple[str, str] | None = None
        if mode == "i2i":
            source = request.get("source_bytes")
            source_mime = request.get("source_mime")
            if not isinstance(source, bytes) or source_mime not in {
                "image/png",
                "image/jpeg",
                "image/webp",
            }:
                return http_failure(operation="generating")
            uploaded = self._upload_input(secret, source, str(source_mime))
            if isinstance(uploaded, ProviderResult):
                return uploaded
            uploaded_input = uploaded

        provider_prompt = _bounded_prompt(prompt)

        heartbeat = request.get("_heartbeat")
        cancelled = request.get("_cancelled")
        heartbeat_callback = heartbeat if callable(heartbeat) else lambda: None
        cancelled_callback = cancelled if callable(cancelled) else lambda: False
        images: list[dict[str, str]] = []
        request_id: str | None = None
        for _ in range(count):
            heartbeat_callback()
            if cancelled_callback():
                return ProviderResult(ok=False, stage="cancelled", retryable=False)
            created = self._create_and_wait(
                secret,
                mode=str(mode),
                prompt=provider_prompt,
                model=model.strip(),
                uploaded_input=uploaded_input,
                heartbeat=heartbeat_callback,
                cancelled=cancelled_callback,
            )
            if isinstance(created, ProviderResult):
                return created
            image_url, provider_request_id = created
            request_id = provider_request_id or request_id
            downloaded = self._download(image_url)
            if isinstance(downloaded, ProviderResult):
                return downloaded
            images.append({"base64": base64.b64encode(downloaded).decode("ascii")})
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
        mode: str,
        prompt: str,
        model: str,
        uploaded_input: tuple[str, str] | None,
        heartbeat: Callable[[], object],
        cancelled: Callable[[], object],
    ) -> tuple[str, str | None] | ProviderResult:
        if mode == "i2i":
            if uploaded_input is None:
                return http_failure(operation="creating")
            file_token, file_type = uploaded_input
            payload: dict[str, object] = {
                "type": "generate_image",
                "model_version": model,
                "prompt": prompt,
                "file": {"type": file_type, "file_token": file_token},
            }
            create_url = self._settings.advanced_image_task_url
            task_url = self._settings.advanced_image_task_url
        else:
            payload = {"type": "text_to_image", "prompt": prompt}
            create_url = self._settings.text_to_image_url
            task_url = self._settings.task_url
        heartbeat()
        try:
            response = self._client.post(
                create_url,
                headers={
                    "Authorization": f"Bearer {secret}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        except httpx.HTTPError as error:
            return transport_failure(error, operation="creating", paid_submission=True)
        failed = _failure(response, "creating")
        if failed is not None:
            return failed
        body = _json_object(response) or {}
        task_id = _nested(body, ("data", "task_id"), ("task_id",), ("data", "id"), ("id",))
        if not isinstance(task_id, str) or not task_id:
            return http_failure(
                operation="creating",
                request_id=_request_id(response),
                submission_ambiguous=True,
            )
        request_id = _request_id(response)
        for attempt in range(self._settings.max_poll_attempts):
            heartbeat()
            if cancelled():
                return ProviderResult(ok=False, stage="cancelled", retryable=False)
            try:
                polled = self._client.get(
                    f"{task_url}/{task_id}",
                    headers={"Authorization": f"Bearer {secret}"},
                )
            except httpx.TimeoutException:
                return http_failure(operation="remote_running", timed_out=True)
            except httpx.HTTPError as error:
                return transport_failure(error, operation="remote_running")
            failed = _failure(polled, "remote_running")
            if failed is not None:
                return failed
            task = _json_object(polled) or {}
            request_id = _request_id(polled) or request_id
            status = str(
                _nested(task, ("data", "status"), ("status",), ("data", "task_status")) or ""
            ).lower()
            if status in _SUCCESS:
                image_url = self._image_url(task)
                if image_url is None:
                    return http_failure(operation="remote_running", request_id=request_id)
                return image_url, request_id
            if status in _TERMINAL_FAILURES:
                return http_failure(operation="remote_running", request_id=request_id)
            if attempt + 1 < self._settings.max_poll_attempts:
                self._sleep(self._settings.poll_interval_seconds)
        return http_failure(operation="remote_running", timed_out=True)

    def _upload_input(
        self, secret: str, content: bytes, mime_type: str
    ) -> tuple[str, str] | ProviderResult:
        if not content or len(content) > 20 * 1024 * 1024:
            return http_failure(operation="uploading")
        file_type = {
            "image/png": "png",
            "image/jpeg": "jpeg",
            "image/webp": "webp",
        }.get(mime_type)
        if file_type is None:
            return http_failure(operation="uploading")
        extension = "jpg" if file_type == "jpeg" else file_type
        try:
            response = self._client.post(
                self._settings.advanced_image_upload_url,
                headers={"Authorization": f"Bearer {secret}"},
                files={"file": (f"managed-input.{extension}", content, mime_type)},
            )
        except httpx.HTTPError as error:
            return transport_failure(error, operation="uploading")
        failed = _failure(response, "uploading")
        if failed is not None:
            return failed
        body = _json_object(response) or {}
        token = _nested(
            body,
            ("data", "image_token"),
            ("data", "file_token"),
            ("image_token",),
            ("file_token",),
        )
        if not isinstance(token, str) or not token:
            return http_failure(operation="uploading", request_id=_request_id(response))
        return token, file_type

    @staticmethod
    def _image_url(task: dict[str, Any]) -> str | None:
        output = _nested(task, ("data", "output"), ("output",), ("data", "result"), ("result",))
        if isinstance(output, str):
            return output
        if isinstance(output, dict):
            for key in ("generated_image", "image", "image_url", "rendered_image"):
                value = output.get(key)
                if isinstance(value, str) and value:
                    return value
            for key in ("images", "image_urls"):
                values = output.get(key)
                if isinstance(values, list):
                    found = next((item for item in values if isinstance(item, str) and item), None)
                    if found:
                        return found
        return None

    def _download(self, image_url: str) -> bytes | ProviderResult:
        candidate = image_url.strip()
        if candidate.startswith("//"):
            candidate = f"https:{candidate}"
        elif "://" not in candidate and not candidate.startswith("/"):
            candidate = f"https://{candidate}"
        parsed = urlparse(candidate)
        host = (parsed.hostname or "").lower().rstrip(".")
        host_allowed = any(
            host == allowed_host or host.endswith(f".{allowed_host}")
            for allowed_host in self._settings.allowed_image_hosts
        )
        try:
            port = parsed.port
        except ValueError:
            port = -1
        url_allowed = (
            parsed.scheme.lower() in {"http", "https"}
            and host_allowed
            and parsed.username is None
            and parsed.password is None
            and port in {None, 443}
        )
        if not url_allowed:
            failure = http_failure(operation="downloading", fee_incurred=True)
            if failure.error is None:
                return failure
            detail = failure.error.model_copy(
                update={
                    "technical_message": (
                        "provider_result_url; "
                        f"scheme={parsed.scheme.lower() or 'missing'}; "
                        f"host={_safe_host_token(host)}; allowed=false"
                    )
                }
            )
            return failure.model_copy(update={"error": detail})
        secure_netloc = host if port is None else f"{host}:{port}"
        secure_image_url = parsed._replace(scheme="https", netloc=secure_netloc).geturl()
        try:
            response = self._client.get(secure_image_url)
        except httpx.HTTPError as error:
            return transport_failure(
                error,
                operation="downloading",
                fee_incurred=True,
            )
        failed = _failure(response, "downloading", fee_incurred=True)
        if failed is not None:
            return failed
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        content = response.content
        if not content or len(content) > 50 * 1024 * 1024:
            return http_failure(
                operation="downloading",
                request_id=_request_id(response),
                fee_incurred=True,
            )
        declared_image = content_type in {"image/png", "image/jpeg", "image/webp"}
        binary_image = content_type in {
            "application/octet-stream",
            "binary/octet-stream",
        } and _has_supported_image_signature(content)
        if not declared_image and not binary_image:
            return http_failure(
                operation="downloading",
                request_id=_request_id(response),
                fee_incurred=True,
            )
        return content
