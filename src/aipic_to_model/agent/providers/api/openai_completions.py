from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any, Literal

import httpx

from ....infrastructure.local_inference import normalize_loopback_base_url
from ...core.errors import ProviderError
from ...core.events import CancellationToken
from ...core.models import (
    AssistantMessage,
    ImageContent,
    ProviderEvent,
    ProviderEventType,
    TextContent,
    ThinkingContent,
    ToolCall,
    Usage,
)
from ..base import ModelRequest


class OpenAICompletionsProvider:
    def __init__(
        self,
        credential_resolver: Callable[[str], str | None],
        *,
        client: httpx.AsyncClient | None = None,
        include_stream_usage: bool = True,
        enforce_loopback: bool = False,
    ) -> None:
        self._credential_resolver = credential_resolver
        self._client = client
        self._include_stream_usage = include_stream_usage
        self._enforce_loopback = enforce_loopback

    async def stream(
        self, request: ModelRequest, cancellation: CancellationToken
    ) -> AsyncIterator[ProviderEvent]:
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
        provider_tool_names = self._provider_tool_names(request.tools)
        agent_tool_names = {
            provider_name: agent_name for agent_name, provider_name in provider_tool_names.items()
        }
        payload: dict[str, Any] = {
            "model": request.profile.model,
            "messages": [
                self._message(
                    item,
                    provider_tool_names,
                    include_reasoning=request.reasoning_effort is not None,
                )
                for item in request.messages
            ],
            "stream": True,
        }
        if self._include_stream_usage:
            payload["stream_options"] = {"include_usage": True}
        if request.tools:
            payload["tools"] = [
                self._wire_tool(tool, provider_tool_names) for tool in request.tools
            ]
        if request.tool_choice == "required" and request.tools:
            payload["tool_choice"] = "required"
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_output_tokens or request.profile.max_output_tokens:
            payload["max_tokens"] = request.max_output_tokens or request.profile.max_output_tokens
        if request.reasoning_effort is not None:
            payload["reasoning_effort"] = request.reasoning_effort
        base_url = request.profile.base_url.rstrip("/")
        if self._enforce_loopback:
            base_url = normalize_loopback_base_url(base_url)
        url = f"{base_url}/chat/completions"
        client = self._client or httpx.AsyncClient(
            timeout=request.profile.timeout_seconds,
            follow_redirects=False,
            trust_env=not self._enforce_loopback,
        )
        owns = self._client is None
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        content_order: list[tuple[str, int | None]] = []
        tool_calls: dict[int, dict[str, str]] = {}
        usage = Usage()
        stop_reason = "stop"
        has_finish_reason = False
        response_id: str | None = None
        reasoning_started = False
        reasoning_ended = False
        try:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                if response.status_code >= 400:
                    error_code = await self._safe_error_code(response)
                    suffix = f"; code={error_code}" if error_code is not None else ""
                    raise ProviderError(
                        f"Provider request failed ({response.status_code}{suffix}).",
                        retryable=response.status_code in {429, 500, 502, 503, 504},
                        status_code=response.status_code,
                        error_code=error_code,
                    )
                yield ProviderEvent(ProviderEventType.MESSAGE_START)
                async for line in response.aiter_lines():
                    cancellation.raise_if_cancelled()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if response_id is None and isinstance(chunk.get("id"), str):
                        response_id = chunk["id"]
                    usage_data = chunk.get("usage")
                    if usage_data:
                        usage = Usage(
                            input_tokens=usage_data.get("prompt_tokens", 0),
                            output_tokens=usage_data.get("completion_tokens", 0),
                            total_tokens=usage_data.get("total_tokens", 0),
                        )
                        yield ProviderEvent(
                            ProviderEventType.USAGE,
                            usage=usage,
                        )
                    for choice in chunk.get("choices", []):
                        delta = choice.get("delta", {})
                        finish_reason = choice.get("finish_reason")
                        if finish_reason is not None:
                            stop_reason = _stop_reason(finish_reason)
                            has_finish_reason = True
                        reasoning = _reasoning_delta(delta)
                        if reasoning:
                            if not reasoning_started:
                                reasoning_started = True
                                content_order.append(("thinking", None))
                                yield ProviderEvent(ProviderEventType.REASONING_START)
                            thinking_parts.append(reasoning)
                            yield ProviderEvent(
                                ProviderEventType.REASONING_DELTA, delta=reasoning
                            )
                        if delta.get("content"):
                            if reasoning_started and not reasoning_ended:
                                reasoning_ended = True
                                yield ProviderEvent(ProviderEventType.REASONING_END)
                            if not text_parts:
                                content_order.append(("text", None))
                            text_parts.append(str(delta["content"]))
                            yield ProviderEvent(
                                ProviderEventType.TEXT_DELTA, delta=str(delta["content"])
                            )
                        for call in delta.get("tool_calls", []):
                            if reasoning_started and not reasoning_ended:
                                reasoning_ended = True
                                yield ProviderEvent(ProviderEventType.REASONING_END)
                            index = int(call.get("index", 0))
                            state = tool_calls.get(index)
                            if state is None:
                                state = {
                                    "id": f"call_{index}",
                                    "name": "",
                                    "arguments": "",
                                }
                                tool_calls[index] = state
                                content_order.append(("tool", index))
                            if call.get("id"):
                                state["id"] = str(call["id"])
                            function = call.get("function", {})
                            if function.get("name"):
                                state["name"] = str(function["name"])
                            if "function" in call and call["function"].get("name"):
                                yield ProviderEvent(
                                    ProviderEventType.TOOL_CALL_START,
                                    tool_call=ToolCall(
                                        state["id"],
                                        state["name"],
                                        {},
                                    ),
                                    content_index=index,
                                )
                            if "function" in call and call["function"].get("arguments"):
                                arguments_delta = str(call["function"]["arguments"])
                                state["arguments"] += arguments_delta
                                yield ProviderEvent(
                                    ProviderEventType.TOOL_CALL_ARGUMENTS_DELTA,
                                    delta=arguments_delta,
                                    content_index=index,
                                )
                if reasoning_started and not reasoning_ended:
                    yield ProviderEvent(ProviderEventType.REASONING_END)
                parsed_calls: dict[int, ToolCall] = {}
                for index, call in tool_calls.items():
                    try:
                        arguments = json.loads(call["arguments"] or "{}")
                    except json.JSONDecodeError as error:
                        raise ProviderError("Provider returned invalid tool arguments.") from error
                    if not isinstance(arguments, dict) or not call["name"]:
                        raise ProviderError("Provider returned an incomplete tool call.")
                    parsed_calls[index] = ToolCall(
                        call["id"], agent_tool_names.get(call["name"], call["name"]), arguments
                    )
                content: list[TextContent | ThinkingContent | ToolCall] = []
                for kind, index in content_order:
                    if kind == "thinking":
                        content.append(ThinkingContent("".join(thinking_parts)))
                    elif kind == "text":
                        content.append(TextContent("".join(text_parts)))
                    elif index is not None:
                        content.append(parsed_calls[index])
                if not has_finish_reason:
                    raise ProviderError("Provider stream ended without finish_reason.")
                if tool_calls and stop_reason not in {"tool_use", "length"}:
                    raise ProviderError(
                        "Provider returned tool calls with an invalid finish_reason."
                    )
                if not tool_calls and not text_parts and stop_reason == "stop":
                    raise ProviderError("Provider returned an empty assistant response.")
                yield ProviderEvent(
                    ProviderEventType.MESSAGE_END,
                    message=AssistantMessage(
                        tuple(content),
                        api="openai-completions",
                        provider=request.profile.provider_id,
                        model=request.profile.model,
                        usage=usage,
                        stop_reason=stop_reason,
                        response_id=response_id,
                    ),
                )
        except httpx.TimeoutException as error:
            raise ProviderError("Provider request timed out.", retryable=True) from error
        except httpx.HTTPError as error:
            raise ProviderError("Provider transport failed.", retryable=True) from error
        finally:
            if owns:
                await client.aclose()

    @staticmethod
    def _message(
        message: Any,
        provider_tool_names: dict[str, str] | None = None,
        *,
        include_reasoning: bool = False,
    ) -> dict[str, Any]:
        if message.role in {"user", "system"}:
            return {
                "role": message.role,
                "content": OpenAICompletionsProvider._user_or_system_content(message.content),
            }
        if message.role == "assistant":
            payload: dict[str, Any] = {
                "role": "assistant",
                "content": "".join(item.text for item in message.content if hasattr(item, "text")),
            }
            if include_reasoning:
                reasoning = "".join(
                    item.thinking for item in message.content if isinstance(item, ThinkingContent)
                )
                if reasoning:
                    payload["reasoning"] = reasoning
            tool_calls = [item for item in message.content if isinstance(item, ToolCall)]
            if tool_calls:
                payload["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": (provider_tool_names or {}).get(call.name, call.name),
                            "arguments": json.dumps(call.arguments),
                        },
                    }
                    for call in tool_calls
                ]
            return payload
        if message.role == "tool_result":
            return {
                "role": "tool",
                "tool_call_id": message.tool_call_id,
                "content": "".join(item.text for item in message.content if hasattr(item, "text")),
            }
        return {"role": "user", "content": ""}

    @staticmethod
    def _user_or_system_content(content: object) -> object:
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

    @staticmethod
    def _wire_tool(
        tool: dict[str, object], provider_tool_names: dict[str, str]
    ) -> dict[str, object]:
        function = tool.get("function")
        if not isinstance(function, Mapping) or not isinstance(function.get("name"), str):
            raise TypeError("OpenAI tool entries must contain a function name.")
        wired = dict(tool)
        wired["function"] = {
            **dict(function),
            "name": provider_tool_names[function["name"]],
        }
        return wired

    @staticmethod
    def _provider_tool_names(tools: tuple[dict[str, object], ...]) -> dict[str, str]:
        """Make OpenAI function names wire-safe while preserving Agent-facing names."""

        result: dict[str, str] = {}
        used: set[str] = set()
        for tool in tools:
            function = tool.get("function")
            if not isinstance(function, Mapping) or not isinstance(function.get("name"), str):
                raise TypeError("OpenAI tool entries must contain a function name.")
            agent_name = function["name"]
            stem = re.sub(r"[^A-Za-z0-9_-]", "_", agent_name)[:64] or "tool"
            provider_name = stem
            index = 2
            while provider_name in used:
                suffix = f"_{index}"
                provider_name = f"{stem[: 64 - len(suffix)]}{suffix}"
                index += 1
            result[agent_name] = provider_name
            used.add(provider_name)
        return result

    @staticmethod
    async def _safe_error_code(response: httpx.Response) -> str | None:
        """Extract a machine code/classification, never an error body or request detail."""

        try:
            body = json.loads((await response.aread()).decode("utf-8"))
        except UnicodeDecodeError, json.JSONDecodeError:
            return None
        if not isinstance(body, dict):
            return None
        error = body.get("error")
        if isinstance(error, str):
            return _classify_provider_message(error)
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            code = error["code"][:80]
            message = error.get("message")
            if not isinstance(message, str):
                return code
            normalized = message.lower()
            classifications = {
                "credential": ("api key", "authentication", "authorization", "token"),
                "model": ("model",),
                "billing": ("balance", "quota", "billing"),
                "request_format": ("format", "parameter", "request"),
            }
            for label, tokens in classifications.items():
                if any(token in normalized for token in tokens):
                    return f"{code}/{label}"
            return code
        return None


