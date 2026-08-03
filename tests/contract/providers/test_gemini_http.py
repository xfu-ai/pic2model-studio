from __future__ import annotations

import base64
import json
from io import BytesIO

import httpx
from PIL import Image

from aipic_to_model.domain.provider_models import AnalysisRequest
from aipic_to_model.infrastructure.providers.config import GeminiSettings
from aipic_to_model.infrastructure.providers.gemini import (
    GeminiImageProvider,
    GeminiTextRenderImageProvider,
    GeminiVisionProvider,
)


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (8, 8), "gray").save(output, "PNG")
    return output.getvalue()


def _prompt_json(zh: str, en: str) -> str:
    return json.dumps({
        "schema": "pic2model.prompt.v1",
        "analysis": {"zh": f"{zh}的可见特征", "en": f"visible characteristics of {en}"},
        "generation": {"zh": zh, "en": en},
        "constraints": {"preserve": [en], "avoid": ["text"]},
    }, ensure_ascii=False)


def test_gemini_image_generation_uses_official_protocol_and_extracts_inline_data() -> None:
    image = _png()
    seen: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["x-goog-api-key"] == "secret"
        assert request.url.path.endswith("/models/gemini-3.1-flash-lite-image:generateContent")
        payload = __import__("json").loads(request.content)
        assert payload["generationConfig"]["responseModalities"] == ["TEXT", "IMAGE"]
        assert payload["generationConfig"]["imageConfig"]["aspectRatio"] == "1:1"
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "inlineData": {
                                        "mimeType": "image/png",
                                        "data": base64.b64encode(image).decode("ascii"),
                                    }
                                }
                            ]
                        }
                    }
                ]
            },
            headers={"x-goog-request-id": "gemini-request"},
            request=request,
        )

    provider = GeminiImageProvider(
        GeminiSettings(),
        lambda: "secret",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
        sleep=lambda _seconds: None,
    )

    result = provider.generate(
        {
            "provider_profile": "gemini/google/default",
            "mode": "t2i",
            "model": "configured-by-profile",
            "candidate_count": 1,
            "aspect_ratio": "1:1",
            "prompt": "gray cube",
        }
    )

    assert result.ok
    assert result.provider_request_id == "gemini-request"
    assert result.payload == {"images": [{"base64": base64.b64encode(image).decode("ascii")}]}
    assert len(seen) == 1


def test_gemini_vision_analysis_uses_inline_managed_image() -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        parts = payload["contents"][0]["parts"]
        assert parts[1]["inlineData"]["mimeType"] == "image/png"
        system = payload["systemInstruction"]["parts"][0]["text"]
        assert "subject identity and count" in system
        assert "identity-critical details" in system
        assert "separate visual-direction reference" in system
        assert "pic2model.prompt.v1" in system
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                    {"text": _prompt_json("灰色立方体", "Gray cube")}
                            ]
                        }
                    }
                ]
            },
            request=request,
        )

    provider = GeminiVisionProvider(
        GeminiSettings(),
        lambda: "secret",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )
    result = provider.analyze_image(
        AnalysisRequest(
            asset_id="asset",
            provider_profile="gemini/google/default",
            model="gemini-flash-lite-latest",
            mode="content",
        ),
        image_bytes=_png(),
        mime_type="image/png",
    )

    assert not hasattr(result, "ok")
    assert result.zh_text is not None and "灰色立方体" in result.zh_text
    assert result.en_prompt == "Gray cube"
    assert result.raw_response is not None


def test_gemini_vision_retries_transient_failures_with_legacy_backoff() -> None:
    attempts = 0
    waits: list[float] = []

    def transport(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}}, request=request)
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": _prompt_json("灰色立方体", "Gray cube")}]}}]},
            request=request,
        )

    provider = GeminiVisionProvider(
        GeminiSettings(),
        lambda: "secret",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
        sleep=waits.append,
    )
    result = provider.analyze_image(
        AnalysisRequest(asset_id="asset", provider_profile="gemini/google/default", model="gemini-flash-lite-latest", mode="content"),
        image_bytes=_png(),
        mime_type="image/png",
    )

    assert attempts == 3
    assert waits == [5, 10]
    assert result.en_prompt == "Gray cube"


def test_gemini_vision_keeps_invalid_bilingual_response_for_manual_repair() -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": "prompt"}]}}]},
            request=request,
        )

    provider = GeminiVisionProvider(GeminiSettings(), lambda: "secret", client=httpx.Client(transport=httpx.MockTransport(transport)))
    result = provider.analyze_image(
        AnalysisRequest(asset_id="asset", provider_profile="gemini/google/default", model="gemini-flash-lite-latest", mode="style"),
        image_bytes=_png(),
        mime_type="image/png",
    )

    assert result.raw_response == "prompt"
    assert result.parse_error == "bilingual_prompt_contract_not_met"
    assert result.zh_prompt is None and result.en_prompt is None


