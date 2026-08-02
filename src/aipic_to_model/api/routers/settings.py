"""Settings and secret adapters with application-level idempotency."""

from fastapi import APIRouter, Depends

from ...domain.common import DomainErrorV1
from ..contracts import ProbeProviderRequest, SetSecretRequest, UpdateSettingsRequest
from ..dependencies import AppDependencies
from ..security import OriginGuard


def build_settings_router(guard: OriginGuard, dependencies: AppDependencies) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/settings", dependencies=[Depends(guard.check)])
    def get_settings():
        return dependencies.settings.get_app(dependencies.app_db)

    @router.patch("/v1/settings", dependencies=[Depends(guard.check)])
    def update_settings(body: UpdateSettingsRequest):
        if body.scope == "app":
            result = dependencies.settings.update_app(
                dependencies.app_db, body.patch, body.request_id
            )
            if dependencies.image_provider_monitor is not None:
                dependencies.image_provider_monitor.wake()
            return result
        if not body.project_id:
            raise DomainErrorV1("SCHEMA_VALIDATION_FAILED", "Project settings require project_id.")
        return dependencies.settings.update_project(
            dependencies.root_for(body.project_id), body.patch, body.request_id
        )

    @router.post("/v1/settings/secrets", dependencies=[Depends(guard.check)])
    def set_secret(body: SetSecretRequest):
        result = dependencies.settings.set_secret(
            dependencies.secret_store,
            body.provider_profile,
            body.secret,
            dependencies.app_db,
            body.request_id,
        )
        if dependencies.image_provider_monitor is not None:
            dependencies.image_provider_monitor.wake()
        return result

    @router.get("/v1/settings/image-generation-providers", dependencies=[Depends(guard.check)])
    def image_generation_providers():
        if dependencies.image_provider_monitor is None:
            return {"active_provider": None, "providers": []}
        return dependencies.image_provider_monitor.status_snapshot()

    @router.post(
        "/v1/settings/image-generation-providers/refresh",
        dependencies=[Depends(guard.check)],
    )
    def refresh_image_generation_providers():
        if dependencies.image_provider_monitor is None:
            return {"active_provider": None, "providers": []}
        return dependencies.image_provider_monitor.refresh()

    @router.get("/v1/settings/service-providers", dependencies=[Depends(guard.check)])
    def service_providers():
        if dependencies.image_provider_monitor is None:
            return {"providers": [], "probes_consume_generation_credits": False}
        return dependencies.image_provider_monitor.service_status_snapshot()

    @router.post(
        "/v1/settings/service-providers/refresh",
        dependencies=[Depends(guard.check)],
    )
    def refresh_service_providers():
        if dependencies.image_provider_monitor is None:
            return {"providers": [], "probes_consume_generation_credits": False}
        dependencies.image_provider_monitor.refresh()
        return dependencies.image_provider_monitor.service_status_snapshot()

    @router.post(
        "/v1/settings/service-providers/probe",
        dependencies=[Depends(guard.check)],
    )
    def probe_service_provider(body: ProbeProviderRequest):
        if dependencies.image_provider_monitor is None:
            return {"providers": [], "probes_consume_generation_credits": False}
        try:
            return dependencies.image_provider_monitor.refresh_profile(body.provider_profile)
        except ValueError as error:
            raise DomainErrorV1(
                "SCHEMA_VALIDATION_FAILED", "不支持的 Provider 配置。"
            ) from error

    return router
