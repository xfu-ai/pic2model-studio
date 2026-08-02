"""Official Google Gemini adapters for analysis, prompt rewriting, and image generation."""

from __future__ import annotations

import base64
import json
import math
import re
import time
from collections.abc import Callable
from io import BytesIO
from typing import Any

import httpx
from PIL import Image, ImageDraw

from ...domain.analysis_prompts import REWRITE_SYSTEM_PROMPT, system_prompt_for, user_instruction_for
from ...domain.prompt_parser import PromptParseError, parse_bilingual
from ...domain.provider_models import AnalysisRequest, AnalysisResult, ProviderResult
from .config import GeminiSettings
from .http_errors import http_failure
from .tls import provider_http_client
from .transport_errors import transport_failure

_DEFAULT_RATE_LIMIT_RETRY_SECONDS = 60


def _request_id(response: httpx.Response) -> str | None:
    for name in ("x-request-id", "x-goog-request-id"):
        value = response.headers.get(name)
        if value and len(value) <= 200:
            return value
    return None


def _json_object(response: httpx.Response) -> dict[str, Any] | None:
    try:
        value = response.json()
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _failure(response: httpx.Response, operation: str) -> ProviderResult | None:
    if 200 <= response.status_code < 300:
        return None
    data = _json_object(response)
    error = data.get("error") if data is not None else None
    status = str(error.get("status") or "").upper() if isinstance(error, dict) else ""
    return http_failure(
        operation=operation,
        status_code=response.status_code,
        request_id=_request_id(response),
        retry_after_seconds=(
            _retry_after_seconds(response) if response.status_code == 429 else None
        ),
        credits_exhausted=status == "BILLING_DISABLED",
        model_unavailable=status == "NOT_FOUND",
    )


def _retry_after_seconds(response: httpx.Response) -> int:
    """Read only a bounded retry delay; never retain the Provider response body."""
    header = response.headers.get("retry-after")
    if header:
        try:
            return max(1, min(math.ceil(float(header)), 3600))
        except ValueError:
            pass
    data = _json_object(response)
    error = data.get("error") if data is not None else None
    details = error.get("details") if isinstance(error, dict) else None
    if isinstance(details, list):
        for detail in details:
            if not isinstance(detail, dict):
                continue
            value = detail.get("retryDelay") or detail.get("retry_delay")
            if not isinstance(value, str):
                continue
            match = re.fullmatch(r"(\d+(?:\.\d+)?)s", value.strip())
            if match:
                return max(1, min(math.ceil(float(match.group(1))), 3600))
    return _DEFAULT_RATE_LIMIT_RETRY_SECONDS


def _provider_should_retry_immediately(outcome: ProviderResult) -> bool:
    """Transport failures retry here; rate limits are delayed by the durable Job layer."""
    return bool(
        outcome.retryable
        and (outcome.error is None or outcome.error.code != "PROVIDER_RATE_LIMITED")
    )


