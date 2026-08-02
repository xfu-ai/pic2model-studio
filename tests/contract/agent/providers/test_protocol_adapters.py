from __future__ import annotations

import pytest

from aipic_to_model.agent.core.models import TextContent, ToolCall, UserMessage
from aipic_to_model.agent.providers.adapters import (
    ADAPTERS,
    build_payload,
    get_adapter,
    parse_sse_event,
)
from aipic_to_model.agent.providers.base import ModelProfile, ModelRequest


def _request() -> ModelRequest:
    return ModelRequest(
        ModelProfile("contract", "model", "https://example.test", max_output_tokens=32),
        (UserMessage((TextContent("hello"),)),),
        (
            {
                "type": "function",
                "function": {
                    "name": "calculator.add",
                    "description": "add",
                    "parameters": {"type": "object"},
                },
            },
        ),
        temperature=0,
    )


@pytest.mark.parametrize("adapter_id", sorted(ADAPTERS))
def test_every_adapter_builds_a_unicode_text_and_tool_payload(adapter_id: str) -> None:
    payload = build_payload(adapter_id, _request())
    assert payload
    encoded = str(payload)
    assert "model" in encoded or "{model}" in get_adapter(adapter_id).path
    assert "calculator.add" in encoded or adapter_id == "openrouter-images"


@pytest.mark.parametrize(
    ("adapter_id", "event_name", "payload"),
    [
        ("openai-completions", None, {"choices": [{"delta": {"content": "雪"}}]}),
        ("mistral-conversations", None, {"choices": [{"delta": {"content": "雪"}}]}),
        ("openai-responses", "response.output_text.delta", {"delta": "雪"}),
        ("azure-openai-responses", "response.output_text.delta", {"delta": "雪"}),
        ("openai-codex-responses", "response.output_text.delta", {"delta": "雪"}),
        ("anthropic-messages", "content_block_delta", {"delta": {"text": "雪"}}),
        ("google-generative-ai", None, {"candidates": [{"content": {"parts": [{"text": "雪"}]}}]}),
        ("google-vertex", None, {"candidates": [{"content": {"parts": [{"text": "雪"}]}}]}),
    ],
)
def test_sse_adapters_tolerate_unicode_text_frames(
    adapter_id: str, event_name: str | None, payload: dict[str, object]
) -> None:
    delta, call, usage = parse_sse_event(adapter_id, event_name, payload)
    assert delta == "雪"
    assert call is None
    assert usage is None


def test_responses_tool_frame_and_usage_are_normalized() -> None:
    delta, call, usage = parse_sse_event(
        "openai-responses",
        "response.function_call_arguments.delta",
        {
            "item_id": "call-1",
            "name": "calculator.add",
            "delta": "{",
            "usage": {"input_tokens": 2, "output_tokens": 3},
        },
    )
    assert delta is None
    assert call == ToolCall("call-1", "calculator.add", {})
    assert usage is not None and usage.total_tokens == 5
