from __future__ import annotations

import pytest

from aipic_to_model.application.approval import ProductionApprovalGate, TestApprovalGate
from aipic_to_model.application.image_generation import ImageGenerationService
from aipic_to_model.domain.provider_models import GenerationRequest
from aipic_to_model.infrastructure.providers.fake import FakeImageGenerationProvider


def _request(profile: str = "production") -> GenerationRequest:
    return GenerationRequest(
        prompt_asset_id="prompt-1",
        source_asset_id=None,
        provider_profile=profile,
        channel="banana",
        mode="t2i",
        model="fake-image",
        candidate_count=2,
    )


def test_unapproved_paid_generation_has_zero_provider_calls() -> None:
    provider = FakeImageGenerationProvider()
    service = ImageGenerationService(provider, ProductionApprovalGate())
    preparation = service.prepare(_request())
    assert preparation.requires_approval
    with pytest.raises(PermissionError, match="APPROVAL_REQUIRED"):
        service.submit(preparation)
    assert provider.calls == []


def test_test_profile_can_submit_only_through_explicit_test_gate() -> None:
    provider = FakeImageGenerationProvider()
    service = ImageGenerationService(provider, TestApprovalGate())
    result = service.submit(service.prepare(_request("test")))
    assert result.ok
    assert provider.calls[0][0] == "image.generate"
