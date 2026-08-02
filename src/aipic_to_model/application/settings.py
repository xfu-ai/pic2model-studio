from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Protocol

from ..domain.common import DomainErrorV1, ErrorCode, canonical_json
from .ports import FilesystemPort, SettingsRepositoryPort

ALLOWED_APP_KEYS = {
    "theme",
    "enter_sends",
    "candidate_count",
    "provider_profiles",
    "blender_path",
    "log_retention_days",
    "image_provider_priority",
    "provider_probe_interval_seconds",
}
ALLOWED_PROJECT_KEYS = {"workspace_preferences", "image_defaults", "provider_overrides"}
_SENSITIVE_SETTING_KEY = re.compile(
    r"(?i)(key|token|secret|password|authorization|signature|sig|credential)"
)
_SIGNED_URL_OR_CREDENTIAL = re.compile(
    r"(?i)(?:[?&](?:x-amz-signature|signature|sig)=|authorization\s*[:=]|\bbearer\s+|"
    r"(?:api[_-]?key|token|secret|password|credential)\s*[:=])"
)
_SECRET_MARKER = re.compile(r"(?i)(api[_-]?key|token|secret|password|credential)")
_OPAQUE_SECRET = re.compile(
    r"(?i)^(?:sk|rk|pk|ak|api[_-]?key|key|token|secret)[_-][a-z0-9_-]{10,}$|"
    r"^AIza[a-z0-9_-]{20,}$|^AKID[a-z0-9]{16,}$|"
    r"^eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+$"
)
_PUBLIC_IMAGE_MODELS = {
    "configured-by-profile",
    "gemini-2.0-flash",
    "gemini-2.0-flash-001",
    "gemini-2.0-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-flash-image",
    "gemini-2.5-pro",
    "gemini-3.1-flash-image",
    "gemini-3.1-flash-lite-image",
    "gemini-3-pro-image-preview",
    "gemini-3.1-flash-image-preview",
    "gemini_3.1_flash_image_preview",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
    "gpt-image-2",
    "gpt-image-2-r1",
    "gpt-image-2-r2",
    "gpt-image-2-stb",
    "nano-banana",
    "seedream_v5",
    "seedream_v4",
    "banana",
    "banana_pro",
    "banana2",
    "chat_image_1",
    "chat_image_1.5",
    "chat_image_2",
}

_IMAGE_PROVIDER_PROFILES = {"tripo3d/default", "meshy/default"}


class SecretStore(Protocol):
    def set(self, profile: str, secret: str) -> None: ...
    def get(self, profile: str) -> str | None: ...
    def delete(self, profile: str) -> None: ...


def _assert_non_sensitive_patch(value: Any) -> None:
    """Reject values that the general settings APIs must never retain."""
    if isinstance(value, dict):
        for key, item in value.items():
            if _SENSITIVE_SETTING_KEY.search(str(key)):
                raise DomainErrorV1(
                    ErrorCode.SCHEMA_VALIDATION_FAILED,
                    "Secrets cannot be stored in general settings.",
                )
            _assert_non_sensitive_patch(item)
    elif isinstance(value, list):
        for item in value:
            _assert_non_sensitive_patch(item)
    elif isinstance(value, str) and (
        _SIGNED_URL_OR_CREDENTIAL.search(value)
        or _SECRET_MARKER.search(value)
        or _OPAQUE_SECRET.fullmatch(value)
    ):
        raise DomainErrorV1(
            ErrorCode.SCHEMA_VALIDATION_FAILED,
            "Signed URLs and credentials cannot be stored in general settings.",
        )


def _assert_nonempty_public_string(value: Any, field: str) -> None:
    """Permit public metadata, but never opaque credentials disguised as an identifier."""
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise DomainErrorV1(ErrorCode.SCHEMA_VALIDATION_FAILED, f"Invalid {field} setting.")
    _assert_non_sensitive_patch(value)


def _assert_public_model(value: Any) -> None:
    """Only allow known public model identifiers, never credential-like input."""
    if value not in _PUBLIC_IMAGE_MODELS:
        raise DomainErrorV1(ErrorCode.SCHEMA_VALIDATION_FAILED, "Unsupported public model setting.")


