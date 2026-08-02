from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from aipic_to_model.agent.core.agent_loop import AgentLoop, AgentLoopConfig
from aipic_to_model.agent.core.events import AgentEventType, CancellationToken
from aipic_to_model.agent.core.models import (
    AssistantMessage,
    Message,
    TextContent,
    ToolCall,
    ToolResult,
    UserMessage,
)
from aipic_to_model.agent.core.tool import ToolContext, ToolRegistry
from aipic_to_model.agent.providers.api.openai_completions import OpenAICompletionsProvider
from aipic_to_model.agent.providers.deepseek import (
    create_deepseek_credential_resolver,
    create_deepseek_profile,
)
from aipic_to_model.agent.providers.fake import FakeProvider, ScriptedResponse


@dataclass
class CalculatorAddTool:
    calls: int = 0
    name: str = "calculator.add"
    label: str = "Calculator add"
    description: str = "Add two integer values and return their sum."
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
        self.calls += 1
        result = ToolResult(
            (TextContent(str(int(arguments["a"]) + int(arguments["b"]))),),
            details={"operation": "add"},
        )
        if on_update is not None:
            update = on_update(result)
            if update is not None:
                await update
        return result


def _response(message: AssistantMessage) -> ScriptedResponse:
    from aipic_to_model.agent.core.models import ProviderEvent, ProviderEventType

    return ScriptedResponse(
        (
            ProviderEvent(ProviderEventType.MESSAGE_START),
            ProviderEvent(ProviderEventType.MESSAGE_END, message=message),
        )
    )


def _event_shape(loop: AgentLoop) -> list[AgentEventType]:
    """Ignore provider chunk granularity when comparing live and Fake trajectories."""

    return [event.type for event in loop.events if event.type is not AgentEventType.MESSAGE_UPDATE]


@pytest.mark.agent
@pytest.mark.live_llm
@pytest.mark.asyncio
async def test_deepseek_text_and_calculator_tool_smoke() -> None:
    if os.environ.get("RUN_LIVE_LLM_TESTS") != "1":
        pytest.skip("Set RUN_LIVE_LLM_TESTS=1 to run the DeepSeek live smoke test.")

    profile = create_deepseek_profile(timeout_seconds=45.0)
    prompt = UserMessage(
        "Use calculator.add exactly once to calculate 17 plus 25. After receiving its result, "
        "reply with the exact phrase CALCULATOR_RESULT_42."
    )
    config = AgentLoopConfig(
        deadline_seconds=90.0,
        temperature=0,
        max_output_tokens=256,
    )
    started = time.monotonic()
    text_loop = AgentLoop(
        OpenAICompletionsProvider(
            create_deepseek_credential_resolver(), include_stream_usage=False
        ),
        profile,
        ToolRegistry(),
        AgentLoopConfig(
            deadline_seconds=45.0,
            temperature=0,
            max_output_tokens=256,
        ),
    )
    text_transcript = await text_loop.run(
        (UserMessage("Reply with the exact phrase DEEPSEEK_STREAM_OK."),), CancellationToken()
    )
    text_message = text_transcript[-1]
    assert isinstance(text_message, AssistantMessage)
    assert "DEEPSEEK_STREAM_OK" in "".join(
        block.text for block in text_message.content if isinstance(block, TextContent)
    )

    error: Exception | None = None
    transcript: tuple[Message, ...] = ()
    tool = CalculatorAddTool()
    live_loop: AgentLoop | None = None
    for attempt in range(2):
        provider = OpenAICompletionsProvider(
            create_deepseek_credential_resolver(), include_stream_usage=False
        )
        live_loop = AgentLoop(provider, profile, ToolRegistry((tool,)), config)
        try:
            transcript = await live_loop.run((prompt,), CancellationToken())
            break
        except Exception as caught:  # one retry only for transient provider errors
            error = caught
            retryable = bool(getattr(caught, "retryable", False))
            if attempt == 0 and retryable:
                continue
            raise
    if (
        error is not None and not transcript
    ):  # pragma: no cover - defensive for future loop changes.
        raise error
    assert live_loop is not None

    assistants = [message for message in transcript if isinstance(message, AssistantMessage)]
    assert len(assistants) == 2
    tool_calls = [
        block for message in assistants for block in message.content if isinstance(block, ToolCall)
    ]
    assert len(tool_calls) == 1 and tool_calls[0].name == "calculator.add"
    assert tool.calls == 1
    final_text = "".join(
        block.text for block in assistants[-1].content if isinstance(block, TextContent)
    )
    assert "CALCULATOR_RESULT_42" in final_text

    fake_tool = CalculatorAddTool()
    fake_loop = AgentLoop(
        FakeProvider(tuple(_response(message) for message in assistants)),
        profile,
        ToolRegistry((fake_tool,)),
        config,
    )
    await fake_loop.run((prompt,), CancellationToken())
    assert _event_shape(live_loop) == _event_shape(fake_loop)

    schema_hash = hashlib.sha256(
        json.dumps(tool.parameters, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    evidence_dir = Path("tests/evidence/agent-live") / time.strftime(
        "%Y%m%dT%H%M%SZ", time.gmtime()
    )
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "deepseek-smoke.json").write_text(
        json.dumps(
            {
                "provider": profile.provider_id,
                "model": profile.model,
                "test_cases": ["streaming_text", "calculator.add", "tool_result_final_answer"],
                "request_correlation_id": assistants[-1].response_id,
                "duration_ms": round((time.monotonic() - started) * 1000),
                "turn_count": len(assistants),
                "tool_count": tool.calls,
                "usage": assistants[-1].usage.to_dict(),
                "event_types": [event.type.value for event in live_loop.events],
                "payload_schema_hash": schema_hash,
                "success": True,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
