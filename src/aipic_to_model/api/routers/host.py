"""Private host-to-sidecar bridge for issuing one-time file capabilities."""

from pathlib import Path

from fastapi import APIRouter, Depends

from ..contracts import HostCapabilityIssueRequest, HostRecentCapabilityRequest
from ..dependencies import AppDependencies
from ..security import HostControlGuard, OriginGuard
from ...application.host_capabilities import HostCapabilityStore


def build_host_router(
    origin_guard: OriginGuard, host_guard: HostControlGuard, capabilities: HostCapabilityStore,
    dependencies: AppDependencies,
) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/v1/host/capabilities",
        dependencies=[Depends(origin_guard.check), Depends(host_guard.check)],
    )
    def issue_capability(body: HostCapabilityIssueRequest):
        """Only the native host holds the additional control token required here."""
        return {"capability_id": capabilities.issue(Path(body.path), body.operation, body.project_id)}

    @router.post(
        "/v1/host/recent-capabilities",
        dependencies=[Depends(origin_guard.check), Depends(host_guard.check)],
    )
    def issue_recent_capability(body: HostRecentCapabilityRequest):
        root = dependencies.app_state.recent_project_root(dependencies.app_db, body.recent_project_id)
        if root is None:
            return {"capability_id": None}
        return {"capability_id": capabilities.issue(root, "open")}

    return router
