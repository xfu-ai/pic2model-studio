from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from ..domain.common import DomainErrorV1, ErrorCode, canonical_json, new_id, utc_now
from .operations import OperationService
from .ports import FilesystemPort, PackageRepositoryPort


class ProjectPackageService:
    _IMPORT_OWNER = ".pic2model-import-owner.json"

    def __init__(
        self,
        repository: PackageRepositoryPort,
        filesystem: FilesystemPort,
        operations: OperationService,
    ) -> None:
        self._repository = repository
        self._filesystem = filesystem
        self._operations = operations

    @staticmethod
    def _destination_fingerprint(destination: Path) -> str:
        """Bind a replay to its output location without recording that location."""
        return hashlib.sha256(str(destination.resolve(strict=False)).encode("utf-8")).hexdigest()

    @staticmethod
    def _capability_fingerprint(capability_id: str | None) -> str | None:
        return hashlib.sha256(capability_id.encode("utf-8")).hexdigest() if capability_id else None

    def _import_owner(self, package: Path, destination: Path) -> dict[str, object]:
        digest = hashlib.sha256()
        with package.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return {
            "version": 1,
            "destination_fingerprint": self._destination_fingerprint(destination),
            "package_sha256": digest.hexdigest(),
        }

    def _owned_staging(self, staging: Path, destination: Path, owner: dict[str, object]) -> bool:
        """Prove a deterministic staging directory belongs to this import."""
        if (
            staging.is_symlink()
            or not staging.is_dir()
            or staging.parent.resolve(strict=False) != destination.parent.resolve(strict=False)
            or staging.name != f".{destination.name}.importing"
        ):
            return False
        marker = staging / self._IMPORT_OWNER
        if marker.is_symlink() or not marker.is_file():
            return False
        try:
            recorded = json.loads(marker.read_text(encoding="utf-8"))
        except OSError, json.JSONDecodeError:
            return False
        return recorded == owner

    def _remove_owned_staging(
        self, staging: Path, destination: Path, owner: dict[str, object]
    ) -> None:
        if not self._owned_staging(staging, destination, owner):
            raise DomainErrorV1(
                ErrorCode.INVALID_ARCHIVE,
                "导入暂存目录不是本次操作创建，已保留且未修改。",
            )
        shutil.rmtree(staging)

    def replay_export_request(
        self, root: Path, format_name: str, request_id: str, capability_id: str | None
    ) -> dict[str, object] | None:
        return self._repository.replay_export_request(
            root / "project.sqlite3",
            request_id,
            {
                "format": format_name,
                "capability_fingerprint": self._capability_fingerprint(capability_id),
            },
        )

    def replay_export(
        self, root: Path, destination: Path, format_name: str, overwrite: bool, request_id: str
    ) -> dict[str, object] | None:
        expected = {
            "format": format_name,
            "destination_fingerprint": self._destination_fingerprint(destination),
            "overwrite": overwrite,
        }
        return self._repository.replay_export_request(
            root / "project.sqlite3", request_id, expected
        )

    def export_v1(
        self,
        root: Path,
        destination: Path,
        overwrite: bool = False,
        request_id: str | None = None,
        capability_id: str | None = None,
    ) -> dict[str, object]:
        operation_id = new_id()
        supplied_request_id = request_id
        request_id = request_id or f"package-export:{operation_id}"
        temporary = root / "temp" / f"package-export-{operation_id}.zip"
        destination_fingerprint = self._destination_fingerprint(destination)
        command_payload = {
            "format": "project_v1",
            "destination_fingerprint": destination_fingerprint,
            "capability_fingerprint": self._capability_fingerprint(capability_id),
            "overwrite": overwrite,
        }
        if supplied_request_id:
            previous = self._repository.replay_export_request(
                root / "project.sqlite3", request_id, command_payload
            )
            if previous:
                return previous
        if destination.exists() and not overwrite:
            raise DomainErrorV1(ErrorCode.INVALID_ARCHIVE, "导出目标已存在。")
        operation_id = self._repository.prepare_export(
            root / "project.sqlite3",
            request_id,
            {
                **command_payload,
                "temporary_relative_path": str(temporary.relative_to(root)).replace("\\", "/"),
            },
        )
        project, assets, links, selections, decisions = self._repository.export_projection_database(
            root / "project.sqlite3"
        )
        assets = []
        for item in self._repository.export_projection_database(root / "project.sqlite3")[1]:
            path = root / item["relative_path"]
            if not path.is_file():
                raise DomainErrorV1(ErrorCode.ASSET_CONTENT_MISMATCH, "正式资产文件缺失。")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != item["sha256"]:
                raise DomainErrorV1(ErrorCode.ASSET_CONTENT_MISMATCH, "正式资产哈希不一致。")
            item["sha256"] = digest
            item["size_bytes"] = path.stat().st_size
            assets.append(item)
        manifest = {
            "format": "Pic2ModelProject",
            "format_version": 1,
            "project": project,
            "assets": assets,
            "asset_links": links,
            "selections": selections,
            "decisions": decisions,
            "exported_at": utc_now(),
        }
        result: dict[str, object] = {}
        manifest_text = canonical_json(manifest)
        manifest_bytes = manifest_text.encode("utf-8")

        def write_archive() -> None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", manifest_bytes)
                for asset in assets:
                    archive.write(root / str(asset["relative_path"]), str(asset["relative_path"]))
            with zipfile.ZipFile(temporary) as archive:
                self._filesystem.validate_zip(archive)
                returned = json.loads(archive.read("manifest.json"))
            if returned["format"] != "Pic2ModelProject":
                raise DomainErrorV1(ErrorCode.INVALID_ARCHIVE, "导出包验证失败。")
            try:
                if overwrite:
                    self._filesystem.atomic_write_bytes(destination, temporary.read_bytes())
                else:
                    self._filesystem.atomic_write_new_bytes(destination, temporary.read_bytes())
            except FileExistsError as error:
                raise DomainErrorV1(ErrorCode.INVALID_ARCHIVE, "导出目标已存在。") from error
            result.update(
                {
                    "format": "project_v1",
                    "path": destination.name,
                    "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                    "asset_count": len(assets),
                }
            )

        try:
            self._operations.execute(
                write_and_verify=write_archive,
                mark_file_written=lambda: self._repository.mark_export_file_written(
                    root / "project.sqlite3", operation_id
                ),
                commit_database=lambda: self._repository.complete_export_committed(
                    root / "project.sqlite3", operation_id, {"result": result}
                ),
                compensate_file=lambda: temporary.unlink(missing_ok=True),
            )
        except Exception:
            self._repository.rollback_export_committed(root / "project.sqlite3", operation_id)
            raise
        finally:
            temporary.unlink(missing_ok=True)
        return result

    def inspect_v1(self, package: Path) -> dict[str, Any]:
        try:
            with zipfile.ZipFile(package) as archive:
                self._filesystem.validate_zip(archive)
                manifest = json.loads(archive.read("manifest.json"))
        except (OSError, KeyError, zipfile.BadZipFile, json.JSONDecodeError) as error:
            raise DomainErrorV1(ErrorCode.INVALID_ARCHIVE, "项目包无效。") from error
        if (
            manifest.get("format") != "Pic2ModelProject"
            or manifest.get("format_version") != 1
            or manifest.get("project", {}).get("root_path") != "."
        ):
            raise DomainErrorV1(ErrorCode.INVALID_ARCHIVE, "项目包格式或根路径无效。")
        schema_path = self._filesystem.project_package_schema_path()
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        if list(Draft202012Validator(schema).iter_errors(manifest)):
            raise DomainErrorV1(ErrorCode.INVALID_ARCHIVE, "project package fails v1 schema")
        for asset in manifest.get("assets", []):
            if (
                Path(asset["relative_path"]).is_absolute()
                or ".." in Path(asset["relative_path"]).parts
            ):
                raise DomainErrorV1(ErrorCode.INVALID_ARCHIVE, "项目包包含不安全资产路径。")
        return manifest

    def import_v1(self, package: Path, destination: Path) -> dict[str, object]:
        """Import into a new directory; the source ZIP remains read-only throughout."""
        destination = self._filesystem.validate_new_root(destination)
        manifest = self.inspect_v1(package)
        project = manifest["project"]
        project_id = project["id"]
        staging = destination.parent / f".{destination.name}.importing"
        owner = self._import_owner(package, destination)
        if staging.exists():
            self._remove_owned_staging(staging, destination, owner)
        staging.mkdir(parents=True, exist_ok=False)
        self._filesystem.atomic_write_text(staging / self._IMPORT_OWNER, canonical_json(owner))
        try:
            for directory in self._filesystem.REQUIRED_DIRS:
                (staging / directory).mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(package) as archive:
                self._filesystem.validate_zip(archive)
                for asset in manifest["assets"]:
                    relative = asset["relative_path"]
                    target = staging / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    data = archive.read(relative)
                    if hashlib.sha256(data).hexdigest() != asset["sha256"]:
                        raise DomainErrorV1(ErrorCode.INVALID_ARCHIVE, "项目包资产哈希不匹配。")
                    target.write_bytes(data)
            metadata = {
                "project_id": project_id,
                "name": project["name"],
                "format_version": 1,
                "created_at": project["created_at"],
                "updated_at": project["updated_at"],
            }
            self._filesystem.atomic_write_text(
                staging / "project.json",
                canonical_json(metadata),
            )
            self._filesystem.migrate(staging / "project.sqlite3", staging / "recovery")
            self._repository.import_manifest_committed(staging / "project.sqlite3", manifest)
            os.replace(staging, destination)
            try:
                (destination / self._IMPORT_OWNER).unlink()
            except OSError:
                # Promotion is already committed.  The non-sensitive ownership
                # marker is safe to leave for later housekeeping.
                if not (destination / self._IMPORT_OWNER).exists():
                    raise
        except Exception as error:
            recovery = staging / "recovery"
            cleanup_staging = True
            if (
                isinstance(error, DomainErrorV1)
                and error.code == ErrorCode.MIGRATION_FAILED
                and recovery.is_dir()
            ):
                retained = (
                    destination.parent / f".{destination.name}.{project_id}.migration-recovery"
                )
                try:
                    os.replace(recovery, retained)
                except OSError:
                    # Ownership is still proven by the marker; leave staging
                    # intact if its diagnostic copy cannot be retained.
                    cleanup_staging = False
            if (
                cleanup_staging
                and staging.exists()
                and self._owned_staging(staging, destination, owner)
            ):
                shutil.rmtree(staging)
            raise
        return {
            "project_id": project_id,
            "name": project["name"],
            "asset_count": len(manifest["assets"]),
        }
