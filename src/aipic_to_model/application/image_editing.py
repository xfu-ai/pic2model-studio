"""Capability-gated B02 image editing requests.

Provider calls remain behind the Job/approval boundary; this layer never
substitutes an unapproved paid service when a requested capability is absent.
"""

from __future__ import annotations

from dataclasses import dataclass

from .approval import ApprovalDecision, ApprovalGate, ProposedCall


@dataclass(frozen=True)
class ImageProviderCapabilities:
    upscale: bool = False
    remove_background: bool = False
    inpaint_selection: bool = False


@dataclass(frozen=True)
class EditRequest:
    source_asset_id: str
    provider_profile: str
    operation: str
    selection_id: str | None = None
    selection_confirmed: bool = False
    scale: int | None = None
    prompt_asset_id: str | None = None


@dataclass(frozen=True)
class EditPreparation:
    available: bool
    reason: str | None
    approved: bool


class ImageEditService:
    def __init__(
        self, capabilities: ImageProviderCapabilities, approval_gate: ApprovalGate
    ) -> None:
        self._capabilities = capabilities
        self._approval_gate = approval_gate

    def prepare(self, request: EditRequest) -> EditPreparation:
        if request.operation not in {"upscale", "remove_background", "inpaint_selection"}:
            raise ValueError("unsupported image edit operation")
        available = bool(getattr(self._capabilities, request.operation))
        if not available:
            return EditPreparation(False, "capability_unavailable", False)
        if request.operation == "upscale" and request.scale not in {2, 4}:
            raise ValueError("upscale scale must be 2 or 4")
        if request.operation == "inpaint_selection":
            if not request.selection_id or not request.selection_confirmed:
                raise PermissionError("APPROVAL_UI_CONFIRMATION_REQUIRED")
            if not request.prompt_asset_id:
                raise ValueError("inpaint selection requires a prompt asset")
        decision = self._approval_gate.authorize(
            ProposedCall(
                tool_name=f"image.{request.operation}",
                provider_profile=request.provider_profile,
                input_asset_ids=tuple(
                    item
                    for item in (
                        request.source_asset_id,
                        request.selection_id,
                        request.prompt_asset_id,
                    )
                    if item
                ),
            )
        )
        return EditPreparation(True, None, decision is ApprovalDecision.APPROVED)
