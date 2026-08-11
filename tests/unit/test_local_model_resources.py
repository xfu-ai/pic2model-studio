from __future__ import annotations

from pathlib import Path

import pytest

from aipic_to_model.infrastructure import local_model_resources, ollama_runtime
from aipic_to_model.infrastructure.stable_diffusion_cpp import (
    STABLE_DIFFUSION_CPP_CAPABILITY,
    Z_IMAGE_DIFFUSION_CAPABILITY,
    Z_IMAGE_LLM_CAPABILITY,
    Z_IMAGE_VAE_CAPABILITY,
    resolve_environment_local_capability,
)
from aipic_to_model.infrastructure.triposr_worker import (
    TRIPOSR_MODEL_CAPABILITY,
    TRIPOSR_RUNNER_CAPABILITY,
    TRIPOSR_WORKER_CAPABILITY,
    resolve_environment_triposr_capability,
)


def _file(root: Path, relative: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fixture")
    return path.resolve()


def test_bundled_local_model_root_resolves_every_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "local-models"
    expected = {
        STABLE_DIFFUSION_CPP_CAPABILITY: _file(
            root, "z-image-turbo/runtime/sd-cli.exe"
        ),
        Z_IMAGE_DIFFUSION_CAPABILITY: _file(
            root, "z-image-turbo/models/z_image_turbo-Q3_K.gguf"
        ),
        Z_IMAGE_VAE_CAPABILITY: _file(
            root, "z-image-turbo/models/ae.safetensors"
        ),
        Z_IMAGE_LLM_CAPABILITY: _file(
            root, "z-image-turbo/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
        ),
        TRIPOSR_WORKER_CAPABILITY: _file(root, "triposr/python/python.exe"),
        TRIPOSR_RUNNER_CAPABILITY: _file(root, "triposr/source/run.py"),
    }
    triposr_model = root / "triposr/model"
    triposr_model.mkdir(parents=True)
    expected[TRIPOSR_MODEL_CAPABILITY] = triposr_model.resolve()

    ollama_executable = _file(root, "ollama/runtime/ollama.exe")
    ollama_models = root / "ollama/models"
    (ollama_models / "blobs").mkdir(parents=True)
    (ollama_models / "manifests").mkdir()

    monkeypatch.setenv("AIPIC_LOCAL_MODEL_ROOT", str(root))
    for name in (
        "AIPIC_ZIMAGE_SD_CLI",
        "AIPIC_ZIMAGE_DIFFUSION_MODEL",
        "AIPIC_ZIMAGE_VAE",
        "AIPIC_ZIMAGE_LLM",
        "AIPIC_TRIPOSR_PYTHON",
        "AIPIC_TRIPOSR_RUNNER",
        "AIPIC_TRIPOSR_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(ollama_runtime.shutil, "which", lambda _command: None)

    for capability in (
        STABLE_DIFFUSION_CPP_CAPABILITY,
        Z_IMAGE_DIFFUSION_CAPABILITY,
        Z_IMAGE_VAE_CAPABILITY,
        Z_IMAGE_LLM_CAPABILITY,
    ):
        assert resolve_environment_local_capability(capability) == expected[capability]
    for capability in (
        TRIPOSR_WORKER_CAPABILITY,
        TRIPOSR_RUNNER_CAPABILITY,
        TRIPOSR_MODEL_CAPABILITY,
    ):
        assert resolve_environment_triposr_capability(capability) == expected[capability]
    assert ollama_runtime.discover_ollama_executable() == ollama_executable
    assert ollama_runtime.discover_ollama_models_directory() == ollama_models.resolve()


def test_explicit_capability_override_does_not_fall_back_when_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "local-models"
    _file(root, "z-image-turbo/runtime/sd-cli.exe")
    monkeypatch.setenv("AIPIC_LOCAL_MODEL_ROOT", str(root))
    monkeypatch.setenv("AIPIC_ZIMAGE_SD_CLI", str(tmp_path / "missing.exe"))

    assert resolve_environment_local_capability(STABLE_DIFFUSION_CPP_CAPABILITY) is None


def test_portable_sidecar_discovers_adjacent_resource_root_without_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resource_root = tmp_path / "app" / "resources" / "local-models"
    resource_root.mkdir(parents=True)
    sidecar = tmp_path / "app" / "resources" / "sidecar" / "pic2model-sidecar.exe"
    sidecar.parent.mkdir()
    sidecar.write_bytes(b"fixture")
    monkeypatch.delenv("AIPIC_LOCAL_MODEL_ROOT", raising=False)
    monkeypatch.setattr(local_model_resources.sys, "executable", str(sidecar))

    assert local_model_resources.local_model_resource_roots()[0] == resource_root.resolve()
