"""Protocol adapters shared by all frozen provider descriptors.

Adapters own wire-format differences.  They intentionally expose a small
testable request/response surface instead of leaking provider identifiers into
the Agent Core or Harness.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from ..core.models import AssistantMessage, ImageContent, Message, TextContent, ToolCall, Usage
from .catalog import ADAPTER_IDS


@dataclass(frozen=True)
class AdapterDescriptor:
    adapter_id: str
    transport: str
    path: str
    text: bool = True
    tools: bool = True
    usage: bool = True
    reasoning: bool = False
    images: bool = False
    cache: bool = False


ADAPTERS = {
    "openai-completions": AdapterDescriptor(
        "openai-completions", "sse", "/chat/completions", reasoning=True, images=True, cache=True
    ),
    "openai-responses": AdapterDescriptor(
        "openai-responses", "sse", "/responses", reasoning=True, images=True, cache=True
    ),
    "azure-openai-responses": AdapterDescriptor(
        "azure-openai-responses", "sse", "/openai/responses", reasoning=True, images=True
    ),
    "openai-codex-responses": AdapterDescriptor(
        "openai-codex-responses", "sse", "/codex/responses", reasoning=True, images=True
    ),
    "anthropic-messages": AdapterDescriptor(
        "anthropic-messages", "sse", "/v1/messages", reasoning=True, images=True, cache=True
    ),
    "bedrock-converse-stream": AdapterDescriptor(
        "bedrock-converse-stream",
        "eventstream",
        "/model/{model}/converse-stream",
        reasoning=True,
        images=True,
        cache=True,
    ),
    "google-generative-ai": AdapterDescriptor(
        "google-generative-ai",
        "sse",
        "/models/{model}:streamGenerateContent?alt=sse",
        reasoning=True,
        images=True,
        cache=True,
    ),
    "google-vertex": AdapterDescriptor(
        "google-vertex",
        "sse",
        "/models/{model}:streamGenerateContent?alt=sse",
        reasoning=True,
        images=True,
        cache=True,
    ),
    "mistral-conversations": AdapterDescriptor(
        "mistral-conversations", "sse", "/v1/chat/completions", images=True
    ),
    "pi-messages": AdapterDescriptor(
        "pi-messages", "sse", "/messages", reasoning=True, images=True, cache=True
    ),
    "openrouter-images": AdapterDescriptor(
        "openrouter-images", "http", "/chat/completions", tools=False, images=True
    ),
}

assert set(ADAPTERS) == set(ADAPTER_IDS)


def get_adapter(adapter_id: str) -> AdapterDescriptor:
    return ADAPTERS[adapter_id]


def build_payload(adapter_id: str, request: Any) -> dict[str, object]:
    """Convert core DTOs into the selected protocol's request body."""

    model = request.profile.model
    messages = request.messages
    tools = request.tools
    if adapter_id == "openai-completions" or adapter_id == "mistral-conversations":
        return _openai_payload(
            model, messages, tools, request, adapter_id == "mistral-conversations"
        )
    if adapter_id in {"openai-responses", "azure-openai-responses", "openai-codex-responses"}:
        payload: dict[str, object] = {
            "model": model,
            "input": _responses_messages(messages),
            "stream": True,
        }
        if tools:
            payload["tools"] = [_responses_tool(tool) for tool in tools]
        if request.max_output_tokens or request.profile.max_output_tokens:
            payload["max_output_tokens"] = (
                request.max_output_tokens or request.profile.max_output_tokens
            )
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if adapter_id == "openai-codex-responses":
            payload["store"] = False
        return payload
    if adapter_id == "anthropic-messages":
        system = "\n".join(_plain_text(item) for item in messages if item.role == "system")
        payload = {
            "model": model,
            "messages": [_anthropic_message(item) for item in messages if item.role != "system"],
            "stream": True,
            "max_tokens": request.max_output_tokens or request.profile.max_output_tokens or 1024,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [_anthropic_tool(tool) for tool in tools]
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        return payload
    if adapter_id in {"google-generative-ai", "google-vertex"}:
        payload = {
            "contents": [_google_message(item) for item in messages if item.role != "system"]
        }
        system = "\n".join(_plain_text(item) for item in messages if item.role == "system")
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            payload["tools"] = [{"functionDeclarations": [_google_tool(tool) for tool in tools]}]
        config: dict[str, object] = {}
        if request.temperature is not None:
            config["temperature"] = request.temperature
        if request.max_output_tokens or request.profile.max_output_tokens:
            config["maxOutputTokens"] = (
                request.max_output_tokens or request.profile.max_output_tokens
            )
        if config:
            payload["generationConfig"] = config
        return payload
    if adapter_id == "bedrock-converse-stream":
        payload = {
            "messages": [_bedrock_message(item) for item in messages if item.role != "system"]
        }
        system = [_plain_text(item) for item in messages if item.role == "system"]
        if system:
            payload["system"] = [{"text": text} for text in system]
        if tools:
            payload["toolConfig"] = {"tools": [{"toolSpec": _bedrock_tool(tool)} for tool in tools]}
        return payload
    if adapter_id == "pi-messages":
        return {
            "model": model,
            "messages": [_pi_message(item) for item in messages],
            "tools": list(tools),
            "stream": True,
        }
    if adapter_id == "openrouter-images":
        payload = _openai_payload(model, messages, (), request, False)
        payload["stream"] = False
        payload["modalities"] = ["image", "text"]
        return payload
    raise ValueError(f"Unknown adapter: {adapter_id}")


def parse_message(
    adapter_id: str, payload: Mapping[str, object], provider: str, model: str
) -> AssistantMessage:
    """Normalize a terminal non-streaming payload for HTTP/image contracts."""

    text, calls, usage, stop_reason = _read_response(adapter_id, payload)
    content = tuple(([TextContent(text)] if text else []) + calls)
    return AssistantMessage(
        content,
        api=adapter_id,
        provider=provider,
        model=model,
        usage=usage,
        stop_reason=stop_reason,
    )


def parse_sse_event(
    adapter_id: str, event_name: str | None, payload: Mapping[str, object]
) -> tuple[str | None, ToolCall | None, Usage | None]:
    """Normalize one SSE JSON frame without assuming event ordering.

    Partial function arguments remain deltas; callers own accumulation and
    parse only after the protocol's terminal event.
    """

    if adapter_id in {"openai-completions", "mistral-conversations"}:
        choices = payload.get("choices", [])
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            delta = choices[0].get("delta", {})
            if isinstance(delta, dict):
                content = delta.get("content")
                if isinstance(content, str):
                    return content, None, _usage(payload)
                tool_calls = delta.get("tool_calls", [])
                if isinstance(tool_calls, list) and tool_calls and isinstance(tool_calls[0], dict):
                    call = tool_calls[0]
                    function = call.get("function", {})
                    if isinstance(function, dict) and isinstance(function.get("name"), str):
                        return (
                            None,
                            ToolCall(str(call.get("id", "call_0")), function["name"], {}),
                            _usage(payload),
                        )
        return None, None, _usage(payload)
    if adapter_id.endswith("responses") and event_name == "response.output_text.delta":
        delta = payload.get("delta")
        return (delta if isinstance(delta, str) else None), None, _usage(payload)
    if adapter_id.endswith("responses") and event_name == "response.output_item.added":
        item = payload.get("item")
        if isinstance(item, Mapping) and item.get("type") == "function_call":
            name = item.get("name")
            identifier = item.get("call_id", item.get("id", "call_0"))
            if isinstance(name, str):
                return None, ToolCall(str(identifier), name, {}), _usage(payload)
    if adapter_id.endswith("responses") and event_name == "response.function_call_arguments.delta":
        name = payload.get("name")
        identifier = payload.get("item_id", payload.get("call_id", "call_0"))
        return None, ToolCall(str(identifier), str(name or ""), {}), _usage(payload)
    if adapter_id in {"google-generative-ai", "google-vertex"}:
        candidates = payload.get("candidates", [])
        if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
            content = candidates[0].get("content", {})
            if isinstance(content, dict):
                parts = content.get("parts", [])
                if isinstance(parts, list) and parts and isinstance(parts[0], dict):
                    part = parts[0]
                    if isinstance(part.get("text"), str):
                        return part["text"], None, _usage(payload)
                    call = part.get("functionCall")
                    if isinstance(call, dict) and isinstance(call.get("name"), str):
                        args = call.get("args", {})
                        return (
                            None,
                            ToolCall(
                                str(call.get("id", "call_0")),
                                call["name"],
                                args if isinstance(args, dict) else {},
                            ),
                            _usage(payload),
                        )
    if adapter_id == "anthropic-messages":
        if event_name == "content_block_start":
            block = payload.get("content_block")
            if isinstance(block, Mapping) and block.get("type") == "tool_use":
                identifier = block.get("id", f"call_{payload.get('index', 0)}")
                name = block.get("name")
                if isinstance(name, str):
                    return None, ToolCall(str(identifier), name, {}), _usage(payload)
        delta = payload.get("delta", {})
        if isinstance(delta, dict) and isinstance(delta.get("text"), str):
            return delta["text"], None, _usage(payload)
    return None, None, _usage(payload)


def _openai_payload(
    model: str,
    messages: tuple[Message, ...],
    tools: tuple[dict[str, object], ...],
    request: Any,
    mistral: bool,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": model,
        "messages": [_openai_message(item) for item in messages],
        "stream": True,
    }
    if tools:
        payload["tools"] = list(tools)
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    if request.max_output_tokens or request.profile.max_output_tokens:
        payload["max_tokens"] = request.max_output_tokens or request.profile.max_output_tokens
    if not mistral:
        payload["stream_options"] = {"include_usage": True}
    return payload


def _openai_message(message: Message) -> dict[str, object]:
    if message.role in {"user", "system"}:
        return {"role": message.role, "content": _openai_content(message)}
    if message.role == "assistant":
        calls = [item for item in message.content if isinstance(item, ToolCall)]
        result: dict[str, object] = {"role": "assistant", "content": _plain_text(message)}
        if calls:
            result["tool_calls"] = [
                {
                    "id": item.id,
                    "type": "function",
                    "function": {"name": item.name, "arguments": json.dumps(item.arguments)},
                }
                for item in calls
            ]
        return result
    if message.role == "tool_result":
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "content": _plain_text(message),
        }
    return {"role": "user", "content": ""}


