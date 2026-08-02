from __future__ import annotations

import httpx
import pytest

from aipic_to_model.agent.core.events import CancellationToken
from aipic_to_model.agent.core.models import ProviderEventType, TextContent, UserMessage
from aipic_to_model.agent.providers.api.google_generative_ai import GoogleGenerativeAIProvider
from aipic_to_model.agent.providers.base import ModelProfile, ModelRequest


@pytest.mark.asyncio
async def test_google_generative_ai_uses_google_api_key_header_and_preserves_usage() -> None:
    seen: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["api_key"] = request.headers["x-goog-api-key"]
        assert "authorization" not in request.headers
        return httpx.Response(
            200,
            content=(
                'data: {"candidates":[{"content":{"parts":[{"text":"ok"}]}}],'
                '"usageMetadata":{"promptTokenCount":5,"cachedContentTokenCount":2,'
                '"candidatesTokenCount":3,"thoughtsTokenCount":1,"totalTokenCount":6}}\n\n'
                "data: [DONE]\n\n"
            ),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GoogleGenerativeAIProvider(lambda _ref: "key", client=client)
    events = [
        event
        async for event in provider.stream(
            ModelRequest(
                ModelProfile(
                    "google", "gemini", "https://generativelanguage.googleapis.com/v1beta"
                ),
                (UserMessage((TextContent("hello"),)),),
            ),
            CancellationToken(),
        )
    ]
    await client.aclose()

    assert seen["api_key"] == "key"
    usage = next(event.usage for event in events if event.type is ProviderEventType.USAGE)
    assert usage is not None
    assert usage.input_tokens == 3 and usage.output_tokens == 4 and usage.cache_read_tokens == 2
    assert usage.reasoning_tokens == 1
