"""Workspace-rooted file and PowerShell execution environment."""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from ..core.events import CancellationToken


class WorkspaceAccessError(PermissionError):
    """Raised when a path is outside an explicitly configured workspace root."""


@dataclass(frozen=True)
class ExecutionResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    artifact_path: str | None = None


class LocalExecutionEnv:
    """All local I/O is rooted, cancellation-aware, and free of ambient secrets."""

    def __init__(
        self,
        roots: tuple[Path, ...],
        *,
        cwd: Path | None = None,
        artifact_dir: Path | None = None,
        max_output_bytes: int = 64 * 1024,
        allowed_env: tuple[str, ...] = (),
    ) -> None:
        if not roots:
            raise ValueError("At least one Agent workspace root is required.")
        self.roots = tuple(path.resolve() for path in roots)
        self.cwd = self.resolve(cwd or self.roots[0])
        self.artifact_dir = (artifact_dir or self.roots[0] / ".agent-artifacts").resolve()
        self.max_output_bytes = max_output_bytes
        self.allowed_env = frozenset(allowed_env)
        self._locks: defaultdict[Path, asyncio.Lock] = defaultdict(asyncio.Lock)

    def resolve(self, path: Path | str) -> Path:
        candidate = Path(path)
        absolute = candidate if candidate.is_absolute() else self.cwd / candidate
        resolved = absolute.resolve(strict=False)
        if not any(_is_within(resolved, root) for root in self.roots):
            raise WorkspaceAccessError(f"Path is outside the Agent workspace: {path}")
        return resolved

    async def read_text(self, path: Path | str) -> str:
        target = self.resolve(path)
        return await asyncio.to_thread(target.read_text, encoding="utf-8")

    async def read_bytes(self, path: Path | str) -> bytes:
        target = self.resolve(path)
        return await asyncio.to_thread(target.read_bytes)

    async def write_text(self, path: Path | str, content: str) -> None:
        target = self.resolve(path)
        async with self._locks[target]:
            await asyncio.to_thread(_atomic_write, target, content.encode("utf-8"))

    async def edit_text(self, path: Path | str, old_text: str, new_text: str) -> None:
        target = self.resolve(path)
        async with self._locks[target]:
            content = await asyncio.to_thread(target.read_text, encoding="utf-8")
            matches = content.count(old_text)
            if matches != 1:
                raise ValueError(f"Edit requires exactly one old_text match; found {matches}.")
            await asyncio.to_thread(
                _atomic_write, target, content.replace(old_text, new_text).encode("utf-8")
            )

    async def exec(
        self,
        command: str,
        *,
        cwd: Path | str | None = None,
        timeout_seconds: float | None = None,
        cancellation: CancellationToken | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        target_cwd = self.resolve(cwd or self.cwd)
        filtered = {key: value for key, value in (env or {}).items() if key in self.allowed_env}
        runtime_keys = {"SystemRoot", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP"}
        process_env = {
            key: os.environ[key] for key in self.allowed_env | runtime_keys if key in os.environ
        }
        process_env.update(filtered)
        process = await asyncio.create_subprocess_exec(
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
            cwd=target_cwd,
            env=process_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        communicate = process.communicate()
        try:
            if cancellation is not None:
                stdout, stderr = await cancellation.wait_for(communicate)
            elif timeout_seconds is not None:
                stdout, stderr = await asyncio.wait_for(communicate, timeout_seconds)
            else:
                stdout, stderr = await communicate
        except TimeoutError, asyncio.CancelledError:
            process.kill()
            await process.wait()
            return ExecutionResult("", "Command timed out or was cancelled.", -1, True)
        output = stdout + stderr
        artifact: str | None = None
        if len(output) > self.max_output_bytes:
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            artifact_file = Path(
                tempfile.mkstemp(prefix="bash-", suffix=".log", dir=self.artifact_dir)[1]
            )
            artifact_file.write_bytes(output)
            artifact = str(artifact_file)
            stdout = stdout[: self.max_output_bytes]
            stderr = b"[Output truncated; full output is stored in a managed artifact.]"
        return ExecutionResult(
            stdout.decode(errors="replace"),
            stderr.decode(errors="replace"),
            process.returncode or 0,
            artifact_path=artifact,
        )


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise
