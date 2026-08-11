"""Deterministic offline image Tools that produce managed derived assets."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError

from ..domain.common import new_id
from ..infrastructure.realesrgan import RealEsrganUpscaler
from .assets import AssetService

MAX_INPUT_PIXELS = 40_000_000
MAX_OUTPUT_PIXELS = 40_000_000
MAX_SPLIT_OUTPUTS = 256


@dataclass(frozen=True)
class LocalImageArtifact:
    content: bytes
    suffix: str
    width: int
    height: int
    metadata: dict[str, object]


def _image_verification(image: Image.Image, *, operation: str) -> dict[str, object]:
    """Return small, deterministic evidence for an image artifact.

    This deliberately reports facts and advisory warnings only.  The caller may
    give the report to an Agent for the next decision, but a quality warning
    never turns a successful local operation into a failed one.
    """

    rgba = image.convert("RGBA")
    alpha = np.asarray(rgba.getchannel("A"), dtype=np.uint8)
    total = int(alpha.size)
    transparent = int(np.count_nonzero(alpha == 0))
    non_opaque = int(np.count_nonzero(alpha < 255))
    opaque = int(np.count_nonzero(alpha == 255))
    facts: dict[str, object] = {
        "width": rgba.width,
        "height": rgba.height,
        "total_pixels": total,
        "transparent_pixel_ratio": round(transparent / total, 6),
        "non_opaque_pixel_ratio": round(non_opaque / total, 6),
        "opaque_pixel_ratio": round(opaque / total, 6),
    }
    checks: list[dict[str, object]] = [
        {
            "code": "image.dimensions",
            "outcome": "pass",
            "observed": {"width": rgba.width, "height": rgba.height},
        }
    ]
    disposition = "verified"
    if operation == "remove_background_local":
        border = np.concatenate(
            (alpha[0, :], alpha[-1, :], alpha[1:-1, 0], alpha[1:-1, -1])
        )
        border_opaque_ratio = float(np.count_nonzero(border >= 250)) / int(border.size)
        corner_alpha = np.asarray(
            (alpha[0, 0], alpha[0, -1], alpha[-1, 0], alpha[-1, -1]),
            dtype=np.uint8,
        )
        opaque_corner_count = int(np.count_nonzero(corner_alpha >= 250))
        facts["border_opaque_ratio"] = round(border_opaque_ratio, 6)
        facts["opaque_corner_count"] = opaque_corner_count
        if non_opaque / total < 0.01:
            checks.append(
                {
                    "code": "image.background_removal_alpha",
                    "outcome": "warn",
                    "expected": {"non_opaque_pixel_ratio_min": 0.01},
                    "observed": {"non_opaque_pixel_ratio": facts["non_opaque_pixel_ratio"]},
                    "message": "Very little transparency was created; inspect the background result.",
                }
            )
            disposition = "review_required"
        if opaque_corner_count:
            checks.append(
                {
                    "code": "image.background_removal_corners",
                    "outcome": "warn",
                    "expected": {"opaque_corner_count_max": 0},
                    "observed": {"opaque_corner_count": opaque_corner_count},
                    "message": "Opaque pixels remain at one or more image corners; inspect for background residue.",
                }
            )
            disposition = "review_required"
        if border_opaque_ratio > 0.05:
            checks.append(
                {
                    "code": "image.background_removal_border",
                    "outcome": "warn",
                    "expected": {"border_opaque_ratio_max": 0.05},
                    "observed": {"border_opaque_ratio": facts["border_opaque_ratio"]},
                    "message": "A substantial opaque region remains on the image border; inspect the background result.",
                }
            )
            disposition = "review_required"
        if opaque / total < 0.02:
            checks.append(
                {
                    "code": "image.background_removal_foreground",
                    "outcome": "warn",
                    "expected": {"opaque_pixel_ratio_min": 0.02},
                    "observed": {"opaque_pixel_ratio": facts["opaque_pixel_ratio"]},
                    "message": "Very little opaque foreground remains; the removal may be too aggressive.",
                }
            )
            disposition = "review_required"
    return {
        "schema_version": 1,
        "kind": "image_artifact",
        "operation": operation,
        "disposition": disposition,
        "facts": facts,
        "checks": checks,
    }


def _open_image(content: bytes) -> Image.Image:
    if not content:
        raise ValueError("Image content is empty.")
    try:
        with Image.open(BytesIO(content)) as source:
            source.load()
            image = ImageOps.exif_transpose(source).copy()
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise ValueError("Image content could not be decoded.") from error
    if image.width < 1 or image.height < 1 or image.width * image.height > MAX_INPUT_PIXELS:
        raise ValueError("Image dimensions exceed the local processing limit.")
    return image


def _png_artifact(image: Image.Image, **metadata: object) -> LocalImageArtifact:
    if image.width * image.height > MAX_OUTPUT_PIXELS:
        raise ValueError("Processed image exceeds the output pixel limit.")
    output = BytesIO()
    image.save(output, "PNG", optimize=True)
    operation = str(metadata.get("operation", "image_process"))
    return LocalImageArtifact(
        output.getvalue(),
        ".png",
        image.width,
        image.height,
        {"format": "png", "verification": _image_verification(image, operation=operation), **metadata},
    )


def trim_transparent(
    content: bytes,
    *,
    padding: int = 0,
    alpha_threshold: int = 0,
) -> LocalImageArtifact:
    image = _open_image(content).convert("RGBA")
    alpha = np.asarray(image.getchannel("A"), dtype=np.uint8)
    foreground = alpha > alpha_threshold
    if not foreground.any():
        raise ValueError("Image is fully transparent at the requested threshold.")
    ys, xs = np.nonzero(foreground)
    left = max(0, int(xs.min()) - padding)
    top = max(0, int(ys.min()) - padding)
    right = min(image.width, int(xs.max()) + 1 + padding)
    bottom = min(image.height, int(ys.max()) + 1 + padding)
    return _png_artifact(
        image.crop((left, top, right, bottom)),
        operation="trim_transparent",
        padding=padding,
        alpha_threshold=alpha_threshold,
        source_bounds={"x": left, "y": top, "width": right - left, "height": bottom - top},
    )


def _target_dimensions(
    width: int,
    height: int,
    *,
    target_width: int | None,
    target_height: int | None,
    max_long_edge: int | None,
    lock_aspect_ratio: bool,
) -> tuple[int, int]:
    result_width, result_height = width, height
    if target_width is not None or target_height is not None:
        if lock_aspect_ratio:
            if target_width is not None and target_height is not None:
                ratio = min(target_width / width, target_height / height)
            elif target_width is not None:
                ratio = target_width / width
            else:
                ratio = target_height / height  # type: ignore[operator]
            result_width = max(1, round(width * ratio))
            result_height = max(1, round(height * ratio))
        else:
            result_width = target_width or width
            result_height = target_height or height
    if max_long_edge is not None and max(result_width, result_height) > max_long_edge:
        ratio = max_long_edge / max(result_width, result_height)
        result_width = max(1, round(result_width * ratio))
        result_height = max(1, round(result_height * ratio))
    if result_width * result_height > MAX_OUTPUT_PIXELS:
        raise ValueError("Normalized image would exceed the output pixel limit.")
    return result_width, result_height


def normalize_image(
    content: bytes,
    *,
    target_width: int | None = None,
    target_height: int | None = None,
    max_long_edge: int | None = None,
    lock_aspect_ratio: bool = True,
    rotate_degrees: int = 0,
    flip: str = "none",
    output_format: str = "png",
    quality: int = 90,
    preserve_alpha: bool = True,
) -> LocalImageArtifact:
    image = _open_image(content)
    if rotate_degrees:
        image = image.rotate(-rotate_degrees, expand=True, resample=Image.Resampling.BICUBIC)
    if flip == "horizontal":
        image = ImageOps.mirror(image)
    elif flip == "vertical":
        image = ImageOps.flip(image)

    size = _target_dimensions(
        image.width,
        image.height,
        target_width=target_width,
        target_height=target_height,
        max_long_edge=max_long_edge,
        lock_aspect_ratio=lock_aspect_ratio,
    )
    if image.size != size:
        image = image.resize(size, Image.Resampling.LANCZOS)

    output = BytesIO()
    if output_format == "jpeg":
        rgba = image.convert("RGBA")
        flattened = Image.new("RGB", rgba.size, (255, 255, 255))
        flattened.paste(rgba, mask=rgba.getchannel("A"))
        flattened.save(output, "JPEG", quality=quality, optimize=True, progressive=True)
        suffix = ".jpg"
    elif output_format == "webp":
        encoded = image.convert("RGBA") if preserve_alpha else image.convert("RGB")
        encoded.save(output, "WEBP", quality=quality, method=6)
        suffix = ".webp"
    else:
        encoded = image.convert("RGBA") if preserve_alpha else image.convert("RGB")
        encoded.save(output, "PNG", optimize=True)
        suffix = ".png"
    return LocalImageArtifact(
        output.getvalue(),
        suffix,
        image.width,
        image.height,
        {
            "operation": "normalize",
            "format": output_format,
            "quality": quality,
            "preserve_alpha": preserve_alpha,
            "rotate_degrees": rotate_degrees,
            "flip": flip,
            "verification": _image_verification(image, operation="normalize"),
        },
    )


def _corner_color(pixels: np.ndarray) -> tuple[int, int, int]:
    samples = np.asarray(
        [
            pixels[0, 0, :3],
            pixels[0, -1, :3],
            pixels[-1, 0, :3],
            pixels[-1, -1, :3],
        ],
        dtype=np.uint8,
    )
    median = np.median(samples, axis=0)
    return int(median[0]), int(median[1]), int(median[2])


def _corner_tolerance(
    pixels: np.ndarray, chosen: tuple[int, int, int], minimum: int
) -> int:
    """Estimate keyed-background variation from small corner regions."""

    height, width = pixels.shape[:2]
    extent = max(2, min(height, width) // 32)
    samples = np.concatenate(
        (
            pixels[:extent, :extent, :3].reshape(-1, 3),
            pixels[:extent, -extent:, :3].reshape(-1, 3),
            pixels[-extent:, :extent, :3].reshape(-1, 3),
            pixels[-extent:, -extent:, :3].reshape(-1, 3),
        )
    ).astype(np.float32)
    distance = np.sqrt(
        np.sum((samples - np.asarray(chosen, dtype=np.float32)) ** 2, axis=1)
    )
    estimated = int(np.ceil(np.percentile(distance, 99))) + 8
    return min(255, max(minimum, estimated))


def _contiguous_background(mask: np.ndarray) -> np.ndarray:
    height, width = mask.shape
    selected = np.zeros_like(mask, dtype=bool)
    queue: deque[tuple[int, int]] = deque()

    def add(x: int, y: int) -> None:
        if mask[y, x] and not selected[y, x]:
            selected[y, x] = True
            queue.append((x, y))

    for x in range(width):
        add(x, 0)
        if height > 1:
            add(x, height - 1)
    for y in range(1, height - 1):
        add(0, y)
        if width > 1:
            add(width - 1, y)
    while queue:
        x, y = queue.popleft()
        if x:
            add(x - 1, y)
        if x + 1 < width:
            add(x + 1, y)
        if y:
            add(x, y - 1)
        if y + 1 < height:
            add(x, y + 1)
    return selected


def _channel_values(rgb: np.ndarray, channel: str) -> np.ndarray:
    values = rgb.astype(np.float32)
    if channel == "red":
        return values[..., 0]
    if channel == "green":
        return values[..., 1]
    if channel == "blue":
        return values[..., 2]
    if channel == "luminance":
        return values[..., 0] * 0.2126 + values[..., 1] * 0.7152 + values[..., 2] * 0.0722
    maximum = values.max(axis=2)
    minimum = values.min(axis=2)
    return np.where(maximum > 0, (maximum - minimum) / maximum * 255.0, 0.0)


def _smoothstep(value: np.ndarray) -> np.ndarray:
    clipped = np.clip(value, 0.0, 1.0)
    return clipped * clipped * (3.0 - 2.0 * clipped)


def remove_background_local(
    content: bytes,
    *,
    method: str,
    target_color: tuple[int, int, int] | None = None,
    tolerance: int = 24,
    contiguous_only: bool = True,
    channel: str = "green",
    min_threshold: int = 0,
    max_threshold: int = 120,
    invert: bool = False,
    feather: int = 0,
    edge_shrink: int = 0,
) -> LocalImageArtifact:
    image = _open_image(content).convert("RGBA")
    pixels = np.asarray(image, dtype=np.uint8).copy()
    original_alpha = pixels[..., 3].astype(np.float32)

    if method == "color_key":
        chosen = target_color or _corner_color(pixels)
        effective_tolerance = (
            tolerance
            if target_color is not None
            else _corner_tolerance(pixels, chosen, tolerance)
        )
        delta = pixels[..., :3].astype(np.int16) - np.asarray(chosen, dtype=np.int16)
        distance = np.sqrt(np.sum(delta.astype(np.float32) ** 2, axis=2))
        candidate = distance <= effective_tolerance
        background = _contiguous_background(candidate) if contiguous_only else candidate
        alpha = original_alpha.copy()
        alpha[background] = 0
        if effective_tolerance > 0:
            boundary = (distance > effective_tolerance) & (distance < effective_tolerance + 8)
            softness = (
                _smoothstep((distance - effective_tolerance) / 8.0) * original_alpha
            )
            alpha[boundary] = np.minimum(alpha[boundary], softness[boundary])
        parameters: dict[str, object] = {
            "target_color": list(chosen),
            "tolerance": effective_tolerance,
            "tolerance_auto": target_color is None,
            "contiguous_only": contiguous_only,
        }
    else:
        values = _channel_values(pixels[..., :3], channel)
        soft_edge = max(min((max_threshold - min_threshold) * 0.08, 8.0), 1.0)
        opacity = np.zeros_like(values, dtype=np.float32)
        inside = (values >= min_threshold) & (values <= max_threshold)
        opacity[inside] = 1.0
        below = (values >= min_threshold - soft_edge) & (values < min_threshold)
        opacity[below] = _smoothstep((values[below] - (min_threshold - soft_edge)) / soft_edge)
        above = (values > max_threshold) & (values <= max_threshold + soft_edge)
        opacity[above] = _smoothstep(((max_threshold + soft_edge) - values[above]) / soft_edge)
        if invert:
            opacity = 1.0 - opacity
        alpha = original_alpha * opacity
        parameters = {
            "channel": channel,
            "min_threshold": min_threshold,
            "max_threshold": max_threshold,
            "invert": invert,
        }

    alpha_image = Image.fromarray(np.rint(np.clip(alpha, 0, 255)).astype(np.uint8), "L")
    if edge_shrink:
        alpha_image = alpha_image.filter(ImageFilter.MinFilter(edge_shrink * 2 + 1))
    if feather:
        alpha_image = alpha_image.filter(ImageFilter.GaussianBlur(radius=feather))
    result = Image.fromarray(pixels, "RGBA")
    result.putalpha(alpha_image)
    return _png_artifact(
        result,
        operation="remove_background_local",
        method=method,
        feather=feather,
        edge_shrink=edge_shrink,
        **parameters,
    )


@dataclass
class _Run:
    y: int
    start: int
    end: int
    label: int


def _component_runs(mask: np.ndarray) -> tuple[list[_Run], list[int]]:
    runs: list[_Run] = []
    parents: list[int] = []

    def find(label: int) -> int:
        while parents[label] != label:
            parents[label] = parents[parents[label]]
            label = parents[label]
        return label

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    previous: list[_Run] = []
    for y, row in enumerate(mask):
        padded = np.pad(row.astype(np.int8), (1, 1))
        changes = np.diff(padded)
        starts = np.flatnonzero(changes == 1)
        ends = np.flatnonzero(changes == -1) - 1
        current: list[_Run] = []
        previous_index = 0
        for start, end in zip(starts.tolist(), ends.tolist(), strict=True):
            label = len(parents)
            parents.append(label)
            run = _Run(y, start, end, label)
            while previous_index < len(previous) and previous[previous_index].end < start - 1:
                previous_index += 1
            overlap_index = previous_index
            while overlap_index < len(previous) and previous[overlap_index].start <= end + 1:
                union(label, previous[overlap_index].label)
                overlap_index += 1
            current.append(run)
            runs.append(run)
        previous = current
    roots = [find(run.label) for run in runs]
    return runs, roots


def split_local_image(
    content: bytes,
    *,
    mode: str,
    columns: int | None = None,
    rows: int | None = None,
    alpha_threshold: int = 0,
    min_area: int = 4,
    padding: int = 0,
    max_outputs: int = 64,
) -> list[LocalImageArtifact]:
    image = _open_image(content).convert("RGBA")
    if max_outputs > MAX_SPLIT_OUTPUTS:
        raise ValueError("Requested split output count exceeds the safety limit.")
    if mode == "grid":
        if (
            not columns
            or not rows
            or columns > image.width
            or rows > image.height
            or columns * rows > max_outputs
        ):
            raise ValueError("Grid columns and rows are invalid.")
        artifacts: list[LocalImageArtifact] = []
        for row in range(rows):
            top = round(row * image.height / rows)
            bottom = round((row + 1) * image.height / rows)
            for column in range(columns):
                left = round(column * image.width / columns)
                right = round((column + 1) * image.width / columns)
                artifacts.append(
                    _png_artifact(
                        image.crop((left, top, right, bottom)),
                        operation="split_local",
                        mode="grid",
                        column=column,
                        row=row,
                    )
                )
        return artifacts

    array = np.asarray(image, dtype=np.uint8)
    mask = array[..., 3] > alpha_threshold
    runs, roots = _component_runs(mask)
    if len(runs) > 1_000_000:
        raise ValueError("Image contains too many disconnected alpha runs.")
    grouped: dict[int, list[_Run]] = defaultdict(list)
    for run, root in zip(runs, roots, strict=True):
        grouped[root].append(run)

    components: list[tuple[int, int, int, int, int, list[_Run]]] = []
    for component_runs in grouped.values():
        area = sum(run.end - run.start + 1 for run in component_runs)
        if area < min_area:
            continue
        left = min(run.start for run in component_runs)
        right = max(run.end for run in component_runs) + 1
        top = min(run.y for run in component_runs)
        bottom = max(run.y for run in component_runs) + 1
        components.append((top, left, right, bottom, area, component_runs))
    components.sort(key=lambda item: (item[0], item[1]))
    if not components:
        raise ValueError("No sprite components matched the requested thresholds.")
    if len(components) > max_outputs:
        raise ValueError("Sprite component count exceeds max_outputs.")

    artifacts = []
    for index, (top, left, right, bottom, area, component_runs) in enumerate(components):
        padded_left = max(0, left - padding)
        padded_top = max(0, top - padding)
        padded_right = min(image.width, right + padding)
        padded_bottom = min(image.height, bottom + padding)
        component = array[padded_top:padded_bottom, padded_left:padded_right].copy()
        component[..., 3] = 0
        for run in component_runs:
            component[
                run.y - padded_top,
                run.start - padded_left : run.end - padded_left + 1,
                3,
            ] = array[run.y, run.start : run.end + 1, 3]
        artifacts.append(
            _png_artifact(
                Image.fromarray(component, "RGBA"),
                operation="split_local",
                mode="alpha_components",
                component_index=index,
                area=area,
                source_bounds={
                    "x": padded_left,
                    "y": padded_top,
                    "width": padded_right - padded_left,
                    "height": padded_bottom - padded_top,
                },
            )
        )
    return artifacts


class LocalImageProcessingService:
    def __init__(self, assets: AssetService) -> None:
        self._assets = assets
        self._upscaler = RealEsrganUpscaler()

    def _read(self, root: Path, project_id: str, asset_id: str) -> tuple[dict[str, Any], bytes]:
        asset = self._assets.get(root, project_id, asset_id)
        if not str(asset.get("mime_type", "")).startswith("image/"):
            raise ValueError("Source asset must be an image.")
        _, content, _, _ = self._assets.read_content(root, project_id, asset_id, None)
        return asset, content

    def _register(
        self,
        root: Path,
        project_id: str,
        source_asset: dict[str, Any],
        artifact: LocalImageArtifact,
        *,
        operation: str,
        request_id: str,
        index: int | None = None,
        asset_type: str = "generated_image",
    ) -> dict[str, Any]:
        suffix = artifact.suffix
        stem = Path(str(source_asset.get("name") or "image")).stem
        output_name = f"{stem}_{operation}{'' if index is None else f'_{index + 1}'}{suffix}"
        temporary = root / "temp" / f"local-image-{new_id()}{suffix}"
        try:
            temporary.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(artifact.content)
            registered = self._assets.register_derived(
                root,
                project_id,
                temporary,
                asset_type,
                request_id,
                parent_asset_id=str(source_asset["id"]),
                input_asset_ids=[str(source_asset["id"])],
                name=output_name,
                provenance={
                    "source_kind": "tool",
                    "parameters": {
                        "operation": operation,
                        "width": artifact.width,
                        "height": artifact.height,
                        **artifact.metadata,
                    },
                },
            )
            return {**registered, "verification": artifact.metadata.get("verification")}
        finally:
            temporary.unlink(missing_ok=True)

    def trim_asset(
        self,
        root: Path,
        project_id: str,
        asset_id: str,
        *,
        padding: int,
        alpha_threshold: int,
        request_id: str,
    ) -> dict[str, Any]:
        asset, content = self._read(root, project_id, asset_id)
        artifact = trim_transparent(
            content,
            padding=padding,
            alpha_threshold=alpha_threshold,
        )
        return self._register(
            root,
            project_id,
            asset,
            artifact,
            operation="trimmed",
            request_id=request_id,
        )

    def normalize_asset(
        self,
        root: Path,
        project_id: str,
        asset_id: str,
        *,
        request_id: str,
        target_width: int | None = None,
        target_height: int | None = None,
        max_long_edge: int | None = None,
        lock_aspect_ratio: bool = True,
        rotate_degrees: int = 0,
        flip: str = "none",
        output_format: str = "png",
        quality: int = 90,
        preserve_alpha: bool = True,
    ) -> dict[str, Any]:
        asset, content = self._read(root, project_id, asset_id)
        artifact = normalize_image(
            content,
            target_width=target_width,
            target_height=target_height,
            max_long_edge=max_long_edge,
            lock_aspect_ratio=lock_aspect_ratio,
            rotate_degrees=rotate_degrees,
            flip=flip,
            output_format=output_format,
            quality=quality,
            preserve_alpha=preserve_alpha,
        )
        return self._register(
            root,
            project_id,
            asset,
            artifact,
            operation="normalized",
            request_id=request_id,
        )

    def remove_background_asset(
        self,
        root: Path,
        project_id: str,
        asset_id: str,
        *,
        request_id: str,
        method: str,
        target_color: tuple[int, int, int] | None = None,
        tolerance: int = 24,
        contiguous_only: bool = True,
        channel: str = "green",
        min_threshold: int = 0,
        max_threshold: int = 120,
        invert: bool = False,
        feather: int = 0,
        edge_shrink: int = 0,
    ) -> dict[str, Any]:
        asset, content = self._read(root, project_id, asset_id)
        artifact = remove_background_local(
            content,
            method=method,
            target_color=target_color,
            tolerance=tolerance,
            contiguous_only=contiguous_only,
            channel=channel,
            min_threshold=min_threshold,
            max_threshold=max_threshold,
            invert=invert,
            feather=feather,
            edge_shrink=edge_shrink,
        )
        return self._register(
            root,
            project_id,
            asset,
            artifact,
            operation="background_removed",
            request_id=request_id,
        )

    def split_asset(
        self,
        root: Path,
        project_id: str,
        asset_id: str,
        *,
        request_id: str,
        mode: str,
        columns: int | None = None,
        rows: int | None = None,
        alpha_threshold: int = 0,
        min_area: int = 4,
        padding: int = 0,
        max_outputs: int = 64,
    ) -> list[dict[str, Any]]:
        asset, content = self._read(root, project_id, asset_id)
        artifacts = split_local_image(
            content,
            mode=mode,
            columns=columns,
            rows=rows,
            alpha_threshold=alpha_threshold,
            min_area=min_area,
            padding=padding,
            max_outputs=max_outputs,
        )
        return [
            self._register(
                root,
                project_id,
                asset,
                artifact,
                operation="sprite",
                request_id=f"{request_id}:{index}",
                index=index,
                asset_type="crop",
            )
            for index, artifact in enumerate(artifacts)
        ]

    def upscale_asset(
        self,
        root: Path,
        project_id: str,
        asset_id: str,
        *,
        scale: int,
        request_id: str,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, Any]:
        asset, content = self._read(root, project_id, asset_id)
        source = _open_image(content)
        result = self._upscaler.upscale(source, scale=scale, on_progress=on_progress)
        artifact = _png_artifact(
            result,
            operation="upscale_local",
            scale=scale,
            model="realesrgan-x4",
        )
        return self._register(
            root,
            project_id,
            asset,
            artifact,
            operation=f"upscaled_x{scale}",
            request_id=request_id,
        )
