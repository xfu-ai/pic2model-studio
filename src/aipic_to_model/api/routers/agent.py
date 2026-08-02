"""Authenticated Agent conversation and replay endpoints."""

from __future__ import annotations

import json
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ...agent.integrations import AgentRuntime
from ..contracts.agent import (
    ActivateSkillRequest,
    AgentConversationRequest,
    AgentQueueMessageRequest,
    CreateConversationRequest,
    SendAgentMessageRequest,
)
from ..security import OriginGuard


def build_agent_router(guard: OriginGuard, runtime: AgentRuntime) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/agent/conversations", dependencies=[Depends(guard.check)])
    def create(body: CreateConversationRequest):
        return runtime.create(body.project_id, system_prompt=body.system_prompt, model=body.model)

    @router.get("/v1/agent/conversations", dependencies=[Depends(guard.check)])
    def conversations(project_id: str, limit: int | None = None):
        return {"items": runtime.conversations(project_id, limit)}

    @router.get("/v1/agent/conversations/{conversation_id}", dependencies=[Depends(guard.check)])
    def status(conversation_id: str, project_id: str):
        return runtime.status(project_id, conversation_id)

    @router.get(
        "/v1/agent/conversations/{conversation_id}/messages", dependencies=[Depends(guard.check)]
    )
    def messages(
        conversation_id: str,
        project_id: str,
        limit: int | None = None,
        before: int | None = None,
    ):
        if limit is not None and not 1 <= limit <= 100:
            raise HTTPException(422, detail={"code": "SCHEMA_VALIDATION_FAILED"})
        if before is not None and before < 1:
            raise HTTPException(422, detail={"code": "SCHEMA_VALIDATION_FAILED"})
        page = runtime.message_page(
            project_id,
            conversation_id,
            limit=limit or 100,
            before=before,
        )
        page["event_cursor"] = runtime.event_cursor(project_id, conversation_id)
        return page

    @router.post(
        "/v1/agent/conversations/{conversation_id}/messages", dependencies=[Depends(guard.check)]
    )
    async def send(conversation_id: str, body: SendAgentMessageRequest, request: Request):
        if request.state.request_id != body.request_id:
            raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
        try:
            return await runtime.send(
                body.project_id,
                conversation_id,
                body.content,
                asset_refs=tuple(body.asset_refs),
                wait=body.wait,
            )
        except RuntimeError as error:
            if str(error) == "conversation_busy":
                raise HTTPException(409, detail={"code": "AGENT_BUSY"}) from error
            if str(error) in {
                "agent_attachment_not_found",
                "agent_attachment_not_image",
            }:
                raise HTTPException(422, detail={"code": "INVALID_AGENT_ATTACHMENT"}) from error
            raise

    @router.post(
        "/v1/agent/conversations/{conversation_id}/abort", dependencies=[Depends(guard.check)]
    )
    async def abort(conversation_id: str, body: AgentConversationRequest, request: Request):
        if request.state.request_id != body.request_id:
            raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
        return await runtime.abort(body.project_id, conversation_id)

    @router.post(
        "/v1/agent/conversations/{conversation_id}/queue", dependencies=[Depends(guard.check)]
    )
    def queue(conversation_id: str, body: AgentQueueMessageRequest, request: Request):
        if request.state.request_id != body.request_id:
            raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
        try:
            return runtime.queue(body.project_id, conversation_id, body.content, body.kind)
        except RuntimeError as error:
            if str(error) == "conversation_idle":
                raise HTTPException(409, detail={"code": "AGENT_IDLE"}) from error
            raise

    @router.get(
        "/v1/agent/conversations/{conversation_id}/skills", dependencies=[Depends(guard.check)]
    )
    def skills(conversation_id: str, project_id: str):
        return {"items": runtime.skills(project_id, conversation_id)}

    @router.post(
        "/v1/agent/conversations/{conversation_id}/skills/activate",
        dependencies=[Depends(guard.check)],
    )
    async def activate_skill(conversation_id: str, body: ActivateSkillRequest, request: Request):
        if request.state.request_id != body.request_id:
            raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
        return await runtime.activate_skill(body.project_id, conversation_id, body.name)

    @router.get(
        "/v1/agent/conversations/{conversation_id}/extensions", dependencies=[Depends(guard.check)]
    )
    def extensions(conversation_id: str, project_id: str):
        return runtime.extensions(project_id, conversation_id)

    @router.get(
        "/v1/agent/conversations/{conversation_id}/events", dependencies=[Depends(guard.check)]
    )
    def events(
        conversation_id: str,
        request: Request,
        project_id: str,
        after: int | None = None,
        limit: int = 100,
    ):
        if set(request.query_params) - {"project_id", "after", "limit"}:
            raise HTTPException(400, detail={"code": "SCHEMA_VALIDATION_FAILED"})
        header_after = request.headers.get("Last-Event-ID")
        sequence = after if after is not None else int(header_after or "0")
        page = runtime.events(project_id, conversation_id, sequence, limit)
        if "text/event-stream" not in request.headers.get("accept", ""):
            return page

        def stream():
            for event in cast(list[dict[str, Any]], page["items"]):
                yield f"id: {event['sequence_no']}\n"
                yield f"event: {event['event_type']}\n"
                yield f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    return router
