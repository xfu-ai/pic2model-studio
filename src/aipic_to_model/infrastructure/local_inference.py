"""Safe local inference discovery, probing, and single-GPU admission control."""

from __future__ import annotations

import ipaddress
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from ..domain.local_inference import (
    LocalEngineKind,
    LocalHealthReason,
    LocalProviderHealth,
    LocalProviderProfile,
)


class LocalInferenceCancelled(RuntimeError):
    pass


class LocalProviderDiscoveryError(RuntimeError):
    """A redacted local discovery failure that contains no response or path data."""


def normalize_loopback_base_url(value: str) -> str:
    """Accept only an explicit unprivileged loopback HTTP(S) endpoint."""

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Local inference endpoint must use HTTP or HTTPS")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Local inference endpoint cannot contain credentials or query data")
    hostname = parsed.hostname
    if not hostname or not _is_loopback_host(hostname):
        raise ValueError("Local inference endpoint must use an explicit loopback host")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("Local inference endpoint has an invalid port") from error
    if port is None or not 1024 <= port <= 65535:
        raise ValueError("Local inference endpoint must use an explicit unprivileged port")
    path_segments = [segment for segment in parsed.path.split("/") if segment]
    if any(segment in {".", ".."} for segment in path_segments):
        raise ValueError("Local inference endpoint path is invalid")
    host = f"[{hostname}]" if ":" in hostname else hostname.lower()
    path = "/" + "/".join(path_segments) if path_segments else ""
    return f"{parsed.scheme.lower()}://{host}:{port}{path}"


def _is_loopback_host(hostname: str) -> bool:
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


class LocalProviderProbe(Protocol):
    def probe(self, profile: LocalProviderProfile) -> LocalProviderHealth: ...


class OllamaOpenAIProbe:
    def __init__(self, *, client: httpx.Client | None = None, timeout_seconds: float = 3.0):
        self._client = client
        self._timeout_seconds = timeout_seconds

    def probe(self, profile: LocalProviderProfile) -> LocalProviderHealth:
        if profile.engine is not LocalEngineKind.OLLAMA or not profile.endpoint:
            raise ValueError("Ollama probe requires an Ollama endpoint profile")
        endpoint = normalize_loopback_base_url(profile.endpoint)
        client = self._client or httpx.Client(
            timeout=self._timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )
        owns = self._client is None
        try:
            response = client.get(f"{endpoint.rstrip('/')}/models")
            if response.status_code != 200 or response.is_redirect:
                return _health(profile, configured=True, reason="runtime_unavailable")
            try:
                payload = response.json()
            except ValueError:
                return _health(profile, configured=True, reason="response_invalid")
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list):
                return _health(profile, configured=True, reason="response_invalid")
            models = {
                str(item.get("id"))
                for item in data
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            }
            if profile.model_id not in models:
                return _health(profile, configured=True, reason="model_not_installed")
            version = response.headers.get("ollama-version")
            return _health(profile, configured=True, available=True, engine_version=version)
        except httpx.HTTPError:
            return _health(profile, configured=True, reason="runtime_unavailable")
        finally:
            if owns:
                client.close()

    def discover(self, profile: LocalProviderProfile) -> tuple[str, ...]:
        """Return installed Ollama model IDs through the credentialless loopback API."""

        if profile.engine is not LocalEngineKind.OLLAMA or not profile.endpoint:
            raise ValueError("Ollama discovery requires an Ollama endpoint profile")
        endpoint = normalize_loopback_base_url(profile.endpoint)
        client = self._client or httpx.Client(
            timeout=self._timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )
        owns = self._client is None
        try:
            response = client.get(f"{endpoint.rstrip('/')}/models")
            if response.status_code != 200 or response.is_redirect:
                raise LocalProviderDiscoveryError("Ollama model discovery is unavailable")
            try:
                payload = response.json()
            except ValueError as error:
                raise LocalProviderDiscoveryError(
                    "Ollama model discovery returned an invalid response"
                ) from error
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list):
                raise LocalProviderDiscoveryError(
                    "Ollama model discovery returned an invalid response"
                )
            models = {
                item["id"]
                for item in data[:1024]
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            }
            return tuple(sorted(models))
        except httpx.HTTPError as error:
            raise LocalProviderDiscoveryError("Ollama model discovery is unavailable") from error
        finally:
            if owns:
                client.close()


class CapabilityLocalProbe:
    """Probe a Host-owned runtime without exposing its resolved path."""

    def __init__(self, resolve: Callable[[str], Mapping[str, Any] | None]):
        self._resolve = resolve

    def probe(self, profile: LocalProviderProfile) -> LocalProviderHealth:
        capability_id = profile.runtime_capability_id
        if not capability_id:
            raise ValueError("Capability probe requires a runtime capability ID")
        try:
            status = self._resolve(capability_id)
        except OSError, RuntimeError, TypeError, ValueError:
            status = None
        if status is None or status.get("configured") is not True:
            return _health(profile, configured=False, reason="runtime_not_configured")
        if status.get("available") is not True:
            return _health(profile, configured=True, reason="runtime_unavailable")
        if status.get("model_present") is not True:
            return _health(profile, configured=True, reason="model_not_installed")
        version = status.get("engine_version")
        return _health(
            profile,
            configured=True,
            available=True,
            engine_version=str(version) if isinstance(version, str) else None,
        )