def _openai_content(message: Message) -> object:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if not isinstance(content, tuple):
        return []
    parts: list[dict[str, object]] = []
    for item in content:
        if isinstance(item, TextContent):
            parts.append({"type": "text", "text": item.text})
        elif isinstance(item, ImageContent):
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{item.mime_type};base64,{item.data}"},
                }
            )
    return parts


def _responses_messages(messages: tuple[Message, ...]) -> list[dict[str, object]]:
    return [_openai_message(message) for message in messages]


def _responses_tool(tool: dict[str, object]) -> dict[str, object]:
    function = tool.get("function", tool)
    if not isinstance(function, Mapping):
        raise TypeError("Response tool must be an object.")
    name = function.get("name")
    if not isinstance(name, str):
        raise TypeError("Response tool must have a name.")
    return {
        "type": "function",
        "name": name,
        "description": function.get("description", ""),
        "parameters": function.get("parameters", {}),
    }


def _anthropic_message(message: Message) -> dict[str, object]:
    if message.role == "tool_result":
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id,
                    "content": _plain_text(message),
                    "is_error": message.is_error,
                }
            ],
        }
    role = "assistant" if message.role == "assistant" else "user"
    content: list[dict[str, object]] = []
    if message.role == "assistant":
        for item in message.content:
            if isinstance(item, TextContent):
                content.append({"type": "text", "text": item.text})
            if isinstance(item, ToolCall):
                content.append(
                    {"type": "tool_use", "id": item.id, "name": item.name, "input": item.arguments}
                )
    else:
        content = _anthropic_content(message)
    return {"role": role, "content": content}


