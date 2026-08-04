from __future__ import annotations

import json
import os
import struct
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from aipic_to_model.infrastructure.triposr_worker import (
    TRIPOSR_MODEL_CAPABILITY,
    TRIPOSR_RUNNER_CAPABILITY,
    TRIPOSR_WORKER_CAPABILITY,
    TripoSRGenerationSpec,
    TripoSROutputInvalid,
    TripoSRRuntimeConfig,
    TripoSRWorkerCancelled,
    TripoSRWorkerError,
    TripoSRWorkerOutOfMemory,
    TripoSRWorkerRunner,
    TripoSRWorkerTimedOut,
)


def _glb() -> bytes:
    document = json.dumps(
        {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{}]},
        separators=(",", ":"),
    ).encode("utf-8")
    document += b" " * (-len(document) % 4)
    length = 12 + 8 + len(document)
    return (
        b"glTF"
        + struct.pack("<II", 2, length)
        + struct.pack("<II", len(document), 0x4E4F534A)
        + document
    )


class _Process:
    def __init__(self, returncode: int | None = 0, *, running_polls: int = 0) -> None:
        self.pid = 4321
        self.returncode = None if running_polls else returncode
        self._result = returncode
        self._running_polls = running_polls

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        if self._running_polls:
            self._running_polls -= 1
            return None
        self.returncode = self._result
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.returncode = -15 if self.returncode is None else self.returncode
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


def _runtime(tmp_path: Path) -> tuple[dict[str, Path], Any]:
    python = tmp_path / ("python.exe" if os.name == "nt" else "python")
    runner = tmp_path / "TripoSR" / "run.py"
    model = tmp_path / "models" / "TripoSR"
    runner.parent.mkdir()
    model.mkdir(parents=True)
    python.write_bytes(b"fixture")
    runner.write_text("# pinned TripoSR fixture", encoding="utf-8")
    (model / "config.yaml").write_text("model: fixture", encoding="utf-8")
    (model / "model.ckpt").write_bytes(b"fixture")
    dino_config = (
        model
        / "huggingface-cache"
        / "models--facebook--dino-vitb16"
        / "snapshots"
        / "f205d5d8e640a89a2b8ef0369670dfc37cc07fc2"
        / "config.json"
    )
    dino_config.parent.mkdir(parents=True)
    dino_config.write_text("{}", encoding="utf-8")
    paths = {
        TRIPOSR_WORKER_CAPABILITY: python,
        TRIPOSR_RUNNER_CAPABILITY: runner,
        TRIPOSR_MODEL_CAPABILITY: model,
    }
    return paths, paths.get


def _image() -> bytes:
    stream = BytesIO()
    Image.new("RGBA", (32, 24), (20, 80, 160, 128)).save(stream, format="PNG")
    return stream.getvalue()


def _spec(**overrides: object) -> TripoSRGenerationSpec:
    values: dict[str, object] = {
        "image_bytes": _image(),
        "mime_type": "image/png",
        "chunk_size": 8192,
        "marching_cubes_resolution": 256,
        "foreground_ratio": 0.85,
        "timeout_seconds": 5.0,
    }
    values.update(overrides)
    return TripoSRGenerationSpec(**values)  # type: ignore[arg-type]


def test_worker_uses_fixed_offline_command_and_returns_validated_glb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, resolver = _runtime(tmp_path)
    captured: dict[str, object] = {}
    monkeypatch.setenv("AIPIC_SECRET_TOKEN", "must-not-reach-worker")

    def factory(command: list[str], **kwargs: object) -> _Process:
        captured.update(command=command, kwargs=kwargs)
        input_path = Path(command[4])
        with Image.open(input_path) as image:
            assert image.format == "PNG" and image.mode == "RGB"
        output = Path(command[command.index("--output-dir") + 1]) / "0" / "mesh.glb"
        output.write_bytes(_glb())
        return _Process()

    output = TripoSRWorkerRunner(resolver, process_factory=factory).generate(
        "job:test",
        TripoSRRuntimeConfig(),
        _spec(),
        tmp_path / "temporary",
        cancelled=lambda: False,
        heartbeat=lambda: True,
    )

    command = captured["command"]
    assert isinstance(command, list)
    assert command[:5] == [
        str(paths[TRIPOSR_WORKER_CAPABILITY]),
        "-E",
        "-s",
        str(paths[TRIPOSR_RUNNER_CAPABILITY]),
        command[4],
    ]
    assert command[command.index("--pretrained-model-name-or-path") + 1] == str(
        paths[TRIPOSR_MODEL_CAPABILITY]
    )
    assert command[command.index("--model-save-format") + 1] == "glb"
    assert "--no-remove-bg" in command and "--device" in command
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict) and kwargs["shell"] is False
    environment = kwargs["env"]
    assert environment["HF_HUB_OFFLINE"] == "1"
    assert environment["HUGGINGFACE_HUB_CACHE"] == str(
        paths[TRIPOSR_MODEL_CAPABILITY] / "huggingface-cache"
    )
    assert environment["TRANSFORMERS_OFFLINE"] == "1"
    assert environment["NO_PROXY"] == "*"
    assert "AIPIC_SECRET_TOKEN" not in environment
    assert output.glb == _glb() and output.marching_cubes_resolution == 256


