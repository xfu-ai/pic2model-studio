"""Controlled registration endpoint for B04-produced local WebGL previews."""

from __future__ import annotations

import base64

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from ...application.model_assets import ModelAssetService, PreviewRegistration
from ...infrastructure.sqlite.model_repository import SqliteModelAssetRepository
from ..dependencies import AppDependencies
from ..security import OriginGuard


class PreviewRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    preview_png_base64: str = Field(min_length=1)
    registration: PreviewRegistration


class CreateMultiviewSetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_asset_id: str = Field(min_length=1)
    view_asset_ids: dict[str, str]
    request_id: str = Field(min_length=1)


def build_model_router(guard: OriginGuard, dependencies: AppDependencies) -> APIRouter:
    router = APIRouter()
    service = ModelAssetService(dependencies.assets, SqliteModelAssetRepository())

    @router.post("/v1/assets/{asset_id}/previews", dependencies=[Depends(guard.check)])
    def register_preview(asset_id: str, body: PreviewRegisterRequest, project_id: str):
        try:
            content = base64.b64decode(body.preview_png_base64, validate=True)
        except ValueError as error:
            from ...domain.errors import DomainErrorV1, ErrorCode

            raise DomainErrorV1(ErrorCode.INVALID_ASSET_CONTENT, "预览编码无效。") from error
        return service.register_preview(
            dependencies.root_for(project_id),
            project_id,
            asset_id,
            content,
            body.registration,
            body.request_id,
        )

    @router.post("/v1/projects/{project_id}/multiview-sets", dependencies=[Depends(guard.check)])
    def create_multiview_set(project_id: str, body: CreateMultiviewSetRequest):
        set_id = dependencies.multiview.create_from_existing_views(
            dependencies.root_for(project_id), project_id, source_asset_id=body.source_asset_id,
            members=body.view_asset_ids, request_id=body.request_id,
        )
        return {"id": set_id}

    return router
