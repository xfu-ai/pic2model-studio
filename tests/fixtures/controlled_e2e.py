"""Deterministic, local-only data for controlled desktop validation."""

from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image

from tests.fixtures.glb import minimal_test_glb


def create_controlled_e2e_fixture_set(destination: Path) -> dict[str, Path]:
    """Create named image/model/package fixtures without a provider or native picker."""

    destination.mkdir(parents=True, exist_ok=True)
    # These directories back the Debug-only Tauri chooser seam.  They are
    # deliberately inside the per-run fixture root, so a controlled test never
    # gains authority over a developer's real project or export location.
    project = destination / "project"
    project.mkdir(exist_ok=True)
    export = destination / "export"
    export.mkdir(exist_ok=True)
    images = {
        "source-a.png": ("RGB", (64, 48), (232, 82, 82)),
        "source-b.png": ("RGBA", (128, 80), (61, 121, 214, 128)),
        "source-c.png": ("RGBA", (48, 96), (43, 181, 111, 0)),
    }
    for name, (mode, size, colour) in images.items():
        Image.new(mode, size, colour).save(destination / name)

    repository = Path(__file__).parents[2]
    fixtures = repository / "tests" / "fixtures" / "project_packages"
    valid_glb = destination / "fixture-model.glb"
    valid_glb.write_bytes(minimal_test_glb())
    corrupt_glb = destination / "corrupt.glb"
    corrupt_glb.write_bytes(b"glTF\x02\x00\x00\x00\xff\xff")

    normal_package = destination / "normal.pic2model"
    shutil.copyfile(fixtures / "complete-v1.pic2model", normal_package)
    corrupt_project = destination / "corrupt-project"
    corrupt_project.mkdir(exist_ok=True)
    (corrupt_project / "project.sqlite3").write_bytes(b"not a sqlite database")

    return {
        "source_a": destination / "source-a.png",
        "source_b": destination / "source-b.png",
        "source_c": destination / "source-c.png",
        "model": valid_glb,
        "corrupt_glb": corrupt_glb,
        "normal_package": normal_package,
        "corrupt_project": corrupt_project,
        "project": project,
        "export": export,
    }
