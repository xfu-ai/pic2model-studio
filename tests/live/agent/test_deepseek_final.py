"""Controlled Phase 14 DeepSeek scenarios; evidence intentionally excludes content."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import pytest

from aipic_to_model.agent.core.agent_loop import AgentLoop, AgentLoopConfig
from aipic_to_model.agent.core.events import CancellationToken
from aipic_to_model.agent.core.models import (
    AssistantMessage,
    SystemMessage,
    TextContent,
    ToolCall,
    ToolResult,
    UserMessage,
)
from aipic_to_model.agent.core.tool import ToolContext, ToolRegistry
from aipic_to_model.agent.execution import LocalExecutionEnv
from aipic_to_model.agent.extensions.registry import ExtensionContext
from aipic_to_model.agent.harness import AgentHarness
from aipic_to_model.agent.harness.context import CompactionSettings
from aipic_to_model.agent.providers.api.openai_completions import OpenAICompletionsProvider
from aipic_to_model.agent.providers.deepseek import (
    create_deepseek_credential_resolver,
    create_deepseek_profile,
)
from aipic_to_model.agent.session.sqlite import LinearSessionRepository
from aipic_to_model.agent.skills.loader import SkillLoader
from aipic_to_model.agent.tools import BashTool, EditTool, ReadTool, WriteTool


@dataclass
class Calculator:
    name: str
    operation: Callable[[int, int], int]
    calls: list[dict[str, object]] = field(default_factory=list)
    label: str = "calculator"
    description: str = "Calculate two integers."
    parameters: dict[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "additionalProperties": False,
            "required": ["a", "b"],
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        }
    )
    execution_mode: str = "sequential"

    async def execute(
        self,
        tool_call_id: str,
        arguments: dict[str, object],
        context: ToolContext,
        cancellation: CancellationToken,
        on_update: Callable[[ToolResult], Awaitable[None] | None] | None = None,
    ) -> ToolResult:
        del tool_call_id, context
        cancellation.raise_if_cancelled()
        self.calls.append(dict(arguments))
        value = self.operation(int(arguments["a"]), int(arguments["b"]))
        result = ToolResult((TextContent(str(value)),), details={"value": value})
        if on_update is not None:
            update = on_update(result)
            if update is not None:
                await update
        return result


async def _run_once(loop: AgentLoop, prompt: UserMessage):
    error: Exception | None = None
    for attempt in range(2):
        try:
            return await loop.run((prompt,), CancellationToken())
        except Exception as caught:
            error = caught
            if attempt == 0 and bool(getattr(caught, "retryable", False)):
                continue
            raise
    raise error or RuntimeError("live run did not start")


def _provider() -> OpenAICompletionsProvider:
    return OpenAICompletionsProvider(
        create_deepseek_credential_resolver(), include_stream_usage=False
    )


def _profile():
    return create_deepseek_profile(timeout_seconds=45.0)


def _record(scenario: str, repeat: int, started: float, messages, events) -> None:
    """Keep only the Phase 14 evidence fields permitted by the objective."""

    assistants = [item for item in messages if isinstance(item, AssistantMessage)]
    evidence = Path("tests/evidence/agent-live") / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / f"phase14-{scenario}-{repeat}.json").write_text(
        json.dumps(
            {
                "provider": "deepseek",
                "model": _profile().model,
                "duration_ms": round((time.monotonic() - started) * 1000),
                "turn_count": len(assistants),
                "tool_count": sum(1 for item in messages if item.role == "tool_result"),
                "usage": assistants[-1].usage.to_dict() if assistants else {},
                "event_types": [event.type.value for event in events],
                "success": True,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


@pytest.mark.agent
@pytest.mark.live_llm
@pytest.mark.asyncio
@pytest.mark.parametrize("repeat", (1, 2))
async def test_deepseek_final_scenario_a_chained_calculator(repeat: int) -> None:
    if os.environ.get("RUN_LIVE_LLM_TESTS") != "1":
        pytest.skip("Set RUN_LIVE_LLM_TESTS=1 to run the DeepSeek final suite.")
    add = Calculator("calculator.add", lambda a, b: a + b, label="calculator.add")
    multiply = Calculator("calculator.multiply", lambda a, b: a * b, label="calculator.multiply")
    profile = create_deepseek_profile(timeout_seconds=45.0)
    loop = AgentLoop(
        OpenAICompletionsProvider(
            create_deepseek_credential_resolver(), include_stream_usage=False
        ),
        profile,
        ToolRegistry((add, multiply)),
        AgentLoopConfig(
            deadline_seconds=90.0,
            temperature=0,
            max_output_tokens=128,
        ),
    )
    started = time.monotonic()
    transcript = await _run_once(
        loop,
        UserMessage(
            "Use calculator.add exactly once for 17 and 25. Then use calculator.multiply exactly "
            "once with the add result and 2. Reply exactly FINAL_84 after both tool results."
        ),
    )
    assistants = [message for message in transcript if isinstance(message, AssistantMessage)]
    calls = [
        block for message in assistants for block in message.content if isinstance(block, ToolCall)
    ]
    final = assistants[-1]
    final_text = "".join(block.text for block in final.content if isinstance(block, TextContent))
    assert [call.name for call in calls] == ["calculator.add", "calculator.multiply"]
    assert add.calls == [{"a": 17, "b": 25}]
    assert multiply.calls == [{"a": 42, "b": 2}]
    assert not any(isinstance(block, ToolCall) for block in final.content)
    assert "FINAL_84" in final_text
    assert time.monotonic() - started < 90
    _record("calculator", repeat, started, transcript, loop.events)


@pytest.mark.agent
@pytest.mark.live_llm
@pytest.mark.asyncio
@pytest.mark.parametrize("repeat", (1, 2))
async def test_deepseek_final_scenario_b_workspace_tools(tmp_path: Path, repeat: int) -> None:
    if os.environ.get("RUN_LIVE_LLM_TESTS") != "1":
        pytest.skip("Set RUN_LIVE_LLM_TESTS=1 to run the DeepSeek final suite.")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    environment = LocalExecutionEnv((workspace,))
    repository = LinearSessionRepository(tmp_path / "agent.sqlite3")
    session = repository.create()
    harness = AgentHarness(
        _provider(),
        _profile(),
        repository,
        session.id,
        tools=(
            WriteTool(environment),
            EditTool(environment),
            ReadTool(environment),
            BashTool(environment),
        ),
    )
    started = time.monotonic()
    messages = await harness.prompt(
        "Use write to create note.txt containing exactly alpha. Use edit to replace alpha with beta. "
        "Use read on note.txt. Use bash with Get-FileHash note.txt. Then reply exactly FILE_TOOLS_OK."
    )
    tool_names = [
        str(event.payload.get("tool_name"))
        for event in harness.events
        if event.type.value == "tool_execution_end"
    ]
    final = messages[-1]
    assert (workspace / "note.txt").read_text(encoding="utf-8") == "beta"
    assert {"write", "edit", "read", "bash"}.issubset(tool_names)
    assert isinstance(final, AssistantMessage)
    assert "FILE_TOOLS_OK" in "".join(
        item.text for item in final.content if isinstance(item, TextContent)
    )
    _record("workspace-tools", repeat, started, messages, harness.events)


@pytest.mark.agent
@pytest.mark.live_llm
@pytest.mark.asyncio
@pytest.mark.parametrize("repeat", (1, 2))
async def test_deepseek_final_scenario_c_skill(tmp_path: Path, repeat: int) -> None:
    if os.environ.get("RUN_LIVE_LLM_TESTS") != "1":
        pytest.skip("Set RUN_LIVE_LLM_TESTS=1 to run the DeepSeek final suite.")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "one.txt").write_text("one", encoding="utf-8")
    (workspace / "two.txt").write_text("two", encoding="utf-8")
    skill = workspace / ".agent-skills" / "combine" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: combine\ndescription: Combine two files\nrequired_tools: read,write\n---\n"
        "Read one.txt and two.txt. The read tool prefixes line numbers; strip those prefixes. "
        "Write summary.txt as exactly one|two, with no line numbers or extra text.",
        encoding="utf-8",
    )
    environment = LocalExecutionEnv((workspace,))
    repository = LinearSessionRepository(tmp_path / "agent.sqlite3")
    session = repository.create()
    loader = SkillLoader(environment, project_roots=(workspace / ".agent-skills",))
    harness = AgentHarness(
        _provider(),
        _profile(),
        repository,
        session.id,
        tools=(ReadTool(environment), WriteTool(environment)),
        skill_loader=loader,
    )
    started = time.monotonic()
    await harness.skill("combine")
    messages = await harness.prompt(
        "Follow the active combine Skill exactly. Reply exactly SKILL_DONE after writing summary.txt."
    )
    assert (workspace / "summary.txt").read_text(encoding="utf-8").strip() == "one|two"
    assert (
        repository.open(session.id).active_skills
        and "combine@" in repository.open(session.id).active_skills[0]
    )
    assert isinstance(messages[-1], AssistantMessage)
    _record("skill", repeat, started, messages, harness.events)


class ProjectNote:
    name = "project.note"
    label = "project.note"
    description = "Store a short project note."
    parameters: ClassVar[dict[str, object]] = {
        "type": "object",
        "required": ["note"],
        "properties": {"note": {"type": "string"}},
    }
    execution_mode = "sequential"

    async def execute(self, tool_call_id, arguments, context, cancellation, on_update=None):
        del tool_call_id, arguments, context, on_update
        cancellation.raise_if_cancelled()
        return ToolResult((TextContent("note stored"),), details={})


class LiveAuditExtension:
    extension_id = "live-audit"
    version = "1"
    priority = 0

    def register(self, context: ExtensionContext) -> None:
        context.add_tool(ProjectNote())
        context.add_context_transform(
            lambda messages: (SystemMessage("Use project.note when requested."),) + messages
        )
        context.add_lifecycle_hook("before_provider_request", lambda _payload: {"temperature": 0})
        context.add_lifecycle_hook(
            "after_tool_call", lambda _payload: {"details": {"audit": "live"}}
        )

    def close(self) -> None:
        return None


@pytest.mark.agent
@pytest.mark.live_llm
@pytest.mark.asyncio
@pytest.mark.parametrize("repeat", (1, 2))
async def test_deepseek_final_scenario_d_extension(tmp_path: Path, repeat: int) -> None:
    if os.environ.get("RUN_LIVE_LLM_TESTS") != "1":
        pytest.skip("Set RUN_LIVE_LLM_TESTS=1 to run the DeepSeek final suite.")
    repository = LinearSessionRepository(tmp_path / "agent.sqlite3")
    session = repository.create()
    harness = AgentHarness(
        _provider(), _profile(), repository, session.id, extensions=(LiveAuditExtension(),)
    )
    started = time.monotonic()
    messages = await harness.prompt(
        "Use project.note exactly once with note extension-check. Then reply EXTENSION_DONE."
    )
    result = next(item for item in messages if item.role == "tool_result")
    assert result.tool_name == "project.note"
    assert result.result.details == {"audit": "live"}
    assert "live-audit" not in harness.extensions.disabled
    assert isinstance(messages[-1], AssistantMessage)
    _record("extension", repeat, started, messages, harness.events)


class OverflowOnceProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.live = _provider()

    async def stream(self, request, cancellation):
        self.calls += 1
        if self.calls == 1:
            from aipic_to_model.agent.core.models import ProviderEvent, ProviderEventType

            yield ProviderEvent(ProviderEventType.PROVIDER_ERROR, error_message="context overflow")
            return
        async for event in self.live.stream(request, cancellation):
            yield event


async def _live_summary(value) -> str:
    facts = "\n".join(
        item.content
        if isinstance(item, UserMessage) and isinstance(item.content, str)
        else item.role
        for item in value.messages
    )
    for _attempt in range(2):
        loop = AgentLoop(
            _provider(),
            _profile(),
            ToolRegistry(),
            AgentLoopConfig(
                deadline_seconds=60,
                temperature=0,
                max_output_tokens=1024,
            ),
        )
        result = await loop.run(
            (
                UserMessage(
                    "Return exactly six short markdown headings in this order: Goal, Constraints, Progress, "
                    "Decisions, Next Steps, Critical Context. Use one sentence (20 words max) under each heading.\n"
                    + facts
                ),
            ),
            CancellationToken(),
        )
        answer = result[-1]
        if isinstance(answer, AssistantMessage):
            summary = "".join(item.text for item in answer.content if isinstance(item, TextContent))
            if summary:
                return summary
    raise RuntimeError("DeepSeek returned no summary text after one safe retry")


@pytest.mark.agent
@pytest.mark.live_llm
@pytest.mark.asyncio
@pytest.mark.parametrize("repeat", (1, 2))
async def test_deepseek_final_scenario_e_compaction_and_overflow(
    tmp_path: Path, repeat: int
) -> None:
    if os.environ.get("RUN_LIVE_LLM_TESTS") != "1":
        pytest.skip("Set RUN_LIVE_LLM_TESTS=1 to run the DeepSeek final suite.")
    repository = LinearSessionRepository(tmp_path / "agent.sqlite3")
    session = repository.create()
    for index in range(4):
        repository.append_message(
            session.id, UserMessage(f"Constraint {index}: retain project-fact-{index}.")
        )
        repository.append_message(
            session.id, AssistantMessage((TextContent(f"Completed item {index}."),))
        )
    provider = OverflowOnceProvider()
    harness = AgentHarness(
        provider,
        _profile(),
        repository,
        session.id,
        context_window=400,
        compaction_settings=CompactionSettings(reserve_tokens=250, keep_recent_tokens=40),
        summarizer=_live_summary,
    )
    started = time.monotonic()
    messages = await harness.prompt(
        "Reply exactly COMPACTION_RECOVERED while retaining project-fact-0."
    )
    record = repository.latest_compaction(session.id)
    event_names = [event.type.value for event in harness.events]
    assert provider.calls == 2
    assert record is not None and record.summary is not None
    assert (
        "Goal" in record.summary
        and "Next Steps" in record.summary
        and "Critical Context" in record.summary
    )
    assert "context_compacted" in event_names and "retry_scheduled" in event_names
    assert len(repository.open(session.id).messages) >= 9
    assert isinstance(messages[-1], AssistantMessage)
    _record("compaction-overflow", repeat, started, messages, harness.events)
