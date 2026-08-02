"""Redacted diagnostics preview and export routes."""

from fastapi import APIRouter, Depends

from ...domain.build_info import about
from ...domain.common import DomainErrorV1, ErrorCode
from ..contracts import (
    DiagnosticExportRequest,
    DiagnosticPreviewRequest,
)
from ..dependencies import AppDependencies
from ..security import OriginGuard


def build_diagnostics_router(guard: OriginGuard, dependencies: AppDependencies) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/diagnostics/preview", dependencies=[Depends(guard.check)])
    def diagnostic_preview(body: DiagnosticPreviewRequest):
        return dependencies.diagnostics.preview(dependencies.root_for(body.project_id), about())

    @router.post("/v1/diagnostics/export", dependencies=[Depends(guard.check)])
    def diagnostic_export(body: DiagnosticExportRequest):
        destination = dependencies.capabilities.resolve_once(
            body.export_capability_id, "diagnostic_export", body.project_id
        )
        if not destination.is_dir():
            raise DomainErrorV1(
                ErrorCode.SECURITY_CAPABILITY_INVALID,
                "Diagnostic export destination must be a folder.",
            )
        return dependencies.diagnostics.export(
            dependencies.root_for(body.project_id),
            destination / "AIPicToModel-diagnostics.zip",
            body.confirmed_manifest_hash,
            about(),
        )

    return router
