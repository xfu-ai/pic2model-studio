from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from aipic_to_model.infrastructure.local_inference import LocalInferenceGate
from aipic_to_model.infrastructure.stable_diffusion_cpp import (
    STABLE_DIFFUSION_CPP_CAPABILITY,
    Z_IMAGE_DIFFUSION_CAPABILITY,
    Z_IMAGE_LLM_CAPABILITY,
    Z_IMAGE_VAE_CAPABILITY,
    StableDiffusionCppRunner,
    ZImageCancelled,
    ZImageExecutionError,
    ZImageGenerationSpec,
    ZImageOutputInvalid,
    ZImageRuntimeConfig,
    ZImageTimedOut,
)


class _Process:
    def __init__(self, *, running_polls: int = 0, on_finish=None) -> None:
        self.pid = 1234
        self.returncode: int | None = None if running_polls else 0
        self._running_polls = running_polls
        self._on_finish = on_finish
        self._finished = False

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        if self._running_polls:
            self._running_polls -= 1
            return None
        self.returncode = 0
        if self._on_finish is not None and not self._finished:
            self._finished = True
            self._on_finish()
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.returncode = self.returncode if self.returncode is not None else -15
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


def _runtime(tmp_path: Path) -> tuple[dict[str, Path], Any]:
    paths = {
        STABLE_DIFFUSION_CPP_CAPABILITY: tmp_path / ("sd-cli.exe" if os.name == "nt" else "sd-cli"),
        Z_IMAGE_DIFFUSION_CAPABILITY: tmp_path / "z-image-turbo-Q8_0.gguf",
        Z_IMAGE_VAE_CAPABILITY: tmp_path / "ae.safetensors",
        Z_IMAGE_LLM_CAPABILITY: tmp_path / "qwen3-4b-Q8_0.gguf",
    }
    for path in paths.values():
        path.write_bytes(b"fixture")
    return paths, paths.get


def _write_outputs(command: list[str], count: int, size: tuple[int, int]) -> None:
    pattern = command[command.index("--output") + 1]
    for index in range(1, count + 1):
        Image.new("RGB", size, (20 * index, 40, 80)).save(pattern % index)


def _spec(**overrides: object) -> ZImageGenerationSpec:
    values: dict[str, object] = {
        "prompt": "a studio photograph of a ceramic robot",
        "width": 512,
        "height": 512,
        "candidate_count": 2,
        "seed": 42,
        "steps": 8,
        "timeout_seconds": 5.0,
    }
    values.update(overrides)
    return ZImageGenerationSpec(**values)  # type: ignore[arg-type]


def test_runner_uses_fixed_z_image_command_and_redacted_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, resolver = _runtime(tmp_path)
    captured: dict[str, object] = {}
    monkeypatch.setenv("AIPIC_SECRET_TOKEN", "must-not-reach-child")
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))

    def factory(command: list[str], **kwargs: object) -> _Process:
        captured.update(command=command, kwargs=kwargs)
        _write_outputs(command, 2, (512, 512))
        return _Process()

    runner = StableDiffusionCppRunner(resolver, process_factory=factory)
    result = runner.generate(
        "job:test",
        ZImageRuntimeConfig(),
        _spec(),
        tmp_path / "temporary",
        cancelled=lambda: False,
        heartbeat=lambda: True,
    )

    command = captured["command"]
    assert isinstance(command, list)
    assert command[:7] == [
        str(paths[STABLE_DIFFUSION_CPP_CAPABILITY]),
        "--diffusion-model",
        str(paths[Z_IMAGE_DIFFUSION_CAPABILITY]),
        "--vae",
        str(paths[Z_IMAGE_VAE_CAPABILITY]),
        "--llm",
        str(paths[Z_IMAGE_LLM_CAPABILITY]),
    ]
    assert command[command.index("--cfg-scale") + 1] == "1.0"
    assert command[command.index("--steps") + 1] == "8"
    assert command[command.index("--seed") + 1] == "42"
    assert command[command.index("--batch-count") + 1] == "2"
    assert command[command.index("--output-begin-idx") + 1] == "1"
    assert "--offload-to-cpu" in command and "--diffusion-fa" in command
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["shell"] is False
    assert "AIPIC_SECRET_TOKEN" not in kwargs["env"]
    assert len(result.images) == 2 and result.seed == 42 and result.steps == 8


