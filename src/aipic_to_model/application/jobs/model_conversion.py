"""Controlled local model conversion, optimization capability, and packaging."""

from __future__ import annotations

import json
import shutil
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from ...application.assets import AssetService
from ...domain.errors import DomainErrorV1, ErrorCode
from ...domain.ids import new_id
from ...domain.production_models import Capability
from ...domain.provider_models import ProviderResult
from ..model_inspection import validate_glb_bytes

CONVERSION_TIMEOUT_SECONDS = 180
_ALLOWED_PACKAGE_TYPES = frozenset({"glb", "fbx", "texture", "preview"})


@dataclass(frozen=True)
class BackendAttempt:
    backend: Literal["blender", "geometry_fbx"]
    status: Literal["succeeded", "failed", "skipped"]
    summary: str


class ConversionBackend(Protocol):
    @property
    def name(self) -> Literal["blender", "geometry_fbx"]: ...

    def convert(
        self, source: Path, destination: Path, *, timeout_seconds: int
    ) -> BackendAttempt: ...


class ModelOptimizer(Protocol):
    def optimize(
        self,
        content: bytes,
        *,
        target_triangles: int | None,
        max_texture_bytes: int | None,
        on_progress: Callable[[str, int, dict[str, int]], None] | None = None,
    ) -> ProviderResult: ...


def _validate_fbx(path: Path) -> None:
    if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
        raise ValueError("empty conversion output")
    prefix = path.read_bytes()[:32]
    if not prefix.startswith((b"Kaydara FBX Binary", b"; FBX")):
        raise ValueError("unrecognised FBX output")


def _safe_remove_workdir(root: Path, workdir: Path) -> None:
    resolved_root, resolved_workdir = root.resolve(), workdir.resolve()
    if resolved_workdir.parent != (resolved_root / "temp").resolve() or not workdir.name.startswith(
        "conversion-"
    ):
        raise RuntimeError("refusing to remove unmanaged conversion directory")
    shutil.rmtree(workdir)


class ModelConversionService:
    """Converts asset IDs through Blender, then a geometry-only fallback."""

    def __init__(
        self,
        assets: AssetService,
        backends: Sequence[ConversionBackend] | Callable[[], Sequence[ConversionBackend]],
    ) -> None:
        expected = ["blender", "geometry_fbx"]
        selected = tuple(backends() if callable(backends) else backends)
        if [backend.name for backend in selected] != expected:
            raise ValueError("conversion backend order must be Blender, Geometry FBX")
        self._assets = assets
        self._backends = backends

    def _configured_backends(self) -> tuple[ConversionBackend, ...]:
        backends = tuple(self._backends() if callable(self._backends) else self._backends)
        if [backend.name for backend in backends] != ["blender", "geometry_fbx"]:
            raise RuntimeError("conversion backend order changed after service initialization")
        return backends

    def convert(
        self,
        root: Path,
        project_id: str,
        glb_asset_id: str,
        *,
        target_format: Literal["fbx"],
        request_id: str,
    ) -> tuple[dict[str, object] | None, list[BackendAttempt]]:
        if target_format != "fbx":
            raise DomainErrorV1(ErrorCode.TOOL_ARGUMENT_INVALID, "仅支持转换为 FBX。")
        source_asset = self._assets.get(root, project_id, glb_asset_id)
        if source_asset["asset_type"] != "glb":
            raise DomainErrorV1(ErrorCode.MODEL3D_PARSE_FAILED, "转换输入必须是受管 GLB。")
        original_hash = str(source_asset["sha256"])
        _, content, _, _ = self._assets.read_content(root, project_id, glb_asset_id, None)
        validate_glb_bytes(content)
        workdir = root / "temp" / f"conversion-{new_id()}"
        source, output = workdir / "input.glb", workdir / "output.fbx"
        attempts: list[BackendAttempt] = []
        try:
            workdir.mkdir(parents=True, exist_ok=False)
            source.write_bytes(content)
            for backend in self._configured_backends():
                attempt = backend.convert(
                    source, output, timeout_seconds=CONVERSION_TIMEOUT_SECONDS
                )
                attempts.append(attempt)
                if attempt.status != "succeeded":
                    continue
                try:
                    _validate_fbx(output)
                except OSError, ValueError:
                    attempts[-1] = BackendAttempt(
                        backend.name, "failed", "Output validation failed."
                    )
                    output.unlink(missing_ok=True)
                    continue
                result = self._assets.register_derived(
                    root,
                    project_id,
                    output,
                    "fbx",
                    request_id,
                    parent_asset_id=glb_asset_id,
                    input_asset_ids=[glb_asset_id],
                    name="model.fbx",
                    provenance={
                        "source_kind": "conversion",
                        "model": backend.name,
                        "parameters": {
                            "target_format": "fbx",
                            "timeout_seconds": CONVERSION_TIMEOUT_SECONDS,
                        },
                    },
                )
                current = self._assets.get(root, project_id, glb_asset_id)
                if str(current["sha256"]) != original_hash:
                    raise RuntimeError("conversion altered the original GLB")
                return result, attempts
            return None, attempts
        finally:
            if workdir.exists():
                _safe_remove_workdir(root, workdir)