def _anthropic_content(message: Message) -> list[dict[str, object]]:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, tuple):
        return []
    return [
        {"type": "text", "text": item.text}
        if isinstance(item, TextContent)
        else {
            "type": "image",
            "source": {"type": "base64", "media_type": item.mime_type, "data": item.data},
        }
        for item in content
        if isinstance(item, TextContent | ImageContent)
    ]


def _anthropic_tool(tool: dict[str, object]) -> dict[str, object]:
    function = tool.get("function", tool)
    if not isinstance(function, Mapping) or not isinstance(function.get("name"), str):
        raise TypeError("Anthropic tool must have a name.")
    return {
        "name": function["name"],
        "description": function.get("description", ""),
        "input_schema": function.get("parameters", {}),
    }


def _google_message(message: Message) -> dict[str, object]:
    role = "model" if message.role == "assistant" else "user"
    if message.role == "tool_result":
        return {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "name": message.tool_name,
                        "response": {"content": _plain_text(message)},
                    }
                }
            ],
        }
    return {"role": role, "parts": [{"text": _plain_text(message)}]}


def _google_tool(tool: dict[str, object]) -> dict[str, object]:
    function = tool.get("function", tool)
    if not isinstance(function, Mapping) or not isinstance(function.get("name"), str):
        raise TypeError("Google tool must have a name.")
    return {
        "name": function["name"],
        "description": function.get("description", ""),
        "parameters": function.get("parameters", {}),
    }


