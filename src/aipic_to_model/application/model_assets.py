"""Managed GLB import, inspection, and B04 preview registration primitives."""

from __future__ import annotations

import math
import mimetypes
from pathlib import Path
from typing import Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..domain.errors import DomainErrorV1, ErrorCode
from ..domain.ids import new_id
from ..domain.production_models import Capability, ModelInspection
from .assets import AssetService
from .host_capabilities import HostCapabilityStore
from .model_inspection import MAX_GLB_BYTES, inspect_glb, validate_glb_bytes
from .ports import ModelAssetRepositoryPort

MAX_PREVIEW_BYTES = 20 * 1024 * 1024


class PreviewCamera(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    projection: Literal["perspective", "orthographic"] = "perspective"
    position: tuple[float, float, float]
    target: tuple[float, float, float]
    fov_degrees: float = Field(ge=5, le=120)

    @field_validator("position", "target")
    @classmethod
    def _finite_coordinates(cls, value: tuple[float, float, float]) -> tuple[float, float, float]:
        if not all(math.isfinite(item) for item in value):
            raise ValueError("camera coordinates must be finite")
        return value


class PreviewRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    view: Literal["front", "side", "back", "top"]
    camera: PreviewCamera


class ModelAssetService:
    """Only IDs and whitelisted DTOs cross this service's public boundary."""

    def __init__(self, assets: AssetService, repository: ModelAssetRepositoryPort) -> None:
        self._assets = assets
        self._repository = repository

    def import_staged(
        self,
        root: Path,
        project_id: str,
        staged_file_id: str,
        capabilities: HostCapabilityStore,
        request_id: str,
    ) -> dict[str, object]:
        source = capabilities.resolve_once(staged_file_id, "model3d.import_local", project_id)
        guessed_mime = mimetypes.guess_type(source.name)[0]
        if (
            source.suffix.lower() != ".glb"
            or guessed_mime not in {"model/gltf-binary", "application/octet-stream"}
            or not source.is_file()
            or source.is_symlink()
            or source.stat().st_size > MAX_GLB_BYTES
        ):
            raise DomainErrorV1(ErrorCode.MODEL3D_PARSE_FAILED, "导入文件不是受支持的 GLB 模型。")
        try:
            content = source.read_bytes()
            validate_glb_bytes(content)
        except (OSError, ValueError) as error:
            raise DomainErrorV1(
                ErrorCode.MODEL3D_PARSE_FAILED, "GLB 文件真实性校验失败。"
            ) from error
        temporary = root / "temp" / f"validated-model-{new_id()}.glb"
        try:
            temporary.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(content)
            registered = self._assets.register_derived(
                root,
                project_id,
                temporary,
                "glb",
                request_id,
                name="model.glb",
                provenance={"source_kind": "import"},
            )
        finally:
            temporary.unlink(missing_ok=True)
        inspection = self.inspect(root, project_id, str(registered["id"]))
        return {"asset": registered, "inspection": inspection.model_dump(mode="json")}

    def inspect(self, root: Path, project_id: str, asset_id: str) -> ModelInspection:
        asset = self._assets.get(root, project_id, asset_id)
        if asset["asset_type"] != "glb":
            raise DomainErrorV1(ErrorCode.MODEL3D_PARSE_FAILED, "只能检查受管 GLB 资产。")
        relative_path = self._repository.relative_path(
            root / "project.sqlite3", project_id, asset_id
        )
        if relative_path is None:
            raise DomainErrorV1(ErrorCode.ASSET_NOT_FOUND, "GLB 资产不存在。")
        _, content, _, _ = self._assets.read_content(root, project_id, asset_id, None)
        provenance = asset.get("provenance", {})
        source_job_id = provenance.get("source_job_id") if isinstance(provenance, dict) else None
        if not isinstance(source_job_id, str) and isinstance(provenance, dict):
            parameters = provenance.get("parameters")
            source_job_id = (
                parameters.get("source_job_id") if isinstance(parameters, dict) else None
            )
        inspection = inspect_glb(
            content,
            local_relative_path=relative_path,
            source_job_id=source_job_id if isinstance(source_job_id, str) else None,
        )
        if not self._repository.store_inspection(
            root / "project.sqlite3", project_id, asset_id, inspection.model_dump(mode="json")
        ):
            raise DomainErrorV1(ErrorCode.ASSET_NOT_FOUND, "GLB 资产不存在。")
        return inspection

    def preview_renderer_capability(self, *, available: bool) -> Capability:
        return Capability(
            available=available,
            reason=None if available else "No approved local PreviewRenderer is available.",
            tool_name="model3d.render_preview",
        )

    def register_preview(
        self,
        root: Path,
        project_id: str,
        model_asset_id: str,
        image_bytes: bytes,
        request: PreviewRegistration,
        request_id: str,
    ) -> dict[str, object]:
        model = self._assets.get(root, project_id, model_asset_id)
        if model["asset_type"] != "glb":
            raise DomainErrorV1(ErrorCode.MODEL3D_PARSE_FAILED, "预览必须关联受管 GLB 资产。")
        if (
            not image_bytes
            or len(image_bytes) > MAX_PREVIEW_BYTES
            or image_bytes[:8] != b"\x89PNG\r\n\x1a\n"
        ):
            raise DomainErrorV1(ErrorCode.INVALID_ASSET_CONTENT, "预览必须是大小受限的 PNG 文件。")
        temporary = root / "temp" / f"preview-{new_id()}.png"
        try:
            temporary.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(image_bytes)
            with Image.open(temporary) as image:
                image.verify()
                if image.format != "PNG":
                    raise ValueError("not PNG")
            return self._assets.register_derived(
                root,
                project_id,
                temporary,
                "preview",
                request_id,
                parent_asset_id=model_asset_id,
                input_asset_ids=[model_asset_id],
                name=f"{request.view}-preview.png",
                provenance={
                    "source_kind": "tool",
                    "parameters": {
                        "view": request.view,
                        "camera": request.camera.model_dump(mode="json"),
                    },
                },
            )
        except (OSError, ValueError) as error:
            raise DomainErrorV1(ErrorCode.INVALID_ASSET_CONTENT, "预览 PNG 内容无效。") from error
        finally:
            temporary.unlink(missing_ok=True)
