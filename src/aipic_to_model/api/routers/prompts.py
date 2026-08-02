"""Managed Prompt version write boundary for the desktop parameter drawer."""

from fastapi import APIRouter, Depends, Request

from ...domain.prompt_parser import BilingualPrompt
from ..contracts import SavePromptVersionRequest
from ..dependencies import AppDependencies
from ..security import OriginGuard


def build_prompt_router(guard: OriginGuard, dependencies: AppDependencies) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/v1/projects/{project_id}/prompts",
        dependencies=[Depends(guard.check)],
    )
    def save_prompt(
        project_id: str,
        body: SavePromptVersionRequest,
        request: Request,
    ):
        if request.state.request_id != body.request_id:
            from fastapi import HTTPException

            raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
        return dependencies.prompt_versions.create_bilingual(
            dependencies.root_for(project_id),
            project_id,
            kind=body.kind,
            bilingual=BilingualPrompt(
                body.zh_prompt,
                body.en_prompt,
                body.zh_prompt,
                body.en_prompt,
                ("用户确认的主体、构图和生成要求",),
                ("未经请求的主体或场景变化",),
            ),
            request_id=body.request_id,
            parent_asset_id=body.parent_asset_id,
            provenance={"parameters": {"source": "prompt_parameter_drawer"}},
        )

    return router