def _parts(data: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        return []
    content = candidates[0].get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    return [part for part in parts if isinstance(part, dict)] if isinstance(parts, list) else []


def _text(data: dict[str, Any]) -> str | None:
    values = [part.get("text") for part in _parts(data) if isinstance(part.get("text"), str)]
    combined = "\n".join(str(value) for value in values).strip()
    return combined or None


def _safe_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = stripped.rsplit("```", 1)[0]
    value = json.loads(stripped)
    if not isinstance(value, dict):
        raise TypeError("Gemini JSON result must be an object")
    return value


class GeminiVisionProvider:
    def __init__(
        self,
        settings: GeminiSettings,
        credential: Callable[[], str | None],
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._credential = credential
        self._client = client or provider_http_client(
            timeout_seconds=settings.timeout_seconds
        )
        self._sleep = sleep

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
        instruction = user_instruction_for(request.mode)
        model = (
            request.model if request.model.startswith("gemini-") else self._settings.analysis_model
        )
        payload: dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": instruction},
                        {
                            "inlineData": {
                                "mimeType": mime_type,
                                "data": base64.b64encode(image_bytes).decode("ascii"),
                            }
                        },
                    ],
                }
            ],
            "generationConfig": {"temperature": 0.2},
        }
        if system_prompt := system_prompt_for(request.mode):
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}
        payload["generationConfig"]["responseMimeType"] = "application/json"
        response = self._post(model, payload, "analyzing")
        if isinstance(response, ProviderResult):
            return response
        data = _json_object(response)
        text = _text(data or {}) if data is not None else None
        if not text:
            return http_failure(operation="analyzing", request_id=_request_id(response))
        try:
            if request.mode == "3d_suitability":
                value = _safe_json(text)
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
                    model=model,
                )
            parsed = parse_bilingual(text)
            return AnalysisResult(
                mode=request.mode,
                zh_text=parsed.zh_segment,
                en_text=parsed.en_segment,
                zh_prompt=parsed.zh_prompt,
                en_prompt=parsed.en_prompt,
                preserve=list(parsed.preserve),
                avoid=list(parsed.avoid),
                raw_response=text,
                provider_request_id=_request_id(response) or "provider-request-unavailable",
                model=model,
            )
        except (PromptParseError, TypeError, ValueError, json.JSONDecodeError):
            # The response remains a managed analysis record for inspection and manual repair,
            # but cannot pass prompt.extract_bilingual until both prompts are usable.
            return AnalysisResult(
                mode=request.mode,
                raw_response=text,
                parse_error="bilingual_prompt_contract_not_met",
                provider_request_id=_request_id(response) or "provider-request-unavailable",
                model=model,
            )

    def rewrite(self, *, prompt: str, instruction: str, model: str) -> ProviderResult:
        if not prompt.strip() or not instruction.strip():
            return http_failure(operation="rewriting")
        resolved_model = model if model.startswith("gemini-") else self._settings.text_model
        response = self._post(
            resolved_model,
            {
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": (
                                    f"Requested change:\n{instruction}\n\n"
                                    f"Existing prompt document:\n{prompt}"
                                )
                            }
                        ],
                    }
                ],
                "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
                "systemInstruction": {"parts": [{"text": REWRITE_SYSTEM_PROMPT}]},
            },
            "rewriting",
        )
        if isinstance(response, ProviderResult):
            return response
        data = _json_object(response)
        text = _text(data or {}) if data is not None else None
        if not text:
            return http_failure(operation="rewriting", request_id=_request_id(response))
        try:
            parse_bilingual(text)
        except PromptParseError:
            return http_failure(operation="rewriting", request_id=_request_id(response))
        return ProviderResult(
            ok=True,
            provider_request_id=_request_id(response) or "provider-request-unavailable",
            stage="rewriting",
            retryable=False,
            payload={"text": text},
        )

    def _post(
        self,
        model: str,
        payload: dict[str, Any],
        operation: str,
    ) -> httpx.Response | ProviderResult:
        secret = self._credential()
        if not secret:
            return http_failure(operation=operation, configuration_missing=True)
        for attempt in range(4):
            try:
                response = self._client.post(
                    self._settings.generate_url(model),
                    headers={"x-goog-api-key": secret, "Content-Type": "application/json"},
                    json=payload,
                )
                outcome: httpx.Response | ProviderResult = _failure(response, operation) or response
            except httpx.TimeoutException:
                outcome = http_failure(operation=operation, timed_out=True)
            except httpx.HTTPError:
                outcome = http_failure(operation=operation, status_code=503)
            if (
                not isinstance(outcome, ProviderResult)
                or not _provider_should_retry_immediately(outcome)
                or attempt == 3
            ):
                return outcome
            self._sleep((5, 10, 20)[attempt])
        raise AssertionError("unreachable")


