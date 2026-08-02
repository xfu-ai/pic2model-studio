from __future__ import annotations

from typing import Any

from aipic_to_model.domain.provider_models import AnalysisRequest, ProviderResult
from aipic_to_model.infrastructure.providers.routing import (
    RoutedImageProvider,
    RoutedVisionProvider,
)


class _VisionStub:
    def __init__(self, name: str) -> None:
        self.name = name

    def analyze_image(self, *_args: Any, **_kwargs: Any) -> ProviderResult:
        return ProviderResult(
            ok=True,
            stage="analyzing",
            retryable=False,
            payload={"provider": self.name},
        )

    def rewrite(self, **_kwargs: Any) -> ProviderResult:
        return ProviderResult(
            ok=True,
            stage="rewriting",
            retryable=False,
            payload={"provider": self.name},
        )


class _ImageStub:
    def __init__(self, name: str) -> None:
        self.name = name

    def generate(self, _request: dict[str, object]) -> ProviderResult:
        return ProviderResult(
            ok=True,
            stage="generating",
            retryable=False,
            payload={"provider": self.name},
        )


def test_vision_router_selects_gemini_by_profile_or_model() -> None:
    router = RoutedVisionProvider(_VisionStub("fallback"), _VisionStub("gemini"))

    analyzed = router.analyze_image(
        AnalysisRequest(
            asset_id="asset",
            provider_profile="gemini/google/default",
            model="configured-by-profile",
            mode="content",
        ),
        image_bytes=b"image",
        mime_type="image/png",
    )
    rewritten = router.rewrite(
        prompt="old",
        instruction="new",
        model="gemini-flash-lite-latest",
    )

    assert analyzed.payload["provider"] == "gemini"
    assert rewritten.payload["provider"] == "gemini"


def test_image_router_preserves_openai_fallback_and_routes_gemini() -> None:
    router = RoutedImageProvider(_ImageStub("fallback"), _ImageStub("gemini"))

    gemini = router.generate(
        {
            "provider_profile": "gemini/google/default",
            "model": "configured-by-profile",
        }
    )
    fallback = router.generate(
        {
            "provider_profile": "openai/default",
            "model": "gpt-image-2",
        }
    )

    assert gemini.payload["provider"] == "gemini"
    assert fallback.payload["provider"] == "fallback"