def _classify_provider_message(message: str) -> str | None:
    """Classify a local Provider error without retaining response text or paths."""

    normalized = message.lower()
    classifications = {
        "resource_exhausted": (
            "out of memory",
            "memory allocation",
            "cuda error",
            "page file",
            "not enough memory",
        ),
        "runner_unavailable": ("runner process", "runner exited", "llama runner"),
        "context_overflow": ("context length", "input is too long", "context overflow"),
        "vision_request": ("image", "vision", "multimodal"),
        "model_load_failed": ("load model", "model not found", "model is required"),
        "request_format": ("invalid request", "invalid parameter", "invalid format"),
    }
    for label, tokens in classifications.items():
        if any(token in normalized for token in tokens):
            return label
    return "provider_internal"


def _stop_reason(value: object) -> Literal["stop", "length", "tool_use"]:
    """Normalize OpenAI-compatible terminal reasons using Pi's strict contract."""

    reason = str(value)
    if reason in {"stop", "end"}:
        return "stop"
    if reason == "length":
        return "length"
    if reason in {"function_call", "tool_calls"}:
        return "tool_use"
    raise ProviderError(f"Provider finish_reason: {reason}")


def _reasoning_delta(delta: object) -> str | None:
    """Read Ollama's OpenAI field plus common compatible aliases."""

    if not isinstance(delta, Mapping):
        return None
    for field in ("reasoning", "reasoning_content", "thinking"):
        value = delta.get(field)
        if isinstance(value, str) and value:
            return value
    return None
