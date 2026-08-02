from __future__ import annotations

import json

from aipic_to_model.infrastructure.providers import config


def test_public_provider_config_resolves_from_source_root_outside_working_directory(
    tmp_path, monkeypatch
) -> None:
    source_root = tmp_path / "checkout"
    provider_module = (
        source_root
        / "src"
        / "aipic_to_model"
        / "infrastructure"
        / "providers"
        / "config.py"
    )
    provider_module.parent.mkdir(parents=True)
    provider_module.write_text("# test module location", encoding="utf-8")
    local = source_root / ".local"
    local.mkdir()
    (local / "openaimodel.local.json").write_text(
        json.dumps(
            {
                "openai_base_url": "https://gateway.example.test/v1",
                "analysis_model": "vision-model",
                "image_model": "image-model",
            }
        ),
        encoding="utf-8",
    )
    launch_directory = tmp_path / "desktop" / "src-tauri"
    launch_directory.mkdir(parents=True)
    monkeypatch.chdir(launch_directory)
    monkeypatch.setattr(config, "__file__", str(provider_module))

    settings = config.load_openai_public_settings()

    assert settings.base_url == "https://gateway.example.test/v1"
    assert settings.analysis_model == "vision-model"
    assert settings.image_model == "image-model"