class GeminiImageProvider:
    def __init__(
        self,
        settings: GeminiSettings,
        credential: Callable[[], str | None],
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._credential = credential
        self._client = client or provider_http_client(
            timeout_seconds=settings.timeout_seconds
        )
        self._sleep = sleep

    def generate(self, request: dict[str, object]) -> ProviderResult:
        secret = self._credential()
        if not secret:
            return http_failure(operation="generating", configuration_missing=True)
        count = request.get("candidate_count", request.get("n", 1))
        prompt = request.get("prompt")
        if (
            not isinstance(count, int)
            or isinstance(count, bool)
            or not 1 <= count <= 8
            or not isinstance(prompt, str)
            or not prompt.strip()
        ):
            return http_failure(operation="generating")
        model_value = request.get("model")
        model = (
            model_value
            if isinstance(model_value, str) and model_value.startswith("gemini-")
            else self._settings.image_model
        )
        aspect_ratio = request.get("aspect_ratio")
        if not isinstance(aspect_ratio, str):
            aspect_ratio = self._settings.aspect_ratio
        source = request.get("source_bytes")
        source_bytes = source if isinstance(source, bytes) else None
        mime_type = request.get("source_mime")
        mode = request.get("mode")
        if mode == "i2i" and (
            source_bytes is None
            or not isinstance(mime_type, str)
            or mime_type not in {"image/png", "image/jpeg", "image/webp"}
        ):
            return http_failure(operation="generating")

        images: list[dict[str, str]] = []
        request_id: str | None = None
        for _ in range(count):
            parts: list[dict[str, object]] = [{"text": prompt}]
            if mode == "i2i":
                assert source_bytes is not None
                parts.append(
                    {
                        "inlineData": {
                            "mimeType": mime_type,
                            "data": base64.b64encode(source_bytes).decode("ascii"),
                        }
                    }
                )
            payload = {
                "contents": [{"role": "user", "parts": parts}],
                "generationConfig": {
                    "responseModalities": ["TEXT", "IMAGE"],
                    "imageConfig": {"aspectRatio": aspect_ratio},
                },
            }
            response = self._post_with_retry(model, payload, secret)
            if isinstance(response, ProviderResult):
                return response
            request_id = request_id or _request_id(response)
            data = _json_object(response)
            encoded = self._extract_image(data or {})
            if encoded is None:
                return http_failure(operation="generating", request_id=_request_id(response))
            images.append({"base64": encoded})
        return ProviderResult(
            ok=True,
            provider_request_id=request_id or "provider-request-unavailable",
            stage="generating",
            retryable=False,
            payload={"images": images},
        )

    def _post_with_retry(
        self, model: str, payload: dict[str, Any], secret: str
    ) -> httpx.Response | ProviderResult:
        for attempt in range(4):
            try:
                response = self._client.post(
                    self._settings.generate_url(model),
                    headers={"x-goog-api-key": secret, "Content-Type": "application/json"},
                    json=payload,
                )
                outcome: httpx.Response | ProviderResult = _failure(response, "generating") or response
            except httpx.HTTPError as error:
                outcome = transport_failure(
                    error,
                    operation="generating",
                    paid_submission=True,
                )
            if (
                not isinstance(outcome, ProviderResult)
                or not _provider_should_retry_immediately(outcome)
                or attempt == 3
            ):
                return outcome
            self._sleep((5, 10, 20)[attempt])
        raise AssertionError("unreachable")

    @staticmethod
    def _extract_image(data: dict[str, Any]) -> str | None:
        for part in _parts(data):
            inline = part.get("inlineData")
            if not isinstance(inline, dict):
                inline = part.get("inline_data")
            if not isinstance(inline, dict):
                continue
            encoded = inline.get("data")
            if isinstance(encoded, str) and encoded:
                return encoded
        return None


_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


class GeminiTextRenderImageProvider:
    """Free-tier flow adapter: Gemini produces a safe scene plan rendered locally.

    This backend is deliberately a schematic workflow substitute, not a claim that a
    text-only Gemini model is equivalent to a native diffusion/image model.
    """

    def __init__(
        self,
        settings: GeminiSettings,
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
        prompt = request.get("prompt")
        if (
            not isinstance(count, int)
            or isinstance(count, bool)
            or not 1 <= count <= 8
            or not isinstance(prompt, str)
            or not prompt.strip()
        ):
            return http_failure(operation="generating")
        model_value = request.get("model")
        model = (
            model_value
            if isinstance(model_value, str) and model_value.startswith("gemini-")
            else self._settings.image_model
        )
        source = request.get("source_bytes")
        source_bytes = source if isinstance(source, bytes) else None
        source_mime = request.get("source_mime")
        if request.get("mode") == "i2i" and (
            source_bytes is None
            or not isinstance(source_mime, str)
            or source_mime not in {"image/png", "image/jpeg", "image/webp"}
        ):
            return http_failure(operation="generating")

        images: list[dict[str, str]] = []
        request_id: str | None = None
        for index in range(count):
            parts: list[dict[str, object]] = [
                {
                    "text": (
                        "Create a simple centered 2D concept-image plan for the prompt below. "
                        "Return JSON only. Use a white or light neutral background and at most "
                        "eight geometric shapes. Coordinates are integers from 0 to 1000. "
                        "Allowed kinds: rectangle, ellipse, triangle. Colors must be #RRGGBB. "
                        f"Variation {index + 1}. Prompt: {prompt}"
                    )
                }
            ]
            if request.get("mode") == "i2i":
                assert source_bytes is not None and isinstance(source_mime, str)
                parts.append(
                    {
                        "inlineData": {
                            "mimeType": source_mime,
                            "data": base64.b64encode(source_bytes).decode("ascii"),
                        }
                    }
                )
            payload = {
                "contents": [{"role": "user", "parts": parts}],
                "generationConfig": {
                    "temperature": 0.4,
                    "responseMimeType": "application/json",
                    "responseSchema": {
                        "type": "OBJECT",
                        "properties": {
                            "background": {"type": "STRING"},
                            "shapes": {
                                "type": "ARRAY",
                                "maxItems": 8,
                                "items": {
                                    "type": "OBJECT",
                                    "properties": {
                                        "kind": {
                                            "type": "STRING",
                                            "enum": ["rectangle", "ellipse", "triangle"],
                                        },
                                        "x": {"type": "INTEGER"},
                                        "y": {"type": "INTEGER"},
                                        "w": {"type": "INTEGER"},
                                        "h": {"type": "INTEGER"},
                                        "fill": {"type": "STRING"},
                                    },
                                    "required": ["kind", "x", "y", "w", "h", "fill"],
                                },
                            },
                        },
                        "required": ["background", "shapes"],
                    },
                },
            }
            try:
                response = self._client.post(
                    self._settings.generate_url(model),
                    headers={"x-goog-api-key": secret, "Content-Type": "application/json"},
                    json=payload,
                )
            except httpx.TimeoutException:
                return http_failure(operation="generating", timed_out=True)
            except httpx.HTTPError:
                return http_failure(operation="generating", status_code=503)
            failed = _failure(response, "generating")
            if failed is not None:
                return failed
            data = _json_object(response)
            text = _text(data or {}) if data is not None else None
            if not text:
                return http_failure(operation="generating", request_id=_request_id(response))
            try:
                rendered = self._render(_safe_json(text))
            except TypeError, ValueError, json.JSONDecodeError:
                return http_failure(operation="generating", request_id=_request_id(response))
            request_id = request_id or _request_id(response)
            images.append({"base64": base64.b64encode(rendered).decode("ascii")})
        return ProviderResult(
            ok=True,
            provider_request_id=request_id or "provider-request-unavailable",
            stage="generating",
            retryable=False,
            payload={"images": images},
        )

    @staticmethod
    def _render(spec: dict[str, Any]) -> bytes:
        background = spec.get("background")
        if not isinstance(background, str) or not _HEX_COLOR.fullmatch(background):
            background = "#FFFFFF"
        shapes = spec.get("shapes")
        if not isinstance(shapes, list) or not 1 <= len(shapes) <= 8:
            raise ValueError("Gemini scene plan has no safe shapes")
        image = Image.new("RGB", (512, 512), background)
        draw = ImageDraw.Draw(image)
        for item in shapes:
            if not isinstance(item, dict):
                raise TypeError("Gemini scene shape must be an object")
            kind = item.get("kind")
            fill = item.get("fill")
            coordinates = [item.get(key) for key in ("x", "y", "w", "h")]
            if (
                kind not in {"rectangle", "ellipse", "triangle"}
                or not isinstance(fill, str)
                or not _HEX_COLOR.fullmatch(fill)
                or not all(
                    isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 1000
                    for value in coordinates
                )
            ):
                raise ValueError("Gemini scene shape is outside the safe schema")
            numeric_coordinates = [
                value
                for value in coordinates
                if isinstance(value, int) and not isinstance(value, bool)
            ]
            if len(numeric_coordinates) != 4:
                raise ValueError("Gemini scene shape coordinates are incomplete")
            x, y, width, height = (value * 512 // 1000 for value in numeric_coordinates)
            if width <= 0 or height <= 0 or x + width > 512 or y + height > 512:
                raise ValueError("Gemini scene shape is outside the canvas")
            bounds = (x, y, x + width, y + height)
            if kind == "rectangle":
                draw.rounded_rectangle(bounds, radius=max(2, min(width, height) // 12), fill=fill)
            elif kind == "ellipse":
                draw.ellipse(bounds, fill=fill)
            else:
                draw.polygon(
                    [(x + width // 2, y), (x, y + height), (x + width, y + height)],
                    fill=fill,
                )
        output = BytesIO()
        image.save(output, format="PNG", optimize=True)
        return output.getvalue()
