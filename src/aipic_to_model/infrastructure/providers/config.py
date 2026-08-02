"""Non-secret production Provider configuration and credential resolution.

Only profile names and public endpoints live here. Credentials are resolved
at call time from the process environment or the OS keyring and are never
included in returned configuration objects.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ...application.settings import SecretStore

OPENAI_PROFILE = "openai/default"
TRIPO_PROFILE = "tripo3d/default"
NANOBANANA_PROFILE = "nanobanana/xais/default"
GEMINI_PROFILE = "gemini/google/default"
MESHY_PROFILE = "meshy/default"


def _local_config_path(name: str) -> Path:
    """Resolve ignored public config without depending on the launch directory."""

    working = Path.cwd().resolve()
    for directory in (working, *working.parents):
        candidate = directory / ".local" / name
        if candidate.is_file():
            return candidate
    source_root = Path(__file__).resolve().parents[4]
    return source_root / ".local" / name


def _plain_https_base(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Provider base URL must be a plain HTTPS URL")
    return value.rstrip("/")


@dataclass(frozen=True)
class OpenAICompatibleSettings:
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: float = 300.0
    analysis_model: str = "gpt-4.1-mini"
    image_model: str = "gpt-image-2"

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", _plain_https_base(self.base_url))
        if not 1 <= self.timeout_seconds <= 900:
            raise ValueError("Provider timeout is outside the approved range")
        if not self.analysis_model or not self.image_model:
            raise ValueError("Provider models must not be empty")

    @property
    def chat_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    @property
    def generation_url(self) -> str:
        return f"{self.base_url}/images/generations"

    @property
    def edits_url(self) -> str:
        return f"{self.base_url}/images/edits"


@dataclass(frozen=True)
class GeminiSettings:
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    timeout_seconds: float = 120.0
    text_model: str = "gemini-flash-lite-latest"
    analysis_model: str = "gemini-flash-lite-latest"
    image_model: str = "gemini-3.1-flash-lite-image"
    image_backend: str = "native"
    aspect_ratio: str = "1:1"
    output_format: str = "png"

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", _plain_https_base(self.base_url))
        if not 1 <= self.timeout_seconds <= 900:
            raise ValueError("Provider timeout is outside the approved range")
        if not self.text_model or not self.analysis_model or not self.image_model:
            raise ValueError("Gemini models must not be empty")
        if self.aspect_ratio not in {"1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"}:
            raise ValueError("Unsupported Gemini image aspect ratio")
        if self.output_format not in {"png", "jpg", "webp"}:
            raise ValueError("Unsupported Gemini output format")
        if self.image_backend not in {"native", "text_render"}:
            raise ValueError("Unsupported Gemini image backend")

    def generate_url(self, model: str) -> str:
        return f"{self.base_url}/models/{model}:generateContent"


@dataclass(frozen=True)
class MeshyImageSettings:
    """Public settings for Meshy's asynchronous 2D text-to-image API."""

    base_url: str = "https://api.meshy.ai"
    timeout_seconds: float = 300.0
    poll_interval_seconds: float = 2.0
    max_poll_attempts: int = 120
    allowed_image_hosts: frozenset[str] = frozenset({"assets.meshy.ai"})

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", _plain_https_base(self.base_url))
        if not 1 <= self.timeout_seconds <= 900:
            raise ValueError("Meshy timeout is outside the approved range")
        if not 0 <= self.poll_interval_seconds <= 30:
            raise ValueError("Meshy poll interval is outside the approved range")
        if not 1 <= self.max_poll_attempts <= 600:
            raise ValueError("Meshy poll attempts are outside the approved range")
        if not self.allowed_image_hosts:
            raise ValueError("at least one Meshy image host must be approved")

    @property
    def text_to_image_url(self) -> str:
        return f"{self.base_url}/openapi/v1/text-to-image"

    @property
    def image_to_image_url(self) -> str:
        return f"{self.base_url}/openapi/v1/image-to-image"

    @property
    def balance_url(self) -> str:
        return f"{self.base_url}/openapi/v1/balance"


