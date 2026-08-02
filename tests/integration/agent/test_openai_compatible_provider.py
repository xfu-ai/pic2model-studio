import json

import httpx
import pytest

from aipic_to_model.agent.core.errors import AgentCancelledError, ProviderError
from aipic_to_model.agent.core.events import CancellationToken
from aipic_to_model.agent.core.models import (
    AssistantMessage,
    ProviderEventType,
    TextContent,
    ToolCall,
    UserMessage,
)
from aipic_to_model.agent.providers.api.openai_completions import OpenAICompletionsProvider
from aipic_to_model.agent.providers.base import ModelProfile, ModelRequest


@pytest.mark.agent
@pytest.mark.asyncio
async def test_openai_sse_parses_text_tool_and_usage() -> None:
    body = 'data: {"id":"response-1","choices":[{"delta":{"content":"hi"}}]}\n\ndata: {"choices":[{"delta":{"tool_calls":[{"id":"c1","function":{"name":"calculator.add","arguments":"{\\"a\\":1}"}}]},"finish_reason":"tool_calls"}]}\n\ndata: {"usage":{"prompt_tokens":2,"completion_tokens":3,"total_tokens":5}}\n\ndata: [DONE]\n\n'
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=body, headers={"content-type": "text/event-stream"}
            )
        )
    )
    provider = OpenAICompletionsProvider(lambda _: "secret", client=client)
    request = ModelRequest(
        ModelProfile("test", "demo", "https://example.test", credential_ref="x"),
        (UserMessage("hi"),),
    )
    events = [event async for event in provider.stream(request, CancellationToken())]
    await client.aclose()
    assert [event.type for event in events] == [
        ProviderEventType.MESSAGE_START,
        ProviderEventType.TEXT_DELTA,
        ProviderEventType.TOOL_CALL_START,
        ProviderEventType.TOOL_CALL_ARGUMENTS_DELTA,
        ProviderEventType.USAGE,
        ProviderEventType.MESSAGE_END,
    ]
    assert (
        events[1].delta == "hi"
        and events[2].tool_call
        and events[2].tool_call.name == "calculator.add"
    )
    assert events[-1].message is not None
    assert events[-1].message.stop_reason == "tool_use"
    assert events[-1].message.content[-1].arguments == {"a": 1}
    assert events[-1].message.response_id == "response-1"


@pytest.mark.agent
@pytest.mark.asyncio
@pytest.mark.parametrize("status,retryable", [(401, False), (429, True), (500, True)])
async def test_openai_sse_maps_http_errors_without_headers(status: int, retryable: bool) -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, headers={"x-secret": "never"})
        )
    )
    provider = OpenAICompletionsProvider(lambda _: "secret", client=client)
    request = ModelRequest(
        ModelProfile("test", "demo", "https://example.test"), (UserMessage("hi"),)
    )
    with pytest.raises(ProviderError) as error:
        _ = [event async for event in provider.stream(request, CancellationToken())]
    await client.aclose()
    assert error.value.retryable is retryable
    assert "never" not in error.value.message


@pytest.mark.agent
@pytest.mark.asyncio
async def test_openai_sse_rejects_a_stream_without_finish_reason() -> None:
    body = "event: ping\n\ndata: definitely-not-json\n\ndata: [DONE]\n\n"
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    )
    provider = OpenAICompletionsProvider(lambda _: None, client=client)
    request = ModelRequest(
        ModelProfile("test", "demo", "https://example.test"), (UserMessage("hi"),)
    )
    with pytest.raises(ProviderError, match="without finish_reason"):
        _ = [event async for event in provider.stream(request, CancellationToken())]
    await client.aclose()


@pytest.mark.agent
@pytest.mark.asyncio
async def test_openai_sse_rejects_an_empty_stop_response() -> None:
    body = 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    )
    provider = OpenAICompletionsProvider(lambda _: None, client=client)
    request = ModelRequest(
        ModelProfile("test", "demo", "https://example.test"), (UserMessage("hi"),)
    )
    with pytest.raises(ProviderError, match="empty assistant response"):
        _ = [event async for event in provider.stream(request, CancellationToken())]
    await client.aclose()


@pytest.mark.agent
@pytest.mark.asyncio
async def test_openai_sse_rejects_filtered_terminal_response() -> None:
    body = 'data: {"choices":[{"delta":{},"finish_reason":"content_filter"}]}\n\ndata: [DONE]\n\n'
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    )
    provider = OpenAICompletionsProvider(lambda _: None, client=client)
    request = ModelRequest(
        ModelProfile("test", "demo", "https://example.test"), (UserMessage("hi"),)
    )
    with pytest.raises(ProviderError, match="content_filter"):
        _ = [event async for event in provider.stream(request, CancellationToken())]
    await client.aclose()


@pytest.mark.agent
@pytest.mark.asyncio
async def test_openai_sse_honours_pre_cancelled_request() -> None:
    cancellation = CancellationToken()
    cancellation.cancel("stop")
    provider = OpenAICompletionsProvider(lambda _: None)
    request = ModelRequest(
        ModelProfile("test", "demo", "https://example.test"), (UserMessage("hi"),)
    )
    with pytest.raises(AgentCancelledError, match="stop"):
        _ = [event async for event in provider.stream(request, cancellation)]


@pytest.mark.agent
@pytest.mark.asyncio
async def test_openai_adapter_normalizes_dot_tool_names_on_wire_and_restores_them() -> None:
    captured: dict[str, object] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            content=(
                'data: {"choices":[{"delta":{"tool_calls":[{"id":"call-1","index":0,'
                '"function":{"name":"calculator_add","arguments":"{}"}}]},'
                '"finish_reason":"tool_calls"}]}\n\ndata: [DONE]\n\n'
            ),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    provider = OpenAICompletionsProvider(lambda _: None, client=client)
    request = ModelRequest(
        ModelProfile("test", "demo", "https://example.test"),
        (UserMessage("hi"),),
        tools=(
            {
                "type": "function",
                "function": {"name": "calculator.add", "description": "add", "parameters": {}},
            },
        ),
    )

    events = [event async for event in provider.stream(request, CancellationToken())]
    await client.aclose()

    assert captured["tools"][0]["function"]["name"] == "calculator_add"
    assert events[-1].message is not None
    assert events[-1].message.content[-1].name == "calculator.add"


@pytest.mark.agent
def test_openai_request_preserves_assistant_tool_call_for_tool_result_turn() -> None:
    assistant = AssistantMessage(
        (TextContent("calculating"), ToolCall("call-1", "calculator.add", {"a": 1, "b": 2})),
        stop_reason="tool_use",
    )

    payload = OpenAICompletionsProvider._message(assistant)

    assert payload["content"] == "calculating"
    assert payload["tool_calls"] == [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": "calculator.add", "arguments": '{"a": 1, "b": 2}'},
        }
    ]


@pytest.mark.agent
@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [httpx.ReadTimeout("timed out"), httpx.ReadError("dropped")])
async def test_openai_sse_normalizes_timeout_and_interrupted_transport(
    failure: httpx.HTTPError,
) -> None:
    def fail(_request: httpx.Request) -> httpx.Response:
        raise failure

    client = httpx.AsyncClient(transport=httpx.MockTransport(fail))
    provider = OpenAICompletionsProvider(lambda _: None, client=client)
    request = ModelRequest(
        ModelProfile("test", "demo", "https://example.test"), (UserMessage("hi"),)
    )
    with pytest.raises(ProviderError) as error:
        _ = [event async for event in provider.stream(request, CancellationToken())]
    await client.aclose()
    assert error.value.retryable
