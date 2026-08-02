from __future__ import annotations

import httpx

from aipic_to_model.infrastructure.providers.config import OpenAICompatibleSettings
from aipic_to_model.infrastructure.providers.openai_compatible import (
    OpenAICompatibleImageProvider,
)
from aipic_to_model.infrastructure.providers.tripo_http import (
    HttpTripo3DProvider,
    TripoHttpSettings,
)


def test_tripo_credit_error_is_not_reported_as_bad_credentials() -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "code": 2010,
                "message": "provider-credit-sentinel",
                "suggestion": "provider-suggestion-sentinel",
            },
            request=request,
        )

    provider = HttpTripo3DProvider(
        TripoHttpSettings(
            "https://api.tripo3d.example",
            frozenset({"artifacts.tripo3d.example"}),
        ),
        lambda: "secret",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )

    result = provider.create({"input": "opaque-upload-token"}, idempotency_key="stable")

    assert not result.ok
    assert result.error is not None
    assert result.error.code == "PROVIDER_CREDITS_EXHAUSTED"
    assert result.error.recommended_action == "open_details"
    assert "sentinel" not in result.error.user_message


def test_openai_compatible_model_error_is_actionable_and_redacted() -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={
                "error": {
                    "code": "model_not_found",
                    "message": "provider-model-sentinel",
                }
            },
            request=request,
        )

    provider = OpenAICompatibleImageProvider(
        OpenAICompatibleSettings(
            base_url="https://images.example/v1",
            image_model="gpt-image-2",
        ),
        lambda: "secret",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )

    result = provider.generate(
        {
            "channel": "gpt_image",
            "mode": "t2i",
            "model": "gpt-image-2",
            "candidate_count": 1,
            "size": "1024x1024",
            "quality": "low",
            "output_format": "png",
            "prompt": "gray cube",
        }
    )

    assert not result.ok
    assert result.error is not None
    assert result.error.code == "PROVIDER_MODEL_UNAVAILABLE"
    assert result.error.recommended_action == "configure_provider"
    assert "sentinel" not in result.error.user_message
