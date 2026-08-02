"""Redacted OpenAI-compatible Vision and Image HTTP adapters."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Any

import httpx

from ...domain.analysis_prompts import REWRITE_SYSTEM_PROMPT, system_prompt_for, user_instruction_for
from ...domain.prompt_parser import parse_bilingual
from ...domain.provider_models import AnalysisRequest, AnalysisResult, ProviderResult
from .config import OpenAICompatibleSettings
from .http_errors import http_failure
from .image_payloads import banana_payload, gpt_image_payload
from .tls import provider_http_client
from .transport_errors import transport_failure

_REQUEST_HEADERS = ("x-request-id", "request-id", "openai-request-id")


def _request_id(response: httpx.Response) -> str | None:
    for name in _REQUEST_HEADERS:
        value = response.headers.get(name)
        if value and len(value) <= 200:
            return value
    return None


def _retry_after(response: httpx.Response) -> int | None:
    try:
        value = int(response.headers.get("retry-after", ""))
    except ValueError:
        return None
    return value if 0 <= value <= 86_400 else None


def _failure(response: httpx.Response, operation: str) -> ProviderResult | None:
    if 200 <= response.status_code < 300:
        return None
    data = _json_object(response)
    provider_error = data.get("error") if data is not None else None
    provider_code = (
        str(provider_error.get("code")).lower()
        if isinstance(provider_error, dict) and provider_error.get("code") is not None
        else ""
    )
    return http_failure(
        operation=operation,
        status_code=response.status_code,
        request_id=_request_id(response),
        retry_after_seconds=_retry_after(response),
        credits_exhausted=provider_code in {"insufficient_quota", "billing_hard_limit_reached"},
        model_unavailable=provider_code in {"model_not_found", "model_not_available"},
    )


def _json_object(response: httpx.Response) -> dict[str, Any] | None:
    try:
        value = response.json()
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _root(data: dict[str, Any]) -> dict[str, Any]:
    nested = data.get("data")
    return nested if isinstance(nested, dict) else data


def _content(data: dict[str, Any]) -> str | None:
    choices = _root(data).get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return None
    value = message.get("content")
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [
            str(item.get("text", ""))
            for item in value
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
        return "".join(parts) or None
    return None


def _structured_analysis(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        first_newline = stripped.find("\n")
        if first_newline >= 0:
            stripped = stripped[first_newline + 1 : -3].strip()
    try:
        value = json.loads(stripped)
    except ValueError:
        start = stripped.find("{")
        if start < 0:
            return None
        try:
            value, _ = json.JSONDecoder().raw_decode(stripped[start:])
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def _analysis_result(
    text: str,
    request: AnalysisRequest,
    *,
    provider_request_id: str,
) -> AnalysisResult:
    parsed = parse_bilingual(text)
    zh_text = parsed.zh_segment
    en_text = parsed.en_segment
    zh_prompt = parsed.zh_prompt
    en_prompt = parsed.en_prompt
    if not all((zh_text, en_text, zh_prompt, en_prompt)):
        raise ValueError("analysis fields must be non-empty")
    if zh_prompt.casefold() == "prompt" or en_prompt.casefold() == "prompt":
        raise ValueError("prompt fence marker is not a substantive prompt")
    return AnalysisResult(
        mode=request.mode,
        zh_text=zh_text,
        en_text=en_text,
        zh_prompt=zh_prompt,
        en_prompt=en_prompt,
        preserve=list(parsed.preserve),
        avoid=list(parsed.avoid),
        provider_request_id=provider_request_id,
        model=request.model,
    )


def _authorization(secret: str) -> str:
    return secret if secret.lower().startswith("bearer ") else f"Bearer {secret}"


class OpenAICompatibleVisionProvider:
    def __init__(
        self,
        settings: OpenAICompatibleSettings,
        credential: Callable[[], str | None],
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self._settings = settings
        self._credential = credential
        self._client = client or provider_http_client(
            timeout_seconds=settings.timeout_seconds
        )

    def analyze_image(
        self,
        request: AnalysisRequest,
        *,
        image_bytes: bytes,
        mime_type: str,
    ) -> AnalysisResult | ProviderResult:
        secret = self._credential()
        if not secret:
            return http_failure(operation="analyzing", configuration_missing=True)
        if mime_type not in {"image/png", "image/jpeg", "image/webp"} or not image_bytes:
            return http_failure(operation="analyzing")
        system = system_prompt_for(request.mode)
        if system is None:
            return http_failure(operation="analyzing")
        encoded = base64.b64encode(image_bytes).decode("ascii")
        payload = {
            "model": request.model,
            "stream": False,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                        },
                        {"type": "text", "text": user_instruction_for(request.mode)},
                    ],
                },
            ],
        }
        response: httpx.Response | None = None
        text: str | None = None
        for attempt in range(2):
            try:
                response = self._client.post(
                    self._settings.chat_url,
                    headers={
                        "Authorization": _authorization(secret),
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            except httpx.TimeoutException:
                if attempt == 0:
                    continue
                return http_failure(operation="analyzing", timed_out=True)
            except httpx.HTTPError:
                if attempt == 0:
                    continue
                return http_failure(operation="analyzing", status_code=503)
            failed = _failure(response, "analyzing")
            if failed is not None:
                if attempt == 0 and (
                    response.status_code in {408, 425, 429} or response.status_code >= 500
                ):
                    continue
                return failed
            data = _json_object(response)
            text = _content(data or {}) if data is not None else None
            if text:
                break
        if not text:
            return http_failure(
                operation="analyzing",
                request_id=_request_id(response) if response is not None else None,
            )
        assert response is not None
        if request.mode == "3d_suitability":
            try:
                value = json.loads(text)
                if not isinstance(value, dict):
                    raise ValueError("invalid suitability result")
                return AnalysisResult(
                    mode=request.mode,
                    zh_text=str(value.get("zh_text") or ""),
                    en_text=str(value.get("en_text") or ""),
                    dimensions={
                        str(key): str(item)
                        for key, item in dict(value.get("dimensions") or {}).items()
                    },
                    suitability_issues=[
                        str(item) for item in list(value.get("suitability_issues") or [])
                    ],
                    provider_request_id=_request_id(response) or "provider-request-unavailable",
                    model=request.model,
                )
            except TypeError, ValueError:
                return http_failure(operation="analyzing", request_id=_request_id(response))
        try:
            return _analysis_result(
                text,
                request,
                provider_request_id=_request_id(response) or "provider-request-unavailable",
            )
        except TypeError, ValueError:
            return self._repair_analysis(request, secret=secret, prior_text=text)

    def _repair_analysis(
        self,
        request: AnalysisRequest,
        *,
        secret: str,
        prior_text: str,
    ) -> AnalysisResult | ProviderResult:
        """Normalize one malformed successful response without uploading the image again."""

        try:
            response = self._client.post(
                self._settings.chat_url,
                headers={
                    "Authorization": _authorization(secret),
                    "Content-Type": "application/json",
                },
                json={
                    "model": request.model,
                    "stream": False,
                    "temperature": 0,
                    "max_tokens": 1200,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Repair the supplied response so it follows the required JSON schema "
                                "exactly. Preserve every supported observation and do not invent new content.\n\n"
                                f"{system_prompt_for(request.mode)}"
                            ),
                        },
                        {
                            "role": "user",
                            "content": prior_text[:12_000],
                        },
                    ],
                },
            )
        except httpx.TimeoutException:
            return http_failure(operation="analyzing", timed_out=True)
        except httpx.HTTPError:
            return http_failure(operation="analyzing", status_code=503)
        failed = _failure(response, "analyzing")
        if failed is not None:
            return failed
        text = _content(_json_object(response) or {})
        if not text:
            return http_failure(operation="analyzing", request_id=_request_id(response))
        try:
            return _analysis_result(
                text,
                request,
                provider_request_id=_request_id(response) or "provider-request-unavailable",
            )
        except TypeError, ValueError:
            return http_failure(operation="analyzing", request_id=_request_id(response))

    def rewrite(self, *, prompt: str, instruction: str, model: str) -> ProviderResult:
        """Rewrite a managed bilingual Prompt without exposing raw response data."""
        secret = self._credential()
        if not secret:
            return http_failure(operation="rewriting", configuration_missing=True)
        try:
            response = self._client.post(
                self._settings.chat_url,
                headers={
                    "Authorization": _authorization(secret),
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "stream": False,
                    "temperature": 0.2,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                REWRITE_SYSTEM_PROMPT
                            ),
                        },
                        {
                            "role": "user",
                            "content": f"Instruction:\n{instruction}\n\nPrompt:\n{prompt}",
                        },
                    ],
                },
            )
        except httpx.TimeoutException:
            return http_failure(operation="rewriting", timed_out=True)
        except httpx.HTTPError:
            return http_failure(operation="rewriting", status_code=503)
        failed = _failure(response, "rewriting")
        if failed is not None:
            return failed
        data = _json_object(response)
        text = _content(data or {}) if data is not None else None
        try:
            if not text:
                raise ValueError("empty rewrite")
            parse_bilingual(text)
        except ValueError:
            return http_failure(operation="rewriting", request_id=_request_id(response))
        return ProviderResult(
            ok=True,
            provider_request_id=_request_id(response) or "provider-request-unavailable",
            stage="rewriting",
            retryable=False,
            payload={"text": text},
        )


class OpenAICompatibleImageProvider:
    """Supports the established Banana gateway and GPT Image endpoints."""

    def __init__(
        self,
        settings: OpenAICompatibleSettings,
        credential: Callable[[], str | None],
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self._settings = settings
        self._credential = credential
        self._client = client or provider_http_client(
            timeout_seconds=settings.timeout_seconds
        )

    def generate(self, request: dict[str, object]) -> ProviderResult:
        secret = self._credential()
        if not secret:
            return http_failure(operation="generating", configuration_missing=True)
        count = request.get("candidate_count", request.get("n", 1))
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 8:
            return http_failure(operation="generating")
        prompt = request.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return http_failure(operation="generating")
        channel = request.get("channel")
        mode = request.get("mode")
        source = request.get("source_bytes")
        mime = request.get("source_mime")
        try:
            if channel == "gpt_image":
                response = self._gpt_request(request, secret, prompt, source, mime)
            elif channel == "banana":
                response = self._banana_request(request, secret, prompt, source, mime)
            else:
                return http_failure(operation="generating")
        except httpx.HTTPError as error:
            return transport_failure(error, operation="generating", paid_submission=True)
        failed = _failure(response, "generating")
        if failed is not None:
            return failed
        data = _json_object(response)
        images = self._extract_images(data or {}, chat=channel == "banana" and mode == "i2i")
        if len(images) != count:
            return http_failure(operation="generating", request_id=_request_id(response))
        return ProviderResult(
            ok=True,
            provider_request_id=_request_id(response) or "provider-request-unavailable",
            stage="generating",
            retryable=False,
            payload={"images": [{"base64": value} for value in images]},
        )

    def _gpt_request(
        self,
        request: dict[str, object],
        secret: str,
        prompt: str,
        source: object,
        mime: object,
    ) -> httpx.Response:
        from ...domain.provider_models import GenerationRequest

        model_values: dict[str, Any] = {
            key: value for key, value in request.items() if key in GenerationRequest.model_fields
        }
        model_request = GenerationRequest.model_construct(**model_values)
        headers = {"Authorization": _authorization(secret)}
        if model_request.mode == "t2i":
            return self._client.post(
                self._settings.generation_url,
                headers={**headers, "Content-Type": "application/json"},
                json=gpt_image_payload(model_request, prompt=prompt),
            )
        if not isinstance(source, bytes) or not isinstance(mime, str):
            raise TypeError("managed source image is required")
        fields = gpt_image_payload(model_request, prompt=prompt, remote_input_id="managed")
        fields.pop("image_file_id", None)
        return self._client.post(
            self._settings.edits_url,
            headers=headers,
            data={key: str(value) for key, value in fields.items() if value is not None},
            files={"image": ("managed-input", source, mime)},
        )

    def _banana_request(
        self,
        request: dict[str, object],
        secret: str,
        prompt: str,
        source: object,
        mime: object,
    ) -> httpx.Response:
        from ...domain.provider_models import GenerationRequest

        model_values: dict[str, Any] = {
            key: value for key, value in request.items() if key in GenerationRequest.model_fields
        }
        model_request = GenerationRequest.model_construct(**model_values)
        if model_request.mode == "t2i":
            payload = banana_payload(model_request, prompt=prompt)
            url = self._settings.generation_url
        else:
            if not isinstance(source, bytes) or not isinstance(mime, str):
                raise ValueError("managed source image is required")
            payload = banana_payload(model_request, prompt=prompt, remote_input_id="managed")
            content = payload["messages"][0]["content"]  # type: ignore[index]
            content[1] = {  # type: ignore[index]
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime};base64,{base64.b64encode(source).decode('ascii')}"
                },
            }
            url = self._settings.chat_url
        return self._client.post(
            url,
            headers={
                "Authorization": _authorization(secret),
                "Content-Type": "application/json",
            },
            json=payload,
        )

    @staticmethod
    def _extract_images(data: dict[str, Any], *, chat: bool) -> list[str]:
        if chat:
            choices = _root(data).get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                return []
            message = choices[0].get("message")
            items = message.get("images") if isinstance(message, dict) else None
        else:
            items = data.get("data")
            if isinstance(items, dict):
                items = items.get("data")
        if not isinstance(items, list):
            return []
        found: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            value = item.get("b64_json") or item.get("base64")
            if not isinstance(value, str):
                image_url = item.get("image_url")
                if isinstance(image_url, dict):
                    value = image_url.get("url")
                elif isinstance(item.get("url"), str):
                    value = item["url"]
            if isinstance(value, str) and value.startswith("data:image/") and ";base64," in value:
                value = value.split(";base64,", 1)[1]
            if isinstance(value, str) and value and not value.startswith(("http://", "https://")):
                found.append(value)
        return found
