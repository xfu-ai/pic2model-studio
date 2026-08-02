from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path
from typing import Any, cast

from ..domain.common import DomainErrorV1, ErrorCode, canonical_json
from .fs.atomic_io import atomic_write_bytes
from .logging import redact


def preview(root: Path, build: dict[str, str]) -> dict[str, object]:
    files = [
        {"name": f"logs/{path.name}", "size": path.stat().st_size}
        for path in sorted((root / "logs").glob("*.log"))
    ]
    manifest = {"build": build, "files": files}
    raw = canonical_json(manifest).encode("utf-8")
    return {
        "manifest": manifest,
        "manifest_hash": hashlib.sha256(raw).hexdigest(),
        "estimated_size": sum(item["size"] for item in files),
    }


def export(
    root: Path, destination: Path, confirmed_manifest_hash: str, build: dict[str, str]
) -> dict[str, object]:
    prepared = preview(root, build)
    if confirmed_manifest_hash != prepared["manifest_hash"]:
        raise DomainErrorV1(
            ErrorCode.IDEMPOTENCY_CONFLICT,
            "Diagnostic manifest changed; preview and confirm again.",
        )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", canonical_json(prepared["manifest"]).encode("utf-8"))
        manifest = cast(dict[str, Any], prepared["manifest"])
        for item in manifest["files"]:
            path = root / item["name"]
            archive.writestr(
                item["name"], redact(path.read_text(encoding="utf-8", errors="replace"))
            )
    atomic_write_bytes(destination, buffer.getvalue())
    return {"path": destination.name, "manifest_hash": prepared["manifest_hash"]}
