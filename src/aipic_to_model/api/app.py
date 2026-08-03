from __future__ import annotations

import hashlib
import json
import os
import platform
import secrets
import ssl
import tempfile
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from ..agent.integrations import FACADE_TOOL_NAMES, AgentRuntime
from ..agent.providers.base import AgentModelProvider, ModelProfile
from ..application.host_capabilities import HostCapabilityStore
from ..composition import compose_local_app
from ..domain.common import DomainErrorV1
from ..infrastructure.providers.config import (
    GEMINI_PROFILE,
    MESHY_PROFILE,
    TRIPO_PROFILE,
    CredentialResolver,
)
from .dependencies import AppDependencies
from .routers.agent import build_agent_router
from .routers.approvals import build_approval_router
from .routers.assets import build_asset_router
from .routers.diagnostics import build_diagnostics_router
from .routers.events import build_event_router
from .routers.health import build_health_router
from .routers.host import build_host_router
from .routers.jobs import build_job_router
from .routers.models import build_model_router
from .routers.projects import build_project_router
from .routers.prompts import build_prompt_router
from .routers.selections import build_selection_router
from .routers.settings import build_settings_router
from .routers.tools import build_tool_router
from .security import HostControlGuard, OriginGuard


def create_app(
    token: str | None = None,
    capabilities: HostCapabilityStore | None = None,
    app_db: Path | None = None,
    agent_provider_factory: Callable[[ModelProfile], AgentModelProvider] | None = None,
    host_control_token: str | None = None,
    renderer_origin: str = "http://tauri.localhost",
) -> FastAPI:
    token = token or secrets.token_urlsafe(32)
    guard = OriginGuard(token, renderer_origin)
    host_guard = HostControlGuard(host_control_token) if host_control_token else None
    caps = capabilities or HostCapabilityStore()
    app_db = app_db or Path(tempfile.gettempdir()) / "Pic2Model Studio" / "app.sqlite3"
    dependencies: AppDependencies = compose_local_app(caps, app_db)
    roots = dependencies.roots
    controlled_health_failures = (
        int(os.environ.get("AIPIC_CONTROLLED_E2E_HEALTH_FAILURES", "0"))
        if os.environ.get("AIPIC_CONTROLLED_E2E") == "1"
        else 0
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if dependencies.job_runner is not None:
            dependencies.job_runner.start()
        if dependencies.image_provider_monitor is not None:
            dependencies.image_provider_monitor.start()
        try:
            yield
        finally:
            if dependencies.image_provider_monitor is not None:
                dependencies.image_provider_monitor.stop()
            if dependencies.job_runner is not None:
                dependencies.job_runner.stop()

    app = FastAPI(
        title="Pic2Model Studio B01",
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[renderer_origin],
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Last-Event-ID",
            "X-Host-Control-Token",
            "X-Request-Id",
        ],
    )
    credential_resolver = CredentialResolver(dependencies.secret_store)

    def agent_runtime_context(project_id: str) -> dict[str, object]:
        """Return current, non-secret execution truth without probing a Provider."""

        root = dependencies.root_for(project_id)
        project = dependencies.projects.open(root, force_read_only=True)
        try:
            workspace = json.loads(dependencies.projects.workspace_state(root, project_id))
        except (json.JSONDecodeError, TypeError):
            workspace = {}
        if not isinstance(workspace, dict):
            workspace = {}
        assets = dependencies.assets.list_by_group(
            root, project_id, group=None, read_only=True
        )
        jobs = dependencies.b02_runtime.job_views(root, include_terminal=False)
        controlled = os.environ.get("AIPIC_CONTROLLED_E2E") == "1"
        public_profiles = dependencies.settings.get_app(dependencies.app_db).get(
            "provider_profiles", {}
        )
        if not isinstance(public_profiles, dict):
            public_profiles = {}

        def provider_state(profile: str) -> dict[str, object]:
            public = public_profiles.get(profile, {})
            enabled = not isinstance(public, dict) or public.get("enabled", True) is not False
            configured = controlled
            if not controlled and enabled:
                try:
                    configured = bool(credential_resolver.get(profile))
                except Exception:  # noqa: BLE001 - state reporting must not expose keyring failures.
                    configured = False
            available = bool(enabled and configured)
            return {
                "configured": configured,
                "available": available,
                "mode": "controlled_offline" if controlled else "live",
                "unavailable_reason": (
                    None
                    if available
                    else "provider_disabled"
                    if not enabled
                    else "provider_not_configured"
                ),
            }

        def image_generation_state() -> dict[str, object]:
            monitor = dependencies.image_provider_monitor
            if monitor is None:
                return provider_state(MESHY_PROFILE)
            status = monitor.status_snapshot()
            providers = status.get("providers", [])
            available = any(
                isinstance(item, dict) and item.get("available") is True
                for item in providers
            )
            configured = any(
                isinstance(item, dict) and item.get("configured") is True
                for item in providers
            )
            return {
                "configured": configured,
                "available": available,
                "mode": "controlled_offline" if controlled else "live",
                "active_provider": status.get("active_provider"),
                "unavailable_reason": None if available else "provider_unavailable",
            }

        counts: dict[str, int] = {}
        for asset in assets:
            kind = str(asset.get("asset_type", "unknown"))
            counts[kind] = counts.get(kind, 0) + 1
        current = next((asset for asset in assets if asset.get("is_current")), None)
        contexts = workspace.get("workflow_contexts", {})
        contexts = contexts if isinstance(contexts, dict) else {}
        references = workspace.get("reference_context", {})
        references = references if isinstance(references, dict) else {}
        multiview = contexts.get("multiview", {})
        multiview = multiview if isinstance(multiview, dict) else {}
        model3d = contexts.get("model3d", {})
        model3d = model3d if isinstance(model3d, dict) else {}
        capabilities = {
            "image_analysis": provider_state(GEMINI_PROFILE),
            "image_generation": image_generation_state(),
            "model3d_generation": provider_state(TRIPO_PROFILE),
            "local_model_processing": {
                "configured": True,
                "available": True,
                "mode": "local",
                "unavailable_reason": None,
            },
        }
        snapshot: dict[str, object] = {
            "schema_version": 1,
            "configuration_state": "current",
            "facade_tools": list(FACADE_TOOL_NAMES),
            "project": {
                "bound": True,
                "read_only": project.root_state == "read_only",
            },
            "workspace": {
                "mode": workspace.get("workspace_mode", "empty"),
                "current_asset_ref": current.get("id") if current else None,
                "current_selection_ref": workspace.get("selection_id"),
                "current_prompt_ref": references.get("merged_prompt_asset_id"),
                "current_multiview_ref": multiview.get("set_id"),
                "current_model_ref": model3d.get("asset_id"),
            },
            "assets": {
                "counts_by_kind": counts,
                "recent": [
                    {
                        "asset_ref": asset.get("id"),
                        "kind": asset.get("asset_type"),
                        "current": bool(asset.get("is_current")),
                    }
                    for asset in assets[-12:]
                ],
            },
            "jobs": {
                "nonterminal": [
                    {
                        "job_ref": job.get("id"),
                        "job_type": job.get("job_type"),
                        "status": job.get("status"),
                        "stage": job.get("stage"),
                        "progress": job.get("progress"),
                        "can_cancel": job.get("can_cancel"),
                        "can_stop_waiting": job.get("can_stop_waiting"),
                    }
                    for job in jobs
                ]
            },
            "capabilities": capabilities,
            "tool_conditions": {
                "generate_images": {
                    "ready": capabilities["image_generation"]["available"],
                    "missing": []
                    if capabilities["image_generation"]["available"]
                    else ["image_generation_provider"],
                },
                "generate_model3d": {
                    "ready": capabilities["model3d_generation"]["available"],
                    "missing": []
                    if capabilities["model3d_generation"]["available"]
                    else ["model3d_provider"],
                },
                "control_job": {
                    "ready": bool(jobs),
                    "missing": [] if jobs else ["job_ref"],
                },
            },
        }
        digest_source = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
        snapshot["snapshot_version"] = int.from_bytes(
            hashlib.sha256(digest_source.encode("utf-8")).digest()[:8], "big"
        )
        return snapshot

    agent_runtime = AgentRuntime(
        dependencies.registry,
        dependencies.root_for,
        provider_factory=agent_provider_factory,
        runtime_context_provider=agent_runtime_context,
        attachment_provider=lambda project_id, asset_id: dependencies.assets.get(
            dependencies.root_for(project_id),
            project_id,
            asset_id,
            read_only=True,
        ),
    )

    def replay_app_command(action: str, payload: dict[str, str], request_id: str) -> dict | None:
        """Replay safe app-scoped commands without persisting paths or capabilities."""
        return dependencies.app_state.replay_command(app_db, action, payload, request_id)

    def complete_app_command(
        action: str, payload: dict[str, str], request_id: str, result: dict
    ) -> None:
        dependencies.app_state.complete_command(app_db, action, payload, request_id, result)

    @app.middleware("http")
    async def require_command_request_id(request: Request, call_next):
        if request.method in {"POST", "PATCH", "PUT", "DELETE"} and request.url.path.startswith(
            "/v1/"
        ):
            request_id = request.headers.get("X-Request-Id")
            if not request_id:
                return JSONResponse(
                    {
                        "code": "SCHEMA_VALIDATION_FAILED",
                        "user_message": "X-Request-Id is required for commands.",
                        "recoverable": False,
                    },
                    status_code=400,
                )
            content_type = request.headers.get("content-type", "")
            if "application/json" in content_type:
                try:
                    body = json.loads(await request.body())
                except json.JSONDecodeError:
                    body = None
                if (
                    isinstance(body, dict)
                    and "request_id" in body
                    and body["request_id"] != request_id
                ):
                    return JSONResponse(
                        {
                            "code": "IDEMPOTENCY_CONFLICT",
                            "user_message": "X-Request-Id must match request_id.",
                            "recoverable": False,
                        },
                        status_code=409,
                    )
            request.state.request_id = request_id
        return await call_next(request)

    @app.exception_handler(DomainErrorV1)
    async def error(_, exc):
        status = {
            "PROJECT_NOT_FOUND": 404,
            "ASSET_NOT_FOUND": 404,
            "SECURITY_CAPABILITY_INVALID": 403,
            "PROJECT_READ_ONLY": 409,
            "ASSET_REFERENCED": 409,
            "IDEMPOTENCY_CONFLICT": 409,
            "SCHEMA_VALIDATION_FAILED": 400,
        }.get(str(exc.code), 400)
        safe = {
            key: value
            for key, value in exc.as_dict().items()
            if key in {"code", "user_message", "recoverable", "retry_after_seconds", "details_ref"}
        }
        return Response(
            content=__import__("json").dumps(safe, ensure_ascii=False),
            status_code=status,
            media_type="application/json",
        )

    @app.exception_handler(HTTPException)
    async def http_error(_, exc: HTTPException):
        body = (
            exc.detail
            if isinstance(exc.detail, dict)
            else {"code": "HTTP_ERROR", "user_message": "请求被拒绝。"}
        )
        return Response(
            content=__import__("json").dumps(body, ensure_ascii=False),
            status_code=exc.status_code,
            media_type="application/json",
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_, __):
        return Response(
            content=__import__("json").dumps(
                {
                    "code": "SCHEMA_VALIDATION_FAILED",
                    "user_message": "请求格式无效。",
                    "recoverable": False,
                },
                ensure_ascii=False,
            ),
            status_code=400,
            media_type="application/json",
        )

    @app.exception_handler(Exception)
    async def unexpected_error(_, __):
        return Response(
            content=__import__("json").dumps(
                {
                    "code": "INTERNAL_ERROR",
                    "user_message": "The local service could not complete the command.",
                    "recoverable": False,
                },
                ensure_ascii=False,
            ),
            status_code=500,
            media_type="application/json",
        )

    @app.get("/v1/openapi.json", dependencies=[Depends(guard.check)])
    def openapi_schema():
        return app.openapi()

    def health_snapshot() -> dict[str, object]:
        nonlocal controlled_health_failures
        if controlled_health_failures > 0:
            controlled_health_failures -= 1
            raise HTTPException(
                status_code=503,
                detail={"code": "CONTROLLED_SIDECAR_OFFLINE", "recoverable": True},
            )
        return {
            **dependencies.app_state.health_snapshot(tuple(roots.values()), app_db),
            **agent_runtime.health(),
            "provider_runtime": {
                "python": platform.python_version(),
                "openssl": ssl.OPENSSL_VERSION,
                "tls_compatible": ssl.OPENSSL_VERSION_INFO[:2] != (3, 5),
            },
        }

    app.include_router(build_health_router(guard, health_snapshot))
    if host_guard is not None:
        app.include_router(build_host_router(guard, host_guard, caps, dependencies))
    app.include_router(
        build_project_router(guard, dependencies, replay_app_command, complete_app_command)
    )
    app.include_router(build_asset_router(guard, dependencies))
    app.include_router(build_model_router(guard, dependencies))
    app.include_router(build_selection_router(guard, dependencies))
    app.include_router(build_tool_router(guard, dependencies))
    app.include_router(build_approval_router(guard, dependencies))
    app.include_router(build_prompt_router(guard, dependencies))
    app.include_router(build_job_router(guard, dependencies))
    app.include_router(build_event_router(guard, dependencies))
    app.include_router(build_agent_router(guard, agent_runtime))
    app.include_router(build_settings_router(guard, dependencies))
    app.include_router(build_diagnostics_router(guard, dependencies))

    return app
