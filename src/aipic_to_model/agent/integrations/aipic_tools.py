"""AIPic ToolRegistry adapter; business actions never bypass application services."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ...application.tools import ToolRegistry as AIPicToolRegistry
from ...domain.tools import ToolManifestV1, ToolResultV1
from ..core.events import CancellationToken
from ..core.models import TextContent, ToolResult
from ..core.tool import ToolContext, ToolUpdateCallback
from .tool_guidance import agent_tool_description


@dataclass(frozen=True)
class AIPicToolInvocation:
    root: Path
    project_id: str
    request_id: str
    run_id: str | None = None
    round_index: int = 0
    provider_profile: str | None = None


class AIPicToolAdapter:
    """Expose one registered AIPic manifest through the generic AgentTool protocol."""

    execution_mode: Literal["sequential"] = "sequential"

    def __init__(
        self,
        registry: AIPicToolRegistry,
        manifest: ToolManifestV1,
        invocation: Callable[[], AIPicToolInvocation],
    ) -> None:
        self._registry = registry
        self._manifest = manifest
        self._invocation = invocation
        self.name = manifest.name
        self.label = manifest.human_name
        self.description = agent_tool_description(manifest)
        self.parameters = manifest.input_schema

    async def execute(
        self,
        tool_call_id: str,
        arguments: dict[str, object],
        context: ToolContext,
        cancellation: CancellationToken,
        on_update: ToolUpdateCallback | None = None,
    ) -> ToolResult:
        del context
        cancellation.raise_if_cancelled()
        invocation = self._invocation()
        request_id = _tool_request_id(invocation.request_id, tool_call_id)
        result = await cancellation.wait_for(
            asyncio.to_thread(
                self._registry.execute,
                invocation.root,
                invocation.project_id,
                self._manifest.name,
                self._manifest.version,
                dict(arguments),
                request_id,
                invocation.run_id,
                invocation.round_index,
                invocation.provider_profile,
            )
        )
        converted = _agent_result(result, tool_call_id)
        if on_update is not None:
            update = on_update(converted)
            if update is not None:
                await update
        return converted


def _tool_request_id(conversation_request_id: str, tool_call_id: str) -> str:
    """Derive one stable idempotency key per model tool call.

    A conversation can execute many different AIPic tools. Reusing the
    conversation id for all of them binds the first payload to every later
    invocation and causes an idempotency conflict. Provider tool-call ids are
    stable across a retry, so hashing both values keeps retries replayable while
    allowing the conversation to make progress through a multi-step workflow.
    """

    digest = hashlib.sha256(
        f"{conversation_request_id}\0{tool_call_id}".encode()
    ).hexdigest()
    return f"agent-tool-{digest}"


def _agent_result(result: ToolResultV1, tool_call_id: str) -> ToolResult:
    try:
        parsed_summary = json.loads(result.summary)
    except (json.JSONDecodeError, TypeError):
        parsed_summary = {}
    data = parsed_summary if isinstance(parsed_summary, dict | list) else {}
    visible_summary = (
        data.get("message")
        if isinstance(data, dict) and isinstance(data.get("message"), str)
        else result.summary
    )
    error = result.error if isinstance(result.error, dict) else {}
    action = result.ui_action if isinstance(result.ui_action, dict) else {}
    retry_allowed = bool(error.get("recoverable", False) and error.get("safe_to_retry", False))
    details: dict[str, Any] = {
        "schema_version": 1,
        "ok": result.ok,
        "status": result.status,
        "tool_call_id": tool_call_id,
        "summary": visible_summary,
        "data": data,
        "output_asset_ids": result.output_asset_ids,
        "output_refs": [
            {"kind": "asset", "id": asset_id, "role": "output"}
            for asset_id in result.output_asset_ids
        ],
        "warnings": result.warnings,
        "retry": {
            "allowed": retry_allowed,
            "automatic": False,
            "requires_approval": action.get("type")
            in {"approval_required", "confirm_external_paid"},
            "after_seconds": error.get("retry_after_seconds"),
            "reason": error.get("recommended_action"),
        },
        "reused": bool(result.reused),
    }
    for name in ("expected_action", "ui_action", "job", "error"):
        value = getattr(result, name)
        if value is not None:
            details[name] = value
    if isinstance(data, dict) and isinstance(data.get("verification"), dict):
        details["verification"] = data["verification"]
    return ToolResult(
        (TextContent(visible_summary),),
        details=details,
        is_error=not result.ok,
        terminate=False,
    )


def available_aipic_tools(
    registry: AIPicToolRegistry,
    invocation: Callable[[], AIPicToolInvocation],
    visibility_context: dict[str, Any],
) -> tuple[AIPicToolAdapter, ...]:
    """Build Agent tools from the same AIPic availability policy used by the UI."""

    allowed = {
        (item["name"], item["version"])
        for item in registry.visible(visibility_context)
        if item["available"]
    }
    return tuple(
        AIPicToolAdapter(registry, manifest, invocation)
        for key, manifest in sorted(registry.manifests.items())
        if key in allowed
    )
