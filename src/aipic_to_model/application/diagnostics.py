"""Application boundary for redacted diagnostics."""

from pathlib import Path
from .ports import FilesystemPort


class DiagnosticsService:
    def __init__(
        self,
        filesystem: FilesystemPort,
    ) -> None:
        self._filesystem = filesystem

    def preview(self, root: Path, build_info: dict[str, str]) -> dict[str, Any]:
        return self._filesystem.diagnostics_preview(root, build_info)

    def export(
        self,
        root: Path,
        destination: Path,
        confirmed_manifest_hash: str,
        build_info: dict[str, str],
    ) -> dict[str, Any]:
        return self._filesystem.diagnostics_export(
            root, destination, confirmed_manifest_hash, build_info
        )
