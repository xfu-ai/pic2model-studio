from __future__ import annotations

import json
from pathlib import Path

from aipic_to_model.infrastructure.providers.config import (
    TRIPO_PROFILE,
    CredentialResolver,
    load_gemini_public_settings,
)


class _Store:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def get(self, profile: str) -> str | None:
        return self._values.get(profile)


def test_keyring_profile_wins_over_stale_environment_credential() -> None:
    resolver = CredentialResolver(
        _Store({TRIPO_PROFILE: "new-keyring-value"}),
        environment={"TRIPO_API_KEY": "stale-environment-value"},
    )

    assert resolver.get(TRIPO_PROFILE) == "new-keyring-value"


def test_gemini_public_config_selects_configurable_text_render_backend(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gemini.json"
    path.write_text(
        json.dumps(
            {
                "enabled": True,
                "provider": "google",
                "protocol": "google_generative_ai",
                "base_url": "https://generativelanguage.googleapis.com/v1beta",
                "default_text_model": "gemini-flash-lite-latest",
                "analysis_model": "gemini-flash-lite-latest",
                "image_generation_model": "gemini-flash-lite-latest",
                "image_backend": "text_render",
            }
        ),
        encoding="utf-8",
    )

    settings = load_gemini_public_settings(path)

    assert settings is not None
    assert settings.image_backend == "text_render"
    assert settings.image_model == "gemini-flash-lite-latest"
