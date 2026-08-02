from __future__ import annotations

import trimesh

from aipic_to_model.application.model_inspection import inspect_glb
from aipic_to_model.infrastructure.model_optimization import FastSimplificationGlbOptimizer


def test_local_optimizer_reduces_a_managed_glb_to_the_requested_triangle_budget() -> None:
    scene = trimesh.Scene(trimesh.creation.icosphere(subdivisions=3))
    original = scene.export(file_type="glb")
    result = FastSimplificationGlbOptimizer().optimize(
        original, target_triangles=100, max_texture_bytes=None
    )
    assert result.ok
    optimized = result.payload["glb_bytes"]
    assert isinstance(optimized, bytes)
    assert len(optimized) < len(original)
    inspection = inspect_glb(optimized, local_relative_path="assets/models/optimized.glb")
    assert inspection.parseable and inspection.triangle_count <= 100


def test_local_optimizer_reports_bounded_progress_for_geometry_and_attribute_mapping() -> None:
    scene = trimesh.Scene(trimesh.creation.icosphere(subdivisions=2))
    events: list[tuple[str, int, dict[str, int]]] = []
    result = FastSimplificationGlbOptimizer().optimize(
        scene.export(file_type="glb"),
        target_triangles=100,
        max_texture_bytes=None,
        on_progress=lambda stage, percent, details: events.append((stage, percent, details)),
    )
    assert result.ok
    assert events[0][0:2] == ("geometry_simplification", 5)
    assert any(stage == "attribute_mapping" and percent == 20 for stage, percent, _ in events)
    assert events[-1][0:2] == ("writing_glb", 95)
    assert all(0 < percent <= 95 for _, percent, _ in events)
