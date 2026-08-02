"""Managed project-file operations with no path exposure outside the project root."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ...domain.errors import DomainErrorV1, ErrorCode
from .project_paths import managed_path


class AssetFileStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def temporary(self, name: str) -> Path:
        return managed_path(self.root, f"temp/{name}")

    def managed(self, relative_path: str) -> Path:
        return managed_path(self.root, relative_path)

    def stage_copy(self, source: Path, temporary: Path) -> None:
        if not source.is_file() or source.is_symlink():
            raise DomainErrorV1(ErrorCode.INVALID_ASSET_CONTENT, "导入文件无效。")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, temporary)

    def commit(self, temporary: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, destination)

    def remove(self, path: Path) -> None:
        path.unlink(missing_ok=True)
