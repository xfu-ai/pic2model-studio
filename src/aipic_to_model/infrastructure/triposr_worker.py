"""Controlled isolated TripoSR worker process execution."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from PIL import Image

from ..application.model_inspection import MAX_GLB_BYTES, validate_glb_bytes
from ..domain.local_inference import LocalEngineKind
from .local_inference import LocalInferenceCancelled, LocalInferenceGate

TRIPOSR_WORKER_CAPABILITY = "local-runtime/triposr-worker"
TRIPOSR_RUNNER_CAPABILITY = "local-runtime/triposr-runner"
TRIPOSR_MODEL_CAPABILITY = "local-model/triposr"

_CAPABILITY_ENVIRONMENT = {
    TRIPOSR_WORKER_CAPABILITY: "AIPIC_TRIPOSR_PYTHON",
    TRIPOSR_RUNNER_CAPABILITY: "AIPIC_TRIPOSR_RUNNER",
    TRIPOSR_MODEL_CAPABILITY: "AIPIC_TRIPOSR_MODEL",
}
_SAFE_ENVIRONMENT_KEYS = {
    "CUDA_PATH",
    "CUDA_VISIBLE_DEVICES",
    "HIP_PATH",
    "PATH",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "WINDIR",
}
_MAX_DIAGNOSTIC_BYTES = 1024 * 1024
_AUXILIARY_CACHE_DIRECTORY = "huggingface-cache"
_DINO_CONFIG_REVISION = "f205d5d8e640a89a2b8ef0369670dfc37cc07fc2"
_DINO_CONFIG_RELATIVE_PATH = (
    Path(_AUXILIARY_CACHE_DIRECTORY)
    / "models--facebook--dino-vitb16"
    / "snapshots"
    / _DINO_CONFIG_REVISION
    / "config.json"
)


class TripoSRWorkerError(RuntimeError):
    pass


class TripoSRWorkerCancelled(TripoSRWorkerError):
    pass


class TripoSRWorkerTimedOut(TripoSRWorkerError):
    pass


class TripoSRWorkerOutOfMemory(TripoSRWorkerError):
    pass


class TripoSROutputInvalid(TripoSRWorkerError):
    pass


@dataclass(frozen=True)
class TripoSRRuntimeConfig:
    python_capability_id: str = TRIPOSR_WORKER_CAPABILITY
    runner_capability_id: str = TRIPOSR_RUNNER_CAPABILITY
    model_capability_id: str = TRIPOSR_MODEL_CAPABILITY


@dataclass(frozen=True)
class TripoSRGenerationSpec:
    image_bytes: bytes
    mime_type: str
    chunk_size: int = 8192
    marching_cubes_resolution: int = 256
    foreground_ratio: float = 0.85
    timeout_seconds: float = 900.0


@dataclass(frozen=True)
class TripoSRGenerationOutput:
    glb: bytes
    chunk_size: int
    marching_cubes_resolution: int
    foreground_ratio: float


def resolve_environment_triposr_capability(capability_id: str) -> Path | None:
    """Resolve fixed Host-owned TripoSR slots without exposing native paths."""

    variable = _CAPABILITY_ENVIRONMENT.get(capability_id)
    raw = os.environ.get(variable, "") if variable is not None else ""
    if not raw:
        return None
    path = Path(raw).resolve()
    if capability_id == TRIPOSR_MODEL_CAPABILITY:
        return path if path.is_dir() else None
    return path if path.is_file() else None


class TripoSRWorkerRunner:
    def __init__(
        self,
        resolve_capability: Callable[[str], Path | None],
        *,
        gate: LocalInferenceGate | None = None,
        process_factory: Callable[..., Any] = subprocess.Popen,
        terminate_tree: Callable[[Any], None] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._resolve = resolve_capability
        self._gate = gate or LocalInferenceGate()
        self._process_factory = process_factory
        self._terminate_tree = terminate_tree or _terminate_process_tree
        self._monotonic = monotonic
        self._sleep = sleep

    def probe(self, config: TripoSRRuntimeConfig) -> bool:
        try:
            self._resolve_runtime(config)
        except TripoSRWorkerError:
            return False
        return True

    def generate(
        self,
        owner: str,
        config: TripoSRRuntimeConfig,
        spec: TripoSRGenerationSpec,
        temporary_root: Path,
        *,
        cancelled: Callable[[], bool],
        heartbeat: Callable[[], bool],
    ) -> TripoSRGenerationOutput:
        self._validate_spec(spec)
        python, runner, model = self._resolve_runtime(config)
        temporary_root.mkdir(parents=True, exist_ok=True)
        with (
            _generation_lease(
                self._gate,
                owner,
                cancelled=cancelled,
                timeout_seconds=spec.timeout_seconds,
            ),
            TemporaryDirectory(prefix="triposr-", dir=temporary_root) as directory,
        ):
            workspace = Path(directory).resolve()
            input_path = workspace / "input.png"
            output_directory = workspace / "output"
            expected_output = output_directory / "0" / "mesh.glb"
            diagnostic_path = workspace / "worker.stderr"
            (output_directory / "0").mkdir(parents=True)
            self._write_input(input_path, spec)
            command = self._command(python, runner, model, input_path, output_directory, spec)
            with diagnostic_path.open("wb") as diagnostic:
                process = self._process_factory(
                    command,
                    cwd=runner.parent,
                    env=_safe_subprocess_environment(model),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=diagnostic,
                    shell=False,
                    creationflags=(
                        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
                    ),
                    start_new_session=os.name != "nt",
                )
                started = self._monotonic()
                try:
                    while process.poll() is None:
                        if cancelled():
                            self._terminate_tree(process)
                            raise TripoSRWorkerCancelled("Local 3D generation was cancelled")
                        if self._monotonic() - started >= spec.timeout_seconds:
                            self._terminate_tree(process)
                            raise TripoSRWorkerTimedOut("Local 3D generation timed out")
                        if not heartbeat():
                            self._terminate_tree(process)
                            raise TripoSRWorkerError("Local 3D generation lease was lost")
                        if (
                            diagnostic_path.is_file()
                            and diagnostic_path.stat().st_size > _MAX_DIAGNOSTIC_BYTES
                        ):
                            self._terminate_tree(process)
                            raise TripoSRWorkerError(
                                "TripoSR worker diagnostics exceeded the limit"
                            )
                        self._sleep(0.1)
                finally:
                    if process.poll() is None:
                        self._terminate_tree(process)
            if process.returncode != 0:
                if _diagnostic_is_out_of_memory(diagnostic_path):
                    raise TripoSRWorkerOutOfMemory("TripoSR requires more local GPU memory")
                raise TripoSRWorkerError("TripoSR worker failed")
            glb = self._read_output(workspace, expected_output)
            return TripoSRGenerationOutput(
                glb=glb,
                chunk_size=spec.chunk_size,
                marching_cubes_resolution=spec.marching_cubes_resolution,
                foreground_ratio=spec.foreground_ratio,
            )

    @staticmethod
    def _command(
        python: Path,
        runner: Path,
        model: Path,
        input_path: Path,
        output_directory: Path,
        spec: TripoSRGenerationSpec,
    ) -> list[str]:
        return [
            str(python),
            "-E",
            "-s",
            str(runner),
            str(input_path),
            "--device",
            "cuda:0",
            "--pretrained-model-name-or-path",
            str(model),
            "--chunk-size",
            str(spec.chunk_size),
            "--mc-resolution",
            str(spec.marching_cubes_resolution),
            "--no-remove-bg",
            "--foreground-ratio",
            str(spec.foreground_ratio),
            "--output-dir",
            str(output_directory),
            "--model-save-format",
            "glb",
        ]

    def _resolve_runtime(self, config: TripoSRRuntimeConfig) -> tuple[Path, Path, Path]:
        if config != TripoSRRuntimeConfig():
            raise TripoSRWorkerError("TripoSR capability slots are invalid")
        python = self._resolve(config.python_capability_id)
        runner = self._resolve(config.runner_capability_id)
        model = self._resolve(config.model_capability_id)
        if python is None or runner is None or model is None:
            raise TripoSRWorkerError("TripoSR worker is not configured")
        if python.name.lower() not in {"python", "python.exe", "python3", "python3.exe"}:
            raise TripoSRWorkerError("Configured TripoSR worker Python is unsupported")
        if runner.name != "run.py" or not runner.is_file():
            raise TripoSRWorkerError("Configured TripoSR runner is unsupported")
        if not model.is_dir() or not (model / "config.yaml").is_file():
            raise TripoSRWorkerError("Configured TripoSR model is incomplete")
        if not (model / "model.ckpt").is_file():
            raise TripoSRWorkerError("Configured TripoSR weights are incomplete")
        if not (model / _DINO_CONFIG_RELATIVE_PATH).is_file():
            raise TripoSRWorkerError("Configured TripoSR auxiliary model is incomplete")
        return python, runner, model

    @staticmethod
    def _validate_spec(spec: TripoSRGenerationSpec) -> None:
        if (
            spec.mime_type not in {"image/png", "image/jpeg", "image/webp"}
            or not 0 < len(spec.image_bytes) <= 24 * 1024 * 1024
            or spec.chunk_size not in {2048, 4096, 8192, 16384}
            or spec.marching_cubes_resolution not in {128, 192, 256, 320, 384, 448, 512}
            or not 0.5 <= spec.foreground_ratio <= 1.0
            or not 1.0 <= spec.timeout_seconds <= 3600.0
        ):
            raise ValueError("Invalid TripoSR generation parameters")

    @staticmethod
    def _write_input(path: Path, spec: TripoSRGenerationSpec) -> None:
        try:
            with Image.open(BytesIO(spec.image_bytes)) as image:
                image.verify()
            with Image.open(BytesIO(spec.image_bytes)) as image:
                if (
                    image.format not in {"PNG", "JPEG", "WEBP"}
                    or image.width > 8192
                    or image.height > 8192
                    or image.width * image.height > 40_000_000
                ):
                    raise ValueError("Unsupported TripoSR source image")
                if image.mode == "RGBA":
                    background = Image.new("RGBA", image.size, (127, 127, 127, 255))
                    normalized = Image.alpha_composite(background, image).convert("RGB")
                else:
                    normalized = image.convert("RGB")
                normalized.save(path, format="PNG")
        except OSError as error:
            raise ValueError("Invalid TripoSR source image") from error

    @staticmethod
    def _read_output(workspace: Path, path: Path) -> bytes:
        resolved = path.resolve()
        if not resolved.is_relative_to(workspace) or not path.is_file() or path.is_symlink():
            raise TripoSROutputInvalid("TripoSR GLB output is missing")
        content = path.read_bytes()
        try:
            validate_glb_bytes(content, maximum_bytes=MAX_GLB_BYTES)
        except ValueError as error:
            raise TripoSROutputInvalid("TripoSR GLB output is invalid") from error
        return content


def _safe_subprocess_environment(model: Path) -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if key.upper() in _SAFE_ENVIRONMENT_KEYS
    }
    environment.update(
        {
            "HF_HUB_OFFLINE": "1",
            "HUGGINGFACE_HUB_CACHE": str((model / _AUXILIARY_CACHE_DIRECTORY).resolve()),
            "TRANSFORMERS_OFFLINE": "1",
            "PYTHONNOUSERSITE": "1",
            "NO_PROXY": "*",
        }
    )
    return environment


def _diagnostic_is_out_of_memory(path: Path) -> bool:
    try:
        content = path.read_bytes()[:_MAX_DIAGNOSTIC_BYTES].lower()
    except OSError:
        return False
    return any(
        marker in content
        for marker in (
            b"cuda out of memory",
            b"torch.cuda.outofmemoryerror",
            b"cublas_status_alloc_failed",
        )
    )


@contextmanager
def _generation_lease(
    gate: LocalInferenceGate,
    owner: str,
    *,
    cancelled: Callable[[], bool],
    timeout_seconds: float,
) -> Iterator[None]:
    try:
        with gate.lease(
            owner,
            LocalEngineKind.TRIPOSR,
            cancelled=cancelled,
            timeout_seconds=timeout_seconds,
        ):
            yield
    except LocalInferenceCancelled as error:
        raise TripoSRWorkerCancelled("Local 3D generation was cancelled") from error
    except TimeoutError as error:
        raise TripoSRWorkerTimedOut("Local 3D generation timed out") from error


def _terminate_process_tree(process: Any) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
        process.wait(timeout=5)
