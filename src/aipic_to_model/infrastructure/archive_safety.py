from __future__ import annotations

import zipfile
from pathlib import PurePosixPath

from ..domain.common import DomainErrorV1, ErrorCode


def validate_zip(
    archive: zipfile.ZipFile,
    max_entries: int = 2048,
    max_file: int = 200 * 1024 * 1024,
    max_total: int = 1024 * 1024 * 1024,
    max_ratio: int = 100,
) -> list[zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > max_entries:
        raise DomainErrorV1(ErrorCode.INVALID_ARCHIVE, "压缩包条目过多。")
    seen = set()
    total = 0
    for info in infos:
        path = PurePosixPath(info.filename.replace("\\", "/"))
        if (
            not info.filename
            or path.is_absolute()
            or ".." in path.parts
            or ":" in path.parts[0]
            or info.is_dir()
            or info.filename in seen
            or bool(info.flag_bits & 0x1)
            or info.file_size > max_file
            or (info.file_size > 0 and info.compress_size == 0)
            or (info.compress_size and info.file_size / info.compress_size > max_ratio)
        ):
            raise DomainErrorV1(ErrorCode.INVALID_ARCHIVE, "压缩包包含不安全条目。")
        if info.external_attr >> 16 & 0o170000 == 0o120000:
            raise DomainErrorV1(ErrorCode.INVALID_ARCHIVE, "压缩包不能含符号链接。")
        seen.add(info.filename)
        total += info.file_size
    if total > max_total:
        raise DomainErrorV1(ErrorCode.INVALID_ARCHIVE, "压缩包解压后过大。")
    return infos
