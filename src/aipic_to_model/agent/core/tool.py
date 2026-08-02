"""Tool contracts, registry, and JSON Schema validation for the Agent loop."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from .errors import InvalidToolArgumentsError, UnknownToolError
from .events import CancellationToken
from .models import Message, ToolCall, ToolResult

ToolUpdateCallback = Callable[[ToolResult], Awaitable[None] | None]
ToolExecutionMode = Literal["sequential", "parallel"]


@dataclass(frozen=True)
class ToolContext:
    messages: tuple[Message, ...]


class AgentTool(Protocol):
    name: str
    label: str
    description: str
    parameters: Mapping[str, object]
    execution_mode: ToolExecutionMode

    async def execute(
        self,
        tool_call_id: str,
        arguments: dict[str, object],
        context: ToolContext,
        cancellation: CancellationToken,
        on_update: ToolUpdateCallback | None = None,
    ) -> ToolResult: ...


class ToolRegistry:
    def __init__(self, tools: tuple[AgentTool, ...] = ()) -> None:
        self._tools: dict[str, AgentTool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: AgentTool) -> None:
        if not tool.name or tool.name in self._tools:
            raise ValueError(f"Tool name must be unique and non-empty: {tool.name!r}")
        try:
            Draft202012Validator.check_schema(dict(tool.parameters))
        except SchemaError as error:
            raise ValueError(f"Invalid JSON Schema for tool {tool.name!r}") from error
        self._tools[tool.name] = tool

    def get(self, name: str) -> AgentTool:
        try:
            return self._tools[name]
        except KeyError as error:
            raise UnknownToolError(name) from error

    def all(self) -> tuple[AgentTool, ...]:
        return tuple(self._tools.values())

    def validate(self, call: ToolCall) -> tuple[AgentTool, dict[str, object]]:
        tool = self.get(call.name)
        arguments = cast(dict[str, object], dict(call.arguments))
        try:
            Draft202012Validator(dict(tool.parameters)).validate(arguments)
        except ValidationError as error:
            message = error.message or "Tool arguments are invalid."
            raise InvalidToolArgumentsError(call.name, message) from error
        return tool, arguments
