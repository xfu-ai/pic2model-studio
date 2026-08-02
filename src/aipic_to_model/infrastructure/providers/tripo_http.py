"""Production HTTP adapters for managed Tripo uploads and task lifecycle.

Credentials, signed URLs, and raw response bodies stay inside these adapters.
Only opaque IDs and redacted provider metadata cross into the Job layer.
"""

from __future__ import annotations

import hashlib
import socket
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Literal, cast
from urllib.parse import urljoin, urlparse

import httpx

from ...application.image_processing import compress_for_provider
from ...application.jobs.secure_download import DownloadResponse, validate_artifact_url
from ...domain.provider_models import ProviderResult, RemoteArtifactRef, RemoteTaskState
from .http_errors import http_failure
from .tls import provider_http_client
from .transport_errors import transport_failure


class ProviderAdapterError(RuntimeError):
    """A deliberately body-free and URL-free adapter error."""


@dataclass(frozen=True)
class TripoHttpSettings:
    base_url: str
    allowed_artifact_hosts: frozenset[str]
    api_version: Literal["v2", "v3"] = "v3"
    upload_path: str = "/v3/files"
    task_path: str = "/v3/tasks"
    image_to_model_path: str = "/v3/generation/image-to-model"
    multiview_to_model_path: str = "/v3/generation/multiview-to-model"
    balance_path: str = "/v3/account/balance"
    timeout_seconds: float = 30.0
    maximum_upload_bytes: int = 20 * 1024 * 1024

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
            raise ValueError("Tripo base URL must be a plain HTTPS origin")
        if not self.allowed_artifact_hosts:
            raise ValueError("at least one artifact host must be approved")


def _request_id(response: httpx.Response) -> str | None:
    for key in ("x-request-id", "request-id", "x-tripo-request-id"):
        value = response.headers.get(key)
        if value and len(value) <= 200:
            return value
    return None


def _retry_after(response: httpx.Response) -> int | None:
    value = response.headers.get("retry-after")
    try:
        parsed = int(value) if value is not None else None
    except ValueError:
        return None
    return parsed if parsed is not None and 0 <= parsed <= 86_400 else None


