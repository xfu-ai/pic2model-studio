from pathlib import Path

import pytest

from aipic_to_model.application.projects import ProjectService
from aipic_to_model.application.settings import SettingsService
from aipic_to_model.domain.common import DomainErrorV1, ErrorCode
from aipic_to_model.infrastructure.sqlite.repositories import SettingsRepository


class MemoryStore:
    def __init__(self):
        self.data = {}

    def set(self, profile, secret):
        self.data[profile] = secret

    def get(self, profile):
        return self.data.get(profile)

    def delete(self, profile):
        self.data.pop(profile, None)


def test_b01_09_settings_reject_secrets_and_keep_key_out_of_db(tmp_path: Path):
    root = tmp_path / "project"
    ProjectService().create(root, "Demo")
    settings = SettingsService(SettingsRepository())
    assert settings.update_project(root, {"workspace_preferences": {"theme": "dark"}})
    assert SettingsService(SettingsRepository()).set_secret(
        MemoryStore(), "image/default", "sentinel-secret-1234"
    )["configured"]
    with pytest.raises(DomainErrorV1):
        settings.update_project(root, {"api_key": "sentinel-secret-1234"})
    assert b"sentinel-secret-1234" not in (root / "project.sqlite3").read_bytes()


def test_b01_09_app_setting_and_secret_request_ids_replay_without_rewriting(tmp_path: Path):
    settings = SettingsService(SettingsRepository())
    app_db = tmp_path / "app.sqlite3"
    first = settings.update_app(app_db, {"theme": "dark"}, "settings-request")
    assert settings.update_app(app_db, {"theme": "dark"}, "settings-request") == first
    with pytest.raises(DomainErrorV1) as conflict:
        settings.update_app(app_db, {"theme": "light"}, "settings-request")
    assert conflict.value.code == ErrorCode.IDEMPOTENCY_CONFLICT
    store = MemoryStore()
    secret = settings.set_secret(store, "image/default", "secret-1234", app_db, "secret-request")
    assert (
        settings.set_secret(store, "image/default", "secret-1234", app_db, "secret-request")
        == secret
    )
    with pytest.raises(DomainErrorV1):
        settings.set_secret(store, "image/default", "other-secret", app_db, "secret-request")
    assert b"secret-1234" not in app_db.read_bytes()


def test_app_settings_accept_blender_path(tmp_path: Path):
    settings = SettingsService(SettingsRepository())
    app_db = tmp_path / "app.sqlite3"
    blender = r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
    assert settings.update_app(app_db, {"blender_path": blender})["blender_path"] == blender


def test_b01_09_project_setting_request_id_replays_or_conflicts(tmp_path: Path):
    root = tmp_path / "project"
    ProjectService().create(root, "Demo")
    settings = SettingsService(SettingsRepository())
    first = settings.update_project(root, {"workspace_preferences": {"theme": "dark"}}, "project")
    assert (
        settings.update_project(root, {"workspace_preferences": {"theme": "dark"}}, "project")
        == first
    )
    with pytest.raises(DomainErrorV1) as conflict:
        settings.update_project(root, {"workspace_preferences": {"theme": "light"}}, "project")
    assert conflict.value.code == ErrorCode.IDEMPOTENCY_CONFLICT


@pytest.mark.parametrize("count", [0, 3, 8, 9, 100, True])
def test_b01_09_candidate_count_is_one_two_or_four(tmp_path: Path, count) -> None:
    root = tmp_path / "project"
    ProjectService().create(root, "Candidates")
    settings = SettingsService(SettingsRepository())
    with pytest.raises(DomainErrorV1):
        settings.update_app(tmp_path / "app.sqlite3", {"candidate_count": count})
    with pytest.raises(DomainErrorV1):
        settings.update_project(root, {"image_defaults": {"n": count}})
    assert settings.update_app(tmp_path / "valid.sqlite3", {"candidate_count": 1})[
        "candidate_count"
    ] == 1
    assert settings.update_project(root, {"image_defaults": {"n": 4}})["image_defaults"]["n"] == 4


def test_b01_09_nested_settings_secrets_and_signed_urls_are_rejected_before_persistence(
    tmp_path: Path,
):
    root = tmp_path / "project"
    ProjectService().create(root, "Settings")
    app_db = tmp_path / "app.sqlite3"
    settings = SettingsService(SettingsRepository())
    with pytest.raises(DomainErrorV1):
        settings.update_project(
            root,
            {"image_defaults": {"api_key": "nested-project-sentinel"}},
            "project-nested-secret",
        )
    with pytest.raises(DomainErrorV1):
        settings.update_app(
            app_db,
            {"provider_profiles": {"one": {"callback": "https://x/?X-Amz-Signature=sentinel"}}},
            "app-signed-url",
        )
    with pytest.raises(DomainErrorV1):
        settings.update_project(
            root,
            {"image_defaults": {"value": "project-plaintext-secret-sentinel"}},
            "neutral-project",
        )
    with pytest.raises(DomainErrorV1):
        settings.update_app(
            app_db,
            {"provider_profiles": {"default": {"credential": "app-plaintext-secret-sentinel"}}},
            "neutral-app",
        )
    with pytest.raises(DomainErrorV1):
        settings.update_project(
            root,
            {"image_defaults": {"value": "sk-proj-0123456789abcdefghijklmnopqrstuvwxyz"}},
            "opaque-project",
        )
    with pytest.raises(DomainErrorV1):
        settings.update_app(
            app_db,
            {
                "provider_profiles": {
                    "default": {"value": "sk-proj-0123456789abcdefghijklmnopqrstuvwxyz"}
                }
            },
            "opaque-app",
        )
    with pytest.raises(DomainErrorV1):
        settings.update_project(
            root,
            {"image_defaults": {"model": "sk-proj-0123456789abcdefghijklmnopqrstuvwxyz"}},
            "opaque-model",
        )
    with pytest.raises(DomainErrorV1):
        settings.update_app(
            app_db,
            {
                "provider_profiles": {
                    "default": {"model": "sk-proj-0123456789abcdefghijklmnopqrstuvwxyz"}
                }
            },
            "opaque-provider-model",
        )
    with pytest.raises(DomainErrorV1):
        settings.update_project(
            root,
            {"image_defaults": {"model": "AKIDabcdefghijklmnopqrstuvwxyz0123456789"}},
            "cos-model",
        )
    with pytest.raises(DomainErrorV1):
        settings.update_project(
            root,
            {"image_defaults": {"model": "aB3dE5fG7hJ9kLmNpQrStUvWxYz0123456789AbCd"}},
            "cos-secret-key",
        )
    assert b"nested-project-sentinel" not in (root / "project.sqlite3").read_bytes()
    assert (
        b"sk-proj-0123456789abcdefghijklmnopqrstuvwxyz"
        not in (root / "project.sqlite3").read_bytes()
    )
    assert (
        b"AKIDabcdefghijklmnopqrstuvwxyz0123456789" not in (root / "project.sqlite3").read_bytes()
    )
    assert (
        b"aB3dE5fG7hJ9kLmNpQrStUvWxYz0123456789AbCd" not in (root / "project.sqlite3").read_bytes()
    )
    assert not app_db.exists() or b"X-Amz-Signature=sentinel" not in app_db.read_bytes()
