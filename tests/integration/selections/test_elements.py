from __future__ import annotations

import pytest

from aipic_to_model.application.approval import ProductionApprovalGate, TestApprovalGate
from aipic_to_model.application.elements import ElementSplitRequest, ElementSplitService


def _request(confirmed: bool, profile: str = "test") -> ElementSplitRequest:
    return ElementSplitRequest("asset-1", "selection-1", confirmed, profile, "prompt-1")


def test_element_split_requires_confirmed_selection_before_approval_or_network() -> None:
    service = ElementSplitService(TestApprovalGate())
    with pytest.raises(PermissionError, match="APPROVAL_UI_CONFIRMATION_REQUIRED"):
        service.prepare(_request(False))
    assert service.prepare(_request(True))
    assert not ElementSplitService(ProductionApprovalGate()).prepare(_request(True, "production"))
