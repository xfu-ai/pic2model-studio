from __future__ import annotations

from io import BytesIO

import numpy as np
from PIL import Image, ImageDraw

from aipic_to_model.application.local_image_processing import (
    normalize_image,
    remove_background_local,
    split_local_image,
    trim_transparent,
)
from aipic_to_model.infrastructure.realesrgan import (
    RealEsrganUpscaler,
    verify_bundled_model,
)


def _bytes(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _open(content: bytes) -> Image.Image:
    with Image.open(BytesIO(content)) as image:
        image.load()
        return image.copy()


def test_trim_transparent_finds_alpha_bounds_and_padding() -> None:
    source = Image.new("RGBA", (20, 16), (0, 0, 0, 0))
    ImageDraw.Draw(source).rectangle((5, 4, 12, 10), fill=(255, 0, 0, 255))

    result = trim_transparent(_bytes(source), padding=2, alpha_threshold=0)

    assert (result.width, result.height) == (12, 11)
    assert result.metadata["source_bounds"] == {"x": 3, "y": 2, "width": 12, "height": 11}


def test_normalize_resizes_rotates_flips_and_encodes_webp() -> None:
    source = Image.new("RGBA", (40, 20), (20, 40, 60, 128))

    result = normalize_image(
        _bytes(source),
        target_width=30,
        lock_aspect_ratio=True,
        rotate_degrees=90,
        flip="horizontal",
        output_format="webp",
        quality=80,
        preserve_alpha=True,
    )

    image = _open(result.content)
    assert result.suffix == ".webp"
    assert image.size == (30, 60)
    assert image.mode == "RGBA"


def test_local_color_key_removes_only_edge_connected_background() -> None:
    source = Image.new("RGBA", (12, 12), (0, 255, 0, 255))
    drawing = ImageDraw.Draw(source)
    drawing.rectangle((3, 3, 8, 8), fill=(220, 20, 20, 255))
    drawing.rectangle((5, 5, 6, 6), fill=(0, 255, 0, 255))

    result = remove_background_local(
        _bytes(source),
        method="color_key",
        target_color=(0, 255, 0),
        tolerance=4,
        contiguous_only=True,
    )
    image = _open(result.content).convert("RGBA")

    assert image.getpixel((0, 0))[3] == 0
    assert image.getpixel((4, 4))[3] == 255
    assert image.getpixel((5, 5))[3] == 255
    verification = result.metadata["verification"]
    assert isinstance(verification, dict)
    assert verification["disposition"] == "verified"


def test_background_removal_requires_review_when_opaque_keyed_corners_remain() -> None:
    source = Image.new("RGBA", (20, 20), (255, 0, 255, 255))
    drawing = ImageDraw.Draw(source)
    drawing.rectangle((6, 6, 13, 13), fill=(220, 20, 20, 255))
    for point in ((0, 0), (19, 0), (0, 19), (19, 19)):
        source.putpixel(point, (230, 20, 230, 255))

    result = remove_background_local(
        _bytes(source),
        method="color_key",
        target_color=(255, 0, 255),
        tolerance=10,
    )

    verification = result.metadata["verification"]
    assert isinstance(verification, dict)
    assert verification["disposition"] == "review_required"
    assert verification["facts"]["opaque_corner_count"] == 4
    assert any(
        check["code"] == "image.background_removal_corners"
        for check in verification["checks"]
    )


def test_corner_derived_color_key_adapts_tolerance_without_model_guessed_rgb() -> None:
    source = Image.new("RGBA", (20, 20), (255, 0, 255, 255))
    drawing = ImageDraw.Draw(source)
    drawing.rectangle((6, 6, 13, 13), fill=(220, 20, 20, 255))
    for point in ((0, 0), (19, 0), (0, 19), (19, 19)):
        source.putpixel(point, (230, 20, 230, 255))

    result = remove_background_local(_bytes(source), method="color_key")
    image = _open(result.content).convert("RGBA")

    assert result.metadata["tolerance_auto"] is True
    assert result.metadata["tolerance"] > 24
    assert all(
        image.getpixel(point)[3] == 0
        for point in ((0, 0), (19, 0), (0, 19), (19, 19))
    )
    assert image.getpixel((10, 10))[3] == 255
    assert result.metadata["verification"]["disposition"] == "verified"


def test_background_removal_reports_advisory_warning_when_no_alpha_is_created() -> None:
    source = Image.new("RGBA", (20, 20), (200, 10, 200, 255))

    result = remove_background_local(
        _bytes(source),
        method="color_key",
        target_color=(1, 2, 3),
        tolerance=1,
    )

    verification = result.metadata["verification"]
    assert isinstance(verification, dict)
    assert verification["disposition"] == "review_required"
    checks = verification["checks"]
    assert isinstance(checks, list)
    assert any(check["code"] == "image.background_removal_alpha" for check in checks)


def test_background_removal_reports_advisory_warning_when_foreground_is_lost() -> None:
    source = Image.new("RGBA", (20, 20), (0, 255, 0, 255))
    source.putpixel((10, 10), (220, 20, 20, 255))

    result = remove_background_local(
        _bytes(source),
        method="color_key",
        target_color=(0, 255, 0),
        tolerance=1,
    )

    verification = result.metadata["verification"]
    assert isinstance(verification, dict)
    assert verification["disposition"] == "review_required"
    checks = verification["checks"]
    assert isinstance(checks, list)
    assert any(check["code"] == "image.background_removal_foreground" for check in checks)


def test_channel_matting_keeps_only_requested_channel_range() -> None:
    source = Image.new("RGBA", (2, 1))
    source.putdata([(10, 200, 10, 255), (10, 20, 10, 255)])

    result = remove_background_local(
        _bytes(source),
        method="channel",
        channel="green",
        min_threshold=150,
        max_threshold=255,
    )
    alpha = np.asarray(_open(result.content).convert("RGBA").getchannel("A")).ravel().tolist()

    assert alpha[0] == 255
    assert alpha[1] == 0


def test_alpha_component_split_filters_noise_and_masks_other_components() -> None:
    source = Image.new("RGBA", (30, 20), (0, 0, 0, 0))
    drawing = ImageDraw.Draw(source)
    drawing.rectangle((2, 2, 8, 8), fill=(255, 0, 0, 255))
    drawing.rectangle((18, 5, 26, 14), fill=(0, 0, 255, 255))
    source.putpixel((15, 1), (255, 255, 255, 255))

    results = split_local_image(
        _bytes(source),
        mode="alpha_components",
        min_area=4,
        padding=1,
        max_outputs=8,
    )

    assert [(result.width, result.height) for result in results] == [(9, 9), (11, 12)]
    assert all(np.asarray(_open(result.content).getchannel("A")).max() == 255 for result in results)


def test_grid_split_covers_remainder_without_losing_pixels() -> None:
    source = Image.new("RGBA", (11, 7), (1, 2, 3, 255))

    results = split_local_image(
        _bytes(source),
        mode="grid",
        columns=3,
        rows=2,
        max_outputs=6,
    )

    assert len(results) == 6
    assert sum(result.width * result.height for result in results) == 11 * 7


def test_bundled_realesrgan_model_is_verified_and_runs_without_network() -> None:
    assert verify_bundled_model().stat().st_size == 66_991_363
    source = Image.new("RGBA", (8, 8), (40, 80, 120, 200))

    result = RealEsrganUpscaler().upscale(source, scale=2)

    assert result.size == (16, 16)
    assert result.mode == "RGBA"
    assert 190 <= result.getchannel("A").getpixel((8, 8)) <= 210