@pytest.mark.parametrize("payload", [b"not-glb", b""])
def test_worker_rejects_invalid_or_missing_glb(tmp_path: Path, payload: bytes) -> None:
    _, resolver = _runtime(tmp_path)

    def factory(command: list[str], **_kwargs: object) -> _Process:
        if payload:
            output = Path(command[command.index("--output-dir") + 1]) / "0" / "mesh.glb"
            output.write_bytes(payload)
        return _Process()

    runner = TripoSRWorkerRunner(resolver, process_factory=factory)
    with pytest.raises(TripoSROutputInvalid):
        runner.generate(
            "job:invalid",
            TripoSRRuntimeConfig(),
            _spec(),
            tmp_path / "temporary",
            cancelled=lambda: False,
            heartbeat=lambda: True,
        )


def test_worker_maps_oom_without_leaking_diagnostics(tmp_path: Path) -> None:
    _, resolver = _runtime(tmp_path)

    def factory(_command: list[str], **kwargs: object) -> _Process:
        diagnostic = kwargs["stderr"]
        diagnostic.write(b"RuntimeError: CUDA out of memory at C:\\secret\\model.ckpt")
        diagnostic.flush()
        return _Process(returncode=1)

    runner = TripoSRWorkerRunner(resolver, process_factory=factory)
    with pytest.raises(TripoSRWorkerOutOfMemory) as captured:
        runner.generate(
            "job:oom",
            TripoSRRuntimeConfig(),
            _spec(),
            tmp_path / "temporary",
            cancelled=lambda: False,
            heartbeat=lambda: True,
        )
    assert "secret" not in str(captured.value).lower()


def test_worker_cancellation_and_timeout_terminate_process_tree(tmp_path: Path) -> None:
    _, resolver = _runtime(tmp_path)
    terminated: list[_Process] = []

    def terminate(process: _Process) -> None:
        terminated.append(process)
        process.returncode = -15

    cancel_checks = iter((False, True))
    cancelled_runner = TripoSRWorkerRunner(
        resolver,
        process_factory=lambda *_args, **_kwargs: _Process(running_polls=100),
        terminate_tree=terminate,
        sleep=lambda _seconds: None,
    )
    with pytest.raises(TripoSRWorkerCancelled):
        cancelled_runner.generate(
            "job:cancel",
            TripoSRRuntimeConfig(),
            _spec(),
            tmp_path / "cancel",
            cancelled=lambda: next(cancel_checks),
            heartbeat=lambda: True,
        )

    times = iter((0.0, 2.0))
    timeout_runner = TripoSRWorkerRunner(
        resolver,
        process_factory=lambda *_args, **_kwargs: _Process(running_polls=100),
        terminate_tree=terminate,
        monotonic=lambda: next(times),
        sleep=lambda _seconds: None,
    )
    with pytest.raises(TripoSRWorkerTimedOut):
        timeout_runner.generate(
            "job:timeout",
            TripoSRRuntimeConfig(),
            _spec(timeout_seconds=1.0),
            tmp_path / "timeout",
            cancelled=lambda: False,
            heartbeat=lambda: True,
        )
    assert len(terminated) == 2


def test_probe_requires_fixed_complete_runtime_slots(tmp_path: Path) -> None:
    _, resolver = _runtime(tmp_path)
    runner = TripoSRWorkerRunner(resolver)
    assert runner.probe(TripoSRRuntimeConfig())
    assert not runner.probe(TripoSRRuntimeConfig(runner_capability_id="arbitrary"))
    assert not TripoSRWorkerRunner(lambda _capability: None).probe(TripoSRRuntimeConfig())


def test_isolated_worker_manifest_pins_python_upstream_and_offline_output_contract() -> None:
    worker_root = Path(__file__).parents[2] / "workers" / "triposr"
    manifest = json.loads((worker_root / "worker-manifest.json").read_text(encoding="utf-8"))
    assert manifest["python"] == ">=3.10,<3.12"
    assert manifest["upstream"]["commit"] == "107cefdc244c39106fa830359024f6a2f1c78871"
    assert manifest["runtime_network"] == "disabled"
    assert manifest["output"] == {
        "format": "glb",
        "relative_path": "output/0/mesh.glb",
    }
    requirements = (worker_root / "requirements.lock").read_text(encoding="utf-8")
    assert "3381600ddc3d2e4d74222f8495866be5fafbace4" in requirements


def test_worker_maps_non_oom_failure_to_redacted_error(tmp_path: Path) -> None:
    _, resolver = _runtime(tmp_path)
    runner = TripoSRWorkerRunner(
        resolver,
        process_factory=lambda *_args, **_kwargs: _Process(returncode=5),
    )
    with pytest.raises(TripoSRWorkerError, match="worker failed") as captured:
        runner.generate(
            "job:failed",
            TripoSRRuntimeConfig(),
            _spec(),
            tmp_path / "failed",
            cancelled=lambda: False,
            heartbeat=lambda: True,
        )
    assert "path" not in str(captured.value).lower()
