"""Real DeepSeek proof for progressive Tool discovery, loading, and execution."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from aipic_to_model.agent.core.events import CancellationToken
from aipic_to_model.agent.execution import LocalExecutionEnv
from aipic_to_model.agent.harness import AgentHarness
from aipic_to_model.agent.integrations.aipic_tools import AIPicToolInvocation
from aipic_to_model.agent.integrations.progressive_tools import (
    PERMANENT_TOOL_NAMES,
    build_progressive_tool_catalog,
)
from aipic_to_model.agent.providers.api.openai_completions import OpenAICompletionsProvider
from aipic_to_model.agent.providers.base import AgentModelProvider, ModelRequest
from aipic_to_model.agent.providers.deepseek import (
    create_deepseek_credential_resolver,
    create_deepseek_profile,
)
from aipic_to_model.agent.session.sqlite import LinearSessionRepository
from aipic_to_model.agent.tools import BashTool, EditTool, ReadTool, WriteTool
from aipic_to_model.application.host_capabilities import HostCapabilityStore
from aipic_to_model.composition import compose_local_app


class RecordingDeepSeekProvider:
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
async def test_deepseek_discovers_loads_and_executes_asset_list(tmp_path: Path) -> None:
    """The external model may reason, but the executed application Tool is local/read-only."""

    if os.environ.get("RUN_LIVE_DEEPSEEK_TOOL_DISCLOSURE") != "1":
        pytest.skip(
            "Set RUN_LIVE_DEEPSEEK_TOOL_DISCLOSURE=1 to run the DeepSeek Tool proof."
        )

    resolver = create_deepseek_credential_resolver()
    if not resolver("agent/deepseek/default"):
        pytest.skip("DeepSeek credential is not configured.")

    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "DeepSeek progressive Tool proof")
    dependencies.roots[project.id] = root
    repository = LinearSessionRepository(root / "agent.sqlite3")
    profile = create_deepseek_profile(timeout_seconds=120.0)
    session = repository.create(
        system_prompt=(
            "Use native Tool Calls. The Tool catalog is progressively disclosed. "
            "When a required Tool is missing, search with toolbox.status, load its exact name "
            "with toolbox.load, wait for the next model turn, then call it. Finish only after "
            "the requested Tool executed successfully."
        ),
        profile={"provider_id": profile.provider_id, "model": profile.model},
        active_tools=PERMANENT_TOOL_NAMES,
    )
    env = LocalExecutionEnv((root,))
    builtins = (ReadTool(env), WriteTool(env), EditTool(env), BashTool(env))
    harness_box: list[AgentHarness] = []
    catalog = build_progressive_tool_catalog(
        builtins,  # type: ignore[arg-type]
        dependencies.registry,
        lambda: AIPicToolInvocation(
            root, project.id, "deepseek-tool-proof", run_id=session.id
        ),
        active_names=lambda: (
            harness_box[0].active_tool_names if harness_box else PERMANENT_TOOL_NAMES
        ),
    )
    provider: AgentModelProvider = RecordingDeepSeekProvider()
    harness = AgentHarness(
        provider,
        profile,
        repository,
        session.id,
        tool_catalog=catalog,
        active_tool_names=PERMANENT_TOOL_NAMES,
    )
    harness_box.append(harness)

    started = time.monotonic()
    result = await harness.prompt(
        "Complete this exact protocol one native Tool call at a time: first call toolbox.status "
        "and search for the Tool that lists managed assets; then call toolbox.load with the exact "
        "asset.list name returned; on the next turn call asset.list with no group; after its Tool "
        "Result, reply exactly PROGRESSIVE_TOOL_OK. Do not substitute project.get_state."
    )

    recorder = provider
    assert isinstance(recorder, RecordingDeepSeekProvider)
    request_tool_names = [
        tuple(str(tool["function"]["name"]) for tool in request.tools)
        for request in recorder.requests
    ]
    assert request_tool_names[0] == PERMANENT_TOOL_NAMES
    loaded_request_index = next(
        index for index, names in enumerate(request_tool_names) if "asset.list" in names
    )
    assert loaded_request_index > 0
    assert request_tool_names[loaded_request_index][:-1] == PERMANENT_TOOL_NAMES
    assert request_tool_names[loaded_request_index][-1] == "asset.list"
    assert repository.open(session.id).active_tools == (*PERMANENT_TOOL_NAMES, "asset.list")
    for tool_name in ("toolbox.status", "toolbox.load", "asset.list"):
        assert any(
            message.role == "tool_result" and message.tool_name == tool_name
            for message in result
        )
    assert result[-1].role == "assistant"
    assert result[-1].content[-1].text.strip() == "PROGRESSIVE_TOOL_OK"

    evidence = Path("tests/evidence/agent-live-deepseek") / time.strftime(
        "%Y%m%dT%H%M%SZ", time.gmtime()
    )
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "progressive-tool-deepseek.json").write_text(
        json.dumps(
            {
                "provider": "deepseek",
                "model": profile.model,
                "initial_tool_count": len(PERMANENT_TOOL_NAMES),
                "loaded_tool_name": "asset.list",
                "loaded_request_index": loaded_request_index,
                "request_tool_counts": [len(names) for names in request_tool_names],
                "duration_ms": round((time.monotonic() - started) * 1000),
                "success": True,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
