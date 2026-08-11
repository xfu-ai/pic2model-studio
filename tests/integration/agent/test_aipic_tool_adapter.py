from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast

import pytest

from aipic_to_model.agent.core.events import AgentEvent, AgentEventType, CancellationToken
from aipic_to_model.agent.core.models import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResult,
    ToolResultMessage,
)
from aipic_to_model.agent.core.tool import ToolContext
from aipic_to_model.agent.integrations.aipic_tools import (
    AIPicToolAdapter,
    AIPicToolInvocation,
    _tool_request_id,
    available_aipic_tools,
)
from aipic_to_model.application.tools import ToolRegistry as AIPicToolRegistry
from aipic_to_model.domain.common import RiskLevel
from aipic_to_model.domain.tools import ToolManifestV1, ToolResultV1


@dataclass
class Registry:
    calls: list[tuple[object, ...]]

    def execute(self, *args: object) -> ToolResultV1:
        self.calls.append(args)
        return ToolResultV1(True, "succeeded", "aipic-call", ["asset-1"], "Asset updated.", [])

    def visible(self, _context: dict[str, object]) -> list[dict[str, object]]:
        return [
            {"name": "asset.update", "version": "1.0.0", "available": True},
            {"name": "asset.hidden", "version": "1.0.0", "available": False},
        ]


@pytest.mark.agent
@pytest.mark.asyncio
async def test_aipic_adapter_delegates_to_registry_with_asset_ids_and_structured_result(
    tmp_path,
) -> None:
    manifest = ToolManifestV1(
        "asset.update",
        "1.0.0",
        "Update asset",
        "Updates an AIPic asset.",
        {
            "type": "object",
            "required": ["asset_id"],
            "properties": {"asset_id": {"type": "string"}},
        },
        {"type": "object"},
        RiskLevel.READ_ONLY,
        "sync",
        True,
        False,
        [],
        "asset.update",
    )
    registry = Registry([])
    adapter = AIPicToolAdapter(
        registry,  # type: ignore[arg-type]
        manifest,
        lambda: AIPicToolInvocation(tmp_path, "project", "request", provider_profile="profile"),
    )

    result = await adapter.execute(
        "agent-call", {"asset_id": "asset-1"}, ToolContext(()), CancellationToken()
    )

    assert registry.calls[0][2:6] == (
        "asset.update",
        "1.0.0",
        {"asset_id": "asset-1"},
        _tool_request_id("request", "agent-call"),
    )
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "Asset updated."
    assert isinstance(result.details, dict)
    assert result.details["schema_version"] == 1
    assert result.details["ok"] is True
    assert result.details["status"] == "succeeded"
    assert result.details["tool_call_id"] == "agent-call"
    assert result.details["output_asset_ids"] == ["asset-1"]
    assert result.details["output_refs"] == [
        {"kind": "asset", "id": "asset-1", "role": "output"}
    ]
    assert result.details["retry"]["allowed"] is False

    registry.manifests = {("asset.update", "1.0.0"): manifest}  # type: ignore[attr-defined]
    tools = available_aipic_tools(
        cast(AIPicToolRegistry, registry),
        lambda: AIPicToolInvocation(tmp_path, "project", "request"),
        {},
    )
    assert [tool.name for tool in tools] == ["asset.update"]


def test_adapter_preserves_queued_job_and_ui_action_details() -> None:
    from aipic_to_model.agent.integrations.aipic_tools import _agent_result

    queued = ToolResultV1(
        True,
        "queued",
        "call",
        [],
        "Job queued.",
        [],
        job={
            "job_id": "job",
            "status": "queued",
            "job_type": "asset.update",
            "stage": "queued",
            "elapsed_seconds": 0,
            "provider": "local",
            "can_cancel": False,
            "can_stop_waiting": True,
        },
    )
    result = _agent_result(queued, "agent-call")
    assert isinstance(result.details, dict)
    job = result.details["job"]
    assert isinstance(job, dict) and job["job_id"] == "job"


def test_adapter_preserves_advisory_verification_without_changing_tool_success() -> None:
    from aipic_to_model.agent.integrations.aipic_tools import _agent_result

    source = ToolResultV1(
        True,
        "succeeded",
        "call",
        ["asset"],
        json.dumps(
            {
                "message": "Background removed locally.",
                "verification": {
                    "schema_version": 1,
                    "disposition": "review_required",
                    "checks": [
                        {"code": "image.background_removal_alpha", "outcome": "warn"}
                    ],
                },
            }
        ),
        [],
    )

    result = _agent_result(source, "agent-call")

    assert result.is_error is False
    assert isinstance(result.details, dict)
    assert result.details["status"] == "succeeded"
    assert result.content[0] == TextContent("Background removed locally.")
    assert result.details["verification"]["disposition"] == "review_required"


