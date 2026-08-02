"""B02 approval boundary; production defaults to zero external network access."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REQUIRES_USER = "requires_user"
    DENIED = "denied"


@dataclass(frozen=True)
class ProposedCall:
    tool_name: str
    provider_profile: str
    input_asset_ids: tuple[str, ...]


class ApprovalGate(Protocol):
    def authorize(self, call: ProposedCall) -> ApprovalDecision: ...


class ProductionApprovalGate:
    def authorize(self, call: ProposedCall) -> ApprovalDecision:
        del call
        return ApprovalDecision.REQUIRES_USER


class TestApprovalGate:
    """Only explicit test-profile calls may pass in offline tests/smoke harnesses."""

    __test__ = False

    def __init__(self, approved: bool = True) -> None:
        self._approved = approved

    def authorize(self, call: ProposedCall) -> ApprovalDecision:
        if self._approved and call.provider_profile == "test":
            return ApprovalDecision.APPROVED
        return ApprovalDecision.REQUIRES_USER
