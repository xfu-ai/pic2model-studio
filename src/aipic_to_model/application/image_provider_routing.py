"""Cached availability and priority routing for paid image generation."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from typing import Any

from ..domain.provider_models import ProviderResult
from ..infrastructure.providers.http_errors import http_failure

AUTO_IMAGE_PROFILE = "image-generation/auto"
DEFAULT_IMAGE_PROVIDER_PRIORITY = ("tripo3d/default", "meshy/default")


@dataclass(frozen=True)
class ImageProviderRoute:
    profile: str
    label: str
    channel: str
    default_model: str
    modes: frozenset[str]
    provider: Any
    mode_models: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CredentialProbeRoute:
    profile: str
    label: str
    channel: str
    default_model: str
    capabilities: tuple[str, ...]
    provider: Any


class PrioritizedImageGenerationProvider:
    """Select a healthy Provider before any paid create request is made.

    A running task is never moved to another Provider. This prevents an
    ambiguous submission from being repeated and charged twice.
    """

    def __init__(
        self,
        routes: list[ImageProviderRoute],
        settings: Any,
        *,
        stale_after_seconds: float = 300.0,
        credential_probes: list[CredentialProbeRoute] | None = None,
    ) -> None:
        self._routes = {route.profile: route for route in routes}
        self._credential_probes = {
            route.profile: route for route in (credential_probes or [])
        }
        self._settings = settings
        self._stale_after = stale_after_seconds
        self._lock = threading.RLock()
        self._status: dict[str, dict[str, object]] = {}
        self._credential_status: dict[str, dict[str, object]] = {}
        self._last_refresh = 0.0
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="aipic-image-provider-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self._thread = None

    def wake(self) -> None:
        self._wake.set()

    def refresh(self) -> dict[str, object]:
        checked_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        refreshed: dict[str, dict[str, object]] = {}
        for profile, route in self._routes.items():
            refreshed[profile] = self._probe_status(
                profile=profile,
                label=route.label,
                channel=route.channel,
                provider=route.provider,
                checked_at=checked_at,
                modes=sorted(route.modes),
            )
        credential_refreshed: dict[str, dict[str, object]] = {}
        for profile, route in self._credential_probes.items():
            credential_refreshed[profile] = self._probe_status(
                profile=profile,
                label=route.label,
                channel=route.channel,
                provider=route.provider,
                checked_at=checked_at,
                capabilities=list(route.capabilities),
            )
        with self._lock:
            self._status = refreshed
            self._credential_status = credential_refreshed
            self._last_refresh = monotonic()
        return self.status_snapshot()

    def refresh_profile(self, profile: str) -> dict[str, object]:
        checked_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        if profile in self._routes:
            route = self._routes[profile]
            status = self._probe_status(
                profile=profile,
                label=route.label,
                channel=route.channel,
                provider=route.provider,
                checked_at=checked_at,
                modes=sorted(route.modes),
            )
            with self._lock:
                self._status[profile] = status
                self._last_refresh = monotonic()
        elif profile in self._credential_probes:
            route = self._credential_probes[profile]
            status = self._probe_status(
                profile=profile,
                label=route.label,
                channel=route.channel,
                provider=route.provider,
                checked_at=checked_at,
                capabilities=list(route.capabilities),
            )
            with self._lock:
                self._credential_status[profile] = status
        else:
            raise ValueError("Unknown Provider profile")
        return self.service_status_snapshot()

    @staticmethod
    def _probe_status(
        *,
        profile: str,
        label: str,
        channel: str,
        provider: Any,
        checked_at: str,
        modes: list[str] | None = None,
        capabilities: list[str] | None = None,
    ) -> dict[str, object]:
        try:
            result = provider.probe()
        except Exception:  # noqa: BLE001 - provider failures must stay redacted.
            result = http_failure(operation="probing", status_code=503)
        error_code = result.error.code if result.error is not None else None
        return {
            "profile": profile,
            "label": label,
            "channel": channel,
            "configured": error_code != "PROVIDER_NOT_CONFIGURED",
            "available": result.ok,
            "reason": None if result.ok else error_code or "PROVIDER_UNAVAILABLE",
            "last_checked_at": checked_at,
            "modes": modes or [],
            "capabilities": capabilities or [],
        }

    def status_snapshot(self) -> dict[str, object]:
        order = self._priority()
        with self._lock:
            statuses = [dict(self._status.get(profile, self._unknown(profile))) for profile in order]
            for profile in self._routes:
                if profile not in order:
                    statuses.append(dict(self._status.get(profile, self._unknown(profile))))
        for index, status in enumerate(statuses):
            status["priority"] = index + 1
            status["model"] = self._model_for(str(status["profile"]))
            route = self._routes[str(status["profile"])]
            status["models"] = {
                mode: self._model_for(str(status["profile"]), mode)
                for mode in sorted(route.modes)
            }
        active = next(
            (str(item["profile"]) for item in statuses if item.get("available") is True),
            None,
        )
        return {
            "profile": AUTO_IMAGE_PROFILE,
            "active_provider": active,
            "priority": order,
            "probe_interval_seconds": self._probe_interval(),
            "probes_consume_generation_credits": False,
            "providers": statuses,
        }

    def service_status_snapshot(self) -> dict[str, object]:
        image = self.status_snapshot()
        image_providers = [dict(item) for item in image["providers"]]
        with self._lock:
            credential_providers = [
                dict(
                    self._credential_status.get(
                        profile,
                        {
                            "profile": profile,
                            "label": route.label,
                            "channel": route.channel,
                            "configured": False,
                            "available": False,
                            "reason": "not_checked",
                            "last_checked_at": None,
                            "modes": [],
                            "capabilities": list(route.capabilities),
                        },
                    )
                )
                for profile, route in self._credential_probes.items()
            ]
        for item in image_providers:
            item["capabilities"] = [
                label
                for mode, label in (("t2i", "text_to_image"), ("i2i", "image_editing"))
                if mode in item.get("modes", [])
            ]
        for index, item in enumerate([*image_providers, *credential_providers]):
            item["display_order"] = index + 1
            if "model" not in item:
                route = self._credential_probes[str(item["profile"])]
                item["model"] = route.default_model
                item["models"] = {}
        return {
            "probe_interval_seconds": image["probe_interval_seconds"],
            "probes_consume_generation_credits": False,
            "providers": [*image_providers, *credential_providers],
        }

    def generate(self, request: dict[str, object]) -> ProviderResult:
        if self._is_stale():
            self.refresh()
        mode = str(request.get("mode") or "")
        requested_profile = str(request.get("provider_profile") or AUTO_IMAGE_PROFILE)
        candidates = self._priority()
        if requested_profile != AUTO_IMAGE_PROFILE and requested_profile in self._routes:
            candidates = [requested_profile]
        with self._lock:
            selected = next(
                (
                    self._routes[profile]
                    for profile in candidates
                    if profile in self._routes
                    and mode in self._routes[profile].modes
                    and self._status.get(profile, {}).get("available") is True
                ),
                None,
            )
        if selected is None:
            configured = any(
                self._status.get(profile, {}).get("configured") is True
                for profile in candidates
                if profile in self._routes and mode in self._routes[profile].modes
            )
            return http_failure(
                operation="routing",
                configuration_missing=not configured,
                status_code=503 if configured else None,
            )
        routed_request = {
            **request,
            "provider_profile": selected.profile,
            "channel": selected.channel,
            "model": self._model_for(selected.profile, mode),
        }
        result = selected.provider.generate(routed_request)
        if not result.ok:
            return result
        return result.model_copy(
            update={
                "payload": {
                    **result.payload,
                    "routing": {
                        "provider_profile": selected.profile,
                        "channel": selected.channel,
                        "model": routed_request["model"],
                    },
                }
            }
        )

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.refresh()
            self._wake.wait(self._probe_interval())
            self._wake.clear()

    def _is_stale(self) -> bool:
        with self._lock:
            return not self._status or monotonic() - self._last_refresh >= self._stale_after

    def _settings_snapshot(self) -> dict[str, Any]:
        try:
            value = self._settings()
        except Exception:  # noqa: BLE001 - settings reads must not break routing.
            return {}
        return value if isinstance(value, dict) else {}

    def _priority(self) -> list[str]:
        value = self._settings_snapshot().get("image_provider_priority")
        requested = value if isinstance(value, list) else list(DEFAULT_IMAGE_PROVIDER_PRIORITY)
        valid = [item for item in requested if isinstance(item, str) and item in self._routes]
        defaults = [profile for profile in DEFAULT_IMAGE_PROVIDER_PRIORITY if profile in self._routes]
        return list(dict.fromkeys([*valid, *defaults]))

    def _probe_interval(self) -> float:
        value = self._settings_snapshot().get("provider_probe_interval_seconds", 300)
        if isinstance(value, int) and not isinstance(value, bool) and 60 <= value <= 3600:
            return float(value)
        return 300.0

    def _model_for(self, profile: str, mode: str | None = None) -> str:
        route = self._routes[profile]
        profiles = self._settings_snapshot().get("provider_profiles")
        if isinstance(profiles, dict):
            details = profiles.get(profile)
            if isinstance(details, dict):
                model = details.get("model")
                if isinstance(model, str) and model.strip():
                    return model.strip()
        if mode is not None:
            mode_model = route.mode_models.get(mode)
            if isinstance(mode_model, str) and mode_model.strip():
                return mode_model.strip()
        return route.default_model

    def _unknown(self, profile: str) -> dict[str, object]:
        route = self._routes[profile]
        return {
            "profile": profile,
            "label": route.label,
            "channel": route.channel,
            "configured": False,
            "available": False,
            "reason": "not_checked",
            "last_checked_at": None,
            "modes": sorted(route.modes),
        }
