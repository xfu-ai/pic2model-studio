from __future__ import annotations

from aipic_to_model.application.image_provider_routing import (
    AUTO_IMAGE_PROFILE,
    CredentialProbeRoute,
    ImageProviderRoute,
    PrioritizedImageGenerationProvider,
)
from aipic_to_model.domain.provider_models import ProviderResult
from aipic_to_model.infrastructure.providers.http_errors import http_failure


class StubProvider:
    def __init__(self, *, available: bool = True, generation_ok: bool = True) -> None:
        self.available = available
        self.generation_ok = generation_ok
        self.requests: list[dict[str, object]] = []

    def probe(self) -> ProviderResult:
        if not self.available:
            return http_failure(operation="probing", status_code=503)
        return ProviderResult(ok=True, stage="probing", retryable=False)

    def generate(self, request: dict[str, object]) -> ProviderResult:
        self.requests.append(request)
        if not self.generation_ok:
            return http_failure(operation="creating", status_code=503)
        return ProviderResult(
            ok=True,
            stage="generating",
            retryable=False,
            payload={"images": [{"base64": "opaque"}, {"base64": "opaque"}]},
        )


def _router(tripo: StubProvider, meshy: StubProvider, priority: list[str]):
    return PrioritizedImageGenerationProvider(
        [
            ImageProviderRoute(
                "tripo3d/default",
                "Tripo3D",
                "tripo",
                "seedream_v5",
                frozenset({"t2i", "i2i"}),
                tripo,
                {"i2i": "gemini_3.1_flash_image_preview"},
            ),
            ImageProviderRoute(
                "meshy/default", "Meshy", "meshy", "nano-banana", frozenset({"t2i", "i2i"}), meshy
            ),
        ],
        lambda: {"image_provider_priority": priority},
    )


def test_router_uses_highest_priority_available_provider_before_submission() -> None:
    tripo = StubProvider(available=False)
    meshy = StubProvider()
    router = _router(tripo, meshy, ["tripo3d/default", "meshy/default"])

    result = router.generate(
        {
            "provider_profile": AUTO_IMAGE_PROFILE,
            "mode": "t2i",
            "candidate_count": 2,
            "prompt": "cube",
        }
    )

    assert result.ok
    assert not tripo.requests
    assert meshy.requests[0]["channel"] == "meshy"
    assert result.payload["routing"]["provider_profile"] == "meshy/default"


def test_router_honours_priority_and_never_resubmits_after_selected_provider_failure() -> None:
    tripo = StubProvider(generation_ok=False)
    meshy = StubProvider()
    router = _router(tripo, meshy, ["tripo3d/default", "meshy/default"])

    result = router.generate(
        {
            "provider_profile": AUTO_IMAGE_PROFILE,
            "mode": "t2i",
            "candidate_count": 2,
            "prompt": "cube",
        }
    )

    assert not result.ok
    assert len(tripo.requests) == 1
    assert not meshy.requests


def test_router_uses_priority_and_mode_specific_model_for_image_to_image() -> None:
    tripo = StubProvider()
    meshy = StubProvider()
    router = _router(tripo, meshy, ["tripo3d/default", "meshy/default"])

    result = router.generate(
        {
            "provider_profile": AUTO_IMAGE_PROFILE,
            "mode": "i2i",
            "candidate_count": 2,
            "prompt": "cube",
        }
    )

    assert result.ok
    assert len(tripo.requests) == 1
    assert not meshy.requests
    assert tripo.requests[0]["model"] == "gemini_3.1_flash_image_preview"
    assert result.payload["routing"]["provider_profile"] == "tripo3d/default"


def test_image_to_image_falls_back_to_meshy_before_submission() -> None:
    tripo = StubProvider(available=False)
    meshy = StubProvider()
    router = _router(tripo, meshy, ["tripo3d/default", "meshy/default"])

    result = router.generate(
        {
            "provider_profile": AUTO_IMAGE_PROFILE,
            "mode": "i2i",
            "candidate_count": 2,
            "prompt": "cube",
        }
    )

    assert result.ok
    assert not tripo.requests
    assert len(meshy.requests) == 1
    assert result.payload["routing"]["provider_profile"] == "meshy/default"


def test_service_status_includes_independently_refreshable_non_image_providers() -> None:
    deepseek = StubProvider()
    router = PrioritizedImageGenerationProvider(
        [
            ImageProviderRoute(
                "tripo3d/default",
                "Tripo3D",
                "tripo",
                "seedream_v5",
                frozenset({"t2i", "i2i"}),
                StubProvider(),
            )
        ],
        lambda: {"image_provider_priority": ["tripo3d/default"]},
        credential_probes=[
            CredentialProbeRoute(
                "agent/deepseek/default",
                "DeepSeek Agent",
                "deepseek",
                "deepseek-v4-flash",
                ("agent_chat",),
                deepseek,
            )
        ],
    )

    snapshot = router.refresh_profile("agent/deepseek/default")

    status = next(
        item for item in snapshot["providers"] if item["profile"] == "agent/deepseek/default"
    )
    assert status["available"] is True
    assert status["capabilities"] == ["agent_chat"]
    assert status["model"] == "deepseek-v4-flash"
