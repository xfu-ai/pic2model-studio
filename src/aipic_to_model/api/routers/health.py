from collections.abc import Callable

from fastapi import APIRouter, Depends

from ..security import OriginGuard


def build_health_router(guard: OriginGuard, snapshot: Callable) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/health", dependencies=[Depends(guard.check)])
    def health():
        return snapshot()

    return router
