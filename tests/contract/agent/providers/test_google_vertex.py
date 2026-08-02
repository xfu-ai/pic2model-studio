from __future__ import annotations

import httpx
import pytest

from aipic_to_model.agent.core.events import CancellationToken
from aipic_to_model.agent.core.models import ProviderEventType, TextContent, UserMessage
from aipic_to_model.agent.providers.api.google_credentials import GoogleAccessToken
from aipic_to_model.agent.providers.api.google_vertex import GoogleVertexProvider
from aipic_to_model.agent.providers.base import ModelProfile, ModelRequest


class TokenSource:
    async def access_token(self) -> GoogleAccessToken:
        return GoogleAccessToken("adc", 9_999_999_999)


@pytest.mark.asyncio
async def test_vertex_uses_adc_bearer_and_constructs_project_location_endpoint() -> None:
    seen: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers["authorization"]
        return httpx.Response(
            200,
            content='data: {"candidates":[{"content":{"parts":[{"text":"ok"}]}}]}\n\ndata: [DONE]\n\n',
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GoogleVertexProvider(
        lambda _ref: None,
        environment={"GOOGLE_CLOUD_PROJECT": "project", "GOOGLE_CLOUD_LOCATION": "us-central1"},
        credentials=TokenSource(),
        client=client,
    )
    request = ModelRequest(
        ModelProfile("google-vertex", "gemini", ""), (UserMessage((TextContent("hi"),)),)
    )
    events = [event async for event in provider.stream(request, CancellationToken())]
    await client.aclose()
    assert seen["authorization"] == "Bearer adc"
    assert seen["url"].startswith(
        "https://us-central1-aiplatform.googleapis.com/v1/projects/project/locations/us-central1/publishers/google/models/gemini"
    )
    assert events[-1].type == ProviderEventType.MESSAGE_END
