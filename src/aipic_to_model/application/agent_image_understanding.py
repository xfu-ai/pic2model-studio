"""Direct visual understanding for the text-only Agent.

Unlike production content/style analysis, this service never creates an analysis
asset or changes workflow context. Its only output is grounded text returned to
the calling Agent tool.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from ..domain.provider_models import ProviderResult
from ..domain.tools import ToolResultV1
from .image_processing import compress_for_provider


class AgentImageUnderstandingProvider(Protocol):
    def understand_image(
        self,
        *,
        asset_id: str,
        question: str,
        model: str,
        image_bytes: bytes,
        mime_type: str,
    ) -> ProviderResult: ...


class AgentImageUnderstandingService:
    """Read one managed image and return a provider's plain-language answer."""

    def __init__(self, assets: Any, provider: AgentImageUnderstandingProvider) -> None:
        self._assets = assets
        self._provider = provider

    def understand(
        self,
        root: Path,
        project_id: str,
        arguments: dict[str, object],
        call_id: str,
    ) -> ToolResultV1:
        asset_id = str(arguments["asset_id"])
        question = str(arguments["question"]).strip()
        model = str(arguments["model"])
        asset = self._assets.get(root, project_id, asset_id)
        if asset["asset_type"] not in {
            "source_image",
            "generated_image",
            "annotation",
            "crop",
            "multiview",
        }:
            return self._failure(
                call_id,
                "IMAGE_UNDERSTANDING_INPUT_INVALID",
                "The selected managed asset is not an image.",
                recoverable=False,
                safe_to_retry=False,
            )
        _, content, _mime_type, _headers = self._assets.read_content(
            root, project_id, asset_id, None
        )
        preview = compress_for_provider(content)
        result = self._provider.understand_image(
            asset_id=asset_id,
            question=question,
            model=model,
            image_bytes=preview.content,
            mime_type=preview.mime_type,
        )
        if not result.ok:
            return self._provider_failure(call_id, result)
        text = result.payload.get("text")
        if not isinstance(text, str) or not text.strip():
            return self._failure(
                call_id,
                "PROVIDER_RESPONSE_INVALID",
                "The image understanding provider returned no usable text.",
                recoverable=True,
                safe_to_retry=True,
            )
        return ToolResultV1(
            True,
            "succeeded",
            call_id,
            [],
            json.dumps(
                {"text": text.strip(), "provider_request_id": result.provider_request_id},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            [],
        )

    @staticmethod
    def _provider_failure(call_id: str, result: ProviderResult) -> ToolResultV1:
        error = result.error
        if error is None:
            return AgentImageUnderstandingService._failure(
                call_id,
                "PROVIDER_RESPONSE_INVALID",
                "The image understanding provider failed without an error detail.",
                recoverable=bool(result.retryable),
                safe_to_retry=bool(result.retryable),
            )
        return ToolResultV1(
            False,
            "failed",
            call_id,
            [],
            error.user_message,
            [],
            error={
                "code": error.code,
                "category": error.category.value,
                "user_message": error.user_message,
                "recoverable": error.recoverable,
                "failed_object": error.failed_object,
                "failed_step": error.failed_step,
                "fee_incurred": error.fee_incurred,
                "preserved_asset_ids": error.preserved_asset_ids,
                "safe_to_retry": error.safe_to_retry,
                "recommended_action": error.recommended_action.value,
                "retry_after_seconds": error.retry_after_seconds,
            },
        )

    @staticmethod
    def _failure(
        call_id: str,
        code: str,
        message: str,
        *,
        recoverable: bool,
        safe_to_retry: bool,
    ) -> ToolResultV1:
        return ToolResultV1(
            False,
            "failed",
            call_id,
            [],
            message,
            [],
            error={
                "code": code,
                "category": "input_invalid",
                "user_message": message,
                "recoverable": recoverable,
                "failed_object": "asset",
                "failed_step": "understand_image",
                "safe_to_retry": safe_to_retry,
                "recommended_action": "fix_input" if not safe_to_retry else "retry",
            },
        )
