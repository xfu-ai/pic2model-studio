"""Parameter-bound production approval decisions."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from ..dependencies import AppDependencies
from ..security import OriginGuard


class ApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    approved: bool


def build_approval_router(guard: OriginGuard, dependencies: AppDependencies) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/approvals/{approval_id}/decision", dependencies=[Depends(guard.check)])
    def decide(approval_id: str, body: ApprovalDecisionRequest):
        result = dependencies.b02_runtime.decide_approval(
            dependencies.root_for(body.project_id),
            body.project_id,
            approval_id,
            approved=body.approved,
        )
        if body.approved and dependencies.job_runner is not None:
            dependencies.job_runner.wake()
        return result.__dict__

    return router
