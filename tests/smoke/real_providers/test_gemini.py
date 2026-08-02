from __future__ import annotations

import base64
from io import BytesIO
from typing import Literal

import pytest
from PIL import Image

from aipic_to_model.domain.provider_models import AnalysisRequest, AnalysisResult
from aipic_to_model.infrastructure.keyring_store import OSKeyringStore
from aipic_to_model.infrastructure.providers.config import (
    GEMINI_PROFILE,
    CredentialResolver,
    load_gemini_public_settings,
)
from aipic_to_model.infrastructure.providers.gemini import (
    GeminiImageProvider,
    GeminiTextRenderImageProvider,
    GeminiVisionProvider,
)


@pytest.mark.real_provider
def test_gemini_text_render_t2i_returns_two_valid_candidate_images() -> None:
    settings = load_gemini_public_settings()
    assert settings is not None, "enabled Gemini config is not available"
    credentials = CredentialResolver(OSKeyringStore())
    assert credentials.get(GEMINI_PROFILE), "gemini/google/default is not configured"
    provider_type = (
        GeminiTextRenderImageProvider
        if settings.image_backend == "text_render"
        else GeminiImageProvider
    )
    provider = provider_type(settings, credentials.callback(GEMINI_PROFILE))

    result = provider.generate(
        {
            "provider_profile": GEMINI_PROFILE,
            "mode": "t2i",
            "model": settings.image_model,
            "candidate_count": 2,
            "aspect_ratio": "1:1",
            "prompt": (
                "A single matte gray cube centered on a plain white background, "
                "minimal product reference, no text."
            ),
        }
    )

    assert result.ok, result.error.code if result.error else "invalid Provider response"
    images = result.payload.get("images")
    assert isinstance(images, list) and len(images) == 2
    for item in images:
        content = base64.b64decode(str(item["base64"]), validate=True)
        with Image.open(BytesIO(content)) as image:
            image.verify()


@pytest.mark.real_provider
def test_gemini_minimal_content_analysis_returns_bilingual_result() -> None:
    settings = load_gemini_public_settings()
    assert settings is not None, "enabled Gemini config is not available"
    credentials = CredentialResolver(OSKeyringStore())
    provider = GeminiVisionProvider(settings, credentials.callback(GEMINI_PROFILE))
    source = BytesIO()
    Image.new("RGB", (64, 64), "gray").save(source, "PNG")

    result = provider.analyze_image(
        AnalysisRequest(
            asset_id="smoke-image",
            provider_profile=GEMINI_PROFILE,
            model=settings.analysis_model,
            mode="content",
        ),
        image_bytes=source.getvalue(),
        mime_type="image/png",
    )

    assert isinstance(result, AnalysisResult), (
        result.error.code if result.error else "invalid Provider response"
    )
    assert result.zh_text and result.en_text
    assert result.zh_prompt and result.en_prompt


@pytest.mark.real_provider
@pytest.mark.parametrize("mode", ["style", "3d_suitability"])
def test_gemini_additional_analysis_modes_return_structured_results(
    mode: Literal["style", "3d_suitability"],
) -> None:
    settings = load_gemini_public_settings()
    assert settings is not None, "enabled Gemini config is not available"
    credentials = CredentialResolver(OSKeyringStore())
    provider = GeminiVisionProvider(settings, credentials.callback(GEMINI_PROFILE))
    source = BytesIO()
    Image.new("RGB", (64, 64), "gray").save(source, "PNG")

    result = provider.analyze_image(
        AnalysisRequest(
            asset_id="smoke-image",
            provider_profile=GEMINI_PROFILE,
            model=settings.analysis_model,
            mode=mode,
        ),
        image_bytes=source.getvalue(),
        mime_type="image/png",
    )

    assert isinstance(result, AnalysisResult), (
        result.error.code if result.error else "invalid Provider response"
    )
    assert result.zh_text and result.en_text
    if mode == "style":
        assert result.zh_prompt and result.en_prompt
    else:
        assert isinstance(result.dimensions, dict)
        assert isinstance(result.suitability_issues, list)


@pytest.mark.real_provider
def test_gemini_text_render_i2i_returns_valid_image() -> None:
    settings = load_gemini_public_settings()
    assert settings is not None, "enabled Gemini config is not available"
    credentials = CredentialResolver(OSKeyringStore())
    provider = GeminiTextRenderImageProvider(settings, credentials.callback(GEMINI_PROFILE))
    source = BytesIO()
    Image.new("RGB", (64, 64), "gray").save(source, "PNG")

    result = provider.generate(
        {
            "provider_profile": GEMINI_PROFILE,
            "mode": "i2i",
            "model": settings.image_model,
            "candidate_count": 1,
            "aspect_ratio": "1:1",
            "prompt": "Turn the gray cube into a blue cube on a white background.",
            "source_bytes": source.getvalue(),
            "source_mime": "image/png",
        }
    )

    assert result.ok, result.error.code if result.error else "invalid Provider response"
    images = result.payload.get("images")
    assert isinstance(images, list) and len(images) == 1
    content = base64.b64decode(str(images[0]["base64"]), validate=True)
    with Image.open(BytesIO(content)) as image:
        image.verify()
