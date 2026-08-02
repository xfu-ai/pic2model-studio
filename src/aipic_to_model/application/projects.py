from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from ..domain.common import DomainErrorV1, ErrorCode, ProjectRef, canonical_json, new_id, utc_now
from .operations import OperationService
from .ports import FilesystemPort, ProjectRepositoryPort


class ProjectService:
    """Project use cases; repository operations own SQLite lifecycle and transactions."""

    def __init__(
        self,
        repository: ProjectRepositoryPort,
        filesystem: FilesystemPort,
        operations: OperationService,
    ) -> None:
        self._repository = repository
        self._filesystem = filesystem
        self._operations = operations

    @staticmethod
    def _root_state(root: Path) -> str:
        probe = root / ".formweaver-write-probe"
        try:
            with probe.open("xb") as stream:
                stream.write(b"probe")
                stream.flush()
                os.fsync(stream.fileno())
            probe.unlink()
            return "available"
        except OSError:
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                return "read_only"
            return "read_only"

    def inspect_root(self, root: Path) -> dict[str, str]:
        root = root.resolve(strict=False)
        if not root.is_dir():
            raise DomainErrorV1(ErrorCode.PROJECT_NOT_FOUND, "Project root does not exist.")
        return {"root_path": ".", "root_state": self._root_state(root)}

    def create(self, root: Path, name: str) -> ProjectRef:
        root = self._filesystem.validate_new_root(root)
        selected_empty_root_existed = root.exists()
        project_id, now = new_id(), utc_now()
        name = name.strip()
        if not name:
            raise DomainErrorV1(ErrorCode.SCHEMA_VALIDATION_FAILED, "Project name cannot be empty.")
        staging = root.parent / f".{root.name}.{project_id}.creating"
        staging.mkdir(parents=True, exist_ok=False)
        try:
            for directory in self._filesystem.REQUIRED_DIRS:
                (staging / directory).mkdir(parents=True, exist_ok=True)
            self._filesystem.atomic_write_text(
                staging / "project.json",
                canonical_json(
                    {
                        "project_id": project_id,
                        "name": name,
                        "format_version": 1,
                        "created_at": now,
                        "updated_at": now,
                    },
                ),
            )
            database = staging / "project.sqlite3"
            self._filesystem.migrate(database, staging / "recovery")
            self._repository.create_database(database, project_id, name, now)
            if root.exists():
                # Native folder pickers return an existing directory.  Removing
                # that still-empty shell lets Windows perform the atomic rename.
                root.rmdir()
            os.replace(staging, root)
        except Exception as error:
            cleanup_staging = True
            if (
                isinstance(error, DomainErrorV1)
                and error.code == ErrorCode.MIGRATION_FAILED
                and (staging / "recovery").is_dir()
            ):
                retained = root.parent / f".{root.name}.{project_id}.migration-recovery"
                try:
                    os.replace(staging / "recovery", retained)
                except OSError:
                    # Keep the whole staging directory rather than discard
                    # the only failed-database diagnostic copy.
                    cleanup_staging = False
            if cleanup_staging:
                shutil.rmtree(staging, ignore_errors=True)
            if selected_empty_root_existed and not root.exists():
                root.mkdir(parents=False, exist_ok=True)
            raise
        return ProjectRef(project_id, name, ".", "available", 1, now)

    def open(self, root: Path, *, force_read_only: bool = False) -> ProjectRef:
        root = root.resolve(strict=False)
        try:
            metadata = json.loads((root / "project.json").read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise DomainErrorV1(ErrorCode.PROJECT_NOT_FOUND, "Project was not found.") from error
        except json.JSONDecodeError as error:
            raise DomainErrorV1(
                ErrorCode.PROJECT_METADATA_INCONSISTENT, "Invalid project metadata."
            ) from error
        if metadata.get("format_version") != 1:
            raise DomainErrorV1(ErrorCode.PROJECT_SCHEMA_UNSUPPORTED, "Unsupported project format.")
        state = self._root_state(root)
        database = root / "project.sqlite3"
        if not force_read_only and state != "read_only":
            self._filesystem.migrate(database, root / "recovery")
        row = self._repository.open_database(
            database, root, force_read_only or state == "read_only"
        )
        metadata = json.loads((root / "project.json").read_text(encoding="utf-8"))
        if (
            row is None
            or row["id"] != metadata.get("project_id")
            or row["root_path"] != "."
            or row["name"] != metadata.get("name")
            or row["updated_at"] != metadata.get("updated_at")
        ):
            raise DomainErrorV1(
                ErrorCode.PROJECT_METADATA_INCONSISTENT, "Project metadata is inconsistent."
            )
        return ProjectRef(
            row["id"], row["name"], ".", state, metadata["format_version"], row["updated_at"]
        )

    def rename(self, root: Path, project_id: str, name: str, request_id: str) -> ProjectRef:
        project = self.open(root)
        if project.id != project_id:
            raise DomainErrorV1(ErrorCode.PROJECT_NOT_FOUND, "Project does not exist.")
        if project.root_state == "read_only":
            raise DomainErrorV1(ErrorCode.PROJECT_READ_ONLY, "Project is read only.")
        name = name.strip()
        if not name:
            raise DomainErrorV1(ErrorCode.SCHEMA_VALIDATION_FAILED, "Project name cannot be empty.")
        metadata_path = root / "project.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        old_name, old_updated_at = metadata["name"], metadata["updated_at"]
        metadata["name"], metadata["updated_at"] = name, utc_now()
        database = root / "project.sqlite3"
        operation_id = self._repository.prepare_rename(
            database,
            request_id,
            old_name,
            name,
            old_updated_at,
            metadata["updated_at"],
        )
        if operation_id is None:
            return self.open(root)

        def restore_old_metadata() -> None:
            metadata["name"] = old_name
            self._filesystem.atomic_write_text(metadata_path, canonical_json(metadata))

        self._operations.execute(
            write_and_verify=lambda: self._filesystem.atomic_write_text(
                metadata_path, canonical_json(metadata)
            ),
            mark_file_written=lambda: self._repository.mark_operation(
                database, operation_id, "file_written"
            ),
            commit_database=lambda: self._repository.commit_rename(
                database, project_id, name, metadata["updated_at"], request_id, operation_id
            ),
            compensate_file=restore_old_metadata,
        )
        return ProjectRef(project_id, name, ".", "available", 1, metadata["updated_at"])

    def save_checkpoint(self, root: Path, project_id: str, request_id: str) -> dict[str, object]:
        self._filesystem.require_writable_root(root)
        project = self.open(root)
        if project.id != project_id:
            raise DomainErrorV1(ErrorCode.PROJECT_NOT_FOUND, "Project does not exist.")
        return dict(
            self._repository.save_checkpoint(root / "project.sqlite3", project_id, request_id)
        )

    def update_workspace_state(
        self, root: Path, project_id: str, state: dict[str, object], request_id: str
    ) -> dict[str, object]:
        project = self.open(root)
        if project.id != project_id:
            raise DomainErrorV1(ErrorCode.PROJECT_NOT_FOUND, "Project does not exist.")
        if project.root_state == "read_only":
            raise DomainErrorV1(ErrorCode.PROJECT_READ_ONLY, "Project is read only.")
        _validate_workspace_state(state)
        return dict(self._repository.update_workspace_state(root / "project.sqlite3", project_id, state, request_id))

    def workspace_state(self, root: Path, project_id: str) -> str:
        project = self.open(root)
        if project.id != project_id:
            raise DomainErrorV1(ErrorCode.PROJECT_NOT_FOUND, "Project does not exist.")
        state = self._repository.workspace_state(root / "project.sqlite3", project_id)
        return state if state is not None else "{}"


def _validate_workspace_state(state: dict[str, object]) -> None:
    allowed = {
        "workspace_mode", "agent_panel_width", "agent_panel_collapsed", "parameter_drawer",
        "canvas", "selection_id", "focus_target", "reference_context", "dismissed_job_ids", "image_generation_job_id", "workflow_contexts",
    }
    if set(state) - allowed:
        raise DomainErrorV1(ErrorCode.SCHEMA_VALIDATION_FAILED, "Unsupported workspace state field.")
    if "workspace_mode" in state and state["workspace_mode"] not in {
        "empty", "prompt_image", "image", "compare", "selection", "target_extract", "element_split", "box_split", "candidate", "multiview", "model3d", "task_waiting", "error_diagnostics",
    }:
        raise DomainErrorV1(ErrorCode.SCHEMA_VALIDATION_FAILED, "Unsupported workspace mode.")
    width = state.get("agent_panel_width")
    if width is not None and (not isinstance(width, int) or not 360 <= width <= 520):
        raise DomainErrorV1(ErrorCode.SCHEMA_VALIDATION_FAILED, "Agent panel width is outside the supported range.")
    canvas = state.get("canvas")
    if canvas is not None and (
        not isinstance(canvas, dict) or set(canvas) - {"zoom", "pan_x", "pan_y"}
        or not all(isinstance(value, (int, float)) for value in canvas.values())
    ):
        raise DomainErrorV1(ErrorCode.SCHEMA_VALIDATION_FAILED, "Canvas state is invalid.")
    reference_context = state.get("reference_context")
    reference_fields = {
        "content_asset_id", "style_asset_id", "content_analysis_asset_id", "style_analysis_asset_id",
        "content_prompt_asset_id", "style_prompt_asset_id", "merged_prompt_asset_id",
    }
    if reference_context is not None and (
        not isinstance(reference_context, dict)
        or set(reference_context) - reference_fields
        or not all(value is None or isinstance(value, str) for value in reference_context.values())
    ):
        raise DomainErrorV1(ErrorCode.SCHEMA_VALIDATION_FAILED, "Reference context is invalid.")
    dismissed = state.get("dismissed_job_ids")
    if dismissed is not None and (
        not isinstance(dismissed, list)
        or len(dismissed) > 200
        or not all(isinstance(value, str) and value for value in dismissed)
    ):
        raise DomainErrorV1(ErrorCode.SCHEMA_VALIDATION_FAILED, "Dismissed job list is invalid.")
    image_job = state.get("image_generation_job_id")
    if image_job is not None and not isinstance(image_job, str):
        raise DomainErrorV1(ErrorCode.SCHEMA_VALIDATION_FAILED, "Image generation job is invalid.")
    contexts = state.get("workflow_contexts")
    allowed_contexts = {"prompt_image", "target_extract", "element_split", "box_split", "multiview", "model3d"}
    if contexts is None:
        return
    if not isinstance(contexts, dict) or set(contexts) - allowed_contexts or not all(isinstance(value, dict) for value in contexts.values()):
        raise DomainErrorV1(ErrorCode.SCHEMA_VALIDATION_FAILED, "Workflow context is invalid.")

    def nullable_string(value: object) -> bool:
        return value is None or (isinstance(value, str) and len(value) <= 256)

    def bounded_text(value: object) -> bool:
        return isinstance(value, str) and len(value) <= 10_000

    def valid_rect(value: object) -> bool:
        if not isinstance(value, dict):
            return False
        required = {"x", "y", "width", "height"}
        allowed = required | {"rect_id", "label"}
        if not required.issubset(value) or set(value) - allowed:
            return False
        if not all(
            isinstance(value[key], (int, float))
            and not isinstance(value[key], bool)
            and value[key] >= 0
            for key in required
        ):
            return False
        if value["width"] < 1 or value["height"] < 1:
            return False
        return all(
            key not in value
            or (isinstance(value[key], str) and len(value[key]) <= 256)
            for key in ("rect_id", "label")
        )

    def valid_id_map(value: object) -> bool:
        return isinstance(value, dict) and len(value) <= 16 and all(isinstance(key, str) and len(key) <= 32 and isinstance(item, str) and len(item) <= 256 for key, item in value.items())

    prompt = contexts.get("prompt_image")
    if prompt is not None and (
        set(prompt)
        - {
            "prompt",
            "zh_prompt",
            "en_prompt",
            "display_language",
            "source_prompt_asset_id",
            "candidate_count",
            "aspect_ratio",
            "selected_candidate_id",
            "job_id",
            "rewrite_job_id",
        }
        or not bounded_text(prompt.get("prompt", ""))
        or not bounded_text(prompt.get("zh_prompt", ""))
        or not bounded_text(prompt.get("en_prompt", ""))
        or prompt.get("display_language", "zh") not in {"zh", "en"}
        or not nullable_string(prompt.get("source_prompt_asset_id"))
        or not isinstance(prompt.get("candidate_count", 2), int)
        or not 1 <= prompt.get("candidate_count", 2) <= 8
        or not isinstance(prompt.get("aspect_ratio", "1:1"), str)
        or len(prompt.get("aspect_ratio", "1:1")) > 32
        or not nullable_string(prompt.get("selected_candidate_id"))
        or not nullable_string(prompt.get("job_id"))
        or not nullable_string(prompt.get("rewrite_job_id"))
    ):
        raise DomainErrorV1(ErrorCode.SCHEMA_VALIDATION_FAILED, "Prompt image context is invalid.")
    element = contexts.get("element_split")
    if element is not None and (set(element) - {"source_asset_id", "split_result_asset_id", "target_crop_asset_id", "selection_rect", "prompt", "job_id"} or not all(nullable_string(element.get(key)) for key in ("source_asset_id", "split_result_asset_id", "target_crop_asset_id", "job_id")) or not bounded_text(element.get("prompt", "")) or (element.get("selection_rect") is not None and not valid_rect(element["selection_rect"]))):
        raise DomainErrorV1(ErrorCode.SCHEMA_VALIDATION_FAILED, "Element split context is invalid.")
    box = contexts.get("box_split")
    if box is not None and (set(box) - {"source_asset_id", "selection_id", "result_asset_id", "workflow", "prompt", "job_id"} or not all(nullable_string(box.get(key)) for key in ("source_asset_id", "selection_id", "result_asset_id", "job_id")) or box.get("workflow", "boxsplit") not in {"crop", "scene", "character", "custom", "boxsplit"} or not bounded_text(box.get("prompt", ""))):
        raise DomainErrorV1(ErrorCode.SCHEMA_VALIDATION_FAILED, "Box split context is invalid.")
    target = contexts.get("target_extract")
    target_fields = {
        "method", "stage", "source_asset_id", "source_selection_id", "source_selection_rect",
        "preset", "custom_prompt", "prompt_asset_id", "breakdown_asset_id",
        "breakdown_selection_id", "breakdown_selection_rect", "result_asset_ids",
        "active_result_asset_id", "job_id", "pending_action_id", "agent_action_id",
        "agent_run_id", "agent_instruction",
    }
    if target is not None and (
        set(target) - target_fields
        or target.get("method", "direct") not in {"direct", "breakdown"}
        or target.get("stage", "select_source") not in {
            "select_source", "select_target", "configure_breakdown", "awaiting_approval",
            "generating", "select_breakdown_part", "result", "error",
        }
        or target.get("preset", "scene") not in {"scene", "character", "custom"}
        or not all(nullable_string(target.get(key)) for key in (
            "source_asset_id", "source_selection_id", "prompt_asset_id",
            "breakdown_asset_id", "breakdown_selection_id", "active_result_asset_id",
            "job_id", "pending_action_id", "agent_action_id", "agent_run_id",
        ))
        or not bounded_text(target.get("custom_prompt", ""))
        or not bounded_text(target.get("agent_instruction", ""))
        or (
            target.get("source_selection_rect") is not None
            and not valid_rect(target["source_selection_rect"])
        )
        or (
            target.get("breakdown_selection_rect") is not None
            and not valid_rect(target["breakdown_selection_rect"])
        )
        or not isinstance(target.get("result_asset_ids", []), list)
        or len(target.get("result_asset_ids", [])) > 64
        or not all(nullable_string(item) and item is not None for item in target.get("result_asset_ids", []))
    ):
        raise DomainErrorV1(ErrorCode.SCHEMA_VALIDATION_FAILED, "Target extraction context is invalid.")
    multi = contexts.get("multiview")
    if multi is not None and (set(multi) - {"selected", "regions", "checks", "quality_confirmed", "set_id", "job_id"} or not valid_id_map(multi.get("selected", {})) or not isinstance(multi.get("regions", {}), dict) or len(multi.get("regions", {})) > 16 or not all(isinstance(key, str) and len(key) <= 32 and valid_rect(item) for key, item in multi.get("regions", {}).items()) or not isinstance(multi.get("checks", {}), dict) or len(multi.get("checks", {})) > 16 or not all(isinstance(key, str) and len(key) <= 32 and item in {"passed", "warning", "blocking"} for key, item in multi.get("checks", {}).items()) or not isinstance(multi.get("quality_confirmed", False), bool) or not nullable_string(multi.get("set_id")) or not nullable_string(multi.get("job_id"))):
        raise DomainErrorV1(ErrorCode.SCHEMA_VALIDATION_FAILED, "Multiview context is invalid.")
    model = contexts.get("model3d")
    if model is not None and (
        set(model) - {"asset_id", "target_triangles", "generation_job_id"}
        or not nullable_string(model.get("asset_id"))
        or not nullable_string(model.get("generation_job_id"))
        or not isinstance(model.get("target_triangles", 50_000), int)
        or not 4 <= model.get("target_triangles", 50_000) <= 10_000_000
    ):
        raise DomainErrorV1(ErrorCode.SCHEMA_VALIDATION_FAILED, "3D model context is invalid.")
