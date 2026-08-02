from __future__ import annotations

from aipic_to_model.agent.core.models import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ToolCall,
    ToolResult,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from aipic_to_model.agent.harness.context import (
    CompactionSettings,
    estimate_context_tokens,
    find_safe_cut,
    project_context,
    should_compact,
)


def test_context_estimate_uses_latest_valid_usage_then_trailing_messages() -> None:
    messages = (
        UserMessage("old"),
        AssistantMessage((TextContent("done"),), usage=Usage(total_tokens=80)),
        UserMessage((ImageContent("x", "image/png"),)),
        AssistantMessage(
            (TextContent("failed"),), stop_reason="error", usage=Usage(total_tokens=999)
        ),
    )

    estimate = estimate_context_tokens(messages, image_token_cost=12)

    assert estimate.usage_tokens == 80
    assert estimate.last_usage_index == 1
    assert estimate.trailing_tokens >= 12
    assert estimate.tokens == estimate.usage_tokens + estimate.trailing_tokens


def test_settings_are_clamped_and_threshold_has_no_hidden_over_budget() -> None:
    settings = CompactionSettings(reserve_tokens=90, keep_recent_tokens=90).normalized(100)

    assert settings.reserve_tokens == 90
    assert settings.keep_recent_tokens == 10
    assert should_compact(11, 100, settings)
    assert not should_compact(10, 100, CompactionSettings(enabled=False))


def test_safe_cut_retains_a_complete_tool_call_result_pair() -> None:
    call = ToolCall("call-1", "read", {"path": "a.txt"})
    messages = (
        UserMessage("first"),
        AssistantMessage((TextContent("done"),)),
        UserMessage("use a tool"),
        AssistantMessage((call,)),
        ToolResultMessage("call-1", "read", ToolResult((TextContent("contents"),))),
        AssistantMessage((TextContent("final"),)),
    )

    cut = find_safe_cut(messages, keep_recent_tokens=1)

    retained = messages[cut:]
    assert cut == 2
    assert isinstance(retained[1], AssistantMessage)
    assert isinstance(retained[2], ToolResultMessage)


def test_projection_keeps_raw_tail_and_injects_summary_without_deleting_history() -> None:
    raw = (UserMessage("old"), AssistantMessage((TextContent("old answer"),)), UserMessage("new"))

    projection = project_context(raw, summary="## Goal\nContinue", first_kept_sequence=3)

    assert projection.messages[0].role == "system"
    assert projection.messages[1:] == raw[2:]
    assert len(raw) == 3
