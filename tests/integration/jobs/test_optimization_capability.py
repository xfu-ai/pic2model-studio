from __future__ import annotations

from test_conversion import _model

from aipic_to_model.application.jobs.model_conversion import ModelOptimizationService
from aipic_to_model.domain.provider_models import ProviderResult


class Optimizer:
    def optimize(
        self, content: bytes, *, target_triangles: int | None, max_texture_bytes: int | None,
        on_progress=None,
    ) -> ProviderResult:
        del on_progress
        return ProviderResult(
            ok=True, stage="postprocessing", retryable=False, payload={"glb_bytes": content}
        )


def test_optimization_is_capability_gated_and_available_provider_creates_new_glb(tmp_path) -> None:
    dependencies, project, model = _model(tmp_path)
    unavailable = ModelOptimizationService(dependencies.assets, None)
    assert not unavailable.capability().available
    available = ModelOptimizationService(dependencies.assets, Optimizer())
    optimized = available.optimize(
        tmp_path / "project",
        project.id,
        str(model["id"]),
        target_triangles=100,
        max_texture_bytes=None,
        request_id="optimize",
    )
    assert optimized["asset_type"] == "glb" and optimized["id"] != model["id"]