def _assert_keys(value: Any, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - allowed:
        raise DomainErrorV1(ErrorCode.SCHEMA_VALIDATION_FAILED, "Unsupported settings shape.")
    return value


def _assert_non_secret_settings_schema(patch: dict[str, Any], scope: str) -> None:
    """Frozen recursive allowlist: settings are metadata, never an arbitrary JSON vault."""
    _assert_non_sensitive_patch(patch)
    if scope == "app":
        for key, value in patch.items():
            if key == "blender_path" and value is None:
                continue
            if key in {"theme", "blender_path"}:
                _assert_nonempty_public_string(value, key)
            if key in {"enter_sends"} and not isinstance(value, bool):
                raise DomainErrorV1(ErrorCode.SCHEMA_VALIDATION_FAILED, "Invalid settings value.")
            if key == "candidate_count" and (
                not isinstance(value, int) or isinstance(value, bool) or value not in {1, 2, 4}
            ):
                raise DomainErrorV1(
                    ErrorCode.SCHEMA_VALIDATION_FAILED,
                    "Candidate count must be 1, 2, or 4.",
                )
            if key == "log_retention_days" and (
                not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 14
            ):
                raise DomainErrorV1(ErrorCode.SCHEMA_VALIDATION_FAILED, "Invalid settings value.")
            if key == "provider_probe_interval_seconds" and (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 60 <= value <= 3600
            ):
                raise DomainErrorV1(
                    ErrorCode.SCHEMA_VALIDATION_FAILED,
                    "Provider probe interval must be between 60 and 3600 seconds.",
                )
            if key == "image_provider_priority" and (
                not isinstance(value, list)
                or len(value) != len(_IMAGE_PROVIDER_PROFILES)
                or set(value) != _IMAGE_PROVIDER_PROFILES
                or not all(isinstance(item, str) for item in value)
            ):
                raise DomainErrorV1(
                    ErrorCode.SCHEMA_VALIDATION_FAILED,
                    "Image Provider priority must list Tripo and Meshy exactly once.",
                )
            if key == "provider_profiles":
                for profile in _assert_keys(
                    value, set(value) if isinstance(value, dict) else set()
                ).values():
                    details = _assert_keys(
                        profile,
                        {
                            "enabled",
                            "label",
                            "model",
                            "endpoint",
                            "insecure_http",
                            "timeout_seconds",
                        },
                    )
                    if any(
                        not isinstance(item, (str, bool, int)) or isinstance(item, bytes)
                        for item in details.values()
                    ):
                        raise DomainErrorV1(
                            ErrorCode.SCHEMA_VALIDATION_FAILED, "Invalid provider profile."
                        )
                    for string_key in {"label", "model", "endpoint"} & set(details):
                        _assert_nonempty_public_string(details[string_key], string_key)
                    if "model" in details:
                        _assert_public_model(details["model"])
    else:
        schemas = {
            "workspace_preferences": {"theme", "layout", "show_hidden", "sort", "zoom"},
            "image_defaults": {
                "model",
                "size",
                "aspect_ratio",
                "n",
                "quality",
                "output_format",
                "use_source",
            },
            "provider_overrides": {
                "model",
                "size",
                "aspect_ratio",
                "n",
                "timeout_seconds",
                "enabled",
            },
        }
        for key, value in patch.items():
            details = _assert_keys(value, schemas[key])
            if any(not isinstance(item, (str, bool, int, float)) for item in details.values()):
                raise DomainErrorV1(
                    ErrorCode.SCHEMA_VALIDATION_FAILED, "Invalid project settings value."
                )
            for string_key, string_value in details.items():
                if isinstance(string_value, str):
                    _assert_nonempty_public_string(string_value, string_key)
            if "model" in details:
                _assert_public_model(details["model"])
            if "n" in details and (
                not isinstance(details["n"], int)
                or isinstance(details["n"], bool)
                or details["n"] not in {1, 2, 4}
            ):
                raise DomainErrorV1(
                    ErrorCode.SCHEMA_VALIDATION_FAILED,
                    "Candidate count must be 1, 2, or 4.",
                )


class SettingsService:
    def __init__(self, repository: SettingsRepositoryPort, filesystem: FilesystemPort) -> None:
        self._repository = repository
        self._filesystem = filesystem

    def get_app(self, app_db: Path) -> dict[str, Any]:
        """Return the isolated non-sensitive application settings database."""
        return self._repository.get_app(app_db)

    def update_app(
        self, app_db: Path, patch: dict[str, Any], request_id: str | None = None
    ) -> dict[str, Any]:
        if set(patch) - ALLOWED_APP_KEYS:
            raise DomainErrorV1(ErrorCode.SCHEMA_VALIDATION_FAILED, "应用设置键不受支持。")
        _assert_non_secret_settings_schema(patch, "app")
        payload_hash = hashlib.sha256(canonical_json(patch).encode()).hexdigest()
        return self._repository.update_app(app_db, patch, payload_hash, request_id)

    def update_project(
        self, root: Path, patch: dict[str, Any], request_id: str | None = None
    ) -> dict[str, Any]:
        self._filesystem.require_writable_root(root)
        if set(patch) - ALLOWED_PROJECT_KEYS:
            raise DomainErrorV1(ErrorCode.SCHEMA_VALIDATION_FAILED, "设置键不受支持。")
        _assert_non_secret_settings_schema(patch, "project")
        payload_hash = hashlib.sha256(canonical_json(patch).encode()).hexdigest()
        return self._repository.update_project(
            root / "project.sqlite3", patch, payload_hash, request_id
        )

    def set_secret(
        self,
        store: SecretStore,
        provider_profile: str,
        secret: str,
        app_db: Path | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        if not provider_profile or not secret:
            raise DomainErrorV1(ErrorCode.SCHEMA_VALIDATION_FAILED, "Provider 配置和密钥不能为空。")
        payload_hash = hashlib.sha256(f"{provider_profile}\n{secret}".encode()).hexdigest()
        if app_db and request_id:
            previous = self._repository.replay_app_operation(
                app_db, "settings.set_secret", payload_hash, request_id
            )
            if previous is not None:
                return previous
        try:
            store.set(provider_profile, secret)
        except Exception as error:
            raise DomainErrorV1(
                ErrorCode.SECURE_STORAGE_UNAVAILABLE, "安全存储不可用。", True
            ) from error
        result = {
            "provider_profile": provider_profile,
            "configured": True,
            "mask": "••••" + secret[-4:],
        }
        if app_db and request_id:
            self._repository.record_app_operation(
                app_db, "settings.set_secret", payload_hash, request_id, result
            )
        return result
