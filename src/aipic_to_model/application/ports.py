"""Narrow application ports used by the B01 composition root.

This module intentionally contains interfaces only.  It has no runtime
registration, adapter factories, SQLite imports, or package-import side
effects.  Infrastructure adapters are selected exactly once in
``composition.py`` and passed into the application services that need them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, TypedDict


class AssetContentRecord(TypedDict):
    relative_path: str
    mime_type: str
    sha256: str


class SelectionMetadataRecord(TypedDict):
    metadata_json: str


class SourceAssetRecord(TypedDict):
    relative_path: str


class ProjectRecord(TypedDict):
    id: str
    name: str
    root_path: str
    updated_at: str


class AssetFileStorePort(Protocol):
    def stage_copy(self, source: Path, temporary: Path) -> None: ...
    def commit(self, temporary: Path, target: Path) -> None: ...


class FilesystemPort(Protocol):
    REQUIRED_DIRS: tuple[str, ...]

    def migrate(self, path: Path, recovery: Path) -> None: ...
    def migrate_app(self, path: Path) -> None: ...
    def validate_new_root(self, path: Path) -> Path: ...
    def require_writable_root(self, path: Path) -> None: ...
    def managed_path(self, root: Path, relative_path: str) -> Path: ...
    def redact(self, value: str) -> str: ...
    def redact_structure(self, value: Any) -> Any: ...
    def validate_zip(self, archive: Any) -> None: ...
    def atomic_write_text(self, path: Path, text: str) -> None: ...
    def atomic_write_bytes(self, path: Path, content: bytes) -> None: ...
    def atomic_write_new_bytes(self, path: Path, content: bytes) -> None: ...
    def asset_file_store(self, root: Path) -> AssetFileStorePort: ...
    def project_package_schema_path(self) -> Path: ...
    def diagnostics_preview(self, root: Path, build_info: dict[str, str]) -> dict[str, Any]: ...
    def diagnostics_export(
        self,
        root: Path,
        destination: Path,
        confirmed_manifest_hash: str,
        build_info: dict[str, str],
    ) -> dict[str, Any]: ...


class ProjectRepositoryPort(Protocol):
    def create_database(
        self, database: Path, project_id: str, name: str, created_at: str
    ) -> None: ...
    def open_database(
        self, database: Path, root: Path, read_only: bool
    ) -> ProjectRecord | None: ...
    def prepare_rename(
        self,
        database: Path,
        request_id: str,
        old_name: str,
        new_name: str,
        old_updated_at: str,
        new_updated_at: str,
    ) -> str | None: ...
    def mark_operation(self, database: Path, operation_id: str, state: str) -> None: ...
    def commit_rename(
        self,
        database: Path,
        project_id: str,
        name: str,
        updated_at: str,
        request_id: str,
        operation_id: str,
    ) -> None: ...
    def save_checkpoint(
        self, database: Path, project_id: str, request_id: str
    ) -> Mapping[str, object]: ...
    def update_workspace_state(
        self, database: Path, project_id: str, state: Mapping[str, object], request_id: str
    ) -> Mapping[str, object]: ...
    def workspace_state(self, database: Path, project_id: str) -> str | None: ...


class AssetRepositoryPort(Protocol):
    def get(
        self, database: Path, project_id: str, asset_id: str, *, read_only: bool = False
    ) -> Mapping[str, object] | None: ...
    def content(
        self, database: Path, project_id: str, asset_id: str
    ) -> AssetContentRecord | None: ...
    def prepare_import(
        self, database: Path, *, project_id: str, request_id: str, payload: Mapping[str, object]
    ) -> dict[str, object]: ...
    def commit_import(
        self,
        database: Path,
        *,
        operation_id: str,
        project_id: str,
        asset_id: str,
        parent_asset_id: str | None,
        asset_type: str,
        asset_group: str | None,
        name: str,
        relative_path: str,
        mime: str,
        size: int,
        digest: str,
        metadata: Mapping[str, object],
        provenance: Mapping[str, object],
        created_at: str,
        thumbnail: tuple[str, str, str, int, str] | None,
    ) -> None: ...
    def mark_operation_failed(self, database: Path, operation_id: str) -> None: ...
    def mark_operation_file_written(self, database: Path, operation_id: str) -> None: ...
    def list_with_usage(
        self,
        database: Path,
        project_id: str,
        include_trashed: bool,
        group: str | None,
        include_hidden: bool,
        *,
        read_only: bool = False,
    ) -> list[Mapping[str, object]]: ...
    def usage_counts(
        self, database: Path, project_id: str, asset_id: str, *, read_only: bool = False
    ) -> dict[str, int] | None: ...
    def set_current_committed(
        self,
        database: Path,
        *,
        project_id: str,
        asset_id: str,
        source: str,
        reason: str | None,
        request_id: str,
    ) -> dict[str, object]: ...
    def prepare_derived(
        self, database: Path, *, project_id: str, request_id: str, payload: Mapping[str, object]
    ) -> dict[str, object]: ...
    def commit_derived(
        self,
        database: Path,
        *,
        operation_id: str,
        project_id: str,
        asset_id: str,
        parent_asset_id: str | None,
        input_asset_ids: Sequence[str],
        lineage_mode: str,
        asset_type: str,
        asset_group: str | None,
        name: str,
        relative_path: str,
        mime_type: str,
        size: int,
        digest: str,
        metadata: Mapping[str, object],
        provenance: Mapping[str, object],
        created_at: str,
    ) -> None: ...
    def lineage(
        self, database: Path, project_id: str, asset_id: str, *, read_only: bool = False
    ) -> dict[str, object] | None: ...
    def hide_committed(
        self,
        database: Path,
        *,
        project_id: str,
        asset_id: str,
        hidden: bool,
        request_id: str | None,
    ) -> bool: ...
    def impact(
        self, database: Path, project_id: str, asset_id: str, issued_at: int
    ) -> dict[str, object]: ...
    def prepare_trash(
        self,
        database: Path,
        *,
        project_id: str,
        asset_id: str,
        token: str | None,
        request_id: str | None,
        now_seconds: int,
    ) -> dict[str, object]: ...
    def commit_trash_committed(
        self,
        database: Path,
        *,
        operation_id: str,
        project_id: str,
        asset_id: str,
        old: str,
        new: str,
        was_current: bool,
    ) -> None: ...
    def prepare_restore(
        self, database: Path, *, project_id: str, asset_id: str, request_id: str
    ) -> dict[str, object]: ...
    def commit_restore_committed(
        self,
        database: Path,
        *,
        operation_id: str,
        project_id: str,
        asset_id: str,
        relative_path: str,
    ) -> None: ...


class SelectionRepositoryPort(Protocol):
    def get(
        self, database: Path, project_id: str, selection_id: str, *, read_only: bool = False
    ) -> Mapping[str, object] | None: ...
    def ids_for_asset(self, database: Path, project_id: str, asset_id: str) -> list[str]: ...
    def current_id(
        self, database: Path, project_id: str, asset_id: str, *, read_only: bool = False
    ) -> str | None: ...
    def editable_metadata(
        self, database: Path, project_id: str, asset_id: str
    ) -> SelectionMetadataRecord | None: ...
    def save_committed(
        self,
        database: Path,
        *,
        project_id: str,
        asset_id: str,
        selection_id: str,
        expected_revision: int | None,
        rects: Sequence[Mapping[str, object]],
        label: str,
        confidence: float | None,
        source: str,
        status: str,
        payload_hash: str,
        request_id: str | None,
    ) -> dict[str, object]: ...
    def confirm_committed(
        self,
        database: Path,
        *,
        project_id: str,
        selection_id: str,
        expected_revision: int,
        payload_hash: str,
        request_id: str | None,
    ) -> dict[str, object]: ...
    def cancel_committed(
        self,
        database: Path,
        *,
        project_id: str,
        selection_id: str | None,
        action_id: str,
        run_id: str | None,
    ) -> None: ...
    def source_asset(
        self, database: Path, project_id: str, asset_id: str
    ) -> SourceAssetRecord | None: ...


class PackageRepositoryPort(Protocol):
    def replay_export_request(
        self, database: Path, request_id: str, expected: Mapping[str, Any]
    ) -> dict[str, Any] | None: ...
    def prepare_export(
        self, database: Path, request_id: str, payload: Mapping[str, Any]
    ) -> str: ...
    def export_projection_database(
        self, database: Path
    ) -> tuple[
        dict[str, Any],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
    ]: ...
    def mark_export_file_written(self, database: Path, operation_id: str) -> None: ...
    def complete_export_committed(
        self, database: Path, operation_id: str, recovery: Mapping[str, Any]
    ) -> None: ...
    def rollback_export_committed(self, database: Path, operation_id: str) -> None: ...
    def import_manifest_committed(self, database: Path, manifest: Mapping[str, Any]) -> None: ...


class ModelAssetRepositoryPort(Protocol):
    def relative_path(self, database: Path, project_id: str, asset_id: str) -> str | None: ...
    def store_inspection(
        self, database: Path, project_id: str, asset_id: str, inspection: Mapping[str, Any]
    ) -> bool: ...


class PromptVersionRepositoryPort(Protocol):
    def append(
        self,
        database: Path,
        *,
        project_id: str,
        asset_id: str,
        kind: str,
        language: str,
        body: str,
        parser_version: int,
    ) -> str: ...

    def list_for_asset(
        self, database: Path, *, project_id: str, asset_id: str
    ) -> list[Mapping[str, object]]: ...


class ToolRepositoryPort(Protocol):
    def reserve_committed(
        self,
        database: Path,
        *,
        project_id: str,
        call_id: str,
        request_id: str,
        request_payload_hash: str,
        run_id: str | None,
        round_index: int,
        name: str,
        version: str,
        arguments_json: str,
        arguments: Mapping[str, object],
        provider_profile: str | None,
        risk_level: str,
        input_asset_ids: list[str],
        key_factory: Callable[[str, str, Mapping[str, object], list[str], str | None], str],
    ) -> dict[str, object]: ...
    def complete_request_committed(self, database: Path, request_id: str, payload: str) -> None: ...
    def finish_committed(
        self,
        database: Path,
        *,
        project_id: str,
        call_id: str,
        request_id: str,
        key: str,
        status: str,
        state: str,
        payload: str,
        duration_ms: int,
        output_asset_ids: list[str],
        ui_action: Mapping[str, object] | None,
        run_id: str | None,
    ) -> None: ...
    def fail_committed(
        self,
        database: Path,
        key: str,
        request_id: str,
        unknown: bool,
        error_payload: Mapping[str, Any],
    ) -> None: ...


class SettingsRepositoryPort(Protocol):
    def get_app(self, app_db: Path) -> dict[str, Any]: ...
    def update_app(
        self, app_db: Path, patch: dict[str, Any], payload_hash: str, request_id: str | None
    ) -> dict[str, Any]: ...
    def update_project(
        self, database: Path, patch: dict[str, Any], payload_hash: str, request_id: str | None
    ) -> dict[str, Any]: ...
    def replay_app_operation(
        self, app_db: Path, action: str, payload_hash: str, request_id: str
    ) -> dict[str, Any] | None: ...
    def record_app_operation(
        self, app_db: Path, action: str, payload_hash: str, request_id: str, result: dict[str, Any]
    ) -> None: ...


class EventRepositoryPort(Protocol):
    def append_named_in_tx(
        self,
        transaction: object,
        project_id: str,
        event_type: str,
        payload: Mapping[str, object],
        entity_id: str | None = None,
        run_id: str | None = None,
    ) -> Any: ...
    def replay_project(
        self, database: Path, project_id: str, sequence: int, limit: int
    ) -> list[Any]: ...
    def ack_committed(
        self, database: Path, project_id: str, consumer_id: str, sequence_no: int
    ) -> None: ...


class AppStateRepository(Protocol):
    def replay_operation(
        self, app_db: Path, action: str, payload_hash: str, request_id: str
    ) -> dict[str, object] | None: ...
    def complete_operation(
        self,
        app_db: Path,
        action: str,
        payload_hash: str,
        request_id: str,
        result: dict[str, object],
    ) -> None: ...
    def health_snapshot(self, roots: tuple[Path, ...], app_db: Path) -> dict[str, object]: ...
    def record_recent_project(self, app_db: Path, project_id: str, root: Path) -> None: ...
    def list_recent_projects(self, app_db: Path) -> list[dict[str, object]]: ...
    def recent_project_root(self, app_db: Path, project_id: str) -> Path | None: ...
