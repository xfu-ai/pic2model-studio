"""JSON-safe DTOs shared by future Agent Core, provider, and tool layers."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

type JsonPrimitive = str | int | float | bool | None
type JsonValue = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]


def utc_timestamp_ms() -> int:
    """Return an integer Unix timestamp in milliseconds, matching Pi messages."""

    return int(datetime.now(UTC).timestamp() * 1000)


def new_id(prefix: str = "") -> str:
    """Create an opaque, locally generated identifier without provider semantics."""

    value = str(uuid.uuid4())
    return f"{prefix}_{value}" if prefix else value


def json_dumps(value: Any) -> str:
    """Serialize public DTOs deterministically without accepting secret fields."""

    return json.dumps(_jsonable(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _jsonable(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _jsonable(item) for key, item in asdict(value).items() if item is not None}
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items() if item is not None}
    return value


@dataclass(frozen=True)
class JsonModel:
    def to_dict(self) -> dict[str, Any]:
        value = _jsonable(self)
        if not isinstance(value, dict):  # pragma: no cover - class invariant
            raise TypeError("JSON model must serialize to an object")
        return value

    def to_json(self) -> str:
        return json_dumps(self)


@dataclass(frozen=True)
class TextContent(JsonModel):
    text: str
    type: Literal["text"] = "text"
    text_signature: str | None = None


@dataclass(frozen=True)
class ThinkingContent(JsonModel):
    thinking: str
    type: Literal["thinking"] = "thinking"
    thinking_signature: str | None = None
    redacted: bool | None = None


@dataclass(frozen=True)
class ImageContent(JsonModel):
    data: str
    mime_type: str
    type: Literal["image"] = "image"


@dataclass(frozen=True)
class ToolCall(JsonModel):
    id: str
    name: str
    arguments: dict[str, JsonValue]
    type: Literal["tool_call"] = "tool_call"
    thought_signature: str | None = None


type ContentBlock = TextContent | ThinkingContent | ImageContent | ToolCall
type VisibleContent = TextContent | ImageContent


@dataclass(frozen=True)
class Cost(JsonModel):
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    total: float = 0.0


@dataclass(frozen=True)
class Usage(JsonModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    total_tokens: int = 0
    cache_write_1h_tokens: int | None = None
    reasoning_tokens: int | None = None
    cost: Cost = field(default_factory=Cost)


@dataclass(frozen=True)
class ManagedAssetAttachment(JsonModel):
    """Safe metadata for an image already owned by the current project."""

    asset_id: str
    name: str
    mime_type: str


@dataclass(frozen=True)
class UserMessage(JsonModel):
    content: str | tuple[TextContent | ImageContent, ...]
    id: str = field(default_factory=lambda: new_id("msg"))
    timestamp: int = field(default_factory=utc_timestamp_ms)
    role: Literal["user"] = "user"
    display_content: str | None = None
    attachments: tuple[ManagedAssetAttachment, ...] = ()


@dataclass(frozen=True)
class SystemMessage(JsonModel):
    content: str | tuple[TextContent, ...]
    id: str = field(default_factory=lambda: new_id("msg"))
    timestamp: int = field(default_factory=utc_timestamp_ms)
    role: Literal["system"] = "system"


@dataclass(frozen=True)
class AssistantMessage(JsonModel):
    content: tuple[ContentBlock, ...]
    api: str = "unknown"
    provider: str = "unknown"
    model: str = "unknown"
    usage: Usage = field(default_factory=Usage)
    stop_reason: Literal["stop", "length", "tool_use", "error", "aborted"] = "stop"
    id: str = field(default_factory=lambda: new_id("msg"))
    timestamp: int = field(default_factory=utc_timestamp_ms)
    response_model: str | None = None
    response_id: str | None = None
    error_message: str | None = None
    role: Literal["assistant"] = "assistant"


@dataclass(frozen=True)
class ToolResult(JsonModel):
    content: tuple[VisibleContent, ...]
    details: JsonValue | None = None
    usage: Usage | None = None
    is_error: bool = False
    added_tool_names: tuple[str, ...] = ()
    terminate: bool = False


@dataclass(frozen=True)
class ToolResultMessage(JsonModel):
    tool_call_id: str
    tool_name: str
    result: ToolResult
    id: str = field(default_factory=lambda: new_id("msg"))
    timestamp: int = field(default_factory=utc_timestamp_ms)
    role: Literal["tool_result"] = "tool_result"

    @property
    def content(self) -> tuple[VisibleContent, ...]:
        return self.result.content

    @property
    def is_error(self) -> bool:
        return self.result.is_error


@dataclass(frozen=True)
class CustomMessage(JsonModel):
    name: str
    payload: dict[str, JsonValue]
    id: str = field(default_factory=lambda: new_id("msg"))
    timestamp: int = field(default_factory=utc_timestamp_ms)
    role: Literal["custom"] = "custom"


type Message = UserMessage | SystemMessage | AssistantMessage | ToolResultMessage | CustomMessage


class ProviderEventType(StrEnum):
    MESSAGE_START = "message_start"
    TEXT_DELTA = "text_delta"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_ARGUMENTS_DELTA = "tool_call_arguments_delta"
    TOOL_CALL_END = "tool_call_end"
    USAGE = "usage"
    MESSAGE_END = "message_end"
    PROVIDER_ERROR = "provider_error"


@dataclass(frozen=True)
class ProviderEvent(JsonModel):
    """Normalized, provider-independent event emitted during one model response."""

    type: ProviderEventType
    event_id: str = field(default_factory=lambda: new_id("pev"))
    timestamp: int = field(default_factory=utc_timestamp_ms)
    message_id: str | None = None
    content_index: int | None = None
    delta: str | None = None
    tool_call: ToolCall | None = None
    usage: Usage | None = None
    message: AssistantMessage | None = None
    error_message: str | None = None


def message_from_dict(value: dict[str, Any]) -> Message:
    """Restore a public message DTO from its JSON object representation."""

    role = value.get("role")
    common = {"id": str(value["id"]), "timestamp": int(value["timestamp"])}
    if role == "user":
        return UserMessage(
            content=_user_content(value["content"]),
            display_content=(
                str(value["display_content"]) if value.get("display_content") is not None else None
            ),
            attachments=tuple(
                ManagedAssetAttachment(
                    asset_id=str(item["asset_id"]),
                    name=str(item["name"]),
                    mime_type=str(item["mime_type"]),
                )
                for item in value.get("attachments", [])
                if isinstance(item, dict)
                and all(key in item for key in ("asset_id", "name", "mime_type"))
            ),
            **common,
        )
    if role == "system":
        return SystemMessage(content=_system_content(value["content"]), **common)
    if role == "assistant":
        return AssistantMessage(
            content=tuple(_content_from_dict(item) for item in value["content"]),
            api=str(value["api"]),
            provider=str(value["provider"]),
            model=str(value["model"]),
            usage=_usage_from_dict(value["usage"]),
            stop_reason=str(value["stop_reason"]),  # type: ignore[arg-type]
            response_model=value.get("response_model"),
            response_id=value.get("response_id"),
            error_message=value.get("error_message"),
            **common,
        )
    if role == "tool_result":
        result_value = value["result"]
        return ToolResultMessage(
            tool_call_id=str(value["tool_call_id"]),
            tool_name=str(value["tool_name"]),
            result=ToolResult(
                content=tuple(_visible_content_from_dict(item) for item in result_value["content"]),
                details=result_value.get("details"),
                usage=_usage_from_dict(result_value["usage"])
                if result_value.get("usage") is not None
                else None,
                is_error=bool(result_value.get("is_error", False)),
                added_tool_names=tuple(result_value.get("added_tool_names", [])),
                terminate=bool(result_value.get("terminate", False)),
            ),
            **common,
        )
    if role == "custom":
        return CustomMessage(name=str(value["name"]), payload=dict(value["payload"]), **common)
    raise ValueError(f"Unknown agent message role: {role!r}")


def message_from_json(value: str) -> Message:
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise TypeError("Message JSON must contain an object")
    return message_from_dict(decoded)


def _user_content(value: Any) -> str | tuple[TextContent | ImageContent, ...]:
    if isinstance(value, str):
        return value
    return tuple(_visible_content_from_dict(item) for item in value)


def _system_content(value: Any) -> str | tuple[TextContent, ...]:
    if isinstance(value, str):
        return value
    content = tuple(_content_from_dict(item) for item in value)
    if not all(isinstance(item, TextContent) for item in content):
        raise ValueError("System message content must contain text blocks")
    return tuple(item for item in content if isinstance(item, TextContent))


def _content_from_dict(value: dict[str, Any]) -> ContentBlock:
    content_type = value.get("type")
    if content_type == "text":
        return TextContent(text=str(value["text"]), text_signature=value.get("text_signature"))
    if content_type == "thinking":
        return ThinkingContent(
            thinking=str(value["thinking"]),
            thinking_signature=value.get("thinking_signature"),
            redacted=value.get("redacted"),
        )
    if content_type == "image":
        return ImageContent(data=str(value["data"]), mime_type=str(value["mime_type"]))
    if content_type == "tool_call":
        return ToolCall(
            id=str(value["id"]),
            name=str(value["name"]),
            arguments=dict(value["arguments"]),
            thought_signature=value.get("thought_signature"),
        )
    raise ValueError(f"Unknown content block type: {content_type!r}")


def _visible_content_from_dict(value: dict[str, Any]) -> VisibleContent:
    content = _content_from_dict(value)
    if isinstance(content, TextContent | ImageContent):
        return content
    raise ValueError("Tool result content must be text or image")


def _usage_from_dict(value: dict[str, Any]) -> Usage:
    cost_value = value.get("cost", {})
    return Usage(
        input_tokens=int(value.get("input_tokens", 0)),
        output_tokens=int(value.get("output_tokens", 0)),
        cache_read_tokens=int(value.get("cache_read_tokens", 0)),
        cache_write_tokens=int(value.get("cache_write_tokens", 0)),
        total_tokens=int(value.get("total_tokens", 0)),
        cache_write_1h_tokens=value.get("cache_write_1h_tokens"),
        reasoning_tokens=value.get("reasoning_tokens"),
        cost=Cost(
            input=float(cost_value.get("input", 0)),
            output=float(cost_value.get("output", 0)),
            cache_read=float(cost_value.get("cache_read", 0)),
            cache_write=float(cost_value.get("cache_write", 0)),
            total=float(cost_value.get("total", 0)),
        ),
    )
