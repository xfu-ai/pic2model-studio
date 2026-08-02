"""Direct API views for durable Jobs; mutations reuse canonical Tool dispatch."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from ..dependencies import AppDependencies
from ..security import OriginGuard


class JobCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)


def build_job_router(guard: OriginGuard, dependencies: AppDependencies) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/projects/{project_id}/jobs", dependencies=[Depends(guard.check)])
    def list_jobs(project_id: str, include_terminal: bool = False):
        return {"items": dependencies.b02_runtime.job_views(dependencies.root_for(project_id), include_terminal=include_terminal)}

    @router.get("/v1/jobs/{job_id}", dependencies=[Depends(guard.check)])
    def get_job(job_id: str, project_id: str):
        return dependencies.b02_runtime.job_view(dependencies.root_for(project_id), job_id)

    def command(name: str, job_id: str, body: JobCommandRequest):
        result = dependencies.registry.execute(
            dependencies.root_for(body.project_id),
            body.project_id,
            name,
            "1.0.0",
            {"job_id": job_id},
            body.request_id,
        )
        return result.__dict__

    @router.post("/v1/jobs/{job_id}/cancel", dependencies=[Depends(guard.check)])
    def cancel(job_id: str, body: JobCommandRequest):
        return command("job.cancel", job_id, body)

    @router.post("/v1/jobs/{job_id}/retry", dependencies=[Depends(guard.check)])
    def retry(job_id: str, body: JobCommandRequest):
        return command("job.retry", job_id, body)

    @router.post(
        "/v1/jobs/{job_id}/confirm-new-submission",
        dependencies=[Depends(guard.check)],
    )
    def confirm_new_submission(job_id: str, body: JobCommandRequest):
        return command("job.confirm_new_submission", job_id, body)

    return router
