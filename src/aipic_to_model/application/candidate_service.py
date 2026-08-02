"""Materialize provider image results as immutable B02 candidate assets."""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from ..domain.common import new_id
from ..domain.provider_models import GenerationRequest, ProviderResult
from .assets import AssetService


class _CandidateDraft:
    def __init__(
        self,
        asset_id: str,
        provider: str,
        model: str,
        parameters: dict[str, object],
        evaluation_status: str,
        short_evaluation: str | None,
        anomalies: tuple[str, ...],
    ) -> None:
        self.asset_id = asset_id
        self.provider = provider
        self.model = model
        self.parameters = parameters
        self.evaluation_status = evaluation_status
        self.short_evaluation = short_evaluation
        self.anomalies = anomalies


class CandidateService:
    """Never accepts a path or URL and never mutates the source image."""

    def __init__(self, assets: AssetService, repository: Any) -> None:
        self._assets = assets
        self._repository = repository

    def materialize_group(
        self,
        root: Path,
        project_id: str,
        request: GenerationRequest,
        result: ProviderResult,
        *,
        request_id: str,
        tool_call_id: str | None = None,
    ) -> dict[str, object]:
        if not result.ok:
            raise ProviderGenerationError(result)
        payloads = self._image_payloads(result.payload, request.candidate_count)
        parent_asset_id = request.source_asset_id
        input_asset_ids = [request.prompt_asset_id] + (
            [request.source_asset_id] if request.source_asset_id else []
        )
        assets: list[dict[str, Any]] = []
        for ordinal, payload in enumerate(payloads, start=1):
            temporary = root / "temp" / f"image-candidate-{new_id()}.{payload['suffix']}"
            try:
                temporary.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_bytes(payload["content"])
                asset = self._assets.register_derived(
                    root,
                    project_id,
                    temporary,
                    "generated_image",
                    f"{request_id}:candidate:{ordinal}",
                    parent_asset_id=parent_asset_id,
                    input_asset_ids=input_asset_ids,
                    name=f"generated-{ordinal}.{payload['suffix']}",
                    provenance={
                        "source_kind": "tool",
                        "prompt_asset_id": request.prompt_asset_id,
                        "tool_call_id": tool_call_id,
                        "provider_profile": request.provider_profile,
                        "model": request.model,
                        "parameters": request.model_dump(mode="json"),
                    },
                )
                assets.append(asset)
            finally:
                temporary.unlink(missing_ok=True)
        group_id = self._repository.create(
            root / "project.sqlite3",
            project_id=project_id,
            prompt_asset_id=request.prompt_asset_id,
            source_asset_id=request.source_asset_id,
            provider=request.channel,
            request=request.model_dump(mode="json"),
            items=[
                _CandidateDraft(
                    asset_id=str(asset["id"]),
                    provider=request.channel,
                    model=request.model,
                    parameters=request.model_dump(mode="json"),
                    evaluation_status=str(payload["evaluation_status"]),
                    short_evaluation=payload["short_evaluation"],
                    anomalies=tuple(payload["anomalies"]),
                )
                for asset, payload in zip(assets, payloads, strict=True)
            ],
        )
        return {"candidate_group_id": group_id, "asset_ids": [str(item["id"]) for item in assets]}

    def select(
        self,
        root: Path,
        project_id: str,
        group_id: str,
        asset_ids: list[str],
        selection_mode: str,
    ) -> None:
        self._repository.select(
            root / "project.sqlite3",
            project_id=project_id,
            group_id=group_id,
            asset_ids=asset_ids,
            selection_mode=selection_mode,
        )

    def materialize_edit(
        self,
        root: Path,
        project_id: str,
        *,
        operation: str,
        source_asset_id: str,
        provider_profile: str,
        model: str,
        result: ProviderResult,
        request_id: str,
        prompt_asset_id: str | None = None,
        selection_id: str | None = None,
        additional_input_asset_ids: list[str] | None = None,
        parameters: dict[str, object] | None = None,
        tool_call_id: str | None = None,
    ) -> dict[str, Any]:
        """Registers upscale/background/inpaint output as a new image asset."""
        if operation not in {
            "upscale",
            "remove_background",
            "inpaint_selection",
            "element_split",
            "export_transparent",
            "multiview_regenerate",
        }:
            raise ValueError("unsupported image edit operation")
        if not result.ok:
            raise ProviderGenerationError(result)
        image = self._image_payloads(result.payload, 1)[0]
        temporary = root / "temp" / f"image-edit-{new_id()}.{image['suffix']}"
        try:
            temporary.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(image["content"])
            return self._assets.register_derived(
                root,
                project_id,
                temporary,
                "generated_image",
                request_id,
                parent_asset_id=source_asset_id,
                input_asset_ids=[
                    source_asset_id,
                    *([prompt_asset_id] if prompt_asset_id else []),
                    *(additional_input_asset_ids or []),
                ],
                name=f"{operation}.{image['suffix']}",
                provenance={
                    "source_kind": "tool",
                    "prompt_asset_id": prompt_asset_id,
                    "selection_ids": [selection_id] if selection_id else [],
                    "tool_call_id": tool_call_id,
                    "provider_profile": provider_profile,
                    "model": model,
                    "parameters": {"operation": operation, **(parameters or {})},
                },
            )
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _image_payloads(payload: dict[str, Any], count: int) -> list[dict[str, Any]]:
        encoded = payload.get("images")
        if not isinstance(encoded, list) or len(encoded) != count:
            raise ValueError("provider must return exactly the requested image candidates")
        decoded: list[dict[str, Any]] = []
        for item in encoded:
            if not isinstance(item, dict) or set(item) - {
                "base64",
                "evaluation_status",
                "short_evaluation",
                "anomalies",
            }:
                raise ValueError("provider image response is invalid")
            raw = item.get("base64")
            if not isinstance(raw, str):
                raise TypeError("provider image is missing binary content")
            try:
                content = base64.b64decode(raw, validate=True)
                with Image.open(BytesIO(content)) as image:
                    image.verify()
                with Image.open(BytesIO(content)) as image:
                    image_format = image.format
            except (OSError, ValueError) as error:
                raise ValueError("provider returned an invalid image") from error
            suffix = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}.get(image_format or "")
            if suffix is None:
                raise ValueError("provider returned an unsupported image format")
            status = item.get("evaluation_status", "not_evaluated")
            if status not in {"evaluated", "not_evaluated", "failed"}:
                raise ValueError("provider returned an invalid evaluation status")
            anomalies = item.get("anomalies", [])
            if not isinstance(anomalies, list) or not all(
                isinstance(value, str) for value in anomalies
            ):
                raise ValueError("provider returned invalid anomalies")
            short = item.get("short_evaluation")
            if short is not None and not isinstance(short, str):
                raise ValueError("provider returned an invalid evaluation summary")
            decoded.append(
                {
                    "content": content,
                    "suffix": suffix,
                    "evaluation_status": status,
                    "short_evaluation": short,
                    "anomalies": anomalies,
                }
            )
        return decoded


class ProviderGenerationError(RuntimeError):
    """Preserves only the structured provider error, never a raw response."""

    def __init__(self, result: ProviderResult) -> None:
        self.error = result.error
        super().__init__(result.error.code if result.error else "PROVIDER_GENERATION_FAILED")
