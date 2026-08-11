from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import time
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from ..domain.common import DomainErrorV1, ErrorCode, new_id, utc_now
from .operations import OperationService
from .ports import AssetRepositoryPort, FilesystemPort

GROUPS = {
    "source_image": "input_images",
    "generated_image": "generated_images",
    "annotation": "split_elements",
    "crop": "split_elements",
    "multiview": "multiview_and_crops",
    "glb": "models",
    "fbx": "models",
    "texture": "models",
    "export": "exports",
}

IMPACT_TOKEN_TTL_SECONDS = 60
IMAGE_ASSET_TYPES = {
    "source_image",
    "generated_image",
    "annotation",
    "crop",
    "multiview",
    "preview",
    "texture",
}


def _safe_storage_error(error: OSError) -> DomainErrorV1:
    message = str(error).lower()
    if error.errno in {28, 30} or "disk full" in message or "no space" in message:
        return DomainErrorV1(
            ErrorCode.LOCAL_STORAGE_UNAVAILABLE,
            "本地存储空间不足，导入未完成。",
            True,
            retry_after_seconds=5,
        )
    if "read-only" in message or "access is denied" in message:
        return DomainErrorV1(
            ErrorCode.PROJECT_READ_ONLY,
            "项目目录不可写。",
            True,
            retry_after_seconds=5,
        )
    return DomainErrorV1(
        ErrorCode.LOCAL_STORAGE_UNAVAILABLE,
        "本地存储不可用，导入未完成。",
        True,
        retry_after_seconds=5,
    )


def _hash(path: Path) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _pack_bits(bits: list[bool]) -> bytes:
    packed = bytearray()
    value = 0
    for index, bit in enumerate(bits, start=1):
        value = (value << 1) | int(bit)
        if index % 8 == 0:
            packed.append(value)
            value = 0
    return bytes(packed)


