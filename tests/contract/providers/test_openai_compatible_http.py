from __future__ import annotations

import base64
import json
from io import BytesIO

import httpx
from PIL import Image

from aipic_to_model.domain.provider_models import AnalysisRequest
from aipic_to_model.infrastructure.providers.config import OpenAICompatibleSettings
from aipic_to_model.infrastructure.providers.openai_compatible import (
    OpenAICompatibleImageProvider,
    OpenAICompatibleVisionProvider,
)


def _png_base64() -> str:
    output = BytesIO()
    Image.new("RGB", (8, 8), "red").save(output, "PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


def _prompt_json(zh: str, en: str) -> str:
    return json.dumps({
        "schema": "aipic.prompt.v3",
        "analysis": {"zh": f"{zh}的可见特征", "en": f"visible characteristics of {en}"},
        "generation": {"zh": zh, "en": en},
        "constraints": {"preserve": [en], "avoid": ["text"]},
    }, ensure_ascii=False)


def test_gpt_image_http_adapter_returns_only_base64_and_request_id() -> None:
    secret = "test-secret"
    seen: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            headers={"x-request-id": "image-request"},
            json={"data": [{"b64_json": _png_base64()}, {"b64_json": _png_base64()}]},
        )

    provider = OpenAICompatibleImageProvider(
        OpenAICompatibleSettings("https://api.example.test/v1"),
        lambda: secret,
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )
    result = provider.generate(
        {
            "prompt_asset_id": "prompt-1",
            "provider_profile": "openai/default",
            "channel": "gpt_image",
            "mode": "t2i",
            "model": "gpt-image-2",
            "candidate_count": 2,
            "prompt": "a small red cube",
        }
    )
    assert result.ok and result.provider_request_id == "image-request"
    assert len(result.payload["images"]) == 2
    assert secret not in result.model_dump_json()
    assert seen[0].headers["authorization"] == f"Bearer {secret}"


def test_vision_http_adapter_parses_strict_bilingual_response() -> None:
    response_text = _prompt_json("红色方块", "red cube")

    def transport(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"x-request-id": "vision-request"},
            json={"choices": [{"message": {"content": response_text}}]},
        )

    provider = OpenAICompatibleVisionProvider(
        OpenAICompatibleSettings("https://api.example.test/v1"),
        lambda: "test-secret",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )
    result = provider.analyze_image(
        AnalysisRequest(
            asset_id="asset-1",
            provider_profile="openai/default",
            model="gpt-4.1-mini",
            mode="content",
        ),
        image_bytes=base64.b64decode(_png_base64()),
        mime_type="image/png",
    )
    assert not hasattr(result, "error")
    assert result.zh_prompt == "红色方块"
    assert result.en_prompt == "red cube"
    assert result.provider_request_id == "vision-request"


def test_vision_http_adapter_parses_structured_json_response() -> None:
    response_text = _prompt_json("银色骑士，正面构图", "silver knight, frontal composition")

    def transport(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"x-request-id": "structured-vision-request"},
            json={"choices": [{"message": {"content": response_text}}]},
        )

    provider = OpenAICompatibleVisionProvider(
        OpenAICompatibleSettings("https://api.example.test/v1"),
        lambda: "test-secret",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )
    result = provider.analyze_image(
        AnalysisRequest(
            asset_id="asset-1",
            provider_profile="openai/default",
            model="vision-model",
            mode="content",
        ),
        image_bytes=base64.b64decode(_png_base64()),
        mime_type="image/png",
    )

    assert not hasattr(result, "error")
    assert result.zh_prompt == "银色骑士，正面构图"
    assert result.en_prompt == "silver knight, frontal composition"
    assert result.provider_request_id == "structured-vision-request"


def test_vision_http_adapter_rejects_prompt_fence_marker_as_prompt() -> None:
    response_text = (
        "## ZH\n风格描述\n```\nprompt\n```\n"
        "## EN\nStyle description\n```\nprompt\n```"
    )

    def transport(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"x-request-id": "invalid-vision-request"},
            json={"choices": [{"message": {"content": response_text}}]},
        )

    provider = OpenAICompatibleVisionProvider(
        OpenAICompatibleSettings("https://api.example.test/v1"),
        lambda: "test-secret",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )
    result = provider.analyze_image(
        AnalysisRequest(
            asset_id="asset-1",
            provider_profile="openai/default",
            model="vision-model",
            mode="style",
        ),
        image_bytes=base64.b64decode(_png_base64()),
        mime_type="image/png",
    )

    assert result.ok is False
    assert result.error is not None


def test_vision_http_adapter_repairs_one_malformed_success_without_reuploading() -> None:
    calls: list[httpx.Request] = []
    malformed = (
        "## ZH\n风格描述\n```\nprompt\n```\n"
        "## EN\nStyle description\n```\nprompt\n```"
    )
    repaired = _prompt_json("史诗奇幻，戏剧性光照", "epic fantasy, dramatic lighting")

    def transport(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        content = malformed if len(calls) == 1 else repaired
        return httpx.Response(
            200,
            headers={"x-request-id": f"vision-request-{len(calls)}"},
            json={"choices": [{"message": {"content": content}}]},
        )

    provider = OpenAICompatibleVisionProvider(
        OpenAICompatibleSettings("https://api.example.test/v1"),
        lambda: "test-secret",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )
    result = provider.analyze_image(
        AnalysisRequest(
            asset_id="asset-1",
            provider_profile="openai/default",
            model="vision-model",
            mode="style",
        ),
        image_bytes=base64.b64decode(_png_base64()),
        mime_type="image/png",
    )

    assert result.zh_prompt == "史诗奇幻，戏剧性光照"
    assert result.en_prompt == "epic fantasy, dramatic lighting"
    assert result.provider_request_id == "vision-request-2"
    assert len(calls) == 2
    assert b"image_url" in calls[0].content
    assert b"image_url" not in calls[1].content


def test_vision_http_adapter_retries_one_transient_gateway_failure() -> None:
    calls: list[httpx.Request] = []
    response_text = _prompt_json("史诗奇幻，戏剧性光照", "epic fantasy, dramatic lighting")

    def transport(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": {"code": "gateway_unavailable"}})
        return httpx.Response(
            200,
            headers={"x-request-id": "vision-request-retried"},
            json={"choices": [{"message": {"content": response_text}}]},
        )

    provider = OpenAICompatibleVisionProvider(
        OpenAICompatibleSettings("https://api.example.test/v1"),
        lambda: "test-secret",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )
    result = provider.analyze_image(
        AnalysisRequest(
            asset_id="asset-1",
            provider_profile="openai/default",
            model="vision-model",
            mode="style",
        ),
        image_bytes=base64.b64decode(_png_base64()),
        mime_type="image/png",
    )

    assert result.en_prompt == "epic fantasy, dramatic lighting"
    assert result.provider_request_id == "vision-request-retried"
    assert len(calls) == 2