def _json_object(response: httpx.Response) -> dict[str, Any] | None:
    try:
        value = response.json()
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _nested(data: dict[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        value: Any = data
        for key in path:
            if not isinstance(value, dict) or key not in value:
                break
            value = value[key]
        else:
            return value
    return None


def _status_result(response: httpx.Response, operation: str) -> ProviderResult | None:
    if 200 <= response.status_code < 300:
        return None
    data = _json_object(response)
    provider_code = data.get("code") if data is not None else None
    return http_failure(
        operation=operation,
        status_code=response.status_code,
        request_id=_request_id(response),
        retry_after_seconds=_retry_after(response),
        credits_exhausted=provider_code == 2010,
    )


class HttpFileTransferProvider:
    """Uploads bytes resolved from an already-managed asset ID."""

    def __init__(
        self,
        settings: TripoHttpSettings,
        credential: Callable[[], str | None],
        content_loader: Callable[[str], bytes],
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self._settings = settings
        self._credential = credential
        self._content_loader = content_loader
        self._client = client or provider_http_client(
            timeout_seconds=settings.timeout_seconds,
            follow_redirects=False,
        )

    def upload(
        self,
        *,
        asset_id: str,
        content_sha256: str,
        size_bytes: int,
        mime_type: str,
    ) -> ProviderResult:
        token = self._credential()
        if not token:
            return http_failure(operation="uploading", configuration_missing=True)
        content = self._content_loader(asset_id)
        accepted_mime_types = {
            "image/png",
            "image/jpeg",
            "image/webp",
            "image/bmp",
            "image/x-ms-bmp",
        }
        if (
            not isinstance(content, bytes)
            or len(content) != size_bytes
            or len(content) > self._settings.maximum_upload_bytes
            or hashlib.sha256(content).hexdigest() != content_sha256
            or mime_type not in accepted_mime_types
        ):
            return http_failure(operation="uploading")
        if mime_type not in {"image/png", "image/jpeg"}:
            try:
                normalized = compress_for_provider(content)
            except ValueError:
                return http_failure(operation="uploading")
            content = normalized.content
            mime_type = normalized.mime_type
            if len(content) > self._settings.maximum_upload_bytes:
                return http_failure(operation="uploading")
        extension = {"image/png": "png", "image/jpeg": "jpg"}[mime_type]
        try:
            response = self._client.post(
                urljoin(self._settings.base_url, self._settings.upload_path),
                headers={"Authorization": f"Bearer {token}"},
                files={"file": (f"managed-input.{extension}", content, mime_type)},
            )
        except httpx.TimeoutException:
            return http_failure(operation="uploading", timed_out=True)
        except httpx.HTTPError:
            return http_failure(operation="uploading", status_code=503)
        failed = _status_result(response, "uploading")
        if failed is not None:
            return failed
        data = _json_object(response)
        opaque_id = (
            _nested(
                data or {},
                ("data", "file_token"),
                ("data", "image_token"),
                ("data", "file_id"),
                ("file_token",),
                ("image_token",),
                ("file_id",),
                ("id",),
            )
            if data is not None
            else None
        )
        if not isinstance(opaque_id, str) or not opaque_id:
            return http_failure(operation="uploading", request_id=_request_id(response))
        return ProviderResult(
            ok=True,
            provider_request_id=_request_id(response),
            stage="uploading",
            retryable=False,
            payload={
                "remote_input": {
                    "provider": "tripo3d",
                    "opaque_input_id": opaque_id,
                    "kind": "upload_token",
                }
            },
        )


class HttpTripo3DProvider:
    """Tripo task adapter with in-memory-only signed artifact URLs."""

    def __init__(
        self,
        settings: TripoHttpSettings,
        credential: Callable[[], str | None],
        *,
        client: httpx.Client | None = None,
        resolver: Callable[[str], tuple[str, ...]] | None = None,
        poll_retry_attempts: int = 3,
        poll_retry_initial_delay_seconds: float = 0.25,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if poll_retry_attempts < 1:
            raise ValueError("poll retry attempts must be at least one")
        if poll_retry_initial_delay_seconds < 0:
            raise ValueError("poll retry delay must not be negative")
        self._settings = settings
        self._credential = credential
        self._client = client or provider_http_client(
            timeout_seconds=settings.timeout_seconds,
            follow_redirects=False,
        )
        self._resolver = resolver or self._resolve
        self._poll_retry_attempts = poll_retry_attempts
        self._poll_retry_initial_delay_seconds = poll_retry_initial_delay_seconds
        self._sleep = sleep
        self._artifact_urls: dict[tuple[str, str], str] = {}

    def balance(self) -> ProviderResult:
        token = self._credential()
        if not token:
            return http_failure(operation="checking_balance", configuration_missing=True)
        try:
            response = self._client.get(
                urljoin(self._settings.base_url, self._settings.balance_path),
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.TimeoutException:
            return http_failure(operation="checking_balance", timed_out=True)
        except httpx.HTTPError:
            return http_failure(operation="checking_balance", status_code=503)
        failed = _status_result(response, "checking_balance")
        if failed is not None:
            return failed
        data = _json_object(response)
        available = _nested(data or {}, ("data", "balance"), ("balance",))
        frozen = _nested(data or {}, ("data", "frozen"), ("frozen",))
        if (
            not isinstance(available, int | float)
            or isinstance(available, bool)
            or not isinstance(frozen, int | float)
            or isinstance(frozen, bool)
        ):
            return http_failure(
                operation="checking_balance",
                request_id=_request_id(response),
            )
        return ProviderResult(
            ok=True,
            provider_request_id=_request_id(response),
            stage="checking_balance",
            retryable=False,
            payload={"balance": float(available), "frozen": float(frozen)},
        )

    def create(self, payload: dict[str, object], *, idempotency_key: str) -> ProviderResult:
        token = self._credential()
        if not token:
            return http_failure(operation="creating", configuration_missing=True)
        if self._settings.api_version == "v3":
            if isinstance(payload.get("input"), str):
                path = self._settings.image_to_model_path
            elif isinstance(payload.get("inputs"), list):
                path = self._settings.multiview_to_model_path
            else:
                return http_failure(operation="creating")
            request_payload = dict(payload)
        else:
            path = self._settings.task_path
            request_payload = self._official_task_payload(payload)
        try:
            response = self._client.post(
                urljoin(self._settings.base_url, path),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Idempotency-Key": idempotency_key,
                    "Content-Type": "application/json",
                },
                json=request_payload,
            )
        except httpx.HTTPError as error:
            return transport_failure(error, operation="creating", paid_submission=True)
        failed = _status_result(response, "creating")
        if failed is not None:
            return failed
        data = _json_object(response)
        external_id = (
            _nested(data or {}, ("data", "task_id"), ("task_id",), ("data", "id"), ("id",))
            if data is not None
            else None
        )
        if not isinstance(external_id, str) or not external_id:
            return http_failure(
                operation="creating",
                request_id=_request_id(response),
                submission_ambiguous=True,
            )
        return ProviderResult(
            ok=True,
            provider_request_id=_request_id(response),
            stage="creating",
            retryable=False,
            payload={"external_task_id": external_id},
        )

    def get(self, external_task_id: str) -> RemoteTaskState | ProviderResult:
        response: tuple[dict[str, Any], str | None] | ProviderResult
        for attempt in range(self._poll_retry_attempts):
            response = self._task_request("GET", external_task_id, "remote_running")
            if not isinstance(response, ProviderResult):
                break
            error_code = response.error.code if response.error is not None else ""
            if error_code not in {"PROVIDER_UNAVAILABLE", "JOB_TIMEOUT"}:
                return response
            if attempt + 1 == self._poll_retry_attempts:
                return response
            self._sleep(self._poll_retry_initial_delay_seconds * (2**attempt))
        if isinstance(response, ProviderResult):
            return response
        data, request_id = response
        vendor = str(
            _nested(data, ("data", "status"), ("status",), ("data", "task_status")) or "unknown"
        ).lower()
        status = cast(
            Literal["queued", "running", "succeeded", "failed", "cancelled", "unknown"],
            {
                "queued": "queued",
                "pending": "queued",
                "running": "running",
                "processing": "running",
                "success": "succeeded",
                "succeeded": "succeeded",
                "completed": "succeeded",
                "failed": "failed",
                "cancelled": "cancelled",
                "canceled": "cancelled",
            }.get(vendor, "unknown"),
        )
        progress_value = _nested(data, ("data", "progress"), ("progress",))
        progress = (
            int(progress_value)
            if isinstance(progress_value, (int, float))
            and not isinstance(progress_value, bool)
            and 0 <= progress_value <= 100
            else None
        )
        artifacts = self._artifacts(external_task_id, data)
        del request_id
        return RemoteTaskState(
            external_task_id=external_task_id,
            status=status,
            progress=progress,
            artifacts=artifacts,
        )

    def cancel(self, external_task_id: str) -> ProviderResult:
        token = self._credential()
        if not token:
            return http_failure(operation="cancel_requested", configuration_missing=True)
        try:
            response = self._client.post(
                f"{self._task_url()}/{external_task_id}/cancel",
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.TimeoutException:
            return http_failure(operation="cancel_requested", timed_out=True)
        except httpx.HTTPError:
            return http_failure(operation="cancel_requested", status_code=503)
        if response.status_code in {404, 405, 409, 501}:
            from ...domain.provider_models import (
                ErrorCategory,
                ErrorDetail,
                RecommendedAction,
            )

            detail = ErrorDetail(
                code="PROVIDER_CANCEL_UNSUPPORTED",
                category=ErrorCategory.SERVICE_REJECTED,
                user_message="The Provider cannot cancel this task.",
                recoverable=False,
                failed_object="provider",
                failed_step="cancel_requested",
                fee_incurred=None,
                safe_to_retry=False,
                recommended_action=RecommendedAction.STOP_WAITING,
            )
            return ProviderResult(
                ok=False,
                provider_request_id=_request_id(response),
                stage="cancel_requested",
                retryable=False,
                error=detail,
            )
        failed = _status_result(response, "cancel_requested")
        if failed is not None:
            return failed
        return ProviderResult(
            ok=True,
            provider_request_id=_request_id(response),
            stage="cancel_requested",
            retryable=False,
        )

    def open_artifact(
        self, *, external_task_id: str, artifact: RemoteArtifactRef, offset: int
    ) -> DownloadResponse:
        url = self._artifact_urls.get((external_task_id, artifact.artifact_id))
        if url is None:
            refreshed = self.get(external_task_id)
            if isinstance(refreshed, ProviderResult):
                raise ProviderAdapterError("PROVIDER_UNAVAILABLE")
            url = self._artifact_urls.get((external_task_id, artifact.artifact_id))
        if url is None:
            raise ProviderAdapterError("REMOTE_ARTIFACT_NOT_FOUND")
        parsed = urlparse(url)
        host = parsed.hostname or ""
        resolved = self._resolver(host)
        validate_artifact_url(
            url,
            allowed_hosts=self._settings.allowed_artifact_hosts,
            resolved_ips=resolved,
        )
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        request = self._client.build_request("GET", url, headers=headers)
        try:
            response = self._client.send(request, stream=True, follow_redirects=False)
        except httpx.HTTPError as error:
            raise ProviderAdapterError("DOWNLOAD_INTERRUPTED") from error
        second_resolution = self._resolver(host)
        if set(second_resolution) != set(resolved):
            response.close()
            raise ProviderAdapterError("SECURITY_UNTRUSTED_URL")
        peer_ip = self._peer_ip(response) or resolved[0]
        return DownloadResponse(
            url=url,
            resolved_ips=resolved,
            peer_ip=peer_ip,
            status_code=response.status_code,
            content_type=response.headers.get("content-type"),
            content_range=response.headers.get("content-range"),
            chunks=self._closing_chunks(response),
        )

    def _task_request(
        self, method: str, external_task_id: str, operation: str
    ) -> tuple[dict[str, Any], str | None] | ProviderResult:
        token = self._credential()
        if not token:
            return http_failure(operation=operation, configuration_missing=True)
        try:
            response = self._client.request(
                method,
                f"{self._task_url()}/{external_task_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.TimeoutException:
            return http_failure(operation=operation, timed_out=True)
        except httpx.HTTPError:
            return http_failure(operation=operation, status_code=503)
        failed = _status_result(response, operation)
        if failed is not None:
            return failed
        data = _json_object(response)
        if data is None:
            return http_failure(operation=operation, request_id=_request_id(response))
        return data, _request_id(response)

    def _artifacts(self, external_task_id: str, data: dict[str, Any]) -> list[RemoteArtifactRef]:
        candidates = _nested(data, ("data", "artifacts"), ("artifacts",), ("data", "output"))
        if isinstance(candidates, dict):
            expanded: list[dict[str, Any]] = []
            for key in ("model", "base_model", "pbr_model", "rendered_image", "generated_image"):
                value = candidates.get(key)
                if isinstance(value, str):
                    expanded.append(
                        {
                            "id": key,
                            "kind": "glb" if "model" in key else "render",
                            "url": value,
                        }
                    )
            candidates = expanded or [candidates]
        if not isinstance(candidates, list):
            candidates = []
        refs: list[RemoteArtifactRef] = []
        for index, item in enumerate(candidates):
            if not isinstance(item, dict):
                continue
            url = item.get("url") or item.get("model_url")
            if not isinstance(url, str):
                continue
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            if (
                "*" not in self._settings.allowed_artifact_hosts
                and host not in self._settings.allowed_artifact_hosts
            ):
                continue
            artifact_id = str(item.get("id") or item.get("artifact_id") or f"artifact-{index}")
            kind_value = str(item.get("kind") or item.get("type") or "glb").lower()
            if kind_value not in {"glb", "render", "image"}:
                kind_value = "glb" if urlparse(url).path.lower().endswith(".glb") else "render"
            kind = cast(Literal["glb", "render", "image"], kind_value)
            size = item.get("size") or item.get("size_bytes")
            expected_size = (
                int(size)
                if isinstance(size, int) and not isinstance(size, bool) and size >= 0
                else None
            )
            etag = item.get("etag")
            self._artifact_urls[(external_task_id, artifact_id)] = url
            refs.append(
                RemoteArtifactRef(
                    artifact_id=artifact_id,
                    kind=kind,
                    host_fingerprint=hashlib.sha256(host.encode()).hexdigest()[:16],
                    expected_size=expected_size,
                    etag_hash=(
                        hashlib.sha256(str(etag).encode()).hexdigest() if etag is not None else None
                    ),
                )
            )
        return refs

    @staticmethod
    def _official_task_payload(payload: dict[str, object]) -> dict[str, object]:
        """Translate the stable provider-neutral body at the HTTP boundary."""
        converted = dict(payload)
        model = converted.pop("model", None)
        if isinstance(model, str) and model:
            converted["model_version"] = model
        single = converted.pop("input", None)
        if isinstance(single, str) and single:
            converted.update(
                {
                    "type": "image_to_model",
                    "file": {"type": "png", "file_token": single},
                }
            )
            return converted
        multiview = converted.pop("inputs", None)
        if isinstance(multiview, list):
            tokens: list[str | None] = []
            for key, item in zip(("front", "left", "back"), multiview, strict=False):
                token = item.get(key) if isinstance(item, dict) else None
                tokens.append(token if isinstance(token, str) and token else None)
            if len(tokens) == 3 and all(tokens):
                converted.update(
                    {
                        "type": "multiview_to_model",
                        "files": [
                            *[{"type": "png", "file_token": token} for token in tokens],
                            {"type": "png"},
                        ],
                    }
                )
                return converted
        return converted

    def _task_url(self) -> str:
        return urljoin(self._settings.base_url, self._settings.task_path)

    @staticmethod
    def _resolve(host: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                {str(item[4][0]) for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
            )
        )

    @staticmethod
    def _peer_ip(response: httpx.Response) -> str | None:
        stream = response.extensions.get("network_stream")
        if stream is None or not hasattr(stream, "get_extra_info"):
            return None
        for key in ("server_addr", "peername"):
            value = stream.get_extra_info(key)
            if isinstance(value, tuple) and value and isinstance(value[0], str):
                return value[0]
        return None

    @staticmethod
    def _closing_chunks(response: httpx.Response) -> Iterator[bytes]:
        try:
            yield from response.iter_bytes()
        finally:
            response.close()
