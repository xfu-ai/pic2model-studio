from __future__ import annotations

import os
import tempfile
from pathlib import Path

from ...domain.common import DomainErrorV1, ErrorCode


def _raise_safe_storage_error(error: OSError) -> None:
    message = str(error).lower()
    if error.errno in {28, 30} or "disk full" in message or "no space" in message:
        raise DomainErrorV1(
            ErrorCode.LOCAL_STORAGE_UNAVAILABLE,
            "本地存储空间不足，操作未完成。",
            True,
            retry_after_seconds=5,
        ) from error
    if "read-only" in message or "access is denied" in message:
        raise DomainErrorV1(
            ErrorCode.PROJECT_READ_ONLY,
            "项目目录不可写。",
            True,
            retry_after_seconds=5,
        ) from error
    raise error


def atomic_write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temp = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, target)
    except OSError as error:
        temp.unlink(missing_ok=True)
        _raise_safe_storage_error(error)
    except Exception:
        temp.unlink(missing_ok=True)
        raise


def atomic_write_new_bytes(target: Path, data: bytes) -> None:
    """Commit new bytes without ever replacing a destination created by another process."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temp = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def atomic_write_text(target: Path, data: str) -> None:
    atomic_write_bytes(target, data.encode("utf-8"))
