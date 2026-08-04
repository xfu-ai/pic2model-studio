"""Qwen3-VL profile for a loopback-only Ollama OpenAI-compatible endpoint."""

from __future__ import annotations

import os
from collections.abc import Callable

from ...infrastructure.local_inference import normalize_loopback_base_url
from .base import ModelProfile

OLLAMA_PROVIDER_ID = "ollama"
QWEN3_VL_PROFILE_REF = "agent/ollama/qwen3-vl"
QWEN3_VL_DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
QWEN3_VL_DEFAULT_MODEL = "qwen3-vl:8b"
QWEN3_VL_SUPPORTED_MODELS = (QWEN3_VL_DEFAULT_MODEL, "qwen3-vl:4b")
# Keep the harness budget aligned with the Ollama server configuration used by
# the desktop runtime.  The model metadata advertises a much larger theoretical
# context, but Ollama defaults to 4K unless OLLAMA_CONTEXT_LENGTH is set when the
# server starts.  A vision turn plus the fixed Agent tool catalog is already
# roughly 7.2K tokens, so the supported local configuration uses 32K.
QWEN3_VL_CONTEXT_WINDOW = 32_768
QWEN3_VL_CONTEXT_SAFETY_TOKENS = 4_096
QWEN3_VL_DEFAULT_MAX_OUTPUT_TOKENS = (
    QWEN3_VL_CONTEXT_WINDOW - QWEN3_VL_CONTEXT_SAFETY_TOKENS
)
QWEN3_VL_DEFAULT_TIMEOUT_SECONDS = 600.0


def qwen3_vl_context_window(_model: str) -> int:
    """Return the bounded context capacity used by the local Agent harness."""

    return min(
        _positive_environment_value("QWEN3_VL_AGENT_CONTEXT_WINDOW", QWEN3_VL_CONTEXT_WINDOW),
        QWEN3_VL_CONTEXT_WINDOW,
    )


def create_qwen3_vl_profile(
    *,
    base_url: str | None = None,
    model: str | None = None,
    timeout_seconds: float = QWEN3_VL_DEFAULT_TIMEOUT_SECONDS,
) -> ModelProfile:
    """Build the local Qwen3-VL profile without credentials or non-loopback URLs."""

    selected_model = model or os.environ.get("QWEN3_VL_MODEL") or QWEN3_VL_DEFAULT_MODEL
    if selected_model not in QWEN3_VL_SUPPORTED_MODELS:
        raise ValueError(
            "Qwen3-VL model must be explicitly selected from the supported local models"
        )
    requested_output = _positive_environment_value(
        "QWEN3_VL_AGENT_MAX_OUTPUT_TOKENS", QWEN3_VL_DEFAULT_MAX_OUTPUT_TOKENS
    )
    endpoint = normalize_loopback_base_url(
        base_url or os.environ.get("OLLAMA_BASE_URL") or QWEN3_VL_DEFAULT_BASE_URL
    )
    return ModelProfile(
        provider_id=OLLAMA_PROVIDER_ID,
        model=selected_model,
        base_url=endpoint,
        credential_ref=None,
        timeout_seconds=timeout_seconds,
        max_output_tokens=min(requested_output, QWEN3_VL_DEFAULT_MAX_OUTPUT_TOKENS),
    )


def create_ollama_credential_resolver() -> Callable[[str], str | None]:
    """Return a resolver that can never add credentials to a local Ollama request."""

    return lambda _credential_ref: None


def _positive_environment_value(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default
