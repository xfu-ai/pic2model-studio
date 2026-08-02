"""Pure, managed-asset-ready provider image normalization."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from math import sqrt
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from ..domain.common import new_id
from .assets import AssetService

@dataclass(frozen=True)
class CompressedImage:
    content: bytes
    width: int
    height: int
    quality: int
    format: str = "JPEG"
    mime_type: str = "image/jpeg"


@dataclass(frozen=True)
class ProviderPreviewPolicy:
    max_edge: int
    max_pixels: int
    jpeg_quality: int


STANDARD_PREVIEW = ProviderPreviewPolicy(max_edge=1536, max_pixels=2_359_296, jpeg_quality=85)
COMPACT_PREVIEW = ProviderPreviewPolicy(max_edge=960, max_pixels=921_600, jpeg_quality=78)


def _fit_dimensions(width: int, height: int, policy: ProviderPreviewPolicy) -> tuple[int, int]:
    edge_scale = min(policy.max_edge / width, policy.max_edge / height)
    area_scale = sqrt(policy.max_pixels / (width * height))
    scale = min(1.0, edge_scale, area_scale)
    return max(1, round(width * scale)), max(1, round(height * scale))


def compress_for_provider(content: bytes, *, minimum: bool = False) -> CompressedImage:
    """Normalize dimensions and quality without accepting or returning a path.

    Callers must persist the returned bytes as a *new* managed asset; this
    function deliberately has no filesystem capability.
    """
    if not content:
        raise ValueError("图像内容为空。")
    policy = COMPACT_PREVIEW if minimum else STANDARD_PREVIEW
    try:
        with Image.open(BytesIO(content)) as source:
            source.load()
            image = ImageOps.exif_transpose(source)
            rgba = image.convert("RGBA")
            canvas = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            image = Image.alpha_composite(canvas, rgba).convert("RGB")
            target_size = _fit_dimensions(image.width, image.height, policy)
            if target_size != image.size:
                image = image.resize(target_size, Image.Resampling.LANCZOS)
            result = BytesIO()
            image.save(
                result,
                "JPEG",
                quality=policy.jpeg_quality,
                optimize=True,
                progressive=True,
                subsampling="4:2:0",
            )
            return CompressedImage(
                result.getvalue(), image.width, image.height, policy.jpeg_quality
            )
    except (OSError, ValueError) as error:
        raise ValueError("图片无法解码或压缩。") from error


class ImageProcessingService:
    """Turns provider compression into a new managed image asset."""

    def __init__(self, assets: AssetService) -> None:
        self._assets = assets

    def compress_asset(
        self,
        root: Path,
        project_id: str,
        asset_id: str,
        *,
        minimum: bool,
        request_id: str,
    ) -> dict[str, Any]:
        _, content, _mime, _headers = self._assets.read_content(root, project_id, asset_id, None)
        compressed = compress_for_provider(content, minimum=minimum)
        temporary = root / "temp" / f"provider-compress-{new_id()}.jpg"
        try:
            temporary.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(compressed.content)
            return self._assets.register_derived(
                root,
                project_id,
                temporary,
                "generated_image",
                request_id,
                parent_asset_id=asset_id,
                input_asset_ids=[asset_id],
                name="provider-compressed.jpg",
                provenance={
                    "source_kind": "tool",
                    "parameters": {
                        "operation": "image.compress_for_provider",
                        "minimum": minimum,
                        "quality": compressed.quality,
                        "width": compressed.width,
                        "height": compressed.height,
                    },
                },
            )
        finally:
            temporary.unlink(missing_ok=True)