def test_gemini_rewrite_requires_and_accepts_strict_bilingual_response() -> None:
    response_text = _prompt_json("白色背景中的灰色立方体", "a gray cube on a white background")
    seen: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            headers={"x-request-id": "rewrite-request"},
            json={"candidates": [{"content": {"parts": [{"text": response_text}]}}]},
            request=request,
        )

    provider = GeminiVisionProvider(
        GeminiSettings(),
        lambda: "secret",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )
    result = provider.rewrite(
        prompt=_prompt_json("灰色立方体", "gray cube"),
        instruction="Use a white background.",
        model="gemini-flash-lite-latest",
    )

    assert result.ok
    assert result.provider_request_id == "rewrite-request"
    assert result.payload["text"] == response_text
    payload = __import__("json").loads(seen[0].content)
    request_text = payload["contents"][0]["parts"][0]["text"]
    assert "Existing prompt document:" in request_text
    assert "Requested change:\nUse a white background." in request_text
    assert "pic2model.prompt.v1" in payload["systemInstruction"]["parts"][0]["text"]


def test_gemini_rewrite_rejects_non_bilingual_response() -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": "gray cube"}]}}]},
            request=request,
        )

    provider = GeminiVisionProvider(
        GeminiSettings(),
        lambda: "secret",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )
    result = provider.rewrite(
        prompt=_prompt_json("灰色立方体", "gray cube"),
        instruction="Use a white background.",
        model="gemini-flash-lite-latest",
    )

    assert not result.ok
    assert result.error is not None


def test_gemini_not_found_maps_to_model_unavailable_without_raw_message() -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={
                "error": {
                    "status": "NOT_FOUND",
                    "message": "raw-provider-sentinel",
                }
            },
            request=request,
        )

    provider = GeminiImageProvider(
        GeminiSettings(),
        lambda: "secret",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )
    result = provider.generate(
        {
            "mode": "t2i",
            "model": "gemini-missing-image",
            "candidate_count": 1,
            "prompt": "gray cube",
        }
    )

    assert not result.ok
    assert result.error is not None
    assert result.error.code == "PROVIDER_MODEL_UNAVAILABLE"
    assert "sentinel" not in result.error.user_message


def test_gemini_text_render_backend_returns_a_valid_local_png() -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        assert payload["generationConfig"]["responseMimeType"] == "application/json"
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": (
                                        '{"background":"#FFFFFF","shapes":['
                                        '{"kind":"rectangle","x":300,"y":300,'
                                        '"w":400,"h":400,"fill":"#777777"}]}'
                                    )
                                }
                            ]
                        }
                    }
                ]
            },
            request=request,
        )

    provider = GeminiTextRenderImageProvider(
        GeminiSettings(
            image_model="gemini-flash-lite-latest",
            image_backend="text_render",
        ),
        lambda: "secret",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )
    result = provider.generate(
        {
            "mode": "t2i",
            "model": "gemini-flash-lite-latest",
            "candidate_count": 1,
            "prompt": "gray cube",
        }
    )

    assert result.ok
    content = base64.b64decode(str(result.payload["images"][0]["base64"]), validate=True)
    with Image.open(BytesIO(content)) as image:
        image.verify()
        assert image.format == "PNG"


def test_gemini_resource_exhausted_429_is_rate_limited_not_bad_credentials() -> None:
    attempts = 0
    waits: list[float] = []

    def transport(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            429,
            json={
                "error": {
                    "status": "RESOURCE_EXHAUSTED",
                    "message": "quota sentinel",
                    "details": [{"retryDelay": "12.5s"}],
                }
            },
            request=request,
        )

    provider = GeminiImageProvider(
        GeminiSettings(),
        lambda: "secret",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
        sleep=waits.append,
    )
    result = provider.generate(
        {
            "mode": "t2i",
            "model": "gemini-3.1-flash-lite-image",
            "candidate_count": 1,
            "prompt": "gray cube",
        }
    )

    assert not result.ok
    assert result.error is not None
    assert result.error.code == "PROVIDER_RATE_LIMITED"
    assert result.error.safe_to_retry
    assert result.error.retry_after_seconds == 13
    assert "sentinel" not in result.error.user_message
    assert attempts == 1
    assert waits == []


def test_gemini_image_generation_retries_transient_failures() -> None:
    image = _png()
    attempts = 0
    waits: list[float] = []

    def transport(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}}, request=request)
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": base64.b64encode(image).decode("ascii")}}]}}]},
            request=request,
        )

    provider = GeminiImageProvider(
        GeminiSettings(),
        lambda: "secret",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
        sleep=waits.append,
    )
    result = provider.generate({"mode": "t2i", "model": "gemini-3.1-flash-lite-image", "candidate_count": 1, "prompt": "gray cube"})

    assert result.ok
    assert attempts == 3
    assert waits == [5, 10]
