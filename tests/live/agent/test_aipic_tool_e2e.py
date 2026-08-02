"""Controlled real-LLM proof for an Agent call into the AIPic ToolRegistry."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from aipic_to_model.agent.core.events import CancellationToken
from aipic_to_model.agent.integrations.runtime import AgentRuntime
from aipic_to_model.agent.providers.api.openai_completions import OpenAICompletionsProvider
from aipic_to_model.agent.providers.base import ModelRequest
from aipic_to_model.agent.providers.deepseek import create_deepseek_credential_resolver
from aipic_to_model.application.host_capabilities import HostCapabilityStore
from aipic_to_model.composition import compose_local_app
from aipic_to_model.infrastructure.sqlite.connection import connect


class RecordingLiveProvider:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []
        self._provider = OpenAICompletionsProvider(
            create_deepseek_credential_resolver(), include_stream_usage=False
        )

    async def stream(self, request: ModelRequest, cancellation: CancellationToken):
        self.requests.append(request)
        async for event in self._provider.stream(request, cancellation):
            yield event


@pytest.mark.agent
@pytest.mark.live_llm
@pytest.mark.asyncio
async def test_deepseek_calls_fixed_facade_tool_end_to_end(tmp_path: Path) -> None:
    """Exercise live model -> AgentRuntime -> AIPic registry -> SQLite audit -> response.

    The chosen Tool is read-only and operates only on the pytest-created project, so this
    proof does not submit jobs, contact an image/3D provider, or alter a user project.
    """

    if os.environ.get("RUN_LIVE_LLM_TESTS") != "1":
        pytest.skip("Set RUN_LIVE_LLM_TESTS=1 to run the DeepSeek live E2E test.")

    root = tmp_path / "project"
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    project = dependencies.projects.create(root, "Live Agent Tool E2E")
    dependencies.roots[project.id] = root
    provider = RecordingLiveProvider()
    runtime = AgentRuntime(
        dependencies.registry,
        dependencies.root_for,
        provider_factory=lambda _profile: provider,
        runtime_context_provider=lambda _project_id: {
            "schema_version": 1,
            "snapshot_version": 1,
            "configuration_state": "current",
            "capabilities": {},
            "jobs": {"nonterminal": []},
        },
    )
    conversation = runtime.create(project.id)
    conversation_id = str(conversation["id"])

    started = time.monotonic()
    await runtime.send(
        project.id,
        conversation_id,
        "Call inspect_workspace exactly once with view=summary. Do not pass a project ID. "
        "After receiving the tool result, reply exactly PROJECT_STATE_OK.",
        wait=True,
    )

    messages = runtime.messages(project.id, conversation_id)
    events = runtime.events(project.id, conversation_id, limit=100)["items"]
    connection = connect(root / "project.sqlite3")
    call = connection.execute(
        "SELECT tool_name, status, arguments_json, run_id, duration_ms FROM tool_calls"
    ).fetchone()
    connection.close()

    assert messages[-1] == {
        "id": messages[-1]["id"],
        "role": "assistant",
        "content": [{"type": "text", "text": "PROJECT_STATE_OK"}],
        "stop_reason": messages[-1]["stop_reason"],
        "error_message": None,
    }
    tool_call_message = next(
        item
        for item in messages
        if item["role"] == "assistant"
        and any(block.get("type") == "tool_call" for block in item["content"])
    )
    tool_call = next(block for block in tool_call_message["content"] if block["type"] == "tool_call")
    tool_result = next(item for item in messages if item["role"] == "tool_result")
    assert tool_result["tool_call_id"] == tool_call["id"]
    assert tool_result["tool_name"] == "inspect_workspace"
    exposed_names = {
        str(item["function"]["name"])
        for request in provider.requests
        for item in request.tools
    }
    assert len(provider.requests) == 2
    assert all(len(request.tools) == 15 for request in provider.requests)
    assert exposed_names == {
        "read",
        "write",
        "edit",
        "bash",
        "inspect_workspace",
        "select_asset",
        "analyze_image",
        "prepare_prompt",
        "generate_images",
        "edit_image",
        "split_image",
        "prepare_multiview",
        "generate_model3d",
        "process_model3d",
        "control_job",
    }
    assert tool_call["name"] == "inspect_workspace"
    assert tool_call["arguments"] == {"view": "summary"}
    assert call is not None
    assert call["tool_name"] == "project.get_state"
    assert call["status"] == "succeeded"
    assert call["run_id"] == conversation_id
    assert call["duration_ms"] is not None
    assert json.loads(call["arguments_json"]) == {"project_id": project.id}
    assert [item["event_type"] for item in events].count("tool.completed") == 1
    completed_event = next(
        item
        for item in events
        if item["event_type"] == "tool.completed"
        and item["payload"].get("tool_name") == "inspect_workspace"
    )
    assert completed_event["payload"].get("is_error") is False
    assert completed_event["payload"]["result"]["content"]

    evidence = Path("tests/evidence/agent-live") / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "aipic-tool-e2e.json").write_text(
        json.dumps(
            {
                "provider": "deepseek",
                "scenario": "agent_runtime_fixed_fifteen_facade",
                "model_tool_name": "inspect_workspace",
                "internal_tool_name": "project.get_state",
                "exposed_tool_count": len(provider.requests[0].tools),
                "exposed_tool_names": sorted(exposed_names),
                "tool_status": call["status"],
                "turn_count": sum(item["role"] == "assistant" for item in messages),
                "tool_completed_events": sum(
                    item["event_type"] == "tool.completed" for item in events
                ),
                "duration_ms": round((time.monotonic() - started) * 1000),
                "success": True,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
