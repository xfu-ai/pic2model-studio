"""Scriptable offline providers used by B02 contract and fault tests.

No fake opens sockets.  Scenarios model all externally observable outcomes so
production code can be tested without credentials or a billable submission.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ...application.jobs.secure_download import DownloadResponse, UntrustedDownload
from ...domain.provider_models import (
    AnalysisRequest,
    AnalysisResult,
    ErrorCategory,
    ErrorDetail,
    ProviderResult,
    RecommendedAction,
    RemoteArtifactRef,
    RemoteTaskState,
)


@dataclass(frozen=True)
class FakeScenario:
    """A deterministic ProviderResult payload queued under an operation name."""

    operation: str
    outcome: str = "success"
    payload: dict[str, Any] | None = None
    request_id: str = "fake-request-1"


def _error(outcome: str) -> ErrorDetail:
    table = {
        "missing_config": (
            "PROVIDER_NOT_CONFIGURED",
            ErrorCategory.API_NOT_CONFIGURED,
            False,
            RecommendedAction.CONFIGURE_PROVIDER,
        ),
        "auth": (
            "PROVIDER_AUTH_FAILED",
            ErrorCategory.SERVICE_REJECTED,
            False,
            RecommendedAction.CONFIGURE_PROVIDER,
        ),
        "rate_limited": (
            "PROVIDER_RATE_LIMITED",
            ErrorCategory.SERVICE_REJECTED,
            True,
            RecommendedAction.RETRY,
        ),
        "unavailable": (
            "PROVIDER_UNAVAILABLE",
            ErrorCategory.SERVICE_REJECTED,
            True,
            RecommendedAction.RETRY,
        ),
        "timeout": ("JOB_TIMEOUT", ErrorCategory.TIMEOUT, True, RecommendedAction.RESUME),
        "unknown_submission": (
            "JOB_UNKNOWN_SUBMISSION",
            ErrorCategory.UNKNOWN,
            False,
            RecommendedAction.QUERY_REMOTE,
        ),
        "cancelled": ("JOB_CANCELLED", ErrorCategory.CANCELLED, False, RecommendedAction.NONE),
        "malicious_url": (
            "SECURITY_UNTRUSTED_URL",
            ErrorCategory.INPUT_INVALID,
            False,
            RecommendedAction.FIX_INPUT,
        ),
        "download_interrupted": (
            "DOWNLOAD_INTERRUPTED",
            ErrorCategory.TIMEOUT,
            True,
            RecommendedAction.RESUME,
        ),
        "cancel_unsupported": (
            "PROVIDER_CANCEL_UNSUPPORTED",
            ErrorCategory.SERVICE_REJECTED,
            False,
            RecommendedAction.STOP_WAITING,
        ),
        "bad_mime": (
            "IMAGE_DECODE_FAILED",
            ErrorCategory.FORMAT_UNSUPPORTED,
            False,
            RecommendedAction.FIX_INPUT,
        ),
    }
    code, category, retryable, action = table[outcome]
    return ErrorDetail(
        code=code,
        category=category,
        user_message="测试 Provider 已返回受控错误。",
        recoverable=retryable,
        retry_after_seconds=1 if outcome == "rate_limited" else None,
        failed_object="provider",
        failed_step="fake_provider",
        safe_to_retry=retryable,
        recommended_action=action,
    )


class ScriptedFakeProvider:
    """Shared queue primitive for all B02 Fake Providers."""

    def __init__(self, scenarios: Iterable[FakeScenario] = ()) -> None:
        self._scenarios: dict[str, deque[FakeScenario]] = defaultdict(deque)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        for scenario in scenarios:
            self._scenarios[scenario.operation].append(scenario)

    def _take(self, operation: str, payload: dict[str, Any]) -> FakeScenario:
        self.calls.append((operation, payload))
        return (
            self._scenarios[operation].popleft()
            if self._scenarios[operation]
            else FakeScenario(operation)
        )

    def _result(self, scenario: FakeScenario, stage: str) -> ProviderResult:
        if scenario.outcome == "success":
            return ProviderResult(
                ok=True,
                provider_request_id=scenario.request_id,
                stage=stage,
                payload=scenario.payload or {},
                retryable=False,
            )
        error = _error(scenario.outcome)
        return ProviderResult(
            ok=False,
            provider_request_id=scenario.request_id,
            stage=stage,
            payload=scenario.payload or {},
            retryable=error.recoverable,
            error=error,
        )


class FakeVisionAnalysisProvider(ScriptedFakeProvider):
    def analyze(self, request: AnalysisRequest) -> AnalysisResult | ProviderResult:
        scenario = self._take("vision.analyze", request.model_dump(mode="json"))
        result = self._result(scenario, "analyzing")
        if not result.ok:
            return result
        payload = result.payload
        return AnalysisResult(
            mode=request.mode,
            zh_text=payload.get("zh_text", "内容"),
            en_text=payload.get("en_text", "content"),
            zh_prompt=payload.get("zh_prompt", "中文提示"),
            en_prompt=payload.get("en_prompt", "English prompt"),
            dimensions=payload.get("dimensions", {}),
            suitability_issues=payload.get("suitability_issues", []),
            provider_request_id=result.provider_request_id or scenario.request_id,
            model=request.model,
        )


class FakeImageGenerationProvider(ScriptedFakeProvider):
    def generate(self, request: dict[str, Any]) -> ProviderResult:
        return self._result(self._take("image.generate", request), "generating")


class FakeFileTransferProvider(ScriptedFakeProvider):
    def prepare(self, request: dict[str, Any]) -> ProviderResult:
        return self._result(self._take("file.prepare", request), "uploading")

    def upload(
        self, *, asset_id: str, content_sha256: str, size_bytes: int, mime_type: str
    ) -> ProviderResult:
        return self.prepare(
            {
                "asset_id": asset_id,
                "content_sha256": content_sha256,
                "size_bytes": size_bytes,
                "mime_type": mime_type,
            }
        )


class FakeTripo3DProvider(ScriptedFakeProvider):
    def create(self, request: dict[str, Any], *, idempotency_key: str = "fake") -> ProviderResult:
        return self._result(
            self._take("tripo.create", {**request, "idempotency_key": idempotency_key}), "creating"
        )

    def get(self, external_task_id: str) -> RemoteTaskState | ProviderResult:
        scenario = self._take("tripo.get", {"external_task_id": external_task_id})
        result = self._result(scenario, "remote_running")
        if not result.ok:
            return result
        payload = result.payload
        return RemoteTaskState(
            external_task_id=external_task_id,
            status=payload.get("status", "succeeded"),
            progress=payload.get("progress"),
            artifacts=[RemoteArtifactRef(**item) for item in payload.get("artifacts", [])],
        )

    def cancel(self, external_task_id: str) -> ProviderResult:
        return self._result(
            self._take("tripo.cancel", {"external_task_id": external_task_id}), "cancel_requested"
        )

    def open_artifact(
        self, *, external_task_id: str, artifact: RemoteArtifactRef, offset: int
    ) -> DownloadResponse:
        scenario = self._take(
            "tripo.download",
            {
                "external_task_id": external_task_id,
                "artifact_id": artifact.artifact_id,
                "offset": offset,
            },
        )
        if scenario.outcome != "success":
            raise UntrustedDownload(_error(scenario.outcome).code)
        payload = scenario.payload or {}
        content = payload.get("content", _minimal_glb())
        if not isinstance(content, bytes):
            raise UntrustedDownload("MODEL3D_PARSE_FAILED")
        remaining = content[offset:]
        return DownloadResponse(
            url=str(payload.get("url", "https://artifacts.fake.example/model.glb")),
            resolved_ips=tuple(payload.get("resolved_ips", ("93.184.216.34",))),
            peer_ip=str(payload.get("peer_ip", "93.184.216.34")),
            status_code=206 if offset else 200,
            content_type=str(payload.get("content_type", "model/gltf-binary")),
            chunks=(remaining,),
            content_range=f"bytes {offset}-" if offset else None,
        )


def _minimal_glb() -> bytes:
    document = b'{"asset":{"version":"2.0"},"meshes":[]}'
    document += b" " * ((-len(document)) % 4)
    chunk = len(document).to_bytes(4, "little") + b"JSON" + document
    return b"glTF" + (2).to_bytes(4, "little") + (12 + len(chunk)).to_bytes(4, "little") + chunk


class FakeModelConversionProvider(ScriptedFakeProvider):
    def convert(self, request: dict[str, Any]) -> ProviderResult:
        return self._result(self._take("model.convert", request), "postprocessing")


class FakeModelOptimizationProvider(ScriptedFakeProvider):
    def optimize(self, request: dict[str, Any]) -> ProviderResult:
        return self._result(self._take("model.optimize", request), "postprocessing")
