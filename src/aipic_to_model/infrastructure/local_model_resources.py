"""Resolve repository and portable local-model resources without exposing paths."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def local_model_resource_roots() -> tuple[Path, ...]:
    """Return ordered Host-owned roots for bundled local inference resources."""

    candidates: list[Path] = []
    configured = os.environ.get("AIPIC_LOCAL_MODEL_ROOT")
    if configured:
        candidates.append(Path(configured))

    executable = Path(sys.executable).resolve()
    candidates.extend(
        (
            # Portable sidecar: <app>/resources/sidecar/pic2model-sidecar.exe
            executable.parent.parent / "local-models",
            # Alternate packaged layout: <app>/pic2model-sidecar.exe
            executable.parent / "resources" / "local-models",
            # Source checkout and editable development environment.
            Path(__file__).resolve().parents[3] / "resources" / "local-models",
        )
    )

    roots: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_dir():
            roots.append(resolved)
    return tuple(roots)


def resolve_local_model_resource(relative: Path, *, directory: bool = False) -> Path | None:
    """Resolve one fixed relative resource from a repository or portable root."""

    if relative.is_absolute() or ".." in relative.parts:
        return None
    for root in local_model_resource_roots():
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root):
            continue
        if directory and candidate.is_dir():
            return candidate
        if not directory and candidate.is_file():
            return candidate
    return None


__all__ = ["local_model_resource_roots", "resolve_local_model_resource"]
