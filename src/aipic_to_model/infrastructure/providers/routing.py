"""Configuration-driven routing across interchangeable production Providers."""

from __future__ import annotations

from typing import Any

from ...domain.provider_models import AnalysisRequest, AnalysisResult, ProviderResult
from .config import GEMINI_PROFILE, MESHY_PROFILE


def _uses_gemini(profile: object, model: object) -> bool:
    return profile == GEMINI_PROFILE or (
        isinstance(model, str) and model.lower().startswith("gemini-")
    )


def _uses_meshy(profile: object, channel: object) -> bool:
    return profile == MESHY_PROFILE or channel == "meshy"


class RoutedVisionProvider:
    def __init__(self, fallback: Any, gemini: Any | None = None) -> None:
        self._fallback = fallback
        self._gemini = gemini

    def analyze_image(
        self,
        request: AnalysisRequest,
        *,
        image_bytes: bytes,
        mime_type: str,
    ) -> AnalysisResult | ProviderResult:
        provider = (
            self._gemini
            if self._gemini is not None and _uses_gemini(request.provider_profile, request.model)
            else self._fallback
        )
        return provider.analyze_image(request, image_bytes=image_bytes, mime_type=mime_type)

    def rewrite(self, *, prompt: str, instruction: str, model: str) -> ProviderResult:
        provider = (
            self._gemini
            if self._gemini is not None and _uses_gemini(None, model)
            else self._fallback
        )
        return provider.rewrite(prompt=prompt, instruction=instruction, model=model)


class RoutedImageProvider:
    def __init__(self, fallback: Any, gemini: Any | None = None, meshy: Any | None = None) -> None:
        self._fallback = fallback
        self._gemini = gemini
        self._meshy = meshy

    def generate(self, request: dict[str, object]) -> ProviderResult:
        provider = self._fallback
        if self._meshy is not None and _uses_meshy(
            request.get("provider_profile"), request.get("channel")
        ):
            provider = self._meshy
        elif self._gemini is not None and _uses_gemini(
            request.get("provider_profile"), request.get("model")
        ):
            provider = self._gemini
        return provider.generate(request)
