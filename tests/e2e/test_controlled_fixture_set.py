from __future__ import annotations

import zipfile
from pathlib import Path

from PIL import Image

from tests.fixtures.controlled_e2e import create_controlled_e2e_fixture_set


def test_controlled_e2e_fixture_set_is_named_varied_and_local(tmp_path: Path) -> None:
    fixtures = create_controlled_e2e_fixture_set(tmp_path / "fixtures")

    observed = [Image.open(fixtures[key]) for key in ("source_a", "source_b", "source_c")]
    assert [image.size for image in observed] == [(64, 48), (128, 80), (48, 96)]
    assert [image.mode for image in observed] == ["RGB", "RGBA", "RGBA"]
    assert observed[1].getpixel((0, 0))[3] == 128
    assert observed[2].getpixel((0, 0))[3] == 0
    assert fixtures["model"].read_bytes().startswith(b"glTF")
    assert fixtures["corrupt_glb"].read_bytes() != fixtures["model"].read_bytes()
    assert fixtures["project"].is_dir()
    assert fixtures["export"].is_dir()
    assert (fixtures["corrupt_project"] / "project.sqlite3").read_bytes() == b"not a sqlite database"
    with zipfile.ZipFile(fixtures["normal_package"]) as package:
        assert "manifest.json" in package.namelist()
