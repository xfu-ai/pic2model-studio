"""Element-splitting input guard for B02 Jobs."""

from __future__ import annotations

from dataclasses import dataclass

from .approval import ApprovalDecision, ApprovalGate, ProposedCall


@dataclass(frozen=True)
class ElementSplitRequest:
    source_asset_id: str
    selection_id: str
    selection_confirmed: bool
    provider_profile: str
    prompt_asset_id: str


class ElementSplitService:
    def __init__(self, approval_gate: ApprovalGate) -> None:
        self._approval_gate = approval_gate

    def prepare(self, request: ElementSplitRequest) -> bool:
        if not request.selection_confirmed:
            raise PermissionError("APPROVAL_UI_CONFIRMATION_REQUIRED")
        decision = self._approval_gate.authorize(
            ProposedCall(
                tool_name="element.split",
                provider_profile=request.provider_profile,
                input_asset_ids=(
                    request.source_asset_id,
                    request.selection_id,
                    request.prompt_asset_id,
                ),
            )
        )
        return decision is ApprovalDecision.APPROVED
