from PIL import Image, ImageDraw

from aipic_to_model.application.assets import _visual_identity


def test_visual_identity_is_stable_across_resizing_and_distinguishes_artwork(tmp_path):
    large = tmp_path / "large.png"
    small = tmp_path / "small.png"
    different = tmp_path / "different.png"

    icon = Image.new("RGBA", (252, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon)
    draw.ellipse((12, 10, 240, 244), fill=(186, 108, 48, 255), outline=(72, 35, 18, 255), width=9)
    draw.polygon(((126, 38), (160, 138), (102, 196)), fill=(220, 52, 42, 255))
    draw.rectangle((52, 92, 90, 164), fill=(250, 214, 137, 255))
    icon.save(large)
    icon.resize((187, 190), Image.Resampling.LANCZOS).save(small)

    other = Image.new("RGBA", (252, 256), (0, 0, 0, 0))
    other_draw = ImageDraw.Draw(other)
    other_draw.rounded_rectangle((24, 40, 228, 224), radius=24, fill=(45, 160, 116, 255))
    other_draw.ellipse((70, 72, 182, 184), fill=(240, 236, 190, 255))
    other.save(different)

    large_identity = _visual_identity(large)
    small_identity = _visual_identity(small)
    different_identity = _visual_identity(different)

    differing_bits = sum(
        (int(left, 16) ^ int(right, 16)).bit_count()
        for left, right in zip(large_identity[0], small_identity[0], strict=True)
    )
    assert differing_bits / (len(large_identity[0]) * 4) <= 0.08
    assert abs(large_identity[1] - small_identity[1]) <= 0.025
    assert large_identity[0] != different_identity[0]


def test_visual_identity_distinguishes_flat_colors(tmp_path):
    red = tmp_path / "red.png"
    blue = tmp_path / "blue.png"
    Image.new("RGBA", (64, 48), (238, 74, 78, 255)).save(red)
    Image.new("RGBA", (64, 48), (48, 96, 220, 255)).save(blue)

    red_fingerprint = _visual_identity(red)[0]
    blue_fingerprint = _visual_identity(blue)[0]
    differing_bits = sum(
        (int(left, 16) ^ int(right, 16)).bit_count()
        for left, right in zip(red_fingerprint, blue_fingerprint, strict=True)
    )

    assert len(red_fingerprint) == 128
    assert differing_bits / (len(red_fingerprint) * 4) > 0.08
