"""Provider-neutral analysis orchestration, ready for B02-03 Job handlers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from ..domain.common import new_id
from ..domain.provider_models import AnalysisRequest, AnalysisResult, ProviderResult
from .assets import AssetService
from .image_processing import compress_for_provider


class VisionAnalysisProvider(Protocol):
    def analyze(self, request: AnalysisRequest) -> AnalysisResult | ProviderResult: ...


class ProviderAnalysisError(TypeError):
    """Carry a redacted ProviderResult across the asset-service boundary."""

    def __init__(self, result: ProviderResult) -> None:
        self.result = result
        super().__init__(result.error.code if result.error is not None else "PROVIDER_RESPONSE_INVALID")


class AnalysisService:
    """Validates that failed analysis can never become an empty Prompt asset."""

    def __init__(self, provider: VisionAnalysisProvider) -> None:
        self._provider = provider

    def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        response = self._provider.analyze(request)
        if isinstance(response, ProviderResult):
            raise ProviderAnalysisError(response)
        if response.mode != request.mode or not response.provider_request_id:
            raise ValueError("分析 Provider 返回了不兼容的结果。")
        return response


class AnalysisAssetService:
    """Registers only complete structured analysis as a new managed asset."""

    def __init__(self, assets: AssetService, provider: VisionAnalysisProvider) -> None:
        self._assets = assets
        self._provider = provider

    def analyze_to_asset(
        self,
        root: Path,
        project_id: str,
        request: AnalysisRequest,
        *,
        request_id: str,
        tool_call_id: str | None = None,
    ) -> dict[str, object]:
        source = self._assets.get(root, project_id, request.asset_id)
        if source["asset_type"] not in {
            "source_image",
            "generated_image",
            "annotation",
            "crop",
            "multiview",
        }:
            raise ValueError("analysis requires a managed image asset")
        _, image_bytes, mime_type, _ = self._assets.read_content(
            root, project_id, request.asset_id, None
        )
        # Keep the original managed asset intact. The normalized JPEG is an
        # in-memory provider preview, which makes every vision request predictable
        # across source formats and transparent images.
        preview = compress_for_provider(image_bytes)
        image_bytes, mime_type = preview.content, preview.mime_type
        analyze_image = getattr(self._provider, "analyze_image", None)
        if callable(analyze_image):
            response = analyze_image(
                request,
                image_bytes=image_bytes,
                mime_type=mime_type,
            )
            if isinstance(response, ProviderResult):
                raise ProviderAnalysisError(response)
            if not isinstance(response, AnalysisResult):
                raise TypeError("分析 Provider 返回了不兼容的结果。")
            result = response
        else:
            result = AnalysisService(self._provider).analyze(request)
        # request_id is an idempotency token, never a path component.
        temporary = root / "temp" / f"analysis-{new_id()}.json"
        try:
            temporary.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(result.model_dump_json(), encoding="utf-8")
            return self._assets.register_derived(
                root,
                project_id,
                temporary,
                "analysis",
                request_id,
                parent_asset_id=request.asset_id,
                input_asset_ids=[request.asset_id],
                name=f"{request.mode}-analysis.json",
                provenance={
                    "source_kind": "tool",
                    "tool_call_id": tool_call_id,
                    "provider_profile": request.provider_profile,
                    "model": request.model,
                    "parameters": {
                        "mode": request.mode,
                        "input_normalization": {
                            "format": preview.format,
                            "mime_type": preview.mime_type,
                            "width": preview.width,
                            "height": preview.height,
                            "quality": preview.quality,
                        },
                    },
                },
            )
        finally:
            temporary.unlink(missing_ok=True)

    def read_result(self, root: Path, project_id: str, analysis_asset_id: str) -> AnalysisResult:
        _, content, _mime, _headers = self._assets.read_content(
            root, project_id, analysis_asset_id, None
        )
        return AnalysisResult.model_validate(json.loads(content.decode("utf-8")))