def _bedrock_message(message: Message) -> dict[str, object]:
    if message.role == "tool_result":
        return {
            "role": "user",
            "content": [
                {
                    "toolResult": {
                        "toolUseId": message.tool_call_id,
                        "content": [{"text": _plain_text(message)}],
                        "status": "error" if message.is_error else "success",
                    }
                }
            ],
        }
    role = "assistant" if message.role == "assistant" else "user"
    return {"role": role, "content": [{"text": _plain_text(message)}]}


def _bedrock_tool(tool: dict[str, object]) -> dict[str, object]:
    function = tool.get("function", tool)
    if not isinstance(function, Mapping) or not isinstance(function.get("name"), str):
        raise TypeError("Bedrock tool must have a name.")
    return {
        "name": function["name"],
        "description": function.get("description", ""),
        "inputSchema": {"json": function.get("parameters", {})},
    }


def _pi_message(message: Message) -> dict[str, object]:
    return {"role": message.role, "content": _plain_text(message)}


def _plain_text(message: Message) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if not isinstance(content, tuple):
        return ""
    return "\n".join(item.text for item in content if isinstance(item, TextContent))


def _usage(payload: Mapping[str, object]) -> Usage | None:
    raw = payload.get("usage", payload.get("usageMetadata"))
    if not isinstance(raw, Mapping):
        return None
    input_tokens = raw.get("prompt_tokens", raw.get("input_tokens", raw.get("promptTokenCount", 0)))
    output_tokens = raw.get(
        "completion_tokens", raw.get("output_tokens", raw.get("candidatesTokenCount", 0))
    )
    total_tokens = raw.get("total_tokens", raw.get("totalTokenCount", 0))
    if not all(isinstance(value, int) for value in (input_tokens, output_tokens, total_tokens)):
        return None
    cache_read = raw.get("cache_read_tokens", raw.get("cachedContentTokenCount", 0))
    cache_write = raw.get("cache_creation_input_tokens", raw.get("cacheWriteInputTokens", 0))
    reasoning = raw.get("reasoning_tokens", raw.get("thoughtsTokenCount"))
    if not isinstance(cache_read, int) or not isinstance(cache_write, int):
        return None
    if "promptTokenCount" in raw:
        input_tokens -= cache_read
        if isinstance(reasoning, int):
            output_tokens += reasoning
    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        reasoning_tokens=reasoning if isinstance(reasoning, int) else None,
        total_tokens=total_tokens or input_tokens + output_tokens,
    )


def _read_response(
    adapter_id: str, payload: Mapping[str, object]
) -> tuple[str, list[ToolCall], Usage, Literal["stop", "tool_use"]]:
    if adapter_id == "openrouter-images":
        data = payload.get("data", [])
        if isinstance(data, list) and data and isinstance(data[0], Mapping):
            url = data[0].get("url", "")
            return str(url), [], Usage(), "stop"
    text, call, usage = parse_sse_event(adapter_id, None, payload)
    return text or "", [call] if call else [], usage or Usage(), "tool_use" if call else "stop"
