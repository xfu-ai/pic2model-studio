from __future__ import annotations

import json

import pytest

from aipic_to_model.agent.core.models import (
    AssistantMessage,
    Cost,
    ImageContent,
    ManagedAssetAttachment,
    ProviderEvent,
    ProviderEventType,
    SystemMessage,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResult,
    ToolResultMessage,
    Usage,
    UserMessage,
    json_dumps,
    message_from_json,
)


@pytest.mark.agent
def test_messages_round_trip_through_json_without_secret_fields() -> None:
    messages = [
        SystemMessage("Always be concise.", id="system-1", timestamp=1),
        UserMessage(
            (TextContent("Hello"), ImageContent("aGVsbG8=", "image/png")),
            id="user-1",
            timestamp=2,
            display_content="Hello",
            attachments=(ManagedAssetAttachment("asset-image-1", "reference.png", "image/png"),),
        ),
        AssistantMessage(
            content=(
                TextContent("I will use a tool."),
                ThinkingContent("internal transport-only thought", redacted=True),
                ToolCall("call-1", "calculator.add", {"left": 2, "right": 3}),
            ),
            api="openai-completions",
            provider="deepseek",
            model="deepseek-chat",
            usage=Usage(input_tokens=4, output_tokens=6, total_tokens=10, cost=Cost(total=0.01)),
            stop_reason="tool_use",
            id="assistant-1",
            timestamp=3,
        ),
        ToolResultMessage(
            "call-1",
            "calculator.add",
            ToolResult((TextContent("5"),), details={"sum": 5}),
            id="tool-1",
            timestamp=4,
        ),
    ]

    encoded = [message.to_json() for message in messages]

    assert [message_from_json(item) for item in encoded] == messages
    assert all(
        "api_key" not in item.lower() and "authorization" not in item.lower() for item in encoded
    )


@pytest.mark.agent
def test_provider_event_and_json_serializer_are_stable() -> None:
    event = ProviderEvent(
        ProviderEventType.TOOL_CALL_END,
        event_id="event-1",
        timestamp=10,
        tool_call=ToolCall("call-1", "demo", {"value": True}),
    )

    assert json.loads(json_dumps(event)) == {
        "event_id": "event-1",
        "timestamp": 10,
        "tool_call": {
            "arguments": {"value": True},
            "id": "call-1",
            "name": "demo",
            "type": "tool_call",
        },
        "type": "tool_call_end",
    }


@pytest.mark.agent
def test_unknown_message_role_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown agent message role"):
        message_from_json('{"role":"provider","id":"x","timestamp":0}')