def _health(
    profile: LocalProviderProfile,
    *,
    configured: bool,
    available: bool = False,
    reason: LocalHealthReason | None = None,
    engine_version: str | None = None,
) -> LocalProviderHealth:
    return LocalProviderHealth(
        profile_id=profile.profile_id,
        engine=profile.engine,
        model_id=profile.model_id,
        configured=configured,
        available=available,
        reason=reason,
        engine_version=engine_version,
        capabilities=profile.capabilities,
    )


class LocalProviderMonitor:
    def __init__(
        self,
        profiles: tuple[LocalProviderProfile, ...],
        probes: Mapping[str, LocalProviderProbe],
    ) -> None:
        if len({profile.profile_id for profile in profiles}) != len(profiles):
            raise ValueError("Local provider profile IDs must be unique")
        missing = {profile.profile_id for profile in profiles} - set(probes)
        if missing:
            raise ValueError("Every local provider profile requires a probe")
        self._profiles = {profile.profile_id: profile for profile in profiles}
        self._probes = dict(probes)
        self._statuses = {
            profile.profile_id: _health(profile, configured=False, reason="not_checked")
            for profile in profiles
        }
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, *, interval_seconds: float = 5.0) -> None:
        """Refresh local runtime state in the background until stopped."""

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            thread = threading.Thread(
                target=self._monitor,
                args=(max(0.05, interval_seconds),),
                name="local-provider-monitor",
                daemon=True,
            )
            self._thread = thread
            thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def _monitor(self, interval_seconds: float) -> None:
        while not self._stop_event.is_set():
            try:
                self.refresh()
            except Exception:  # noqa: BLE001 - a later local probe must still run.
                if self._stop_event.wait(interval_seconds):
                    return
                continue
            if self._stop_event.wait(interval_seconds):
                return

    def refresh(self, profile_id: str | None = None) -> tuple[LocalProviderHealth, ...]:
        selected = (
            (self._profiles[profile_id],)
            if profile_id is not None
            else tuple(self._profiles.values())
        )
        refreshed = {
            profile.profile_id: self._probes[profile.profile_id].probe(profile)
            for profile in selected
        }
        with self._lock:
            self._statuses.update(refreshed)
        return self.snapshot()

    def snapshot(self) -> tuple[LocalProviderHealth, ...]:
        with self._lock:
            return tuple(self._statuses[profile_id] for profile_id in self._profiles)

    def status_snapshot(self) -> dict[str, object]:
        """Return public metadata and redacted health without runtime locations."""

        statuses = {status.profile_id: status for status in self.snapshot()}
        providers: list[dict[str, object]] = []
        for profile_id, profile in self._profiles.items():
            status = statuses[profile_id]
            providers.append(
                {
                    "profile": profile.profile_id,
                    "label": profile.label,
                    "engine": profile.engine.value,
                    "transport": profile.transport.value,
                    "model": status.model_id,
                    "capabilities": [item.value for item in status.capabilities],
                    "configured": status.configured,
                    "available": status.available,
                    "reason": status.reason,
                    "engine_version": status.engine_version,
                    "license": profile.license.model_dump(mode="json"),
                }
            )
        return {
            "probes_download_models": False,
            "probes_create_generation_jobs": False,
            "providers": providers,
        }


@dataclass(frozen=True)
class LocalInferenceLease:
    owner: str
    engine: LocalEngineKind
    acquired_at: float


class LocalInferenceGate:
    """Serialize heavy local inference work and expose only redacted state."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active: LocalInferenceLease | None = None

    @contextmanager
    def lease(
        self,
        owner: str,
        engine: LocalEngineKind,
        *,
        cancelled: Callable[[], bool] | None = None,
        timeout_seconds: float | None = None,
        poll_seconds: float = 0.05,
    ) -> Iterator[LocalInferenceLease]:
        if not owner:
            raise ValueError("Local inference lease owner must not be empty")
        is_cancelled = cancelled or (lambda: False)
        deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None
        with self._condition:
            while self._active is not None:
                if is_cancelled():
                    raise LocalInferenceCancelled("Local inference request was cancelled")
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError("Timed out waiting for the local inference gate")
                remaining = deadline - time.monotonic() if deadline is not None else poll_seconds
                self._condition.wait(max(0.0, min(poll_seconds, remaining)))
            if is_cancelled():
                raise LocalInferenceCancelled("Local inference request was cancelled")
            lease = LocalInferenceLease(owner, engine, time.monotonic())
            self._active = lease
        try:
            yield lease
        finally:
            with self._condition:
                if self._active == lease:
                    self._active = None
                    self._condition.notify_all()

    def snapshot(self) -> dict[str, object]:
        with self._condition:
            if self._active is None:
                return {"busy": False, "engine": None}
            return {"busy": True, "engine": self._active.engine.value}
