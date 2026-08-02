from __future__ import annotations

import json

import httpx
import pytest

from aipic_to_model.agent.core.events import CancellationToken
from aipic_to_model.agent.core.models import (
    ProviderEventType,
    TextContent,
    ThinkingContent,
    ToolCall,
    UserMessage,
)
from aipic_to_model.agent.providers.api.adapter_provider import AdapterProvider
from aipic_to_model.agent.providers.base import ModelProfile, ModelRequest


def _sse(*events: tuple[str, dict[str, object]]) -> bytes:
    return (
        b"".join(
            (
                f"event: {name}\n".encode()
                + b"data: "
                + json.dumps(payload, ensure_ascii=False).encode()
                + b"\n\n"
            )
            for name, payload in events
        )
        + b"data: [DONE]\n\n"
    )


def _request(provider_id: str) -> ModelRequest:
    return ModelRequest(
        ModelProfile(provider_id, "demo", "https://example.test"),
        (UserMessage((TextContent("hello"),)),),
    )


@pytest.mark.asyncio
async def test_responses_stream_preserves_call_id_thinking_and_partial_arguments() -> None:
    body = _sse(
        ("response.reasoning_text.delta", {"delta": "think"}),
        (
            "response.output_item.added",
            {
                "item": {
                    "type": "function_call",
                    "id": "fc_item",
                    "call_id": "provider_call",
                    "name": "calculator.add",
                }
            },
        ),
        (
            "response.function_call_arguments.delta",
            {"item_id": "fc_item", "delta": '{"a":'},
        ),
        (
            "response.function_call_arguments.delta",
            {"item_id": "fc_item", "delta": "1}"},
        ),
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = AdapterProvider("openai-responses", lambda _ref: None, client=client)
    events = [event async for event in provider.stream(_request("openai"), CancellationToken())]
    await client.aclose()

    assert [event.type for event in events] == [
        ProviderEventType.MESSAGE_START,
        ProviderEventType.TOOL_CALL_START,
        ProviderEventType.TOOL_CALL_ARGUMENTS_DELTA,
        ProviderEventType.TOOL_CALL_ARGUMENTS_DELTA,
        ProviderEventType.MESSAGE_END,
    ]
    assert events[1].tool_call == ToolCall("provider_call", "calculator.add", {})
    assert events[-1].message is not None
    assert events[-1].message.content == (
        ThinkingContent("think"),
        ToolCall("provider_call", "calculator.add", {"a": 1}),
    )


@pytest.mark.asyncio
async def test_anthropic_stream_keeps_tool_index_and_thinking() -> None:
    body = _sse(
        (
            "content_block_delta",
            {"index": 0, "delta": {"type": "thinking_delta", "thinking": "reason"}},
        ),
        (
            "content_block_start",
            {
                "index": 1,
                "content_block": {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "calculator.add",
                },
            },
        ),
        (
            "content_block_delta",
            {"index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"a":1}'}},
        ),
        ("message_delta", {"usage": {"input_tokens": 2, "output_tokens": 3}}),
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = AdapterProvider("anthropic-messages", lambda _ref: None, client=client)
    events = [event async for event in provider.stream(_request("anthropic"), CancellationToken())]
    await client.aclose()

    assert events[-1].message is not None
    assert events[-1].message.content == (
        ThinkingContent("reason"),
        ToolCall("toolu_1", "calculator.add", {"a": 1}),
    )
    assert events[-1].message.usage.total_tokens == 5


@pytest.mark.asyncio
async def test_mistral_stream_accumulates_every_tool_call_in_a_single_delta() -> None:
    body = _sse(
        (
            "message",
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_one",
                                    "function": {"name": "one", "arguments": "{}"},
                                },
                                {
                                    "index": 1,
                                    "id": "call_two",
                                    "function": {"name": "two", "arguments": '{"n":2}'},
                                },
                            ]
                        }
                    }
                ]
            },
        ),
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = AdapterProvider("mistral-conversations", lambda _ref: None, client=client)
    events = [event async for event in provider.stream(_request("mistral"), CancellationToken())]
    await client.aclose()

    assert events[-1].message is not None
    assert events[-1].message.content == (
        ToolCall("call_one", "one", {}),
        ToolCall("call_two", "two", {"n": 2}),
    )
