"""Stable, serializable failures for the provider-agnostic Agent Core."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(eq=False)
class AgentCoreError(Exception):
    """Base error whose message is safe to expose to the Agent event layer."""

    message: str
    code: str = "agent_error"
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }


class ProviderError(AgentCoreError):
    def __init__(self, message: str, *, retryable: bool = False, **details: Any) -> None:
        super().__init__(message, "provider_error", retryable, details)


class InvalidToolArgumentsError(AgentCoreError):
    def __init__(self, tool_name: str, message: str = "Tool arguments are invalid.") -> None:
        super().__init__(message, "invalid_tool_arguments", False, {"tool_name": tool_name})


class UnknownToolError(AgentCoreError):
    def __init__(self, tool_name: str) -> None:
        super().__init__(
            f"Unknown tool: {tool_name}", "unknown_tool", False, {"tool_name": tool_name}
        )


class ToolExecutionError(AgentCoreError):
    def __init__(
        self,
        tool_name: str,
        message: str = "Tool execution failed.",
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message, "tool_execution_error", retryable, {"tool_name": tool_name})


class AgentCancelledError(AgentCoreError):
    def __init__(self, message: str = "Operation cancelled.") -> None:
        super().__init__(message, "cancelled", False)


# The short name mirrors the migration plan while avoiding an asyncio import in
# consumers that only need to identify Agent Core cancellation.
CancelledError = AgentCancelledError


class ContextOverflowError(AgentCoreError):
    def __init__(self, message: str = "Model context window exceeded.") -> None:
        super().__init__(message, "context_overflow", True)


class ExtensionError(AgentCoreError):
    def __init__(self, extension_name: str, message: str = "Extension failed.") -> None:
        super().__init__(message, "extension_error", False, {"extension_name": extension_name})


class EventStreamClosedError(AgentCoreError):
    def __init__(self) -> None:
        super().__init__("Cannot publish to a closed event stream.", "event_stream_closed")
