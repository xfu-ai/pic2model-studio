from __future__ import annotations

from aipic_to_model.agent.providers.deepseek import (
    DEEPSEEK_DEFAULT_BASE_URL,
    DEEPSEEK_DEFAULT_MODEL,
    DEEPSEEK_PROFILE_REF,
    create_deepseek_credential_resolver,
    create_deepseek_profile,
    deepseek_context_window,
)
from aipic_to_model.agent.integrations.runtime import _upgrade_profile_defaults
from aipic_to_model.agent.providers.base import ModelProfile


def test_deepseek_profile_uses_stable_defaults_without_environment(monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)

    profile = create_deepseek_profile()

    assert profile.base_url == DEEPSEEK_DEFAULT_BASE_URL
    assert profile.model == DEEPSEEK_DEFAULT_MODEL
    assert profile.credential_ref == DEEPSEEK_PROFILE_REF
    assert profile.max_output_tokens == 384_000
    assert deepseek_context_window(profile.model) == 1_000_000


def test_deepseek_agent_limits_can_be_lowered_but_not_exceed_model_limits(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_AGENT_MAX_OUTPUT_TOKENS", "999999")
    monkeypatch.setenv("DEEPSEEK_AGENT_CONTEXT_WINDOW", "250000")

    profile = create_deepseek_profile()

    assert profile.max_output_tokens == 384_000
    assert deepseek_context_window(profile.model) == 250_000


def test_recovering_an_outdated_deepseek_profile_raises_only_the_old_256_token_budget() -> None:
    upgraded, changed = _upgrade_profile_defaults(
        ModelProfile("deepseek", DEEPSEEK_DEFAULT_MODEL, DEEPSEEK_DEFAULT_BASE_URL, max_output_tokens=256)
    )
    preserved, preserved_changed = _upgrade_profile_defaults(
        ModelProfile("deepseek", DEEPSEEK_DEFAULT_MODEL, DEEPSEEK_DEFAULT_BASE_URL, max_output_tokens=1_024)
    )

    assert changed and upgraded.max_output_tokens == 384_000
    assert not preserved_changed and preserved.max_output_tokens == 1_024


def test_deepseek_credential_resolver_uses_env_for_development(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "development-secret")

    resolver = create_deepseek_credential_resolver()

    assert resolver(DEEPSEEK_PROFILE_REF) == "development-secret"
    assert resolver("other") is None