@dataclass(frozen=True)
class TripoImageSettings:
    """Public settings for Tripo's asynchronous image-generation API."""

    base_url: str = "https://openapi.tripo3d.ai"
    advanced_image_base_url: str = "https://api.tripo3d.ai"
    timeout_seconds: float = 300.0
    poll_interval_seconds: float = 2.0
    max_poll_attempts: int = 120
    allowed_image_hosts: frozenset[str] = frozenset(
        {
            "tripo3d.ai",
            "data.tripo3d.com",
        }
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", _plain_https_base(self.base_url))
        object.__setattr__(
            self,
            "advanced_image_base_url",
            _plain_https_base(self.advanced_image_base_url),
        )
        if not 1 <= self.timeout_seconds <= 900:
            raise ValueError("Tripo image timeout is outside the approved range")
        if not 0 <= self.poll_interval_seconds <= 30:
            raise ValueError("Tripo image poll interval is outside the approved range")
        if not 1 <= self.max_poll_attempts <= 600:
            raise ValueError("Tripo image poll attempts are outside the approved range")
        if not self.allowed_image_hosts:
            raise ValueError("at least one Tripo image host must be approved")

    @property
    def text_to_image_url(self) -> str:
        return self.advanced_image_task_url

    @property
    def advanced_image_upload_url(self) -> str:
        return f"{self.advanced_image_base_url}/v2/openapi/upload/sts"

    @property
    def advanced_image_task_url(self) -> str:
        return f"{self.advanced_image_base_url}/v2/openapi/task"

    @property
    def task_url(self) -> str:
        return self.advanced_image_task_url

    @property
    def balance_url(self) -> str:
        return f"{self.base_url}/v3/account/balance"


class CredentialResolver:
    """Resolve known profiles without ever enumerating or returning metadata."""

    def __init__(
        self,
        store: SecretStore,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self._store = store
        self._environment = environment if environment is not None else os.environ

    def callback(self, profile: str) -> Callable[[], str | None]:
        return lambda: self.get(profile)

    def get(self, profile: str) -> str | None:
        for candidate in self._profile_aliases(profile):
            value = self._store.get(candidate)
            if value and value.strip():
                return value.strip()
        env_name = {
            OPENAI_PROFILE: "OPENAI_API_KEY",
            TRIPO_PROFILE: "TRIPO_API_KEY",
            NANOBANANA_PROFILE: "NANOBANANA_API_KEY",
            GEMINI_PROFILE: "GEMINI_API_KEY",
            MESHY_PROFILE: "MESHY_API_KEY",
        }.get(profile)
        if env_name:
            value = self._environment.get(env_name)
            if value and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _profile_aliases(profile: str) -> tuple[str, ...]:
        aliases = {
            OPENAI_PROFILE: (OPENAI_PROFILE, "openai", "gpt-image/default", "gpt_image"),
            TRIPO_PROFILE: (TRIPO_PROFILE, "tripo3d", "tripo/default"),
            NANOBANANA_PROFILE: (NANOBANANA_PROFILE,),
            GEMINI_PROFILE: (GEMINI_PROFILE, "google/gemini/default", "gemini/default"),
            MESHY_PROFILE: (MESHY_PROFILE, "meshy", "meshy/image/default"),
        }
        return aliases.get(profile, (profile,))


def load_openai_public_settings(path: Path | None = None) -> OpenAICompatibleSettings:
    """Load only non-secret fields from the local public settings file.

    API-key fields in this file are intentionally ignored; credentials come
    from the OS keyring/environment through :class:`CredentialResolver`.
    """

    config_path = path or _local_config_path("openaimodel.local.json")
    data: dict[str, Any] = {}
    try:
        parsed = json.loads(config_path.read_text("utf-8-sig"))
        if isinstance(parsed, dict):
            data = parsed
    except OSError, ValueError:
        pass
    base_url = os.environ.get("OPENAI_BASE_URL") or data.get("openai_base_url")
    analysis_model = os.environ.get("AIPICTOMODEL_ANALYSIS_MODEL") or data.get("analysis_model")
    image_model = os.environ.get("AIPICTOMODEL_IMAGE_MODEL") or data.get("image_model")
    resolved_base = str(base_url or "https://api.openai.com/v1")
    default_image_model = (
        "gpt-image-2" if urlparse(resolved_base).hostname == "api.openai.com" else "gpt-image-2-r1"
    )
    return OpenAICompatibleSettings(
        base_url=resolved_base,
        analysis_model=str(analysis_model or "gpt-4.1-mini"),
        image_model=str(image_model or default_image_model),
    )


def load_gemini_public_settings(path: Path | None = None) -> GeminiSettings | None:
    """Load non-secret Google Gemini settings when the local profile is enabled."""

    config_path = path or _local_config_path("gemini.local.json")
    try:
        parsed = json.loads(config_path.read_text("utf-8-sig"))
    except OSError, ValueError:
        return None
    if not isinstance(parsed, dict) or parsed.get("enabled") is not True:
        return None
    if parsed.get("provider") != "google" or parsed.get("protocol") != "google_generative_ai":
        raise ValueError("Gemini config must use the official Google Generative AI protocol")
    return GeminiSettings(
        base_url=str(parsed.get("base_url") or GeminiSettings.base_url),
        timeout_seconds=float(parsed.get("timeout_seconds") or 120),
        text_model=str(parsed.get("default_text_model") or "gemini-flash-lite-latest"),
        analysis_model=str(parsed.get("analysis_model") or "gemini-flash-lite-latest"),
        image_model=str(parsed.get("image_generation_model") or "gemini-3.1-flash-lite-image"),
        image_backend=str(parsed.get("image_backend") or "native"),
        aspect_ratio=str(parsed.get("aspect_ratio") or "1:1"),
        output_format=str(parsed.get("output_format") or "png"),
    )
