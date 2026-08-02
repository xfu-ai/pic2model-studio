"""Finite event replay endpoint; it deliberately does not expose a stream-only route."""

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from ...domain.common import DomainErrorV1, EventCursorCodec
from ..dependencies import AppDependencies
from ..security import OriginGuard


def build_event_router(guard: OriginGuard, dependencies: AppDependencies) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/events", dependencies=[Depends(guard.check)])
    def events(request: Request, project_id: str, after: str | None = None, limit: int = 100):
        if set(request.query_params) - {"project_id", "after", "limit"}:
            raise DomainErrorV1("SCHEMA_VALIDATION_FAILED", "Event query has unknown fields.")
        after = after or request.headers.get("Last-Event-ID")
        result = dependencies.events.replay_project(
            dependencies.root_for(project_id), project_id, after, limit
        )
        if "text/event-stream" in request.headers.get("accept", ""):

            def stream():
                for event in result["items"]:
                    yield f"id: {EventCursorCodec.encode(project_id, event['sequence_no'])}\n"
                    yield f"event: {event['event_type']}\n"
                    yield f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"

            return StreamingResponse(
                stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
            )
        return result

    return router
