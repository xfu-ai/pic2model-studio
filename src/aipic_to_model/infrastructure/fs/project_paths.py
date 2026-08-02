from __future__ import annotations

import os
import secrets
from pathlib import Path

from ...domain.common import DomainErrorV1, ErrorCode

REQUIRED_DIRS = (
    "assets/source",
    "assets/generated",
    "assets/selections",
    "assets/multiview",
    "assets/models",
    "assets/previews",
    "assets/exports",
    "assets/trash",
    "temp",
    "logs",
    "recovery",
)


def validate_new_root(root: Path) -> Path:
    root = root.expanduser().resolve(strict=False)
    if root == root.anchor or root.is_symlink() or (root.exists() and any(root.iterdir())):
        raise DomainErrorV1(ErrorCode.ASSET_PATH_OUTSIDE_PROJECT, "项目目录必须是新的空目录。")
    if not root.parent.exists() or not os.access(root.parent, os.W_OK):
        raise DomainErrorV1(ErrorCode.PROJECT_READ_ONLY, "项目父目录不可写。")
    return root


def managed_path(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve(strict=False)
    try:
        candidate.relative_to(root.resolve())
    except ValueError as error:
        raise DomainErrorV1(ErrorCode.ASSET_PATH_OUTSIDE_PROJECT, "资产路径不在项目内。") from error
    if relative_path.replace("\\", "/").startswith("temp/"):
        raise DomainErrorV1(ErrorCode.ASSET_PATH_OUTSIDE_PROJECT, "临时文件不能登记为资产。")
    return candidate


def require_writable_root(root: Path) -> None:
    """Prove a project root accepts writes before a command changes its state."""
    probe = root / f".formweaver-write-probe-{os.getpid()}-{secrets.token_hex(8)}"
    try:
        with probe.open("xb") as stream:
            stream.write(b"probe")
            stream.flush()
            os.fsync(stream.fileno())
        probe.unlink()
    except OSError as error:
        try:
            probe.unlink(missing_ok=True)
        except OSError as cleanup_error:
            raise DomainErrorV1(
                ErrorCode.PROJECT_READ_ONLY, "Project directory is read-only."
            ) from cleanup_error
        raise DomainErrorV1(
            ErrorCode.PROJECT_READ_ONLY, "Project directory is read-only."
        ) from error
