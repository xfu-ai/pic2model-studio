import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from fastapi import APIRouter, Depends, Header
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from ..contracts import AssetActionRequest, CompareAssetsRequest, ImportAssetRequest, SetCurrentRequest, TrashRequest
from ..dependencies import AppDependencies
from ..security import OriginGuard


class ExportAssetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(min_length=1)
    export_capability_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)


def _open_managed_asset_directory(path: Path) -> None:
    """Open a containing directory without returning native paths to the renderer."""
    if os.environ.get("AIPIC_CONTROLLED_E2E") == "1":
        return
    directory = path.parent
    if sys.platform == "win32":
        subprocess.Popen(
            ["explorer.exe", str(directory)],
            close_fds=True,
        )
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(directory)], close_fds=True)
    else:
        subprocess.Popen(["xdg-open", str(directory)], close_fds=True)


def build_asset_router(guard: OriginGuard, dependencies: AppDependencies) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/projects/{project_id}/assets/import", dependencies=[Depends(guard.check)])
    def import_asset(project_id: str, body: ImportAssetRequest):
        source = dependencies.capabilities.resolve_once(
            body.file_capability_id, "import", project_id
        )
        return dependencies.assets.import_file(
            dependencies.root_for(project_id),
            project_id,
            source,
            body.asset_type,
            body.request_id,
            body.name,
            body.parent_asset_id,
        ).copy()

    @router.get("/v1/projects/{project_id}/assets", dependencies=[Depends(guard.check)])
    def list_assets(
        project_id: str,
        include_trashed: bool = False,
        include_hidden: bool = True,
        include_visual_identities: bool = False,
    ):
        return dependencies.assets.list_by_group(
            dependencies.root_for(project_id), project_id, include_trashed=include_trashed,
            include_hidden=include_hidden,
            include_visual_identities=include_visual_identities,
        )

    @router.get("/v1/assets/{asset_id}", dependencies=[Depends(guard.check)])
    def get_asset(asset_id: str, project_id: str):
        return dependencies.assets.get(dependencies.root_for(project_id), project_id, asset_id)

    @router.get("/v1/assets/{asset_id}/lineage", dependencies=[Depends(guard.check)])
    def lineage(asset_id: str, project_id: str):
        return dependencies.assets.lineage(dependencies.root_for(project_id), project_id, asset_id)

    @router.get("/v1/assets/{asset_id}/impact", dependencies=[Depends(guard.check)])
    def impact(asset_id: str, project_id: str):
        return dependencies.assets.impact(dependencies.root_for(project_id), project_id, asset_id)

    @router.post("/v1/assets/compare", dependencies=[Depends(guard.check)])
    def compare(body: CompareAssetsRequest):
        return dependencies.assets.compare_siblings(
            dependencies.root_for(body.project_id), body.project_id, body.left_id, body.right_id
        )

    @router.get("/v1/assets/{asset_id}/content", dependencies=[Depends(guard.check)])
    def asset_content(
        asset_id: str,
        project_id: str,
        range_header: str | None = Header(default=None, alias="Range"),
    ):
        status, data, mime_type, headers = dependencies.assets.read_content(
            dependencies.root_for(project_id), project_id, asset_id, range_header
        )
        return Response(data, status_code=status, media_type=mime_type, headers=headers)

    @router.get("/v1/assets/{asset_id}/thumbnail", dependencies=[Depends(guard.check)])
    def asset_thumbnail(asset_id: str, project_id: str):
        data, mime_type, headers = dependencies.assets.read_thumbnail(
            dependencies.root_for(project_id), project_id, asset_id
        )
        return Response(data, media_type=mime_type, headers=headers)

    @router.post("/v1/assets/{asset_id}/export", dependencies=[Depends(guard.check)])
    def export_asset(asset_id: str, body: ExportAssetRequest):
        destination = dependencies.capabilities.resolve_once(
            body.export_capability_id, "export", body.project_id
        )
        if not destination.is_dir():
            from ...domain.errors import DomainErrorV1, ErrorCode

            raise DomainErrorV1(ErrorCode.SECURITY_CAPABILITY_INVALID, "导出目标必须是文件夹。")
        root = dependencies.root_for(body.project_id)
        asset = dependencies.assets.get(root, body.project_id, asset_id)
        _, data, _, _ = dependencies.assets.read_content(
            root, body.project_id, asset_id, None
        )
        safe_name = Path(str(asset["name"])).name
        suffix = str(asset["asset_type"]).lower()
        if suffix in {"fbx", "glb"} and not safe_name.lower().endswith(f".{suffix}"):
            safe_name = f"{safe_name}.{suffix}"
        target = destination / safe_name
        temporary = destination / f".{safe_name}.{uuid4().hex}.part"
        try:
            temporary.write_bytes(data)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return {"asset_id": asset_id, "name": safe_name, "bytes": len(data)}

    @router.post("/v1/assets/{asset_id}/reveal", dependencies=[Depends(guard.check)])
    def reveal_asset(asset_id: str, body: AssetActionRequest):
        root = dependencies.root_for(body.project_id)
        path = dependencies.assets.managed_file_for_host(root, body.project_id, asset_id)
        try:
            _open_managed_asset_directory(path)
        except OSError as error:
            from ...domain.errors import DomainErrorV1, ErrorCode

            raise DomainErrorV1(
                ErrorCode.LOCAL_STORAGE_UNAVAILABLE,
                "无法打开资产所在目录。",
                True,
            ) from error
        return {"asset_id": asset_id, "opened": True}

    @router.post("/v1/assets/{asset_id}/set-current", dependencies=[Depends(guard.check)])
    def set_current(asset_id: str, body: SetCurrentRequest):
        return dependencies.assets.set_current(
            dependencies.root_for(body.project_id),
            body.project_id,
            asset_id,
            body.decision_source,
            body.request_id,
            body.reason,
        )

    @router.post("/v1/assets/{asset_id}/hide", dependencies=[Depends(guard.check)])
    def hide(asset_id: str, body: AssetActionRequest):
        return dependencies.assets.hide(
            dependencies.root_for(body.project_id), body.project_id, asset_id, True, body.request_id
        )

    @router.post("/v1/assets/{asset_id}/restore-hidden", dependencies=[Depends(guard.check)])
    def restore_hidden(asset_id: str, body: AssetActionRequest):
        return dependencies.assets.hide(
            dependencies.root_for(body.project_id),
            body.project_id,
            asset_id,
            False,
            body.request_id,
        )

    @router.post("/v1/assets/{asset_id}/trash", dependencies=[Depends(guard.check)])
    def trash(asset_id: str, body: TrashRequest):
        return dependencies.assets.trash(
            dependencies.root_for(body.project_id),
            body.project_id,
            asset_id,
            body.impact_token,
            body.request_id,
        )

    @router.post("/v1/assets/{asset_id}/restore", dependencies=[Depends(guard.check)])
    def restore(asset_id: str, body: AssetActionRequest):
        return dependencies.assets.restore_from_trash(
            dependencies.root_for(body.project_id), body.project_id, asset_id, body.request_id
        )

    return router