def test_execution_outline_parser_is_best_effort_and_non_blocking() -> None:
    from aipic_to_model.agent.integrations.runtime import _execution_outline_from_message

    parsed = _execution_outline_from_message(
        {
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": "<execution_outline>\ngoal: update asset\ncurrent: 1\n</execution_outline>",
                }
            ],
        }
    )

    assert parsed is not None
    assert parsed["parse_state"] == "structured"
    assert parsed["fields"] == {"goal": "update asset", "current": "1"}
    assert _execution_outline_from_message({"role": "assistant", "content": "plain reply"}) is None


def test_facade_exposes_verification_to_the_model_after_a_successful_tool_call() -> None:
    from aipic_to_model.agent.integrations.facade_tools import _facade_agent_result

    result = ToolResultV1(
        True,
        "succeeded",
        "call",
        ["asset"],
        json.dumps(
            {
                "message": "Background removed locally.",
                "verification": {"disposition": "review_required", "checks": []},
            }
        ),
        [],
    )

    converted = _facade_agent_result(result, "agent-call", source_tool="edit_image")

    assert isinstance(converted.details, dict)
    assert converted.details["verification"]["disposition"] == "review_required"
    assert isinstance(converted.content[0], TextContent)
    assert converted.content[0].text.startswith("Background removed locally.")
    assert '"verification"' in converted.content[0].text


def test_runtime_exposes_fixed_tools_and_projects_approval_details(tmp_path) -> None:
    from aipic_to_model.agent.integrations.runtime import (
        AgentRuntime,
        _message_dto,
        _sanitize_message,
    )
    from aipic_to_model.agent.integrations.progressive_tools import (
        AGGREGATE_TOOL_NAMES,
        BUSINESS_TOOL_NAMES,
        PERMANENT_TOOL_NAMES,
    )
    from aipic_to_model.application.host_capabilities import HostCapabilityStore
    from aipic_to_model.composition import compose_local_app

    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Agent production tools")
    dependencies.roots[project.id] = root
    runtime = AgentRuntime(dependencies.registry, dependencies.root_for)
    created = runtime.create(project.id)
    conversation = runtime._conversations[(project.id, str(created["id"]))]
    tool_names = tuple(tool.name for tool in conversation.harness.agent.state.tools)
    assert tool_names == PERMANENT_TOOL_NAMES
    catalog_tools = {
        tool.name: tool for tool in conversation.harness._tool_catalog.all()
    }
    assert set(BUSINESS_TOOL_NAMES) <= set(catalog_tools)
    assert not set(AGGREGATE_TOOL_NAMES) & set(catalog_tools)
    assert all("exact managed references" in catalog_tools[name].description for name in BUSINESS_TOOL_NAMES)
    assert "next model turn" in catalog_tools["toolbox.load"].description
    assert "Never use filesystem or shell tools" in conversation.harness.snapshot().system_prompt
    system_prompt = conversation.harness.snapshot().system_prompt
    assert "already bound to the current managed project" in system_prompt
    assert "Never invent or pass a project ID" in system_prompt
    assert "Async-job contract" in system_prompt

    message = ToolResultMessage(
        "call",
        "generate_model3d",
        ToolResult(
            (TextContent("Approval required."),),
            details={"ui_action": {"action_id": "approval-1"}},
        ),
    )
    assert _message_dto(message)["details"] == {
        "ui_action": {"action_id": "approval-1"}
    }
    sanitized = _sanitize_message(message)
    assert isinstance(sanitized, ToolResultMessage)
    assert sanitized.result.details == {
        "ui_action": {"action_id": "approval-1"}
    }


