from __future__ import annotations

from pathlib import Path

import pytest
import trimesh

from aipic_to_model.infrastructure.converters.controlled import (
    ApprovedConverterSettings,
    default_conversion_backends,
)


def _write_independent_cube(path: Path) -> None:
    scene = trimesh.Scene(trimesh.creation.box(extents=(1.0, 1.5, 0.75)))
    path.write_bytes(scene.export(file_type="glb"))


def test_default_converter_order_is_blender_then_geometry_fallback(tmp_path) -> None:
    backends = default_conversion_backends()
    assert [backend.name for backend in backends] == ["blender", "geometry_fbx"]
    source, destination = tmp_path / "input.glb", tmp_path / "output.fbx"
    source.write_bytes(b"fixture")
    assert backends[0].convert(source, destination, timeout_seconds=1).status == "failed"


def test_configured_blender_backend_is_not_silently_skipped(tmp_path) -> None:
    executable = tmp_path / "converter.exe"
    executable.write_bytes(b"fixture")
    backends = default_conversion_backends(
        ApprovedConverterSettings(blender_executable=Path(executable))
    )
    source, destination = tmp_path / "input.glb", tmp_path / "output.fbx"
    source.write_bytes(b"fixture")
    assert backends[0].convert(source, destination, timeout_seconds=1).status == "failed"


def test_discovered_blender_converts_an_independently_generated_glb(tmp_path) -> None:
    source = tmp_path / "cube.glb"
    destination = tmp_path / "cube.fbx"
    _write_independent_cube(source)
    attempt = default_conversion_backends()[0].convert(source, destination, timeout_seconds=10)
    if attempt.status == "skipped":
        pytest.skip("Blender is not installed on this host")
    assert attempt.status == "succeeded"
    assert destination.read_bytes().startswith(b"Kaydara FBX Binary")


def test_geometry_only_fallback_writes_an_importable_ascii_fbx(tmp_path) -> None:
    source = tmp_path / "cube.glb"
    destination = tmp_path / "fallback.fbx"
    _write_independent_cube(source)
    fallback = default_conversion_backends()[1].convert(source, destination, timeout_seconds=10)
    assert fallback.status == "succeeded"
    assert destination.read_bytes().startswith(b"; FBX")

    verified = tmp_path / "verified.fbx"
    blender = default_conversion_backends()[0]
    imported = blender.convert(destination, verified, timeout_seconds=10)
    assert imported.status == "failed"
