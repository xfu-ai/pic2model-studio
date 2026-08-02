from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..core.events import CancellationToken
from ..core.models import TextContent, ToolResult
from ..core.tool import ToolContext, ToolExecutionMode, ToolUpdateCallback
from ..execution.local import LocalExecutionEnv


@dataclass
class _Tool:
    env: LocalExecutionEnv
    name: str
    label: str
    description: str
    parameters: Mapping[str, object]
    execution_mode: ToolExecutionMode = "sequential"


class ReadTool(_Tool):
    def __init__(self, env: LocalExecutionEnv) -> None:
        super().__init__(
            env,
            "read",
            "read",
            "Read a workspace-local text file. Use for user-requested file inspection; do not use it to read secrets, project databases, managed asset binaries, or paths outside the workspace.",
            {
                "type": "object",
                "required": ["path"],
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer"},
                    "limit": {"type": "integer"},
                },
            },
        )

    async def execute(
        self,
        tool_call_id: str,
        arguments: dict[str, object],
        context: ToolContext,
        cancellation: CancellationToken,
        on_update: ToolUpdateCallback | None = None,
    ) -> ToolResult:
        del tool_call_id, context, on_update
        cancellation.raise_if_cancelled()
        text = await self.env.read_text(str(arguments["path"]))
        lines = text.splitlines()
        offset_value, limit_value = arguments.get("offset", 1), arguments.get("limit", 2000)
        if not isinstance(offset_value, int) or not isinstance(limit_value, int):
            raise TypeError("read offset and limit must be integers.")
        offset, limit = max(1, offset_value), limit_value
        selected = lines[offset - 1 : offset - 1 + limit]
        numbered = "\n".join(f"{index + offset}: {line}" for index, line in enumerate(selected))
        return ToolResult((TextContent(numbered),), details={"total_lines": len(lines)})


class WriteTool(_Tool):
    def __init__(self, env: LocalExecutionEnv) -> None:
        super().__init__(
            env,
            "write",
            "write",
            "Atomically write a workspace-local text file. Use for user-requested notes, configuration, or source files; do not use it to mutate managed assets, project databases, approvals, or provider state.",
            {
                "type": "object",
                "required": ["path", "content"],
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            },
        )

    async def execute(
        self,
        tool_call_id: str,
        arguments: dict[str, object],
        context: ToolContext,
        cancellation: CancellationToken,
        on_update: ToolUpdateCallback | None = None,
    ) -> ToolResult:
        del tool_call_id, context, on_update
        cancellation.raise_if_cancelled()
        await self.env.write_text(str(arguments["path"]), str(arguments["content"]))
        return ToolResult((TextContent("File written."),), details={})


class EditTool(_Tool):
    def __init__(self, env: LocalExecutionEnv) -> None:
        super().__init__(
            env,
            "edit",
            "edit",
            "Replace one exact match in a workspace-local text file. Use for a precise user-requested text edit; do not use it to modify managed assets, project databases, approvals, or provider state.",
            {
                "type": "object",
                "required": ["path", "old_text", "new_text"],
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
            },
        )

    async def execute(
        self,
        tool_call_id: str,
        arguments: dict[str, object],
        context: ToolContext,
        cancellation: CancellationToken,
        on_update: ToolUpdateCallback | None = None,
    ) -> ToolResult:
        del tool_call_id, context, on_update
        cancellation.raise_if_cancelled()
        await self.env.edit_text(
            str(arguments["path"]), str(arguments["old_text"]), str(arguments["new_text"])
        )
        return ToolResult((TextContent("File edited."),), details={})


class BashTool(_Tool):
    def __init__(self, env: LocalExecutionEnv) -> None:
        super().__init__(
            env,
            "bash",
            "bash",
            "Execute PowerShell for user-requested workspace-local scripts and diagnostics. Do not call provider endpoints, read secrets, edit project databases or managed asset files, or bypass AIPic tools, approvals, and UI actions.",
            {
                "type": "object",
                "required": ["command"],
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "number"},
                    "cwd": {"type": "string"},
                },
            },
        )

    async def execute(
        self,
        tool_call_id: str,
        arguments: dict[str, object],
        context: ToolContext,
        cancellation: CancellationToken,
        on_update: ToolUpdateCallback | None = None,
    ) -> ToolResult:
        del tool_call_id, context, on_update
        timeout_value = arguments.get("timeout")
        if timeout_value is not None and not isinstance(timeout_value, int | float):
            raise ValueError("bash timeout must be numeric.")
        result = await self.env.exec(
            str(arguments["command"]),
            cwd=str(arguments["cwd"]) if "cwd" in arguments else None,
            timeout_seconds=float(timeout_value)
            if isinstance(timeout_value, int | float)
            else None,
            cancellation=cancellation,
        )
        text = f"{result.stdout}{result.stderr}".strip() or "(no output)"
        return ToolResult(
            (TextContent(text),),
            details={"exit_code": result.exit_code, "artifact": result.artifact_path},
            is_error=result.exit_code != 0,
        )