def test_runtime_projects_pi_content_blocks_and_tool_result_pairing() -> None:
    from aipic_to_model.agent.integrations.runtime import (
        _event_dto,
        _message_dto,
        _project_model_context,
        _sanitize_message,
    )

    assistant = AssistantMessage(
        (
            ThinkingContent("Inspect Authorization: token at C:\\outside"),
            TextContent("I will inspect the current asset."),
            ToolCall("call-1", "asset.list", {"project_id": "project-1"}),
        ),
        stop_reason="tool_use",
    )
    projected = _message_dto(assistant)
    assert projected["content"] == [
        {"type": "text", "text": "I will inspect the current asset."},
        {
            "type": "tool_call",
            "id": "call-1",
            "name": "asset.list",
            "arguments": {"project_id": "project-1"},
        },
    ]
    persisted = _sanitize_message(assistant)
    assert isinstance(persisted, AssistantMessage)
    assert isinstance(persisted.content[0], ThinkingContent)
    assert persisted.content[0].thinking == "Inspect [REDACTED] at <workspace-path>"
    result = _message_dto(
        ToolResultMessage("call-1", "asset.list", ToolResult((TextContent("[]"),)))
    )
    assert result["tool_call_id"] == "call-1"
    tool_call = _event_dto(
        "conversation-1",
        AgentEvent(
            AgentEventType.MESSAGE_UPDATE,
            {
                "provider_event": {
                    "type": "tool_call_end",
                    "tool_call": {"id": "call-1", "name": "asset.list", "arguments": {"project_id": "project-1"}},
                }
            },
        ),
    )
    assert tool_call == (
        "tool.call",
        {
            "conversation_id": "conversation-1",
            "phase": "tool_call_end",
            "tool_call": {
                "type": "tool_call",
                "id": "call-1",
                "name": "asset.list",
                "arguments": {"project_id": "project-1"},
            },
        },
    )
    completed = _event_dto(
        "conversation-1",
        AgentEvent(
            AgentEventType.TOOL_EXECUTION_END,
            {
                "tool_call_id": "call-1",
                "tool_name": "asset.list",
                "is_error": False,
                "result": ToolResult((TextContent("[]"),)).to_dict(),
            },
        ),
    )
    assert completed is not None
    assert completed[0] == "tool.completed"
    assert completed[1]["tool_call_id"] == "call-1"
    assert completed[1]["result"] == {"content": [{"type": "text", "text": "[]"}], "details": None, "is_error": False}

    inventory = json.dumps(
        [
            {"name": f"asset-{index}", "is_current": index == 99, "metadata": "x" * 80}
            for index in range(100)
        ]
    )
    raw_result = ToolResultMessage("call-2", "asset.list", ToolResult((TextContent(inventory),)))
    projected_context = _project_model_context((raw_result,))
    projected_result = projected_context[0]
    assert isinstance(projected_result, ToolResultMessage)
    assert projected_result.tool_call_id == "call-2"
    assert isinstance(projected_result.content[0], TextContent)
    assert projected_result.content[0].text == (
        "asset.list returned 100 managed assets. Current asset: asset-99. "
        "The full inventory is retained locally; use "
            "asset.get_metadata when an individual entry is needed."
    )
    assert raw_result.content[0].text == inventory


def test_runtime_context_is_fresh_model_only_system_context() -> None:
    from aipic_to_model.agent.core.models import SystemMessage, UserMessage
    from aipic_to_model.agent.integrations.runtime import _with_runtime_context
    from aipic_to_model.agent.providers.base import ModelProfile, ModelRequest

    request = ModelRequest(
        ModelProfile("fake", "fake", "https://example.invalid"),
        (SystemMessage("base"), UserMessage("hello")),
    )
    updated = _with_runtime_context(
        request,
        {
            "schema_version": 1,
            "snapshot_version": 7,
            "capabilities": {"image_generation": {"available": False}},
        },
    )

    assert request.messages[0].content == "base"
    assert len(request.messages) == 2
    assert len(updated.messages) == 3
    assert updated.messages[0].content == "base"
    assert isinstance(updated.messages[1], SystemMessage)
    assert '"snapshot_version":7' in updated.messages[1].content
    assert '"available":false' in updated.messages[1].content


@pytest.mark.agent
@pytest.mark.asyncio
async def test_agent_tool_calls_get_distinct_stable_idempotency_keys(tmp_path) -> None:
    manifest = ToolManifestV1(
        "asset.update",
        "1.0.0",
        "Update asset",
        "Updates an AIPic asset.",
        {
            "type": "object",
            "required": ["asset_id"],
            "properties": {"asset_id": {"type": "string"}},
        },
        {"type": "object"},
        RiskLevel.READ_ONLY,
        "sync",
        True,
        False,
        [],
        "asset.update",
    )
    registry = Registry([])
    adapter = AIPicToolAdapter(
        registry,  # type: ignore[arg-type]
        manifest,
        lambda: AIPicToolInvocation(tmp_path, "project", "conversation-request"),
    )
    context = ToolContext(())
    cancellation = CancellationToken()

    await adapter.execute("call-1", {"asset_id": "asset-1"}, context, cancellation)
    await adapter.execute("call-2", {"asset_id": "asset-2"}, context, cancellation)
    await adapter.execute("call-1", {"asset_id": "asset-1"}, context, cancellation)

    request_ids = [str(call[5]) for call in registry.calls]
    assert request_ids[0] != request_ids[1]
    assert request_ids[0] == request_ids[2]
