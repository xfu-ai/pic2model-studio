from __future__ import annotations

import pytest

from aipic_to_model.application.approval import ProductionApprovalGate, TestApprovalGate
from aipic_to_model.application.image_editing import (
    EditRequest,
    ImageEditService,
    ImageProviderCapabilities,
)


def test_capability_gate_does_not_silently_fallback_to_another_service() -> None:
    service = ImageEditService(ImageProviderCapabilities(), TestApprovalGate())
    prepared = service.prepare(
        EditRequest(
            source_asset_id="asset-1", provider_profile="test", operation="remove_background"
        )
    )
    assert not prepared.available
    assert prepared.reason == "capability_unavailable"


def test_inpaint_requires_a_confirmed_selection_and_approval() -> None:
    service = ImageEditService(
        ImageProviderCapabilities(inpaint_selection=True), ProductionApprovalGate()
    )
    with pytest.raises(PermissionError, match="APPROVAL_UI_CONFIRMATION_REQUIRED"):
        service.prepare(
            EditRequest(
                source_asset_id="asset-1",
                provider_profile="production",
                operation="inpaint_selection",
                selection_id="selection-1",
                selection_confirmed=False,
                prompt_asset_id="prompt-1",
            )
        )
    prepared = service.prepare(
        EditRequest(
            source_asset_id="asset-1",
            provider_profile="production",
            operation="inpaint_selection",
            selection_id="selection-1",
            selection_confirmed=True,
            prompt_asset_id="prompt-1",
        )
    )
    assert prepared.available
    assert not prepared.approved
