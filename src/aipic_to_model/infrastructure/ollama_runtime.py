"""Desktop-owned Ollama lifecycle management for the local Qwen Provider."""

from __future__ import annotations

import http.client
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from .local_inference import normalize_loopback_base_url

DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"
DEFAULT_CONTEXT_LENGTH = 32_768
DEFAULT_KEEP_ALIVE = "10m"
DEFAULT_KV_CACHE_TYPE = "q8_0"


class ManagedProcess(Protocol):
    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


@dataclass(frozen=True)
class OllamaRuntimeStatus:
    """Secret- and path-free lifecycle state suitable for diagnostics and tests."""

    available: bool
    managed: bool
    reused_external: bool
    reason: str | None
    restart_count: int
    diagnostic: str | None = None


def ollama_server_url(base_url: str | None = None) -> str:
    """Return the loopback server origin used by an OpenAI-compatible profile."""

    normalized = normalize_loopback_base_url(
        base_url or os.environ.get("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE_URL
    )
    parsed = urlsplit(normalized)
    return f"{parsed.scheme}://{parsed.netloc}"


def discover_ollama_executable(explicit: str | None = None) -> Path | None:
    """Resolve an Ollama runtime without exposing the path to the renderer."""

    candidates: list[Path] = []
    configured = explicit or os.environ.get("AIPIC_TO_MODEL_OLLAMA_BIN")
    if configured:
        candidates.append(Path(configured))

    command = shutil.which("ollama")
    if command:
        candidates.append(Path(command))

    executable_name = "ollama.exe" if os.name == "nt" else "ollama"
    executable = Path(sys.executable).resolve()
    candidates.extend(
        (
            executable.parent / "resources" / "ollama" / executable_name,
            executable.parent.parent / "ollama" / executable_name,
            executable.parent.parent.parent / "resources" / "ollama" / executable_name,
            executable.parent / "ollama" / executable_name,
        )
    )

    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            local_root = Path(local_app_data)
            candidates.extend(
                (
                    local_root / "Programs" / "Ollama" / "ollama.exe",
                    local_root / "Ollama" / "ollama.exe",
                )
            )

    source_root = Path(__file__).resolve().parents[3]
    development_root = source_root / ".local" / "ollama"
    if development_root.is_dir():
        candidates.extend(
            sorted(
                development_root.glob(f"*/{executable_name}"),
                key=lambda item: item.parent.name,
                reverse=True,
            )
        )

    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            return resolved
    return None


def discover_ollama_models_directory(explicit: str | None = None) -> Path | None:
    """Resolve a complete bundled or development Ollama model store."""

    candidates: list[Path] = []
    configured = (
        explicit
        or os.environ.get("AIPIC_TO_MODEL_OLLAMA_MODELS")
        or os.environ.get("OLLAMA_MODELS")
    )
    if configured:
        candidates.append(Path(configured))

    executable = Path(sys.executable).resolve()
    candidates.extend(
        (
            executable.parent.parent / "ollama-models",
            executable.parent / "resources" / "ollama-models",
            executable.parent.parent / "resources" / "ollama-models",
            Path(__file__).resolve().parents[3] / ".local" / "ollama-models",
        )
    )
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if (resolved / "blobs").is_dir() and (resolved / "manifests").is_dir():
            return resolved
    return None


class OllamaRuntimeManager:
    """Reuse a compatible Ollama service or supervise one owned by the desktop app."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        executable: Path | None = None,
        models_directory: Path | None = None,
        environment: Mapping[str, str] | None = None,
        startup_timeout_seconds: float = 15.0,
        supervisor_interval_seconds: float = 1.0,
        probe_timeout_seconds: float = 0.75,
        probe: Callable[[], bool] | None = None,
        popen_factory: Callable[..., ManagedProcess] | None = None,
    ) -> None:
        self._server_url = ollama_server_url(base_url)
        self._executable = executable
        self._models_directory = models_directory
        self._environment = dict(environment or os.environ)
        self._startup_timeout_seconds = max(0.0, startup_timeout_seconds)
        self._supervisor_interval_seconds = max(0.05, supervisor_interval_seconds)
        self._probe_timeout_seconds = max(0.05, probe_timeout_seconds)
        self._probe_override = probe
        self._popen_factory = popen_factory or subprocess.Popen
        self._process: ManagedProcess | None = None
        self._reused_external = False
        self._reason: str | None = "not_started"
        self._restart_count = 0
        self._diagnostic: str | None = None
        self._started = False
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._supervisor: threading.Thread | None = None

    @classmethod
    def from_environment(cls) -> OllamaRuntimeManager:
        models = discover_ollama_models_directory()
        return cls(
            executable=discover_ollama_executable(),
            models_directory=models,
        )

    def start(self, *, wait_for_ready: bool = True) -> OllamaRuntimeStatus:
        with self._lock:
            if self._started:
                return self.status()
            self._started = True
            self._stop_event.clear()
        self.ensure_running(wait_for_ready=wait_for_ready)
        supervisor = threading.Thread(
            target=self._supervise,
            name="ollama-runtime-supervisor",
            daemon=True,
        )
        self._supervisor = supervisor
        supervisor.start()
        return self.status()

    def ensure_running(self, *, wait_for_ready: bool = False) -> OllamaRuntimeStatus:
        """Ensure a compatible service exists, without taking ownership of external instances."""

        if self._probe():
            with self._lock:
                if self._process is None:
                    self._reused_external = True
                self._reason = None
            return self.status()

        with self._lock:
            process = self._process
            if process is not None and process.poll() is not None:
                self._process = None
                self._restart_count += 1
                process = None
            if process is None:
                executable = self._executable or discover_ollama_executable()
                if executable is None:
                    self._reason = "runtime_not_installed"
                    self._reused_external = False
                    return self.status()
                try:
                    self._process = self._launch(executable)
                except OSError:
                    self._reason = "runtime_start_failed"
                    self._reused_external = False
                    return self.status()
                self._reused_external = False
                self._reason = "runtime_starting"

        if wait_for_ready and self._wait_until_ready():
            with self._lock:
                self._reason = None
        return self.status()

    def stop(self) -> None:
        self._stop_event.set()
        supervisor = self._supervisor
        if supervisor is not None and supervisor is not threading.current_thread():
            supervisor.join(timeout=max(1.0, self._supervisor_interval_seconds * 2))
        with self._lock:
            process, self._process = self._process, None
            self._started = False
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=5.0)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                    process.wait(timeout=2.0)
                except (OSError, subprocess.TimeoutExpired):
                    pass

    def status(self) -> OllamaRuntimeStatus:
        with self._lock:
            process = self._process
            managed = process is not None and process.poll() is None
            reused_external = self._reused_external and not managed
            reason = self._reason
            restarts = self._restart_count
            diagnostic = self._diagnostic
        available = self._probe()
        if available:
            reason = None
        return OllamaRuntimeStatus(
            available=available,
            managed=managed,
            reused_external=reused_external,
            reason=reason,
            restart_count=restarts,
            diagnostic=diagnostic,
        )

    def _launch(self, executable: Path) -> ManagedProcess:
        environment = dict(self._environment)
        environment.update(
            {
                "OLLAMA_HOST": self._server_url,
                "OLLAMA_CONTEXT_LENGTH": str(DEFAULT_CONTEXT_LENGTH),
                "OLLAMA_KEEP_ALIVE": environment.get(
                    "OLLAMA_KEEP_ALIVE", DEFAULT_KEEP_ALIVE
                ),
                # A 32K multimodal Agent turn needs substantially more KV and
                # temporary image memory than a short text completion. Keep the
                # advertised context window while making its memory use stable
                # on consumer GPUs. These settings affect only the app-owned
                # loopback Ollama process; an external service remains untouched.
                "OLLAMA_FLASH_ATTENTION": environment.get(
                    "OLLAMA_FLASH_ATTENTION", "1"
                ),
                "OLLAMA_KV_CACHE_TYPE": environment.get(
                    "OLLAMA_KV_CACHE_TYPE", DEFAULT_KV_CACHE_TYPE
                ),
                "OLLAMA_MAX_LOADED_MODELS": environment.get(
                    "OLLAMA_MAX_LOADED_MODELS", "1"
                ),
                "OLLAMA_NUM_PARALLEL": environment.get("OLLAMA_NUM_PARALLEL", "1"),
            }
        )
        if self._models_directory is not None:
            environment["OLLAMA_MODELS"] = str(self._models_directory)
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = self._popen_factory(
            [str(executable), "serve"],
            cwd=str(executable.parent),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
        )
        stderr = getattr(process, "stderr", None)
        if stderr is not None:
            threading.Thread(
                target=self._drain_diagnostics,
                args=(stderr,),
                name="ollama-runtime-diagnostics",
                daemon=True,
            ).start()
        return process

    def _drain_diagnostics(self, stream: Any) -> None:
        """Drain Ollama stderr and retain only a path- and payload-free category."""

        try:
            for raw_line in stream:
                line = (
                    raw_line.decode("utf-8", errors="replace")
                    if isinstance(raw_line, bytes)
                    else str(raw_line)
                )
                diagnostic = classify_ollama_diagnostic(line)
                if diagnostic is not None:
                    with self._lock:
                        self._diagnostic = diagnostic
        except (OSError, ValueError):
            return

    def _wait_until_ready(self) -> bool:
        deadline = time.monotonic() + self._startup_timeout_seconds
        while not self._stop_event.is_set():
            if self._probe():
                return True
            with self._lock:
                process = self._process
                if process is None or process.poll() is not None:
                    self._reason = "runtime_exited"
                    return False
            if time.monotonic() >= deadline:
                with self._lock:
                    self._reason = "runtime_start_timeout"
                return False
            self._stop_event.wait(0.1)
        return False

    def _supervise(self) -> None:
        while not self._stop_event.wait(self._supervisor_interval_seconds):
            self.ensure_running(wait_for_ready=False)

    def _probe(self) -> bool:
        if self._probe_override is not None:
            try:
                return bool(self._probe_override())
            except (OSError, RuntimeError, TypeError, ValueError):
                return False
        parsed = urlsplit(self._server_url)
        connection: http.client.HTTPConnection | http.client.HTTPSConnection
        connection_type = (
            http.client.HTTPSConnection
            if parsed.scheme == "https"
            else http.client.HTTPConnection
        )
        connection = connection_type(
            parsed.hostname,
            parsed.port,
            timeout=self._probe_timeout_seconds,
        )
        try:
            connection.request("GET", "/api/version", headers={"Accept": "application/json"})
            response = connection.getresponse()
            if response.status != 200:
                return False
            payload: Any = json.loads(response.read(16_384))
            return isinstance(payload, dict) and isinstance(payload.get("version"), str)
        except (OSError, http.client.HTTPException, json.JSONDecodeError, ValueError):
            return False
        finally:
            connection.close()


def classify_ollama_diagnostic(line: str) -> str | None:
    """Reduce an Ollama log line to an allowlisted operational category."""

    normalized = line.lower()
    if any(
        token in normalized
        for token in ("out of memory", "memory allocation", "cuda error", "page file")
    ):
        return "resource_exhausted"
    if any(token in normalized for token in ("runner process", "runner exited", "llama runner")):
        return "runner_unavailable"
    if any(token in normalized for token in ("failed to load model", "unable to load model")):
        return "model_load_failed"
    if "context length" in normalized and any(
        token in normalized for token in ("exceed", "too large", "overflow")
    ):
        return "context_overflow"
    return None


__all__ = [
    "DEFAULT_CONTEXT_LENGTH",
    "DEFAULT_KV_CACHE_TYPE",
    "OllamaRuntimeManager",
    "OllamaRuntimeStatus",
    "classify_ollama_diagnostic",
    "discover_ollama_executable",
    "discover_ollama_models_directory",
    "ollama_server_url",
]
