"""Approval-first image generation request preparation for B02 Job handlers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..domain.provider_models import GenerationRequest, ProviderResult
from .approval import ApprovalDecision, ApprovalGate, ProposedCall


class ImageGenerationProvider(Protocol):
    def generate(self, request: dict[str, object]) -> ProviderResult: ...


@dataclass(frozen=True)
class GenerationPreparation:
    request: GenerationRequest
    approved: bool
    requires_approval: bool


class ImageGenerationService:
    def __init__(self, provider: ImageGenerationProvider, approval_gate: ApprovalGate) -> None:
        self._provider = provider
        self._approval_gate = approval_gate

    def prepare(self, request: GenerationRequest) -> GenerationPreparation:
        decision = self._approval_gate.authorize(
            ProposedCall(
                tool_name={"t2i": "image.generate", "i2i": "image.transform"}[request.mode],
                provider_profile=request.provider_profile,
                input_asset_ids=tuple(
                    asset_id
                    for asset_id in (request.prompt_asset_id, request.source_asset_id)
                    if asset_id
                ),
            )
        )
        return GenerationPreparation(
            request=request,
            approved=decision is ApprovalDecision.APPROVED,
            requires_approval=decision is not ApprovalDecision.APPROVED,
        )

    def submit(self, preparation: GenerationPreparation) -> ProviderResult:
        if not preparation.approved:
            raise PermissionError("APPROVAL_REQUIRED")
        return self._provider.generate(preparation.request.model_dump(mode="json"))
