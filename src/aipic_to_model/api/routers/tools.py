"""Canonical Tool invocation adapter."""

from fastapi import APIRouter, Depends

from ..contracts import ToolInvokeRequest
from ..dependencies import AppDependencies
from ..security import OriginGuard


def build_tool_router(guard: OriginGuard, dependencies: AppDependencies) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/tools/invoke", dependencies=[Depends(guard.check)])
    def invoke_tool(body: ToolInvokeRequest):
        result = dependencies.registry.execute(
            dependencies.root_for(body.project_id),
            body.project_id,
            body.tool_name,
            body.tool_version,
            body.arguments,
            body.request_id,
            body.run_id,
            body.round_index,
            body.provider_profile,
        )
        if result.status == "queued" and dependencies.job_runner is not None:
            dependencies.job_runner.wake()
        return result.__dict__

    return router
