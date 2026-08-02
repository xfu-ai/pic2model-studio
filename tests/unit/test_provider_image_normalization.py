from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from aipic_to_model.application.host_capabilities import HostCapabilityStore
from aipic_to_model.application.image_processing import (
    ImageProcessingService,
    compress_for_provider,
)
from aipic_to_model.composition import compose_local_app


def _png(size: tuple[int, int], mode: str = "RGBA") -> bytes:
    image = Image.new(mode, size, (255, 0, 0, 0) if "A" in mode else 128)
    content = BytesIO()
    image.save(content, "PNG")
    return content.getvalue()


def test_preview_policies_create_distinct_rgb_jpegs() -> None:
    source = _png((2048, 1024))
    standard = compress_for_provider(source)
    compact = compress_for_provider(source, minimum=True)

    assert source != standard.content != compact.content
    assert (standard.width, standard.height, standard.quality) == (1536, 768, 85)
    assert (compact.width, compact.height, compact.quality) == (960, 480, 78)
    with Image.open(BytesIO(standard.content)) as image:
        assert image.mode == "RGB"
        assert image.format == "JPEG"
        assert image.getpixel((0, 0)) == (255, 255, 255)


def test_preview_policy_also_limits_total_pixel_area() -> None:
    result = compress_for_provider(_png((1400, 1400)))
    assert result.width * result.height <= 2_359_296
    assert max(result.width, result.height) <= 1536


def test_palette_image_and_bad_input_are_handled_safely() -> None:
    assert compress_for_provider(_png((100, 100), "P")).mime_type == "image/jpeg"
    with pytest.raises(ValueError, match="无法解码"):
        compress_for_provider(b"not an image")


def test_compression_service_registers_a_new_managed_asset(tmp_path) -> None:
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Compression")
    source = tmp_path / "source.png"
    source.write_bytes(_png((1600, 900)))
    original = dependencies.assets.import_file(root, project.id, source, "source_image", "import-1")

    result = ImageProcessingService(dependencies.assets).compress_asset(
        root, project.id, original["id"], minimum=False, request_id="compress-1"
    )

    assert result["id"] != original["id"]
    assert result["provenance"]["input_asset_ids"] == [original["id"]]
    assert dependencies.assets.get(root, project.id, original["id"])["sha256"] == original["sha256"]