class ModelOptimizationService:
    def __init__(self, assets: AssetService, optimizer: ModelOptimizer | None) -> None:
        self._assets, self._optimizer = assets, optimizer

    def capability(self) -> Capability:
        return Capability(
            available=self._optimizer is not None,
            reason=None
            if self._optimizer is not None
            else "No approved model optimization provider is available.",
            tool_name="model3d.optimize",
        )

    def optimize(
        self,
        root: Path,
        project_id: str,
        glb_asset_id: str,
        *,
        target_triangles: int | None,
        max_texture_bytes: int | None,
        request_id: str,
        on_progress: Callable[[str, int, dict[str, int]], None] | None = None,
    ) -> dict[str, object]:
        if self._optimizer is None:
            raise DomainErrorV1(ErrorCode.TOOL_NOT_ALLOWED, "当前环境不支持模型优化。")
        if target_triangles is not None and not 1 <= target_triangles <= 10_000_000:
            raise DomainErrorV1(ErrorCode.TOOL_ARGUMENT_INVALID, "目标三角面数超出允许范围。")
        if max_texture_bytes is not None and not 1 <= max_texture_bytes <= 200 * 1024 * 1024:
            raise DomainErrorV1(ErrorCode.TOOL_ARGUMENT_INVALID, "纹理大小上限超出允许范围。")
        asset = self._assets.get(root, project_id, glb_asset_id)
        if asset["asset_type"] != "glb":
            raise DomainErrorV1(ErrorCode.MODEL3D_PARSE_FAILED, "优化输入必须是受管 GLB。")
        _, content, _, _ = self._assets.read_content(root, project_id, glb_asset_id, None)
        response = self._optimizer.optimize(
            content,
            target_triangles=target_triangles,
            max_texture_bytes=max_texture_bytes,
            on_progress=on_progress,
        )
        if not response.ok:
            message = (
                response.error.user_message
                if response.error is not None
                else "The local optimizer could not safely optimize this GLB."
            )
            raise DomainErrorV1(ErrorCode.MODEL3D_PARSE_FAILED, message)
        optimized = response.payload.get("glb_bytes") if response.ok else None
        if not isinstance(optimized, bytes):
            raise DomainErrorV1(ErrorCode.MODEL3D_PARSE_FAILED, "优化未产生有效 GLB。")
        validate_glb_bytes(optimized)
        temporary = root / "temp" / f"optimized-{new_id()}.glb"
        try:
            temporary.write_bytes(optimized)
            return self._assets.register_derived(
                root,
                project_id,
                temporary,
                "glb",
                request_id,
                parent_asset_id=glb_asset_id,
                input_asset_ids=[glb_asset_id],
                provenance={
                    "source_kind": "conversion",
                    "model": "optimizer",
                    "parameters": {
                        "target_triangles": target_triangles,
                        "max_texture_bytes": max_texture_bytes,
                    },
                },
            )
        finally:
            temporary.unlink(missing_ok=True)


class ModelPackageService:
    def __init__(self, assets: AssetService) -> None:
        self._assets = assets

    def package(
        self, root: Path, project_id: str, asset_ids: list[str], *, request_id: str
    ) -> dict[str, object]:
        if not 1 <= len(asset_ids) <= 32 or len(asset_ids) != len(set(asset_ids)):
            raise DomainErrorV1(ErrorCode.TOOL_ARGUMENT_INVALID, "模型打包资产列表无效。")
        entries: list[tuple[str, bytes, str]] = []
        for asset_id in asset_ids:
            asset = self._assets.get(root, project_id, asset_id)
            if asset["asset_type"] not in _ALLOWED_PACKAGE_TYPES:
                raise DomainErrorV1(
                    ErrorCode.TOOL_ARGUMENT_INVALID, "打包只支持受管模型和预览资产。"
                )
            _, content, _, _ = self._assets.read_content(root, project_id, asset_id, None)
            suffix = {"glb": "glb", "fbx": "fbx", "texture": "bin", "preview": "png"}[
                asset["asset_type"]
            ]
            entries.append((f"assets/{asset_id}.{suffix}", content, str(asset["sha256"])))
        temporary = root / "temp" / f"model-package-{new_id()}.zip"
        try:
            manifest = {
                "schema_version": 1,
                "assets": [{"path": path, "sha256": digest} for path, _, digest in entries],
            }
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", json.dumps(manifest, separators=(",", ":")))
                for path, content, _ in entries:
                    archive.writestr(path, content)
            return self._assets.register_derived(
                root,
                project_id,
                temporary,
                "export",
                request_id,
                input_asset_ids=asset_ids,
                provenance={
                    "source_kind": "tool",
                    "parameters": {"package_asset_count": len(asset_ids)},
                },
            )
        finally:
            temporary.unlink(missing_ok=True)
