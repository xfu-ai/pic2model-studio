from __future__ import annotations

from pathlib import Path
from typing import Any

from aipic_to_model.infrastructure.ollama_runtime import (
    DEFAULT_CONTEXT_LENGTH,
    DEFAULT_KV_CACHE_TYPE,
    OllamaRuntimeManager,
    classify_ollama_diagnostic,
    discover_ollama_executable,
    discover_ollama_models_directory,
    ollama_server_url,
)


class FakeProcess:
    def __init__(self) -> None:
        self.return_code: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.return_code

    def terminate(self) -> None:
        self.terminated = True
        self.return_code = 0

    def kill(self) -> None:
        self.killed = True
        self.return_code = -1

    def wait(self, timeout: float | None = None) -> int:
        assert timeout is None or timeout >= 0
        return self.return_code or 0


def test_ollama_manager_reuses_external_service_without_taking_ownership() -> None:
    launched: list[dict[str, Any]] = []
    manager = OllamaRuntimeManager(
        probe=lambda: True,
        popen_factory=lambda *args, **kwargs: launched.append(kwargs),  # type: ignore[arg-type,return-value]
        supervisor_interval_seconds=60,
    )

    status = manager.start()
    manager.stop()

    assert status.available is True
    assert status.reused_external is True
    assert status.managed is False
    assert launched == []


def test_ollama_manager_starts_owned_runtime_with_required_context_and_stops_it(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "ollama.exe"
    executable.write_bytes(b"runtime")
    models = tmp_path / "models"
    models.mkdir()
    running = {"available": False}
    captured: dict[str, Any] = {}
    process = FakeProcess()

    def launch(*args: Any, **kwargs: Any) -> FakeProcess:
        captured["args"] = args
        captured["kwargs"] = kwargs
        running["available"] = True
        return process

    manager = OllamaRuntimeManager(
        executable=executable,
        models_directory=models,
        environment={"OLLAMA_KEEP_ALIVE": "20m"},
        probe=lambda: running["available"],
        popen_factory=launch,
        supervisor_interval_seconds=60,
    )

    status = manager.start()
    manager.stop()

    command = captured["args"][0]
    environment = captured["kwargs"]["env"]
    assert command == [str(executable), "serve"]
    assert environment["OLLAMA_HOST"] == "http://127.0.0.1:11434"
    assert environment["OLLAMA_CONTEXT_LENGTH"] == str(DEFAULT_CONTEXT_LENGTH)
    assert environment["OLLAMA_KEEP_ALIVE"] == "20m"
    assert environment["OLLAMA_FLASH_ATTENTION"] == "1"
    assert environment["OLLAMA_KV_CACHE_TYPE"] == DEFAULT_KV_CACHE_TYPE
    assert environment["OLLAMA_MAX_LOADED_MODELS"] == "1"
    assert environment["OLLAMA_NUM_PARALLEL"] == "1"
    assert environment["OLLAMA_MODELS"] == str(models)
    assert status.available is True
    assert status.managed is True
    assert status.reused_external is False
    assert process.terminated is True


def test_ollama_manager_restarts_only_its_owned_failed_process(tmp_path: Path) -> None:
    executable = tmp_path / "ollama.exe"
    executable.write_bytes(b"runtime")
    processes: list[FakeProcess] = []

    def launch(*_args: Any, **_kwargs: Any) -> FakeProcess:
        process = FakeProcess()
        processes.append(process)
        return process

    manager = OllamaRuntimeManager(
        executable=executable,
        probe=lambda: False,
        popen_factory=launch,
        startup_timeout_seconds=0,
        supervisor_interval_seconds=60,
    )

    first = manager.ensure_running()
    processes[0].return_code = 1
    second = manager.ensure_running()
    manager.stop()

    assert first.managed is True
    assert len(processes) == 2
    assert second.managed is True
    assert second.restart_count == 1
    assert processes[1].terminated is True


def test_ollama_manager_can_start_without_blocking_desktop_readiness(tmp_path: Path) -> None:
    executable = tmp_path / "ollama.exe"
    executable.write_bytes(b"runtime")
    process = FakeProcess()
    manager = OllamaRuntimeManager(
        executable=executable,
        probe=lambda: False,
        popen_factory=lambda *_args, **_kwargs: process,
        startup_timeout_seconds=60,
        supervisor_interval_seconds=60,
    )

    status = manager.start(wait_for_ready=False)
    manager.stop()

    assert status.available is False
    assert status.managed is True
    assert status.reason == "runtime_starting"
    assert process.terminated is True


def test_ollama_runtime_resolution_and_endpoint_remain_explicitly_loopback(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "ollama.exe"
    executable.write_bytes(b"runtime")
    models = tmp_path / "models"
    (models / "blobs").mkdir(parents=True)
    (models / "manifests").mkdir()

    assert discover_ollama_executable(str(executable)) == executable.resolve()
    assert discover_ollama_models_directory(str(models)) == models.resolve()
    assert ollama_server_url("http://localhost:22434/v1") == "http://localhost:22434"


def test_ollama_diagnostics_are_reduced_to_safe_categories() -> None:
    assert classify_ollama_diagnostic("CUDA error: out of memory at C:\\private\\model") == (
        "resource_exhausted"
    )
    assert classify_ollama_diagnostic("llama runner process has terminated") == (
        "runner_unavailable"
    )
    assert classify_ollama_diagnostic("ordinary startup metadata") is None
