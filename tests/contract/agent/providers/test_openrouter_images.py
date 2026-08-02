from __future__ import annotations

import json

import httpx
import pytest

from aipic_to_model.agent.core.events import CancellationToken
from aipic_to_model.agent.core.models import (
    ImageContent,
    ProviderEventType,
    TextContent,
    UserMessage,
)
from aipic_to_model.agent.providers.api.openrouter_images import OpenRouterImagesProvider
from aipic_to_model.agent.providers.base import ModelProfile, ModelRequest


@pytest.mark.asyncio
async def test_openrouter_images_provider_normalizes_text_image_usage_and_non_streaming_payload() -> (
    None
):
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "response-1",
                "choices": [
                    {
                        "message": {
                            "content": "done",
                            "images": [{"image_url": "data:image/png;base64,aGVsbG8="}],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenRouterImagesProvider(lambda _ref: "token", client=client)
    request = ModelRequest(
        ModelProfile("openrouter-images", "image-model", "https://openrouter.ai/api/v1"),
        (UserMessage((TextContent("draw"),)),),
    )
    events = [event async for event in provider.stream(request, CancellationToken())]
    await client.aclose()
    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert isinstance(seen["payload"], dict) and seen["payload"]["modalities"] == ["image", "text"]
    assert [event.type for event in events] == [
        ProviderEventType.MESSAGE_START,
        ProviderEventType.USAGE,
        ProviderEventType.MESSAGE_END,
    ]
    assert events[-1].message is not None
    assert events[-1].message.content == (
        TextContent("done"),
        ImageContent("aGVsbG8=", "image/png"),
    )