@pytest.mark.parametrize("kind", ["missing", "corrupt", "wrong_dimensions"])
def test_runner_rejects_missing_or_invalid_outputs(tmp_path: Path, kind: str) -> None:
    _, resolver = _runtime(tmp_path)

    def factory(command: list[str], **_kwargs: object) -> _Process:
        pattern = command[command.index("--output") + 1]
        if kind == "corrupt":
            Path(pattern % 1).write_bytes(b"not-png")
        elif kind == "wrong_dimensions":
            Image.new("RGB", (576, 512), "blue").save(pattern % 1)
        return _Process()

    runner = StableDiffusionCppRunner(resolver, process_factory=factory)
    with pytest.raises(ZImageOutputInvalid):
        runner.generate(
            "job:test",
            ZImageRuntimeConfig(),
            _spec(candidate_count=1),
            tmp_path / "temporary",
            cancelled=lambda: False,
            heartbeat=lambda: True,
        )


def test_runner_cancellation_and_timeout_terminate_the_process_tree(tmp_path: Path) -> None:
    _, resolver = _runtime(tmp_path)
    terminated: list[_Process] = []

    def terminate(process: _Process) -> None:
        terminated.append(process)
        process.returncode = -15

    cancelled_runner = StableDiffusionCppRunner(
        resolver,
        process_factory=lambda *_args, **_kwargs: _Process(running_polls=100),
        terminate_tree=terminate,
        sleep=lambda _seconds: None,
    )
    cancel_checks = iter((False, True))
    with pytest.raises(ZImageCancelled):
        cancelled_runner.generate(
            "job:cancelled",
            ZImageRuntimeConfig(),
            _spec(candidate_count=1),
            tmp_path / "cancelled",
            cancelled=lambda: next(cancel_checks),
            heartbeat=lambda: True,
        )

    times = iter((0.0, 2.0))
    timeout_runner = StableDiffusionCppRunner(
        resolver,
        process_factory=lambda *_args, **_kwargs: _Process(running_polls=100),
        terminate_tree=terminate,
        monotonic=lambda: next(times),
        sleep=lambda _seconds: None,
    )
    with pytest.raises(ZImageTimedOut):
        timeout_runner.generate(
            "job:timeout",
            ZImageRuntimeConfig(),
            _spec(candidate_count=1, timeout_seconds=1.0),
            tmp_path / "timeout",
            cancelled=lambda: False,
            heartbeat=lambda: True,
        )
    assert len(terminated) == 2


def test_probe_rejects_missing_or_non_fixed_capabilities(tmp_path: Path) -> None:
    _, resolver = _runtime(tmp_path)
    runner = StableDiffusionCppRunner(resolver)
    assert runner.probe(ZImageRuntimeConfig())
    assert not runner.probe(ZImageRuntimeConfig(executable_capability_id="arbitrary/path"))
    assert not StableDiffusionCppRunner(lambda _capability: None).probe(ZImageRuntimeConfig())


def test_shared_gpu_gate_serializes_runner_processes(tmp_path: Path) -> None:
    _, resolver = _runtime(tmp_path)
    lock = threading.Lock()
    active = 0
    maximum = 0

    def factory(command: list[str], **_kwargs: object) -> _Process:
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        _write_outputs(command, 1, (512, 512))

        def finish() -> None:
            nonlocal active
            with lock:
                active -= 1

        return _Process(running_polls=3, on_finish=finish)

    runner = StableDiffusionCppRunner(
        resolver,
        gate=LocalInferenceGate(),
        process_factory=factory,
        sleep=lambda _seconds: time.sleep(0.01),
    )

    def generate(owner: str) -> None:
        runner.generate(
            owner,
            ZImageRuntimeConfig(),
            _spec(candidate_count=1),
            tmp_path / "serialized",
            cancelled=lambda: False,
            heartbeat=lambda: True,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(generate, ("job:one", "job:two")))
    assert maximum == 1


def test_runner_does_not_include_raw_process_failure_details(tmp_path: Path) -> None:
    _, resolver = _runtime(tmp_path)
    process = _Process()
    process.returncode = 7
    runner = StableDiffusionCppRunner(
        resolver,
        process_factory=lambda *_args, **_kwargs: process,
    )
    with pytest.raises(ZImageExecutionError, match="generation failed") as captured:
        runner.generate(
            "job:failed",
            ZImageRuntimeConfig(),
            _spec(candidate_count=1),
            tmp_path / "failed",
            cancelled=lambda: False,
            heartbeat=lambda: True,
        )
    assert "prompt" not in str(captured.value).lower()
