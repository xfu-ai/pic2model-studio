from __future__ import annotations

import pytest

from aipic_to_model.agent.integrations.runtime import _upgrade_profile_defaults
from aipic_to_model.agent.providers.base import ModelProfile
from aipic_to_model.agent.providers.qwen3_vl import (
    OLLAMA_PROVIDER_ID,
    QWEN3_VL_CONTEXT_WINDOW,
    QWEN3_VL_DEFAULT_BASE_URL,
    QWEN3_VL_DEFAULT_MAX_OUTPUT_TOKENS,
    QWEN3_VL_DEFAULT_MODEL,
    QWEN3_VL_DEFAULT_TIMEOUT_SECONDS,
    create_ollama_credential_resolver,
    create_qwen3_vl_profile,
    qwen3_vl_context_window,
)


def test_qwen3_vl_profile_uses_loopback_defaults_without_credentials(monkeypatch) -> None:
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("QWEN3_VL_MODEL", raising=False)

    profile = create_qwen3_vl_profile()

    assert profile.provider_id == OLLAMA_PROVIDER_ID
    assert profile.base_url == QWEN3_VL_DEFAULT_BASE_URL
    assert profile.model == QWEN3_VL_DEFAULT_MODEL
    assert profile.credential_ref is None
    assert profile.max_output_tokens == QWEN3_VL_DEFAULT_MAX_OUTPUT_TOKENS
    assert profile.max_output_tokens == 28_672
    assert profile.timeout_seconds == QWEN3_VL_DEFAULT_TIMEOUT_SECONDS
    assert qwen3_vl_context_window(profile.model) == QWEN3_VL_CONTEXT_WINDOW
    assert qwen3_vl_context_window(profile.model) == 32_768
    assert create_ollama_credential_resolver()("any-reference") is None


def test_qwen3_vl_profile_allows_explicit_4b_and_bounded_agent_limits(monkeypatch) -> None:
    monkeypatch.setenv("QWEN3_VL_AGENT_MAX_OUTPUT_TOKENS", "999999")
    monkeypatch.setenv("QWEN3_VL_AGENT_CONTEXT_WINDOW", "64000")

    profile = create_qwen3_vl_profile(model="qwen3-vl:4b")

    assert profile.model == "qwen3-vl:4b"
    assert profile.max_output_tokens == QWEN3_VL_DEFAULT_MAX_OUTPUT_TOKENS
    assert qwen3_vl_context_window(profile.model) == QWEN3_VL_CONTEXT_WINDOW


@pytest.mark.parametrize("legacy_output", (2_048, 16_384))
def test_recovered_qwen_profile_upgrades_the_legacy_output_budget(
    legacy_output: int,
) -> None:
    upgraded, changed = _upgrade_profile_defaults(
        ModelProfile(
            provider_id=OLLAMA_PROVIDER_ID,
            model=QWEN3_VL_DEFAULT_MODEL,
            base_url=QWEN3_VL_DEFAULT_BASE_URL,
            timeout_seconds=120,
            max_output_tokens=legacy_output,
        )
    )

    assert changed is True
    assert upgraded.max_output_tokens == 28_672
    assert upgraded.timeout_seconds == 600
    assert upgraded.base_url == QWEN3_VL_DEFAULT_BASE_URL


@pytest.mark.parametrize(
    "base_url",
    (
        "https://ollama.example/v1",
        "http://127.0.0.1:80/v1",
        "http://user:secret@127.0.0.1:11434/v1",
    ),
)
def test_qwen3_vl_profile_rejects_non_loopback_or_unsafe_endpoints(base_url: str) -> None:
    with pytest.raises(ValueError):
        create_qwen3_vl_profile(base_url=base_url)


def test_qwen3_vl_profile_rejects_an_unapproved_model() -> None:
    with pytest.raises(ValueError, match="supported local models"):
        create_qwen3_vl_profile(model="qwen3-vl:235b")
