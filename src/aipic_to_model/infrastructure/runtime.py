"""Infrastructure implementation registered by the package composition root."""

from __future__ import annotations

from pathlib import Path

from .archive_safety import validate_zip
from .diagnostics import export as diagnostics_export
from .diagnostics import preview as diagnostics_preview
from .fs.asset_files import AssetFileStore
from .fs.atomic_io import atomic_write_bytes, atomic_write_new_bytes, atomic_write_text
from .fs.project_paths import REQUIRED_DIRS, managed_path, require_writable_root, validate_new_root
from .logging import redact, redact_structure
from .sqlite.connection import migrate, migrate_app


class InfrastructureRuntime:
    REQUIRED_DIRS = REQUIRED_DIRS
    migrate = staticmethod(migrate)
    migrate_app = staticmethod(migrate_app)
    validate_new_root = staticmethod(validate_new_root)
    require_writable_root = staticmethod(require_writable_root)
    managed_path = staticmethod(managed_path)
    redact = staticmethod(redact)
    redact_structure = staticmethod(redact_structure)
    validate_zip = staticmethod(validate_zip)
    atomic_write_text = staticmethod(atomic_write_text)
    atomic_write_bytes = staticmethod(atomic_write_bytes)
    atomic_write_new_bytes = staticmethod(atomic_write_new_bytes)
    asset_file_store = staticmethod(AssetFileStore)
    diagnostics_preview = staticmethod(diagnostics_preview)
    diagnostics_export = staticmethod(diagnostics_export)
    project_package_schema_path = staticmethod(
        lambda: Path(__file__).with_name("schemas") / "project-package-v1.schema.json"
    )
