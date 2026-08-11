"""Pre-persistence Provider policy for generation Tool calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..domain.common import DomainErrorV1, ErrorCode, RiskLevel

LOCAL_IMAGE_PROFILE = "image/local/z-image-turbo"
LOCAL_IMAGE_MODEL = "Z-Image-Turbo"
LOCAL_MODEL3D_PROFILE = "model3d/local/triposr"
LOCAL_MODEL3D_MODEL = "stabilityai/TripoSR"
REMOTE_MODEL3D_PROFILE = "tripo3d/default"
REMOTE_MODEL3D_MODEL = "v3.1-20260211"

_BACKENDS = frozenset({"local", "remote", "auto"})


@dataclass(frozen=True)
class ResolvedToolRequest:
    arguments: dict[str, Any]
    risk_level: RiskLevel


class GenerationPolicyResolver:
    """Resolve a concrete generation Provider before a Tool Call is stored."""

    def __init__(self, settings: Any, local_monitor: Any, remote_images: Any) -> None:
        self._settings = settings
        self._local_monitor = local_monitor
        self._remote_images = remote_images

    def resolve(
        self,
        name: str,
        arguments: dict[str, Any],
        risk_level: RiskLevel,
    ) -> ResolvedToolRequest:
        if name == "image.generate":
            return self._resolve_image(arguments)
        if name == "model3d.generate":
            return self._resolve_model3d(arguments)
        return ResolvedToolRequest(dict(arguments), risk_level)

    def visibility_risk(self, name: str, risk_level: RiskLevel) -> RiskLevel:
        if name == "image.generate":
            backend = self._backend("image_generation_backend")
            if backend == "local" or (backend == "auto" and self._local_available(LOCAL_IMAGE_PROFILE)):
                return RiskLevel.LOCAL_REVERSIBLE
        if name == "model3d.generate":
            backend = self._backend("model3d_generation_backend")
            if backend == "local" or (backend == "auto" and self._local_available(LOCAL_MODEL3D_PROFILE)):
                return RiskLevel.LOCAL_REVERSIBLE
        return risk_level

    def _resolve_image(self, arguments: dict[str, Any]) -> ResolvedToolRequest:
        backend = self._backend("image_generation_backend")
        if backend in {"local", "auto"} and self._refresh_local(LOCAL_IMAGE_PROFILE):
            return ResolvedToolRequest(
                {
                    **arguments,
                    "provider_profile": LOCAL_IMAGE_PROFILE,
                    "channel": "z_image",
                    "model": LOCAL_IMAGE_MODEL,
                },
                RiskLevel.LOCAL_REVERSIBLE,
            )
        if backend == "local":
            self._unavailable("Z-Image-Turbo")
        selection = self._remote_images.resolve_route("t2i")
        return ResolvedToolRequest(
            {
                **arguments,
                "provider_profile": selection.profile,
                "channel": selection.channel,
                "model": selection.model,
            },
            RiskLevel.EXTERNAL_PAID,
        )

    def _resolve_model3d(self, arguments: dict[str, Any]) -> ResolvedToolRequest:
        mode = str(arguments.get("mode") or "")
        backend = self._backend("model3d_generation_backend")
        if mode == "image" and backend in {"local", "auto"}:
            if self._refresh_local(LOCAL_MODEL3D_PROFILE):
                parameters = arguments.get("parameters")
                local_parameters = dict(parameters) if isinstance(parameters, dict) else {}
                local_parameters.update(pbr=False, texture=True)
                return ResolvedToolRequest(
                    {
                        **arguments,
                        "provider_profile": LOCAL_MODEL3D_PROFILE,
                        "model": LOCAL_MODEL3D_MODEL,
                        "parameters": local_parameters,
                    },
                    RiskLevel.LOCAL_REVERSIBLE,
                )
            if backend == "local":
                self._unavailable("TripoSR")
        # TripoSR is single-image only. Multiview remains a concrete paid route
        # even when the preferred backend is local.
        return ResolvedToolRequest(
            {
                **arguments,
                "provider_profile": REMOTE_MODEL3D_PROFILE,
                "model": REMOTE_MODEL3D_MODEL,
            },
            RiskLevel.EXTERNAL_PAID,
        )

    def _backend(self, key: str) -> str:
        try:
            settings = self._settings()
        except Exception:  # noqa: BLE001 - a failed read uses the safe auto policy.
            settings = {}
        value = settings.get(key, "auto") if isinstance(settings, dict) else "auto"
        return str(value) if value in _BACKENDS else "auto"

    def _refresh_local(self, profile_id: str) -> bool:
        try:
            statuses = self._local_monitor.refresh(profile_id)
        except Exception:  # noqa: BLE001 - probe details must remain redacted.
            return False
        return any(status.profile_id == profile_id and status.available for status in statuses)

    def _local_available(self, profile_id: str) -> bool:
        try:
            statuses = self._local_monitor.snapshot()
        except Exception:  # noqa: BLE001 - visibility must remain stable.
            return False
        return any(status.profile_id == profile_id and status.available for status in statuses)

    @staticmethod
    def _unavailable(label: str) -> None:
        raise DomainErrorV1(
            ErrorCode.PROVIDER_NOT_CONFIGURED,
            f"{label} 本地运行时或模型尚未配置完成。",
            True,
        )


def effective_generation_risk(
    name: str,
    declared: RiskLevel,
    arguments: dict[str, Any],
) -> RiskLevel:
    """Reproduce the frozen request risk at the runtime boundary."""

    profile = arguments.get("provider_profile")
    if name == "image.generate" and profile == LOCAL_IMAGE_PROFILE:
        return RiskLevel.LOCAL_REVERSIBLE
    if name == "model3d.generate" and profile == LOCAL_MODEL3D_PROFILE:
        return RiskLevel.LOCAL_REVERSIBLE
    return declared
