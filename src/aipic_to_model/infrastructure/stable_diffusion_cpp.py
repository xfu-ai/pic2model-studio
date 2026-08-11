"""Controlled stable-diffusion.cpp execution for Z-Image-Turbo."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from PIL import Image

from ..domain.local_inference import LocalEngineKind
from .local_inference import LocalInferenceCancelled, LocalInferenceGate
from .local_model_resources import resolve_local_model_resource

STABLE_DIFFUSION_CPP_CAPABILITY = "local-runtime/stable-diffusion-cpp"
Z_IMAGE_DIFFUSION_CAPABILITY = "local-model/z-image-turbo/diffusion"
Z_IMAGE_VAE_CAPABILITY = "local-model/z-image-turbo/vae"
Z_IMAGE_LLM_CAPABILITY = "local-model/z-image-turbo/llm"

_CAPABILITY_ENVIRONMENT = {
    STABLE_DIFFUSION_CPP_CAPABILITY: "AIPIC_ZIMAGE_SD_CLI",
    Z_IMAGE_DIFFUSION_CAPABILITY: "AIPIC_ZIMAGE_DIFFUSION_MODEL",
    Z_IMAGE_VAE_CAPABILITY: "AIPIC_ZIMAGE_VAE",
    Z_IMAGE_LLM_CAPABILITY: "AIPIC_ZIMAGE_LLM",
}
_CAPABILITY_RESOURCES = {
    STABLE_DIFFUSION_CPP_CAPABILITY: Path("z-image-turbo/runtime/sd-cli.exe"),
    Z_IMAGE_DIFFUSION_CAPABILITY: Path("z-image-turbo/models/z_image_turbo-Q3_K.gguf"),
    Z_IMAGE_VAE_CAPABILITY: Path("z-image-turbo/models/ae.safetensors"),
    Z_IMAGE_LLM_CAPABILITY: Path("z-image-turbo/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf"),
}
_SAFE_ENVIRONMENT_KEYS = {
    "CUDA_PATH",
    "CUDA_VISIBLE_DEVICES",
    "GGML_CUDA_ENABLE_UNIFIED_MEMORY",
    "HIP_PATH",
    "PATH",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "WINDIR",
}


class ZImageExecutionError(RuntimeError):
    pass


class ZImageCancelled(ZImageExecutionError):
    pass


class ZImageTimedOut(ZImageExecutionError):
    pass


class ZImageOutputInvalid(ZImageExecutionError):
    pass


@dataclass(frozen=True)
class ZImageRuntimeConfig:
    executable_capability_id: str = STABLE_DIFFUSION_CPP_CAPABILITY
    diffusion_capability_id: str = Z_IMAGE_DIFFUSION_CAPABILITY
    vae_capability_id: str = Z_IMAGE_VAE_CAPABILITY
    llm_capability_id: str = Z_IMAGE_LLM_CAPABILITY


@dataclass(frozen=True)
class ZImageGenerationSpec:
    prompt: str
    width: int
    height: int
    candidate_count: int
    seed: int
    steps: int = 8
    cfg_scale: float = 1.0
    timeout_seconds: float = 900.0


@dataclass(frozen=True)
class ZImageGenerationOutput:
    images: tuple[bytes, ...]
    seed: int
    steps: int
    width: int
    height: int


def resolve_environment_local_capability(capability_id: str) -> Path | None:
    """Resolve an override or bundled Host-owned Z-Image capability slot."""

    variable = _CAPABILITY_ENVIRONMENT.get(capability_id)
    raw = os.environ.get(variable, "") if variable is not None else ""
    if raw:
        path = Path(raw).resolve()
        return path if path.is_file() else None
    relative = _CAPABILITY_RESOURCES.get(capability_id)
    return resolve_local_model_resource(relative) if relative is not None else None


class StableDiffusionCppRunner:
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

    def probe(self, config: ZImageRuntimeConfig) -> bool:
        try:
            self._resolve_runtime(config)
        except ZImageExecutionError:
            return False
        return True

    def generate(
        self,
        owner: str,
        config: ZImageRuntimeConfig,
        spec: ZImageGenerationSpec,
        temporary_root: Path,
        *,
        cancelled: Callable[[], bool],
        heartbeat: Callable[[], bool],
    ) -> ZImageGenerationOutput:
        self._validate_spec(spec)
        executable, diffusion, vae, llm = self._resolve_runtime(config)
        temporary_root.mkdir(parents=True, exist_ok=True)
        with (
            _generation_lease(
                self._gate,
                owner,
                LocalEngineKind.STABLE_DIFFUSION_CPP,
                cancelled=cancelled,
                timeout_seconds=spec.timeout_seconds,
            ),
            TemporaryDirectory(prefix="z-image-", dir=temporary_root) as directory,
        ):
            output_directory = Path(directory).resolve()
            output_pattern = output_directory / "candidate_%02d.png"
            command = self._command(
                executable,
                diffusion,
                vae,
                llm,
                output_pattern,
                spec,
            )
            process = self._process_factory(
                command,
                cwd=output_directory,
                env=_safe_subprocess_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
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
                        raise ZImageCancelled("Local image generation was cancelled")
                    if self._monotonic() - started >= spec.timeout_seconds:
                        self._terminate_tree(process)
                        raise ZImageTimedOut("Local image generation timed out")
                    if not heartbeat():
                        self._terminate_tree(process)
                        raise ZImageExecutionError("Local image generation lease was lost")
                    self._sleep(0.1)
                if process.returncode != 0:
                    raise ZImageExecutionError("stable-diffusion.cpp generation failed")
                images = self._read_outputs(output_directory, spec)
            finally:
                if process.poll() is None:
                    self._terminate_tree(process)
            return ZImageGenerationOutput(
                tuple(images),
                spec.seed,
                spec.steps,
                spec.width,
                spec.height,
            )

    @staticmethod
    def _command(
        executable: Path,
        diffusion: Path,
        vae: Path,
        llm: Path,
        output_pattern: Path,
        spec: ZImageGenerationSpec,
    ) -> list[str]:
        return [
            str(executable),
            "--diffusion-model",
            str(diffusion),
            "--vae",
            str(vae),
            "--llm",
            str(llm),
            "--prompt",
            spec.prompt,
            "--cfg-scale",
            str(spec.cfg_scale),
            "--offload-to-cpu",
            "--diffusion-fa",
            "--height",
            str(spec.height),
            "--width",
            str(spec.width),
            "--steps",
            str(spec.steps),
            "--seed",
            str(spec.seed),
            "--batch-count",
            str(spec.candidate_count),
            "--output",
            str(output_pattern),
            "--output-begin-idx",
            "1",
        ]

    def _resolve_runtime(self, config: ZImageRuntimeConfig) -> tuple[Path, Path, Path, Path]:
        if config != ZImageRuntimeConfig():
            raise ZImageExecutionError("Z-Image-Turbo capability slots are invalid")
        values = tuple(
            self._resolve(capability_id)
            for capability_id in (
                config.executable_capability_id,
                config.diffusion_capability_id,
                config.vae_capability_id,
                config.llm_capability_id,
            )
        )
        if any(value is None or not value.is_file() for value in values):
            raise ZImageExecutionError("Z-Image-Turbo runtime is not configured")
        executable, diffusion, vae, llm = values
        assert (
            executable is not None and diffusion is not None and vae is not None and llm is not None
        )
        if executable.name.lower() not in {"sd-cli", "sd-cli.exe"}:
            raise ZImageExecutionError("Configured runtime is not stable-diffusion.cpp CLI")
        if diffusion.suffix.lower() not in {".gguf", ".safetensors"}:
            raise ZImageExecutionError("Configured Z-Image diffusion model is unsupported")
        if vae.suffix.lower() not in {".sft", ".safetensors", ".gguf"}:
            raise ZImageExecutionError("Configured Z-Image VAE is unsupported")
        if llm.suffix.lower() not in {".gguf", ".safetensors"}:
            raise ZImageExecutionError("Configured Z-Image text encoder is unsupported")
        return executable, diffusion, vae, llm

    @staticmethod
    def _validate_spec(spec: ZImageGenerationSpec) -> None:
        if (
            not spec.prompt.strip()
            or len(spec.prompt) > 8_000
            or not 512 <= spec.width <= 1536
            or not 512 <= spec.height <= 1536
            or spec.width % 64
            or spec.height % 64
            or spec.width * spec.height > 1_572_864
            or spec.candidate_count not in {1, 2, 4}
            or not 0 <= spec.seed <= 2_147_483_647
            or not 1 <= spec.steps <= 20
            or not 0.0 <= spec.cfg_scale <= 5.0
            or not 1.0 <= spec.timeout_seconds <= 3_600.0
        ):
            raise ValueError("Invalid Z-Image-Turbo generation parameters")

    @staticmethod
    def _read_outputs(directory: Path, spec: ZImageGenerationSpec) -> list[bytes]:
        images: list[bytes] = []
        for index in range(1, spec.candidate_count + 1):
            path = (directory / f"candidate_{index:02d}.png").resolve()
            if not path.is_relative_to(directory) or not path.is_file() or path.is_symlink():
                raise ZImageOutputInvalid("stable-diffusion.cpp output is missing")
            content = path.read_bytes()
            if not 0 < len(content) <= 25 * 1024 * 1024:
                raise ZImageOutputInvalid("stable-diffusion.cpp output size is invalid")
            try:
                with Image.open(path) as image:
                    if image.format != "PNG" or image.size != (spec.width, spec.height):
                        raise ZImageOutputInvalid(
                            "stable-diffusion.cpp output dimensions are invalid"
                        )
                    image.verify()
            except OSError as error:
                raise ZImageOutputInvalid("stable-diffusion.cpp output is corrupt") from error
            images.append(content)
        return images


def _safe_subprocess_environment() -> dict[str, str]:
    return {
        key: value for key, value in os.environ.items() if key.upper() in _SAFE_ENVIRONMENT_KEYS
    }


@contextmanager
def _generation_lease(
    gate: LocalInferenceGate,
    owner: str,
    engine: LocalEngineKind,
    *,
    cancelled: Callable[[], bool],
    timeout_seconds: float,
) -> Iterator[None]:
    try:
        with gate.lease(
            owner,
            engine,
            cancelled=cancelled,
            timeout_seconds=timeout_seconds,
        ):
            yield
    except LocalInferenceCancelled as error:
        raise ZImageCancelled("Local image generation was cancelled") from error
    except TimeoutError as error:
        raise ZImageTimedOut("Local image generation timed out") from error


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
