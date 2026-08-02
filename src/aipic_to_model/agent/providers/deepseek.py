"""DeepSeek's OpenAI-compatible profile and development credential resolver."""

from __future__ import annotations

import os
from collections.abc import Callable

from ...infrastructure.keyring_store import OSKeyringStore
from .base import ModelProfile
from .model_catalog import load_frozen_catalog

DEEPSEEK_PROVIDER_ID = "deepseek"
DEEPSEEK_PROFILE_REF = "agent/deepseek/default"
DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-flash"
DEEPSEEK_AGENT_FALLBACK_MAX_OUTPUT_TOKENS = 16_384
DEEPSEEK_AGENT_FALLBACK_CONTEXT_WINDOW = 128_000


def deepseek_context_window(model: str) -> int:
    """Return the catalogued context capacity, optionally capped for an Agent run."""

    context_window, _max_output = _model_limits(model)
    return min(_positive_environment_value("DEEPSEEK_AGENT_CONTEXT_WINDOW", context_window), context_window)


def create_deepseek_profile(
    *,
    base_url: str | None = None,
    model: str | None = None,
    timeout_seconds: float = 60.0,
) -> ModelProfile:
    """Build a profile whose output budget is bounded by the selected model."""

    selected_model = model or os.environ.get("DEEPSEEK_MODEL") or DEEPSEEK_DEFAULT_MODEL
    _context_window, model_max_output = _model_limits(selected_model)
    requested_output = _positive_environment_value(
        "DEEPSEEK_AGENT_MAX_OUTPUT_TOKENS", model_max_output
    )

    return ModelProfile(
        provider_id=DEEPSEEK_PROVIDER_ID,
        model=selected_model,
        base_url=(
            base_url or os.environ.get("DEEPSEEK_BASE_URL") or DEEPSEEK_DEFAULT_BASE_URL
        ).rstrip("/"),
        credential_ref=DEEPSEEK_PROFILE_REF,
        timeout_seconds=timeout_seconds,
        max_output_tokens=min(requested_output, model_max_output),
    )


def _model_limits(model: str) -> tuple[int, int]:
    catalogued = next(
        (item for item in load_frozen_catalog().for_provider(DEEPSEEK_PROVIDER_ID) if item.model_id == model),
        None,
    )
    if catalogued is None:
        return DEEPSEEK_AGENT_FALLBACK_CONTEXT_WINDOW, DEEPSEEK_AGENT_FALLBACK_MAX_OUTPUT_TOKENS
    return catalogued.context_window, catalogued.max_output_tokens


def _positive_environment_value(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def create_deepseek_credential_resolver(
    keyring_factory: Callable[[], OSKeyringStore] = OSKeyringStore,
) -> Callable[[str], str | None]:
    """Resolve the profile from OS Keyring, with an env override for development and CI."""

    def resolve(credential_ref: str) -> str | None:
        if credential_ref != DEEPSEEK_PROFILE_REF:
            return None
        environment_value = os.environ.get("DEEPSEEK_API_KEY")
        if environment_value:
            return environment_value
        return keyring_factory().get(DEEPSEEK_PROFILE_REF)

    return resolve
