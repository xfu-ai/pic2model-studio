"""OpenRouter image-generation Chat Completions transport."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx

from ...core.errors import ProviderError
from ...core.events import CancellationToken
from ...core.models import (
    AssistantMessage,
    ImageContent,
    ProviderEvent,
    ProviderEventType,
    TextContent,
    Usage,
)
from ..adapters import build_payload
from ..base import ModelRequest


class OpenRouterImagesProvider:
    def __init__(
        self,
        credential_resolver: Callable[[str], str | None],
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._credential_resolver = credential_resolver
        self._client = client

    async def stream(
        self, request: ModelRequest, cancellation: CancellationToken
    ) -> AsyncIterator[ProviderEvent]:
        cancellation.raise_if_cancelled()
        key = self._credential_resolver(
            request.profile.credential_ref or request.profile.provider_id
        )
        if not key:
            raise ProviderError("OpenRouter image credentials are not configured.")
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {key}",
            **request.profile.headers,
        }
        url = f"{request.profile.base_url.rstrip('/')}/chat/completions"
        client = self._client or httpx.AsyncClient(timeout=request.profile.timeout_seconds)
        owns_client = self._client is None
        try:
            response = await client.post(
                url, headers=headers, json=build_payload("openrouter-images", request)
            )
            if response.status_code >= 400:
                raise ProviderError(
                    f"OpenRouter image request failed ({response.status_code}).",
                    retryable=response.status_code in {408, 429, 500, 502, 503, 504},
                    status_code=response.status_code,
                )
            try:
                payload = response.json()
            except json.JSONDecodeError as error:
                raise ProviderError("OpenRouter image response was invalid JSON.") from error
            if not isinstance(payload, dict):
                raise ProviderError("OpenRouter image response was invalid.")
            content = _content(payload)
            usage = _usage(payload)
            yield ProviderEvent(ProviderEventType.MESSAGE_START)
            if usage.total_tokens:
                yield ProviderEvent(ProviderEventType.USAGE, usage=usage)
            yield ProviderEvent(
                ProviderEventType.MESSAGE_END,
                message=AssistantMessage(
                    tuple(content),
                    api="openrouter-images",
                    provider=request.profile.provider_id,
                    model=request.profile.model,
                    usage=usage,
                    response_id=payload.get("id") if isinstance(payload.get("id"), str) else None,
                ),
            )
        except httpx.TimeoutException as error:
            raise ProviderError("OpenRouter image request timed out.", retryable=True) from error
        except httpx.HTTPError as error:
            raise ProviderError("OpenRouter image transport failed.", retryable=True) from error
        finally:
            if owns_client:
                await client.aclose()


def _content(payload: dict[str, Any]) -> list[TextContent | ImageContent]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ProviderError("OpenRouter image response did not contain a choice.")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ProviderError("OpenRouter image response did not contain a message.")
    content: list[TextContent | ImageContent] = []
    text = message.get("content")
    if isinstance(text, str) and text:
        content.append(TextContent(text))
    images = message.get("images", [])
    if not isinstance(images, list):
        raise ProviderError("OpenRouter image response contained invalid images.")
    for image in images:
        if not isinstance(image, dict):
            continue
        value = image.get("image_url")
        url = (
            value
            if isinstance(value, str)
            else value.get("url")
            if isinstance(value, dict)
            else None
        )
        if not isinstance(url, str) or not url.startswith("data:"):
            continue
        prefix, separator, data = url.partition(",")
        mime = prefix.removeprefix("data:").removesuffix(";base64")
        if separator and mime and data:
            content.append(ImageContent(data, mime))
    return content


def _usage(payload: dict[str, Any]) -> Usage:
    raw = payload.get("usage")
    if not isinstance(raw, dict):
        return Usage()
    prompt = raw.get("prompt_tokens", 0)
    completion = raw.get("completion_tokens", 0)
    total = raw.get("total_tokens", 0)
    if not all(isinstance(value, int) for value in (prompt, completion, total)):
        return Usage()
    return Usage(
        input_tokens=prompt, output_tokens=completion, total_tokens=total or prompt + completion
    )
