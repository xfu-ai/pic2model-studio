"""Selection HTTP adapters; geometry and durable state remain in SelectionService."""

from fastapi import APIRouter, Depends

from ..contracts import (
    CancelSelectionStepRequest,
    ProjectRequest,
    SelectionConfirmRequest,
    SelectionSaveRequest,
    SelectionUpdateRequest,
)
from ..dependencies import AppDependencies
from ..security import OriginGuard


def build_selection_router(guard: OriginGuard, dependencies: AppDependencies) -> APIRouter:
    router = APIRouter()
    selections = dependencies.selections

    @router.post("/v1/assets/{asset_id}/selections", dependencies=[Depends(guard.check)])
    def save_selection(asset_id: str, body: SelectionSaveRequest):
        return selections.save(
            dependencies.root_for(body.project_id),
            body.project_id,
            asset_id,
            body.rects,
            body.label,
            body.source,
            body.status,
            body.selection_id,
            body.expected_revision,
            body.request_id,
            body.confidence,
        )

    @router.get("/v1/assets/{asset_id}/selections", dependencies=[Depends(guard.check)])
    def list_selections(asset_id: str, project_id: str):
        return selections.list_for_asset(dependencies.root_for(project_id), project_id, asset_id)

    @router.patch("/v1/selections/{selection_id}", dependencies=[Depends(guard.check)])
    def update_selection(selection_id: str, body: SelectionUpdateRequest):
        current = selections.get(
            dependencies.root_for(body.project_id), body.project_id, selection_id
        )
        return selections.save(
            dependencies.root_for(body.project_id),
            body.project_id,
            current["asset_id"],
            body.rects,
            body.label or current["label"],
            body.source or current["source"],
            body.status,
            selection_id,
            body.expected_revision,
            body.request_id,
        )

    @router.post("/v1/selections/{selection_id}/confirm", dependencies=[Depends(guard.check)])
    def confirm_selection(selection_id: str, body: SelectionConfirmRequest):
        return selections.confirm(
            dependencies.root_for(body.project_id),
            body.project_id,
            selection_id,
            body.expected_revision,
            body.request_id,
        )

    @router.post("/v1/selections/{selection_id}/crop", dependencies=[Depends(guard.check)])
    def crop_selection(selection_id: str, body: ProjectRequest):
        return selections.crop(
            dependencies.root_for(body.project_id), body.project_id, selection_id, body.request_id
        )

    @router.post(
        "/v1/selections/{selection_id}/render-annotation", dependencies=[Depends(guard.check)]
    )
    def annotate_selection(selection_id: str, body: ProjectRequest):
        return selections.render_annotation(
            dependencies.root_for(body.project_id), body.project_id, selection_id, body.request_id
        )

    @router.post("/v1/selection-actions/cancel", dependencies=[Depends(guard.check)])
    def cancel_selection(body: CancelSelectionStepRequest):
        selections.cancel_step(
            dependencies.root_for(body.project_id),
            body.project_id,
            body.selection_id,
            body.action_id,
            body.run_id,
        )
        return {"cancelled": True, "action_id": body.action_id}

    return router
