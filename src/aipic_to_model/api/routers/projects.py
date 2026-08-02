"""Project routes use application services through the composed dependency container."""

from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, Depends, Request

from ..contracts import (
    CreateProjectRequest,
    ExportProjectRequest,
    OpenProjectRequest,
    RenameProjectRequest,
    UpdateWorkspaceStateRequest,
)
from ..dependencies import AppDependencies
from ..security import OriginGuard
from ...domain.common import DomainErrorV1, ErrorCode


def _export_file_in_directory(directory: Path, project_name: str, suffix: str) -> Path:
    """Derive a safe, user-recognizable package name from a folder capability."""

    safe_stem = "".join(
        "_" if ord(character) < 32 or character in '<>:"/\\|?*' else character
        for character in project_name.strip()
    ).strip(". ")
    if not safe_stem:
        safe_stem = "project"
    return directory / f"{safe_stem[:100]}-backup{suffix}"


def build_project_router(
    guard: OriginGuard,
    dependencies: AppDependencies,
    replay_app_command: Callable[[str, dict[str, str], str], dict | None],
    complete_app_command: Callable[[str, dict[str, str], str, dict], None],
) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/projects", dependencies=[Depends(guard.check)])
    def create(body: CreateProjectRequest, request: Request):
        payload = {"name": body.name, "create_capability_id": body.create_capability_id}
        request_id = request.state.request_id
        replayed = replay_app_command("projects.create", payload, request_id)
        if replayed is not None:
            return replayed
        root = dependencies.capabilities.resolve_once(body.create_capability_id, "create")
        project = dependencies.projects.create(root, body.name)
        dependencies.roots[project.id] = root
        dependencies.app_state.record_recent_project(dependencies.app_db, project.id, root)
        dependencies.job_recovery.recover(root)
        if dependencies.job_runner is not None:
            dependencies.job_runner.wake()
        result = project.__dict__
        complete_app_command("projects.create", payload, request_id, result)
        return result

    @router.post("/v1/projects/open", dependencies=[Depends(guard.check)])
    def open_project(body: OpenProjectRequest, request: Request):
        payload = {"open_capability_id": body.open_capability_id}
        request_id = request.state.request_id
        replayed = replay_app_command("projects.open", payload, request_id)
        if replayed is not None:
            return replayed
        root = dependencies.capabilities.resolve_once(body.open_capability_id, "open")
        project = dependencies.projects.open(root)
        dependencies.roots[project.id] = root
        dependencies.app_state.record_recent_project(dependencies.app_db, project.id, root)
        dependencies.job_recovery.recover(root)
        if dependencies.job_runner is not None:
            dependencies.job_runner.wake()
        result = project.__dict__
        complete_app_command("projects.open", payload, request_id, result)
        return result

    @router.get("/v1/projects/recent", dependencies=[Depends(guard.check)])
    def recent_projects():
        return {"projects": dependencies.app_state.recent_projects(dependencies.app_db)}

    @router.get("/v1/projects/{project_id}", dependencies=[Depends(guard.check)])
    def get_project(project_id: str):
        root = dependencies.root_for(project_id)
        project = dependencies.projects.open(root)
        return {
            **project.__dict__,
            "workspace_state_json": dependencies.projects.workspace_state(root, project_id),
        }

    @router.patch("/v1/projects/{project_id}", dependencies=[Depends(guard.check)])
    def rename_project(project_id: str, body: RenameProjectRequest):
        return dependencies.projects.rename(
            dependencies.root_for(project_id), project_id, body.name, body.request_id
        ).__dict__

    @router.patch("/v1/projects/{project_id}/workspace-state", dependencies=[Depends(guard.check)])
    def update_workspace_state(project_id: str, body: UpdateWorkspaceStateRequest):
        return dependencies.projects.update_workspace_state(
            dependencies.root_for(project_id), project_id, body.state, body.request_id
        )

    @router.post("/v1/projects/{project_id}/export", dependencies=[Depends(guard.check)])
    def export_project(project_id: str, body: ExportProjectRequest):
        root = dependencies.root_for(project_id)
        replayed = dependencies.packages.replay_export_request(
            root, "project_v1", body.request_id, body.export_capability_id
        )
        if replayed is not None:
            return replayed
        destination_directory = dependencies.capabilities.resolve_once(
            body.export_capability_id, "export", project_id
        )
        if not destination_directory.is_dir():
            raise DomainErrorV1(
                ErrorCode.SECURITY_CAPABILITY_INVALID,
                "Export destination must be a folder.",
            )
        project = dependencies.projects.open(root)
        destination = _export_file_in_directory(
            destination_directory, project.name, ".aipicproject"
        )
        return dependencies.packages.export_v1(
            root,
            destination,
            overwrite=True,
            request_id=body.request_id,
            capability_id=body.export_capability_id,
        )

    return router
