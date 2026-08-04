"""Context projection, token accounting, and safe compaction cut points."""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..core.models import (
    AssistantMessage,
    ImageContent,
    Message,
    SystemMessage,
    TextContent,
    ThinkingContent,
    ToolResultMessage,
    UserMessage,
    json_dumps,
)


@dataclass(frozen=True)
class CompactionSettings:
    enabled: bool = True
    reserve_tokens: int = 16_384
    keep_recent_tokens: int = 20_000
    image_token_cost: int = 1_200

    def normalized(self, context_window: int) -> CompactionSettings:
        if context_window < 2:
            raise ValueError("Model context window must be at least two tokens.")
        reserve = min(max(self.reserve_tokens, 0), context_window - 1)
        keep = min(max(self.keep_recent_tokens, 0), context_window - reserve)
        return CompactionSettings(self.enabled, reserve, keep, self.image_token_cost)


DEFAULT_COMPACTION_SETTINGS = CompactionSettings()
CONTEXT_SAFETY_TOKENS = 4_096


@dataclass(frozen=True)
class ContextEstimate:
    tokens: int
    usage_tokens: int
    trailing_tokens: int
    last_usage_index: int | None


@dataclass(frozen=True)
class ContextProjection:
    messages: tuple[Message, ...]
    first_kept_sequence: int | None


def estimate_message_tokens(message: Message, image_token_cost: int = 1_200) -> int:
    """Use a stable conservative fallback when provider usage is unavailable."""

    chars = 0
    if isinstance(message, UserMessage | SystemMessage):
        content = message.content
        if isinstance(content, str):
            chars = len(content)
        else:
            for block in content:
                chars += len(block.text) if isinstance(block, TextContent) else image_token_cost * 4
    elif isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextContent):
                chars += len(block.text)
            elif isinstance(block, ThinkingContent):
                chars += len(block.thinking)
            elif isinstance(block, ImageContent):
                chars += image_token_cost * 4
            else:
                chars += len(block.name) + len(json_dumps(block.arguments))
    elif isinstance(message, ToolResultMessage):
        for block in message.content:
            chars += len(block.text) if isinstance(block, TextContent) else image_token_cost * 4
        if message.result.details is not None:
            chars += len(json.dumps(message.result.details, ensure_ascii=False, sort_keys=True))
    else:
        chars = len(json_dumps(message.to_dict()))
    return max(1, (chars + 3) // 4)


def _valid_usage(message: Message) -> int | None:
    if not isinstance(message, AssistantMessage) or message.stop_reason in {"error", "aborted"}:
        return None
    usage = message.usage
    tokens = usage.total_tokens or (
        usage.input_tokens
        + usage.output_tokens
        + usage.cache_read_tokens
        + usage.cache_write_tokens
    )
    return tokens if tokens > 0 else None


def estimate_context_tokens(
    messages: tuple[Message, ...], image_token_cost: int = 1_200
) -> ContextEstimate:
    for index in range(len(messages) - 1, -1, -1):
        usage = _valid_usage(messages[index])
        if usage is not None:
            trailing = sum(
                estimate_message_tokens(item, image_token_cost) for item in messages[index + 1 :]
            )
            return ContextEstimate(usage + trailing, usage, trailing, index)
    fallback = sum(estimate_message_tokens(item, image_token_cost) for item in messages)
    return ContextEstimate(fallback, 0, fallback, None)


def should_compact(tokens: int, context_window: int, settings: CompactionSettings) -> bool:
    normalized = settings.normalized(context_window)
    return normalized.enabled and tokens > context_window - normalized.reserve_tokens


def clamp_max_output_tokens(
    messages: tuple[Message, ...],
    context_window: int,
    max_output_tokens: int,
    tools: tuple[dict[str, object], ...] = (),
) -> int:
    """Mirror Pi's per-request output clamp against the remaining context."""

    if context_window <= 0:
        return max(1, max_output_tokens)
    tool_tokens = (
        len(json.dumps(tools, ensure_ascii=False, sort_keys=True, separators=(",", ":"))) + 3
    ) // 4
    available = (
        context_window
        - estimate_context_tokens(messages).tokens
        - tool_tokens
        - CONTEXT_SAFETY_TOKENS
    )
    return min(max_output_tokens, max(1, available))


def project_context(
    messages: tuple[Message, ...], *, summary: str | None, first_kept_sequence: int | None
) -> ContextProjection:
    start = max(0, (first_kept_sequence or 1) - 1)
    tail = messages[start:] if first_kept_sequence is not None else messages
    prefix: tuple[Message, ...] = (SystemMessage(summary),) if summary else ()
    return ContextProjection(prefix + tail, first_kept_sequence)


def find_safe_cut(messages: tuple[Message, ...], keep_recent_tokens: int) -> int:
    """Return the first retained index without separating a call from its result."""

    if not messages:
        return 0
    retained = 0
    index = len(messages)
    while index > 0 and retained < keep_recent_tokens:
        index -= 1
        retained += estimate_message_tokens(messages[index])
    # A result is coupled to the assistant message containing its call.  If the
    # cut starts on a result, pull the full originating assistant turn into tail.
    while index > 0 and isinstance(messages[index], ToolResultMessage):
        tool_result = messages[index]
        assert isinstance(tool_result, ToolResultMessage)
        call_id = tool_result.tool_call_id
        found = False
        for candidate in range(index - 1, -1, -1):
            item = messages[candidate]
            if isinstance(item, AssistantMessage) and any(
                getattr(block, "id", None) == call_id for block in item.content
            ):
                index = candidate
                found = True
                break
        if not found:
            index -= 1
    # Prefer a visible user-turn boundary when it does not violate pair safety.
    while index > 0 and not isinstance(messages[index], UserMessage):
        if isinstance(messages[index - 1], UserMessage):
            index -= 1
            break
        index -= 1
    return index


def find_turn_prefix_cut(messages: tuple[Message, ...]) -> int | None:
    """Split an oversized final turn after its user input, never before a result."""

    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], UserMessage):
            candidate = index + 1
            if candidate < len(messages) and not isinstance(messages[candidate], ToolResultMessage):
                return candidate
            return None
    return None
