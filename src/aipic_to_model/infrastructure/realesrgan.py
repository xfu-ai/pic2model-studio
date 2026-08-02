"""Offline Real-ESRGAN inference over the bundled fixed-shape ONNX model."""

from __future__ import annotations

import hashlib
import math
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast

import numpy as np
from PIL import Image

MODEL_SHA256 = "10a7a075719220ee6627124473ce57c74b7ef336b57bed4508d9353eaa8f17ef"
MODEL_RELATIVE_PATH = Path("resources/image_processing/models/realesrgan-x4.onnx")
MODEL_TILE_SIZE = 64
MODEL_SCALE = 4
CORE_SIZE = 48
CONTEXT_SIZE = (MODEL_TILE_SIZE - CORE_SIZE) // 2
MAX_INPUT_PIXELS = 4_194_304
MAX_OUTPUT_PIXELS = 40_000_000

ProgressCallback = Callable[[int, int], None]


class _NodeArgument(Protocol):
    name: str


class _InferenceSession(Protocol):
    def get_inputs(self) -> list[_NodeArgument]: ...

    def get_outputs(self) -> list[_NodeArgument]: ...

    def run(
        self,
        output_names: list[str],
        input_feed: dict[str, np.ndarray],
    ) -> list[np.ndarray]: ...


def bundled_model_path() -> Path:
    return Path(__file__).resolve().parents[1] / MODEL_RELATIVE_PATH


def verify_bundled_model(path: Path | None = None) -> Path:
    model = path or bundled_model_path()
    if not model.is_file():
        raise RuntimeError("Bundled Real-ESRGAN model is missing.")
    digest = hashlib.sha256()
    with model.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != MODEL_SHA256:
        raise RuntimeError("Bundled Real-ESRGAN model failed integrity verification.")
    return model


class RealEsrganUpscaler:
    """CPU upscaler that uses overlapped context and pastes only tile cores."""

    _session: _InferenceSession | None = None
    _session_lock = threading.Lock()

    @classmethod
    def _get_session(cls) -> _InferenceSession:
        with cls._session_lock:
            if cls._session is None:
                import onnxruntime as ort  # pyright: ignore[reportMissingImports]

                options = ort.SessionOptions()
                options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                options.intra_op_num_threads = 1
                options.inter_op_num_threads = 1
                cls._session = cast(
                    _InferenceSession,
                    ort.InferenceSession(
                        str(verify_bundled_model()),
                        sess_options=options,
                        providers=["CPUExecutionProvider"],
                    ),
                )
            return cls._session

    def upscale(
        self,
        image: Image.Image,
        *,
        scale: int,
        on_progress: ProgressCallback | None = None,
    ) -> Image.Image:
        if scale not in {2, 4}:
            raise ValueError("Local upscale scale must be 2 or 4.")
        source = image.convert("RGBA")
        width, height = source.size
        if width < 1 or height < 1 or width * height > MAX_INPUT_PIXELS:
            raise ValueError("Image dimensions exceed the local upscale limit.")
        if width * height * scale * scale > MAX_OUTPUT_PIXELS:
            raise ValueError("Upscaled image would exceed the output pixel limit.")

        rgb = np.asarray(source.convert("RGB"), dtype=np.uint8)
        grid_width = math.ceil(width / CORE_SIZE) * CORE_SIZE
        grid_height = math.ceil(height / CORE_SIZE) * CORE_SIZE
        pad_mode = "reflect" if width > 1 and height > 1 else "edge"
        padded = np.pad(
            rgb,
            (
                (CONTEXT_SIZE, grid_height - height + CONTEXT_SIZE),
                (CONTEXT_SIZE, grid_width - width + CONTEXT_SIZE),
                (0, 0),
            ),
            mode=pad_mode,
        )

        session = self._get_session()
        input_name = session.get_inputs()[0].name
        output_name = session.get_outputs()[0].name
        output = Image.new("RGB", (width * scale, height * scale))
        tiles_x = math.ceil(width / CORE_SIZE)
        tiles_y = math.ceil(height / CORE_SIZE)
        total = tiles_x * tiles_y
        completed = 0

        for y in range(0, grid_height, CORE_SIZE):
            for x in range(0, grid_width, CORE_SIZE):
                tile = padded[y : y + MODEL_TILE_SIZE, x : x + MODEL_TILE_SIZE]
                tensor = tile.astype(np.float32).transpose(2, 0, 1)[np.newaxis, ...] / 255.0
                result = session.run([output_name], {input_name: tensor})[0][0]
                result = np.clip(result.transpose(1, 2, 0), 0.0, 1.0)
                result_image = Image.fromarray(
                    np.rint(result * 255.0).astype(np.uint8),
                    "RGB",
                )

                core_width = min(CORE_SIZE, width - x)
                core_height = min(CORE_SIZE, height - y)
                left = CONTEXT_SIZE * MODEL_SCALE
                top = CONTEXT_SIZE * MODEL_SCALE
                core = result_image.crop(
                    (
                        left,
                        top,
                        left + core_width * MODEL_SCALE,
                        top + core_height * MODEL_SCALE,
                    )
                )
                if scale != MODEL_SCALE:
                    core = core.resize(
                        (core_width * scale, core_height * scale),
                        Image.Resampling.LANCZOS,
                    )
                output.paste(core, (x * scale, y * scale))
                completed += 1
                if on_progress is not None:
                    on_progress(completed, total)

        alpha = source.getchannel("A").resize(output.size, Image.Resampling.LANCZOS)
        rgba = output.convert("RGBA")
        rgba.putalpha(alpha)
        return rgba
