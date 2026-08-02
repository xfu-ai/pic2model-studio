"""HTTP transport shared by the non-OpenAI-completions protocol adapters."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx

from ...core.errors import ContextOverflowError, ProviderError
from ...core.events import CancellationToken
from ...core.models import (
    AssistantMessage,
    ProviderEvent,
    ProviderEventType,
    TextContent,
    ThinkingContent,
    ToolCall,
    Usage,
)
from ..adapters import build_payload, get_adapter, parse_sse_event
from ..base import ModelRequest


class AdapterProvider:
    """Stream a protocol selected by descriptor/catalog metadata.

    The transport deliberately has no provider-id branches: endpoint and wire
    protocol come from the selected adapter, while auth/headers come from the
    descriptor/profile.
    """

    def __init__(
        self,
        adapter_id: str,
        credential_resolver: Callable[[str], str | None],
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._adapter_id = adapter_id
        self._credential_resolver = credential_resolver
        self._client = client

    async def stream(
        self, request: ModelRequest, cancellation: CancellationToken
    ) -> AsyncIterator[ProviderEvent]:
        adapter = get_adapter(self._adapter_id)
        cancellation.raise_if_cancelled()
        key = self._credential_resolver(
            request.profile.credential_ref or request.profile.provider_id
        )
        headers = {
            "accept": "text/event-stream",
            "content-type": "application/json",
            **request.profile.headers,
        }
        if key:
            headers["authorization"] = f"Bearer {key}"
        url = f"{request.profile.base_url.rstrip('/')}{adapter.path.format(model=request.profile.model)}"
        payload = build_payload(adapter.adapter_id, request)
        client = self._client or httpx.AsyncClient(timeout=request.profile.timeout_seconds)
        owns_client = self._client is None
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        # The identity carried by an arguments delta is not universally the
        # provider-visible tool-call identity.  Responses, for example, uses
        # ``item_id`` for deltas and ``call_id`` for the next turn.  Keep the
        # mapping here rather than making Agent Core aware of wire protocols.
        calls: dict[str, dict[str, str]] = {}
        wire_call_ids: dict[str, str] = {}
        usage = Usage()
        response_id: str | None = None
        try:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    if response.status_code == 400 and _is_context_overflow(body):
                        raise ContextOverflowError()
                    raise ProviderError(
                        f"Provider request failed ({response.status_code}).",
                        retryable=response.status_code in {408, 429, 500, 502, 503, 504},
                        status_code=response.status_code,
                    )
                yield ProviderEvent(ProviderEventType.MESSAGE_START)
                event_name: str | None = None
                async for line in response.aiter_lines():
                    cancellation.raise_if_cancelled()
                    if not line:
                        event_name = None
                        continue
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        item = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(item, dict):
                        continue
                    if response_id is None and isinstance(item.get("id"), str):
                        response_id = item["id"]
                    delta, call, item_usage = parse_sse_event(adapter.adapter_id, event_name, item)
                    if item_usage is not None:
                        usage = item_usage
                        yield ProviderEvent(ProviderEventType.USAGE, usage=usage)
                    if delta:
                        text_parts.append(delta)
                        yield ProviderEvent(ProviderEventType.TEXT_DELTA, delta=delta)
                    thinking = _thinking_delta(adapter.adapter_id, event_name, item)
                    if thinking:
                        thinking_parts.append(thinking)
                    openai_tools = _openai_tool_frames(adapter.adapter_id, item)
                    if openai_tools:
                        for wire_id, provider_id, name, arguments_delta in openai_tools:
                            identifier = wire_call_ids.get(wire_id, provider_id or wire_id)
                            if provider_id:
                                wire_call_ids[wire_id] = provider_id
                                identifier = provider_id
                            state = calls.setdefault(identifier, {"name": "", "arguments": ""})
                            if name and not state["name"]:
                                state["name"] = name
                                yield ProviderEvent(
                                    ProviderEventType.TOOL_CALL_START,
                                    tool_call=ToolCall(identifier, name, {}),
                                )
                            if arguments_delta:
                                state["arguments"] += arguments_delta
                                yield ProviderEvent(
                                    ProviderEventType.TOOL_CALL_ARGUMENTS_DELTA,
                                    delta=arguments_delta,
                                )
                    else:
                        if call is not None and call.name:
                            call_id = _provider_call_id(adapter.adapter_id, event_name, item, call)
                            state = calls.setdefault(call_id, {"name": call.name, "arguments": ""})
                            state["name"] = call.name or state["name"]
                            wire_call_ids[_wire_item_id(adapter.adapter_id, event_name, item)] = (
                                call_id
                            )
                            yield ProviderEvent(
                                ProviderEventType.TOOL_CALL_START,
                                tool_call=ToolCall(call_id, state["name"], {}),
                            )
                        arguments_delta = _arguments_delta(adapter.adapter_id, event_name, item)
                        if arguments_delta:
                            wire_id = _wire_item_id(adapter.adapter_id, event_name, item)
                            identifier = wire_call_ids.get(wire_id, wire_id)
                            state = calls.setdefault(
                                identifier,
                                {
                                    "name": _call_name(adapter.adapter_id, event_name, item),
                                    "arguments": "",
                                },
                            )
                            state["arguments"] += arguments_delta
                            yield ProviderEvent(
                                ProviderEventType.TOOL_CALL_ARGUMENTS_DELTA, delta=arguments_delta
                            )
                content: list[TextContent | ThinkingContent | ToolCall] = []
                if text_parts:
                    content.append(TextContent("".join(text_parts)))
                if thinking_parts:
                    content.append(ThinkingContent("".join(thinking_parts)))
                for call_id, state in calls.items():
                    try:
                        arguments = json.loads(state["arguments"] or "{}")
                    except json.JSONDecodeError as error:
                        raise ProviderError("Provider returned invalid tool arguments.") from error
                    if not isinstance(arguments, dict) or not state["name"]:
                        raise ProviderError("Provider returned an incomplete tool call.")
                    content.append(ToolCall(call_id, state["name"], arguments))
                yield ProviderEvent(
                    ProviderEventType.MESSAGE_END,
                    message=AssistantMessage(
                        tuple(content),
                        api=adapter.adapter_id,
                        provider=request.profile.provider_id,
                        model=request.profile.model,
                        usage=usage,
                        stop_reason="tool_use" if calls else "stop",
                        response_id=response_id,
                    ),
                )
        except httpx.TimeoutException as error:
            raise ProviderError("Provider request timed out.", retryable=True) from error
        except httpx.HTTPError as error:
            raise ProviderError("Provider transport failed.", retryable=True) from error
        finally:
            if owns_client:
                await client.aclose()


def _arguments_delta(adapter_id: str, event_name: str | None, item: dict[str, Any]) -> str | None:
    if adapter_id.endswith("responses") and event_name == "response.function_call_arguments.delta":
        value = item.get("delta")
        return value if isinstance(value, str) else None
    if adapter_id == "anthropic-messages" and event_name == "content_block_delta":
        delta = item.get("delta")
        if isinstance(delta, dict) and delta.get("type") == "input_json_delta":
            value = delta.get("partial_json")
            return value if isinstance(value, str) else None
    if adapter_id in {"google-generative-ai", "google-vertex"}:
        candidates = item.get("candidates")
        if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
            content = candidates[0].get("content")
            if isinstance(content, dict):
                parts = content.get("parts")
                if isinstance(parts, list):
                    for part in parts:
                        if not isinstance(part, dict):
                            continue
                        call = part.get("functionCall")
                        if isinstance(call, dict) and isinstance(call.get("args"), dict):
                            return json.dumps(
                                call["args"], ensure_ascii=False, separators=(",", ":")
                            )
    choices = item.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        delta = choices[0].get("delta")
        if isinstance(delta, dict):
            calls = delta.get("tool_calls")
            if isinstance(calls, list) and calls and isinstance(calls[0], dict):
                function = calls[0].get("function")
                if isinstance(function, dict) and isinstance(function.get("arguments"), str):
                    return function["arguments"]
    return None


def _openai_tool_frames(
    adapter_id: str, item: dict[str, Any]
) -> tuple[tuple[str, str | None, str | None, str | None], ...]:
    if adapter_id not in {"openai-completions", "mistral-conversations"}:
        return ()
    choices = item.get("choices")
    if not isinstance(choices, list):
        return ()
    result: list[tuple[str, str | None, str | None, str | None]] = []
    for choice_index, choice in enumerate(choices):
        if not isinstance(choice, dict) or not isinstance(choice.get("delta"), dict):
            continue
        raw_calls = choice["delta"].get("tool_calls")
        if not isinstance(raw_calls, list):
            continue
        for position, raw_call in enumerate(raw_calls):
            if not isinstance(raw_call, dict):
                continue
            index = raw_call.get("index", position)
            function = raw_call.get("function")
            if not isinstance(function, dict):
                continue
            identifier = raw_call.get("id")
            name = function.get("name")
            arguments = function.get("arguments")
            result.append(
                (
                    f"{choice_index}:{index}",
                    identifier if isinstance(identifier, str) else None,
                    name if isinstance(name, str) else None,
                    arguments if isinstance(arguments, str) else None,
                )
            )
    return tuple(result)


def _wire_item_id(adapter_id: str, event_name: str | None, item: dict[str, Any]) -> str:
    if adapter_id.endswith("responses"):
        output = item.get("item")
        if isinstance(output, dict) and isinstance(output.get("id"), str):
            return output["id"]
    if adapter_id == "anthropic-messages":
        return str(item.get("index", "call_0"))
    if adapter_id in {"google-generative-ai", "google-vertex"}:
        candidates = item.get("candidates")
        if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
            content = candidates[0].get("content")
            if isinstance(content, dict):
                parts = content.get("parts")
                if isinstance(parts, list):
                    for index, part in enumerate(parts):
                        if isinstance(part, dict) and isinstance(part.get("functionCall"), dict):
                            call = part["functionCall"]
                            return str(call.get("id", f"call_{index}"))
    value = item.get("item_id", item.get("call_id", item.get("id", "call_0")))
    return str(value)


def _call_name(adapter_id: str, event_name: str | None, item: dict[str, Any]) -> str:
    if adapter_id == "anthropic-messages":
        block = item.get("content_block")
        if isinstance(block, dict):
            value = block.get("name", "")
            return value if isinstance(value, str) else ""
    if adapter_id in {"google-generative-ai", "google-vertex"}:
        candidates = item.get("candidates")
        if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
            content = candidates[0].get("content")
            if isinstance(content, dict):
                parts = content.get("parts")
                if isinstance(parts, list):
                    for part in parts:
                        if isinstance(part, dict) and isinstance(part.get("functionCall"), dict):
                            value = part["functionCall"].get("name", "")
                            return value if isinstance(value, str) else ""
    value = item.get("name", "")
    if isinstance(value, str):
        return value
    return ""


def _provider_call_id(
    adapter_id: str, event_name: str | None, item: dict[str, Any], call: ToolCall
) -> str:
    """Return the ID that must be echoed in a subsequent tool result."""

    if adapter_id.endswith("responses"):
        output = item.get("item")
        if isinstance(output, dict) and isinstance(output.get("call_id"), str):
            return output["call_id"]
        value = item.get("call_id")
        if isinstance(value, str):
            return value
    return call.id


def _thinking_delta(adapter_id: str, event_name: str | None, item: dict[str, Any]) -> str | None:
    if adapter_id.endswith("responses") and event_name in {
        "response.reasoning_summary_text.delta",
        "response.reasoning_text.delta",
    }:
        value = item.get("delta")
        return value if isinstance(value, str) else None
    if adapter_id == "anthropic-messages" and event_name == "content_block_delta":
        delta = item.get("delta")
        if isinstance(delta, dict) and delta.get("type") == "thinking_delta":
            value = delta.get("thinking")
            return value if isinstance(value, str) else None
    if adapter_id in {"google-generative-ai", "google-vertex"}:
        candidates = item.get("candidates")
        if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
            content = candidates[0].get("content")
            if isinstance(content, dict):
                parts = content.get("parts")
                if isinstance(parts, list):
                    values = [
                        text
                        for part in parts
                        if isinstance(part, dict)
                        and part.get("thought") is True
                        and isinstance((text := part.get("text")), str)
                    ]
                    return "".join(values) or None
    return None


def _is_context_overflow(body: bytes) -> bool:
    normalized = body.lower()
    return any(
        marker in normalized
        for marker in (
            b"context length",
            b"context window",
            b"input token count",
            b"too many tokens",
        )
    )