def _visual_identity(path: Path) -> tuple[str, float]:
    """Return a scale-insensitive visual identity without exposing image content."""
    with Image.open(path) as source:
        image = source.convert("RGBA")
    alpha_bounds = image.getchannel("A").getbbox()
    if alpha_bounds is not None:
        image = image.crop(alpha_bounds)
    if image.width <= 0 or image.height <= 0:
        raise ValueError("image has no visible pixels")

    def values(sample: Image.Image, channel: int) -> list[int]:
        result: list[int] = []
        for pixel in sample.get_flattened_data():
            alpha = int(pixel[3])
            result.append(alpha if channel == 3 else (int(pixel[channel]) * alpha + 127) // 255)
        return result

    difference_sample = image.resize((9, 8), Image.Resampling.LANCZOS)
    absolute_sample = image.resize((8, 8), Image.Resampling.LANCZOS)
    bits: list[bool] = []
    for channel in range(4):
        difference = values(difference_sample, channel)
        bits.extend(
            difference[row * 9 + column] > difference[row * 9 + column + 1]
            for row in range(8)
            for column in range(8)
        )
        absolute = values(absolute_sample, channel)
        mean = sum(absolute) / len(absolute)
        bits.extend(mean > threshold for threshold in range(2, 256, 4))
    return _pack_bits(bits).hex(), round(image.width / image.height, 4)


def _row(row: Any) -> dict[str, Any]:
    result = dict(row)
    result.pop("relative_path", None)
    result["metadata"] = json.loads(result.pop("metadata_json"))
    result["provenance"] = json.loads(result.pop("provenance_json"))
    result["is_current"] = bool(result["is_current"])
    result["is_hidden"] = bool(result["is_hidden"])
    result["group"] = result.pop("asset_group")
    return result


class AssetService:
    def __init__(
        self,
        repository: AssetRepositoryPort,
        filesystem: FilesystemPort,
        operations: OperationService,
    ) -> None:
        self._repository = repository
        self._filesystem = filesystem
        self._operations = operations
        self._visual_identity_cache: dict[str, tuple[str, float]] = {}

    def import_file(
        self,
        root: Path,
        project_id: str,
        source: Path,
        asset_type: str,
        request_id: str,
        name: str | None = None,
        parent_asset_id: str | None = None,
    ) -> dict[str, Any]:
        self._filesystem.require_writable_root(root)
        if not source.is_file() or source.is_symlink():
            raise DomainErrorV1(ErrorCode.INVALID_ASSET_CONTENT, "导入文件无效。")
        if source.stat().st_size > 200 * 1024 * 1024:
            raise DomainErrorV1(ErrorCode.INVALID_ASSET_CONTENT, "导入文件过大。")
        metadata: dict[str, Any] = {}
        mime = "text/plain" if asset_type == "prompt" else ""
        if asset_type == "prompt":
            try:
                source.read_text(encoding="utf-8")
            except UnicodeDecodeError as error:
                raise DomainErrorV1(
                    ErrorCode.INVALID_ASSET_CONTENT, "Prompt 必须为 UTF-8 文本。"
                ) from error
        elif asset_type == "source_image":
            try:
                with Image.open(source) as image:
                    image.verify()
                with Image.open(source) as image:
                    metadata = {
                        "width": image.width,
                        "height": image.height,
                        "format": image.format,
                    }
                    mime = Image.MIME.get(image.format or "", "")
            except Exception as error:
                raise DomainErrorV1(ErrorCode.INVALID_ASSET_CONTENT, "图片内容无效。") from error
            if not mime:
                raise DomainErrorV1(ErrorCode.INVALID_ASSET_CONTENT, "不支持的图片格式。")
        elif asset_type == "glb":
            try:
                header = source.read_bytes()[:12]
                if len(header) != 12 or header[:4] != b"glTF" or int.from_bytes(header[4:8], "little") != 2:
                    raise ValueError
                if int.from_bytes(header[8:12], "little") != source.stat().st_size:
                    raise ValueError
                mime = "model/gltf-binary"
            except (OSError, ValueError) as error:
                raise DomainErrorV1(ErrorCode.INVALID_ASSET_CONTENT, "GLB content is invalid.") from error
        else:
            raise DomainErrorV1(ErrorCode.INVALID_ASSET_CONTENT, "不支持的导入资产类型。")
        source_digest, _ = _hash(source)
        asset_id, now = new_id(), utc_now()
        suffix = ".txt" if asset_type == "prompt" else source.suffix.lower()
        rel = (
            f"assets/{'source' if asset_type == 'source_image' else 'models' if asset_type == 'glb' else 'generated'}/{asset_id}{suffix}"
        )
        target = self._filesystem.managed_path(root, rel)
        temp = root / "temp" / f"{asset_id}.part"
        thumbnail_id: str | None = new_id() if asset_type == "source_image" else None
        thumbnail_rel: str | None = f"assets/previews/{thumbnail_id}.jpg" if thumbnail_id else None
        thumbnail_digest: str | None = None
        thumbnail_size: int | None = None
        operation_id: str | None = None
        try:
            prepared = self._repository.prepare_import(
                root / "project.sqlite3",
                project_id=project_id,
                request_id=request_id,
                payload={
                    "asset_id": asset_id,
                    "relative_paths": [path for path in (rel, thumbnail_rel) if path],
                    "source_sha256": source_digest,
                    "asset_type": asset_type,
                    "parent_asset_id": parent_asset_id,
                },
            )
            if prepared["replayed"]:
                return self.get(root, project_id, str(prepared["asset_id"]))
            operation_id = str(prepared["operation_id"])
            written: dict[str, Any] = {}

            def write_import() -> None:
                nonlocal thumbnail_id, thumbnail_rel, thumbnail_digest, thumbnail_size
                self._filesystem.asset_file_store(root).stage_copy(source, temp)
                digest, size = _hash(temp)
                self._filesystem.asset_file_store(root).commit(temp, target)
                if asset_type == "source_image":
                    thumbnail_temp = root / "temp" / f"{thumbnail_id}.part"
                    try:
                        with Image.open(target) as image:
                            preview = image.convert("RGB")
                            preview.thumbnail((512, 512))
                            preview.save(thumbnail_temp, "JPEG", quality=88)
                        thumbnail_digest, thumbnail_size = _hash(thumbnail_temp)
                        if thumbnail_rel is None:
                            raise RuntimeError("thumbnail path was not initialized")
                        os.replace(
                            thumbnail_temp, self._filesystem.managed_path(root, thumbnail_rel)
                        )
                    except OSError, ValueError:
                        thumbnail_temp.unlink(missing_ok=True)
                        thumbnail_id = thumbnail_rel = thumbnail_digest = thumbnail_size = None
                written.update(digest=digest, size=size)

            def commit_import() -> None:
                thumbnail = (
                    (
                        thumbnail_id,
                        thumbnail_rel,
                        thumbnail_digest,
                        thumbnail_size,
                        f"Preview of {name or source.name}",
                    )
                    if thumbnail_id
                    and thumbnail_rel
                    and thumbnail_digest
                    and thumbnail_size is not None
                    else None
                )
                self._repository.commit_import(
                    root / "project.sqlite3",
                    operation_id=operation_id,
                    project_id=project_id,
                    asset_id=asset_id,
                    parent_asset_id=parent_asset_id,
                    asset_type=asset_type,
                    asset_group=GROUPS.get(asset_type),
                    name=name or source.name,
                    relative_path=rel,
                    mime=mime,
                    size=int(written["size"]),
                    digest=str(written["digest"]),
                    metadata=metadata,
                    provenance={
                        "schema_version": 1,
                        "source_kind": "import",
                        "input_asset_ids": [],
                        "prompt_asset_id": None,
                        "selection_ids": [],
                        "tool_call_id": None,
                        "provider_profile": None,
                        "model": None,
                        "parameters": {},
                        "original_filename": source.name,
                        "created_at": now,
                    },
                    created_at=now,
                    thumbnail=thumbnail,
                )

            def compensate_import() -> None:
                target.unlink(missing_ok=True)
                if thumbnail_rel:
                    (root / thumbnail_rel).unlink(missing_ok=True)

            self._operations.execute(
                write_and_verify=write_import,
                mark_file_written=lambda: self._repository.mark_operation_file_written(
                    root / "project.sqlite3", operation_id
                ),
                commit_database=commit_import,
                compensate_file=compensate_import,
            )
        except OSError as error:
            target.unlink(missing_ok=True)
            if thumbnail_rel:
                (root / thumbnail_rel).unlink(missing_ok=True)
            if operation_id:
                self._repository.mark_operation_failed(root / "project.sqlite3", operation_id)
            raise _safe_storage_error(error) from error
        except Exception:
            target.unlink(missing_ok=True)
            if thumbnail_rel:
                (root / thumbnail_rel).unlink(missing_ok=True)
            if operation_id:
                self._repository.mark_operation_failed(root / "project.sqlite3", operation_id)
            raise
        return self.get(root, project_id, asset_id)

    def get(
        self,
        root: Path,
        project_id: str,
        asset_id: str,
        *,
        read_only: bool = False,
    ) -> dict[str, Any]:
        row = self._repository.get(
            root / "project.sqlite3", project_id, asset_id, read_only=read_only
        )
        if not row:
            raise DomainErrorV1(ErrorCode.ASSET_NOT_FOUND, "资产不存在。")
        return _row(row)

    def read_content(
        self, root: Path, project_id: str, asset_id: str, range_header: str | None
    ) -> tuple[int, bytes, str, dict[str, str]]:
        """Read a verified managed asset without exposing its filesystem path."""
        row = self._repository.content(root / "project.sqlite3", project_id, asset_id)
        if not row:
            raise DomainErrorV1(ErrorCode.ASSET_NOT_FOUND, "资产不存在。")
        path = self._filesystem.managed_path(root, row["relative_path"])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            raise DomainErrorV1(ErrorCode.ASSET_CONTENT_MISMATCH, "资产内容不一致。")
        data = path.read_bytes()
        headers = {"Accept-Ranges": "bytes"}
        if range_header is None:
            return 200, data, row["mime_type"], headers
        try:
            unit, value = range_header.split("=", 1)
            start_text, end_text = value.split("-", 1)
            if unit != "bytes" or not start_text:
                raise ValueError
            start = int(start_text)
            end = int(end_text) if end_text else len(data) - 1
            if start < 0 or end < start or start >= len(data):
                raise ValueError
            end = min(end, len(data) - 1)
        except ValueError:
            return 416, b"", row["mime_type"], {"Content-Range": f"bytes */{len(data)}"}
        headers["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
        return 206, data[start : end + 1], row["mime_type"], headers

    def managed_file_for_host(
        self, root: Path, project_id: str, asset_id: str
    ) -> Path:
        """Resolve one managed file for a native-only action without exposing its path."""
        row = self._repository.content(root / "project.sqlite3", project_id, asset_id)
        if not row:
            raise DomainErrorV1(ErrorCode.ASSET_NOT_FOUND, "资产不存在。")
        path = self._filesystem.managed_path(root, str(row["relative_path"]))
        if not path.is_file():
            raise DomainErrorV1(ErrorCode.ASSET_CONTENT_MISMATCH, "资产文件不存在。")
        return path

    def read_thumbnail(
        self, root: Path, project_id: str, asset_id: str
    ) -> tuple[bytes, str, dict[str, str]]:
        """Return a bounded image preview without exposing or decoding originals in the UI."""
        asset = self.get(root, project_id, asset_id)
        if asset["asset_type"] not in IMAGE_ASSET_TYPES:
            raise DomainErrorV1(
                ErrorCode.INVALID_ASSET_CONTENT,
                "This asset does not support an image thumbnail.",
            )
        thumbnail_id = asset.get("thumbnail_asset_id")
        if isinstance(thumbnail_id, str) and thumbnail_id:
            _, data, mime_type, _ = self.read_content(
                root, project_id, thumbnail_id, None
            )
        else:
            _, original, _, _ = self.read_content(root, project_id, asset_id, None)
            try:
                with Image.open(BytesIO(original)) as source:
                    source.thumbnail((640, 640), Image.Resampling.LANCZOS)
                    output = BytesIO()
                    has_alpha = source.mode in {"RGBA", "LA"} or "transparency" in source.info
                    if has_alpha:
                        source.convert("RGBA").save(output, "PNG", optimize=True)
                        mime_type = "image/png"
                    else:
                        source.convert("RGB").save(output, "JPEG", quality=84, optimize=True)
                        mime_type = "image/jpeg"
                    data = output.getvalue()
            except (OSError, ValueError) as error:
                raise DomainErrorV1(
                    ErrorCode.INVALID_ASSET_CONTENT,
                    "The managed asset could not be rendered as a thumbnail.",
                ) from error
        return data, mime_type, {
            "Cache-Control": "private, max-age=31536000, immutable",
            "ETag": f'"{asset["sha256"]}"',
        }

    def list_by_group(
        self,
        root: Path,
        project_id: str,
        include_trashed: bool = False,
        group: str | None = None,
        include_hidden: bool = True,
        *,
        read_only: bool = False,
        include_visual_identities: bool = False,
    ) -> list[dict[str, Any]]:
        if group is not None and group not in set(GROUPS.values()):
            raise DomainErrorV1(ErrorCode.SCHEMA_VALIDATION_FAILED, "资产分组无效。")
        rows = self._repository.list_with_usage(
            root / "project.sqlite3",
            project_id,
            include_trashed,
            group,
            include_hidden,
            read_only=read_only,
        )
        result = []
        for row in rows:
            item = _row(row)
            if include_visual_identities and item["asset_type"] in IMAGE_ASSET_TYPES:
                digest = str(item["sha256"])
                identity = self._visual_identity_cache.get(digest)
                if identity is None:
                    try:
                        identity = _visual_identity(
                            self._filesystem.managed_path(root, str(row["relative_path"]))
                        )
                    except (OSError, ValueError):
                        identity = None
                    if identity is not None:
                        if len(self._visual_identity_cache) >= 2048:
                            self._visual_identity_cache.clear()
                        self._visual_identity_cache[digest] = identity
                if identity is not None:
                    item["visual_fingerprint"], item["visual_aspect_ratio"] = identity
            item["usage"] = {
                "child_count": row["_children"],
                "input_link_count": row["_incoming"],
                "output_link_count": row["_outgoing"],
                "active_run_count": 0,
                "active_job_count": 0,
                "is_project_current": item["is_current"],
            }
            result.append(item)
        return result

    def usage_summary(
        self,
        root: Path,
        project_id: str,
        asset_id: str,
        *,
        read_only: bool = False,
    ) -> dict[str, Any]:
        usage = self._repository.usage_counts(
            root / "project.sqlite3", project_id, asset_id, read_only=read_only
        )
        if usage is None:
            raise DomainErrorV1(ErrorCode.ASSET_NOT_FOUND, "资产不存在。")
        return {
            **usage,
            "active_run_count": 0,
            "active_job_count": 0,
            "is_project_current": bool(usage["is_project_current"]),
        }

    def compare_siblings(
        self,
        root: Path,
        project_id: str,
        left_id: str,
        right_id: str,
        *,
        read_only: bool = False,
    ) -> dict[str, Any]:
        left = self.get(root, project_id, left_id, read_only=read_only)
        right = self.get(root, project_id, right_id, read_only=read_only)
        if left["asset_family_id"] != right["asset_family_id"]:
            raise DomainErrorV1(ErrorCode.INVALID_ASSET_CONTENT, "只能比较同一资产族。")
        return {
            "left": left,
            "right": right,
            "same_family": True,
            "version_delta": left["version_no"] - right["version_no"],
            "left_usage": self.usage_summary(root, project_id, left_id, read_only=read_only),
            "right_usage": self.usage_summary(root, project_id, right_id, read_only=read_only),
        }

    def set_current(
        self,
        root: Path,
        project_id: str,
        asset_id: str,
        source: str,
        request_id: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        self._filesystem.require_writable_root(root)
        # This text is durable audit/provenance metadata.  Never allow a tool
        # supplied reason to put credentials, signed URLs, or local paths in
        # the project database or a later package export.
        reason = self._filesystem.redact(reason) if reason is not None else None
        return self._repository.set_current_committed(
            root / "project.sqlite3",
            project_id=project_id,
            asset_id=asset_id,
            source=source,
            reason=reason,
            request_id=request_id,
        )

    def register_derived(
        self,
        root: Path,
        project_id: str,
        source: Path,
        asset_type: str,
        request_id: str,
        *,
        parent_asset_id: str | None = None,
        input_asset_ids: list[str] | None = None,
        lineage_mode: str = "new_artifact",
        asset_group: str | None = None,
        name: str | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Register an internally produced file without exposing its path to API/Tool callers."""
        self._filesystem.require_writable_root(root)
        if not source.is_file():
            raise DomainErrorV1(ErrorCode.INVALID_ASSET_CONTENT, "派生产物文件不存在。")
        if source.is_symlink() or source.stat().st_size > 200 * 1024 * 1024:
            raise DomainErrorV1(ErrorCode.INVALID_ASSET_CONTENT, "派生产物文件无效。")
        if asset_type not in GROUPS and asset_type not in {"prompt", "analysis", "preview"}:
            raise DomainErrorV1(ErrorCode.INVALID_ASSET_CONTENT, "派生产物类型不支持。")
        asset_id = new_id()
        suffix = source.suffix.lower() or ".bin"
        subdir = {
            "annotation": "selections",
            "crop": "selections",
            "preview": "previews",
            "export": "exports",
            "multiview": "multiview",
            "glb": "models",
            "fbx": "models",
        }.get(asset_type, "generated")
        relative_path = f"assets/{subdir}/{asset_id}{suffix}"
        target = self._filesystem.managed_path(root, relative_path)
        temporary = root / "temp" / f"{asset_id}.part"
        metadata: dict[str, Any] = {}
        mime_type = mimetypes.guess_type(str(source))[0] or "application/octet-stream"
        if asset_type in {"generated_image", "annotation", "crop", "multiview", "preview"}:
            try:
                with Image.open(source) as image:
                    image.verify()
                with Image.open(source) as image:
                    metadata = {
                        "width": image.width,
                        "height": image.height,
                        "format": image.format,
                    }
                    mime_type = Image.MIME.get(image.format or "", mime_type)
            except (OSError, ValueError) as error:
                raise DomainErrorV1(
                    ErrorCode.INVALID_ASSET_CONTENT, "派生图片内容无效。"
                ) from error
        merged_provenance = {
            "schema_version": 1,
            "source_kind": "user_edit",
            "input_asset_ids": input_asset_ids or [],
            "prompt_asset_id": None,
            "selection_ids": [],
            "tool_call_id": None,
            "provider_profile": None,
            "model": None,
            "parameters": {},
            "original_filename": None,
        }
        merged_provenance.update(provenance or {})
        merged_provenance.setdefault("created_at", utc_now())
        source_digest, _ = _hash(source)
        command_payload = {
            "derived": True,
            "source_sha256": source_digest,
            "asset_type": asset_type,
            "parent_asset_id": parent_asset_id,
            "input_asset_ids": input_asset_ids or [],
            "lineage_mode": lineage_mode,
            "asset_group": asset_group,
            "name": name,
            "provenance": merged_provenance,
        }
        operation_id: str | None = None
        try:
            prepared = self._repository.prepare_derived(
                root / "project.sqlite3",
                project_id=project_id,
                request_id=request_id,
                payload={
                    **command_payload,
                    "asset_id": asset_id,
                    "relative_paths": [relative_path],
                },
            )
            if prepared["replayed"]:
                return self.get(root, project_id, str(prepared["asset_id"]))
            operation_id = str(prepared["operation_id"])
            written: dict[str, int | str] = {}

            def write_derived() -> None:
                self._filesystem.asset_file_store(root).stage_copy(source, temporary)
                digest, size = _hash(temporary)
                self._filesystem.asset_file_store(root).commit(temporary, target)
                written.update(digest=digest, size=size)

            self._operations.execute(
                write_and_verify=write_derived,
                mark_file_written=lambda: self._repository.mark_operation_file_written(
                    root / "project.sqlite3", operation_id
                ),
                commit_database=lambda: self._repository.commit_derived(
                    root / "project.sqlite3",
                    operation_id=operation_id,
                    project_id=project_id,
                    asset_id=asset_id,
                    parent_asset_id=parent_asset_id,
                    input_asset_ids=input_asset_ids or [],
                    lineage_mode=lineage_mode,
                    asset_type=asset_type,
                    asset_group=asset_group if asset_group is not None else GROUPS.get(asset_type),
                    name=name or source.name,
                    relative_path=relative_path,
                    mime_type=mime_type,
                    size=int(written["size"]),
                    digest=str(written["digest"]),
                    metadata=metadata,
                    provenance=merged_provenance,
                    created_at=utc_now(),
                ),
                compensate_file=lambda: target.unlink(missing_ok=True),
            )
        except OSError as error:
            temporary.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            if operation_id:
                self._repository.mark_operation_failed(root / "project.sqlite3", operation_id)
            raise _safe_storage_error(error) from error
        except Exception:
            temporary.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            if operation_id:
                self._repository.mark_operation_failed(root / "project.sqlite3", operation_id)
            raise
        return self.get(root, project_id, asset_id)

    def lineage(
        self,
        root: Path,
        project_id: str,
        asset_id: str,
        *,
        read_only: bool = False,
    ) -> dict[str, Any]:
        result = self._repository.lineage(
            root / "project.sqlite3", project_id, asset_id, read_only=read_only
        )
        if result is None:
            raise DomainErrorV1(ErrorCode.ASSET_NOT_FOUND, "资产不存在。")
        return {
            "asset_id": asset_id,
            **result,
            "usage": self.usage_summary(root, project_id, asset_id, read_only=read_only),
        }

    def hide(
        self,
        root: Path,
        project_id: str,
        asset_id: str,
        hidden: bool,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        self._filesystem.require_writable_root(root)
        if not self._repository.hide_committed(
            root / "project.sqlite3",
            project_id=project_id,
            asset_id=asset_id,
            hidden=hidden,
            request_id=request_id,
        ):
            raise DomainErrorV1(ErrorCode.ASSET_NOT_FOUND, "资产不存在。")
        return self.get(root, project_id, asset_id)

    def impact(self, root: Path, project_id: str, asset_id: str) -> dict[str, Any]:
        return self._repository.impact(
            root / "project.sqlite3", project_id, asset_id, int(time.time())
        )

    def trash(
        self,
        root: Path,
        project_id: str,
        asset_id: str,
        token: str | None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        self._filesystem.require_writable_root(root)
        source: Path | None = None
        target: Path | None = None
        moved = False
        try:
            prepared = self._repository.prepare_trash(
                root / "project.sqlite3",
                project_id=project_id,
                asset_id=asset_id,
                token=token,
                request_id=request_id,
                now_seconds=int(time.time()),
            )
            if prepared["replayed"]:
                return self.get(root, project_id, asset_id)
            old, new = str(prepared["source_relative_path"]), str(prepared["trash_relative_path"])
            source, target = root / old, root / new
            target.parent.mkdir(parents=True, exist_ok=True)
            operation_id = str(prepared["operation_id"])

            def move_file() -> None:
                nonlocal moved
                os.replace(source, target)
                moved = True

            self._operations.execute(
                write_and_verify=move_file,
                mark_file_written=lambda: self._repository.mark_operation_file_written(
                    root / "project.sqlite3", operation_id
                ),
                commit_database=lambda: self._repository.commit_trash_committed(
                    root / "project.sqlite3",
                    operation_id=operation_id,
                    project_id=project_id,
                    asset_id=asset_id,
                    old=old,
                    new=new,
                    was_current=bool(prepared["was_current"]),
                ),
                compensate_file=lambda: os.replace(target, source),
            )
        except Exception:
            if (
                moved
                and source is not None
                and target is not None
                and target.exists()
                and not source.exists()
            ):
                os.replace(target, source)
            raise
        return self.get(root, project_id, asset_id)

    def restore_from_trash(
        self, root: Path, project_id: str, asset_id: str, request_id: str
    ) -> dict[str, Any]:
        self._filesystem.require_writable_root(root)
        source: Path | None = None
        target: Path | None = None
        try:
            prepared = self._repository.prepare_restore(
                root / "project.sqlite3",
                project_id=project_id,
                asset_id=asset_id,
                request_id=request_id,
            )
            if prepared["replayed"]:
                return self.get(root, project_id, asset_id)
            source, target = (
                root / str(prepared["trash_relative_path"]),
                root / str(prepared["restored_relative_path"]),
            )
            if not source.is_file():
                raise DomainErrorV1(ErrorCode.ASSET_CONTENT_MISMATCH, "回收资产文件缺失。")
            if target.exists():
                target = target.with_name(f"restored_{asset_id}_{target.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            operation_id = str(prepared["operation_id"])
            rel = target.relative_to(root).as_posix()
            self._operations.execute(
                write_and_verify=lambda: os.replace(source, target),
                mark_file_written=lambda: self._repository.mark_operation_file_written(
                    root / "project.sqlite3", operation_id
                ),
                commit_database=lambda: self._repository.commit_restore_committed(
                    root / "project.sqlite3",
                    operation_id=operation_id,
                    project_id=project_id,
                    asset_id=asset_id,
                    relative_path=rel,
                ),
                compensate_file=lambda: os.replace(target, source),
            )
        except Exception:
            if source and target and target.exists() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, source)
            raise
        return self.get(root, project_id, asset_id)
