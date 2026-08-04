from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from aipic_to_model.agent.core.agent_loop import AgentLoop, AgentLoopConfig
from aipic_to_model.agent.core.errors import ProviderError
from aipic_to_model.agent.core.events import CancellationToken
from aipic_to_model.agent.core.models import (
    AssistantMessage,
    ImageContent,
    ProviderEvent,
    ProviderEventType,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResult,
    UserMessage,
)
from aipic_to_model.agent.core.tool import ToolContext, ToolRegistry
from aipic_to_model.agent.integrations.runtime import AgentRuntime, _consume_task_exception
from aipic_to_model.agent.providers.api.openai_completions import OpenAICompletionsProvider
from aipic_to_model.agent.providers.base import ModelProfile, ModelRequest
from aipic_to_model.agent.providers.fake import FakeProvider, ScriptedResponse
from aipic_to_model.agent.providers.qwen3_vl import create_qwen3_vl_profile
from aipic_to_model.agent.session.sqlite import LinearSessionRepository
from aipic_to_model.application.host_capabilities import HostCapabilityStore
from aipic_to_model.composition import compose_local_app


def _text_response(text: str) -> ScriptedResponse:
    return ScriptedResponse(
        (
            ProviderEvent(ProviderEventType.MESSAGE_START),
            ProviderEvent(ProviderEventType.TEXT_DELTA, delta=text),
            ProviderEvent(
                ProviderEventType.MESSAGE_END,
                message=AssistantMessage((TextContent(text),)),
            ),
        )
    )


def _reasoning_text_response(thinking: str, text: str) -> ScriptedResponse:
    return ScriptedResponse(
        (
            ProviderEvent(ProviderEventType.MESSAGE_START),
            ProviderEvent(ProviderEventType.REASONING_START),
            ProviderEvent(ProviderEventType.REASONING_DELTA, delta=thinking),
            ProviderEvent(ProviderEventType.REASONING_END),
            ProviderEvent(ProviderEventType.TEXT_DELTA, delta=text),
            ProviderEvent(
                ProviderEventType.MESSAGE_END,
                message=AssistantMessage(
                    (ThinkingContent(thinking), TextContent(text)),
                    provider="ollama",
                    model="qwen3-vl:8b",
                ),
            ),
        )
    )


def test_background_agent_task_exception_is_consumed() -> None:
    class CompletedTask:
        exception_reads = 0

        @staticmethod
        def cancelled() -> bool:
            return False

        def exception(self) -> RuntimeError:
            self.exception_reads += 1
            return RuntimeError("persisted provider failure")

    task = CompletedTask()

    _consume_task_exception(task)  # type: ignore[arg-type]

    assert task.exception_reads == 1


@pytest.mark.agent
@pytest.mark.asyncio
async def test_deepseek_is_the_default_and_recovers_a_multi_turn_conversation(
    tmp_path: Path,
) -> None:
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "DeepSeek recovery")
    dependencies.roots[project.id] = root
    captured_profiles: list[ModelProfile] = []
    captured_providers: list[FakeProvider] = []

    def provider_factory(profile: ModelProfile) -> FakeProvider:
        captured_profiles.append(profile)
        provider = FakeProvider((_text_response(f"turn-{len(captured_profiles)}"),))
        captured_providers.append(provider)
        return provider

    first_runtime = AgentRuntime(
        dependencies.registry,
        lambda _project_id: root,
        provider_factory=provider_factory,
    )
    conversation_id = str(first_runtime.create(project.id)["id"])
    await first_runtime.send(project.id, conversation_id, "first", wait=True)

    recovered_runtime = AgentRuntime(
        dependencies.registry,
        lambda _project_id: root,
        provider_factory=provider_factory,
    )
    await recovered_runtime.send(project.id, conversation_id, "second", wait=True)

    session = LinearSessionRepository(root / "agent.sqlite3").open(conversation_id)
    assert [message.role for message in session.messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert [profile.provider_id for profile in captured_profiles] == ["deepseek", "deepseek"]
    assert [profile.model for profile in captured_profiles] == [
        "deepseek-v4-flash",
        "deepseek-v4-flash",
    ]
    assert session.profile["credential_ref"] == "agent/deepseek/default"
    assert session.profile["base_url"] == "https://api.deepseek.com"
    assert all(
        provider.requests[0].reasoning_effort is None for provider in captured_providers
    )


@pytest.mark.agent
def test_agent_model_selector_can_explicitly_choose_local_qwen(tmp_path: Path) -> None:
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Configured Qwen")
    dependencies.roots[project.id] = root
    runtime = AgentRuntime(
        dependencies.registry,
        lambda _project_id: root,
        agent_model_selector=lambda: "qwen3-vl:4b",
    )

    conversation_id = str(runtime.create(project.id)["id"])
    session = LinearSessionRepository(root / "agent.sqlite3").open(conversation_id)

    assert session.profile["provider_id"] == "ollama"
    assert session.profile["model"] == "qwen3-vl:4b"
    assert session.thinking_level == "medium"


@pytest.mark.agent
@pytest.mark.asyncio
async def test_qwen_runtime_projects_live_reasoning_events_without_changing_deepseek(
    tmp_path: Path,
) -> None:
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Reasoning events")
    dependencies.roots[project.id] = root
    providers: list[FakeProvider] = []

    def provider_factory(_profile: ModelProfile) -> FakeProvider:
        provider = FakeProvider((_reasoning_text_response("Inspecting.", "Ready."),))
        providers.append(provider)
        return provider

    runtime = AgentRuntime(
        dependencies.registry,
        lambda _project_id: root,
        provider_factory=provider_factory,
        agent_model_selector=lambda: "qwen3-vl:8b",
    )
    conversation_id = str(runtime.create(project.id)["id"])

    await runtime.send(project.id, conversation_id, "inspect", wait=True)

    event_types = [
        item["event_type"]
        for item in runtime.events(project.id, conversation_id, limit=100)["items"]
    ]
    assert "reasoning.started" in event_types
    assert "reasoning.delta" in event_types
    assert "reasoning.completed" in event_types
    assert providers[0].requests[0].reasoning_effort == "medium"


@pytest.mark.agent
@pytest.mark.asyncio
async def test_ollama_openai_request_is_loopback_credentialless_and_tool_capable() -> None:
    captured: dict[str, object] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            content=(
                'data: {"id":"ollama-1","choices":[{"delta":{"reasoning":"I should calculate."}}]}\n\n'
                'data: {"choices":[{"delta":{"tool_calls":'
                '[{"id":"call-1","index":0,"function":{"name":"calculator_add",'
                '"arguments":"{\\"a\\":1,\\"b\\":2}"}}]},'
                '"finish_reason":"tool_calls"}]}\n\ndata: [DONE]\n\n'
            ),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    provider = OpenAICompletionsProvider(
        lambda _ref: None,
        client=client,
        include_stream_usage=False,
        enforce_loopback=True,
    )
    request = ModelRequest(
        create_qwen3_vl_profile(),
        (UserMessage("add"),),
        tools=(
            {
                "type": "function",
                "function": {
                    "name": "calculator.add",
                    "description": "add two values",
                    "parameters": {"type": "object"},
                },
            },
        ),
        reasoning_effort="medium",
    )

    events = [event async for event in provider.stream(request, CancellationToken())]
    await client.aclose()

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert captured["url"] == "http://127.0.0.1:11434/v1/chat/completions"
    assert captured["authorization"] is None
    assert "stream_options" not in payload
    assert payload["reasoning_effort"] == "medium"
    assert payload["tools"][0]["function"]["name"] == "calculator_add"
    assert [event.type for event in events[:4]] == [
        ProviderEventType.MESSAGE_START,
        ProviderEventType.REASONING_START,
        ProviderEventType.REASONING_DELTA,
        ProviderEventType.REASONING_END,
    ]
    assert events[-1].message is not None
    assert isinstance(events[-1].message.content[0], ThinkingContent)
    tool_call = events[-1].message.content[-1]
    assert isinstance(tool_call, ToolCall)
    assert tool_call.name == "calculator.add"


@pytest.mark.agent
@pytest.mark.asyncio
async def test_ollama_string_error_is_safely_classified_without_exposing_body() -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={"error": "llama runner process exited: CUDA error: out of memory at C:\\secret"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    provider = OpenAICompletionsProvider(
        lambda _ref: None,
        client=client,
        include_stream_usage=False,
        enforce_loopback=True,
    )

    with pytest.raises(ProviderError) as raised:
        _ = [
            event
            async for event in provider.stream(
                ModelRequest(create_qwen3_vl_profile(), (UserMessage("inspect"),)),
                CancellationToken(),
            )
        ]
    await client.aclose()

    assert raised.value.details["status_code"] == 500
    assert raised.value.details["error_code"] == "resource_exhausted"
    assert "secret" not in str(raised.value.to_dict())


@pytest.mark.agent
@pytest.mark.asyncio
async def test_loopback_provider_rejects_a_recovered_non_loopback_profile() -> None:
    provider = OpenAICompletionsProvider(lambda _ref: None, enforce_loopback=True)
    request = ModelRequest(
        ModelProfile("ollama", "qwen3-vl:8b", "https://example.test/v1"),
        (UserMessage("hello"),),
    )

    with pytest.raises(ValueError, match="loopback"):
        _ = [event async for event in provider.stream(request, CancellationToken())]


@pytest.mark.agent
def test_ollama_openai_message_serializes_request_only_image_content() -> None:
    payload = OpenAICompletionsProvider._message(
        UserMessage(
            (
                TextContent("inspect"),
                ImageContent("cGl4ZWxz", "image/png"),
            )
        )
    )

    assert payload == {
        "role": "user",
        "content": [
            {"type": "text", "text": "inspect"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,cGl4ZWxz"},
            },
        ],
    }


@pytest.mark.agent
def test_openai_assistant_reasoning_replay_is_qwen_opt_in() -> None:
    assistant = AssistantMessage(
        (
            ThinkingContent("inspect before answering"),
            ToolCall("call-1", "inspect_workspace", {"view": "summary"}),
        ),
        stop_reason="tool_use",
    )

    qwen_payload = OpenAICompletionsProvider._message(assistant, include_reasoning=True)
    deepseek_payload = OpenAICompletionsProvider._message(assistant)

    assert qwen_payload["reasoning"] == "inspect before answering"
    assert "reasoning" not in deepseek_payload


class _EchoTool:
    name = "echo"
    label = "Echo"
    description = "Return a value to the model"
    execution_mode = "sequential"

    def __init__(self) -> None:
        self.values: list[str] = []
        self.parameters = {
            "type": "object",
            "required": ["value"],
            "properties": {"value": {"type": "string"}},
        }

    async def execute(
        self,
        tool_call_id: str,
        arguments: dict[str, object],
        context: ToolContext,
        cancellation: CancellationToken,
        on_update=None,
    ) -> ToolResult:
        del tool_call_id, context, on_update
        cancellation.raise_if_cancelled()
        value = str(arguments["value"])
        self.values.append(value)
        return ToolResult((TextContent(f"observed:{value}"),))


@pytest.mark.agent
@pytest.mark.asyncio
async def test_qwen_reasoning_tool_result_and_follow_up_form_a_complete_agent_loop() -> None:
    payloads: list[dict[str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        payloads.append(payload)
        if len(payloads) == 1:
            return httpx.Response(
                200,
                content=(
                    'data: {"id":"turn-1","choices":[{"delta":{"reasoning":"Need the tool first."}}]}\n\n'
                    'data: {"choices":[{"delta":{"tool_calls":[{"id":"call-1","index":0,'
                    '"function":{"name":"echo","arguments":"{\\"value\\":\\"scene\\"}"}}]},'
                    '"finish_reason":"tool_calls"}]}\n\ndata: [DONE]\n\n'
                ),
            )
        messages = payload["messages"]
        assert isinstance(messages, list)
        assistant = next(item for item in messages if item.get("role") == "assistant")
        tool_result = next(item for item in messages if item.get("role") == "tool")
        assert assistant["reasoning"] == "Need the tool first."
        assert assistant["tool_calls"][0]["id"] == "call-1"
        assert tool_result["tool_call_id"] == "call-1"
        assert tool_result["content"] == "observed:scene"
        return httpx.Response(
            200,
            content=(
                'data: {"id":"turn-2","choices":[{"delta":{"reasoning":"The tool result is sufficient."}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"Finished after the tool."},'
                '"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
            ),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    provider = OpenAICompletionsProvider(
        lambda _ref: None,
        client=client,
        include_stream_usage=False,
        enforce_loopback=True,
    )
    profile = create_qwen3_vl_profile()
    tool = _EchoTool()
    def enable_reasoning(
        request: ModelRequest, _cancellation: CancellationToken
    ) -> ModelRequest:
        return ModelRequest(
            request.profile,
            request.messages,
            request.tools,
            request.temperature,
            request.max_output_tokens,
            "medium",
        )
    loop = AgentLoop(
        provider,
        profile,
        ToolRegistry((tool,)),
        AgentLoopConfig(before_provider_request=enable_reasoning),
    )
    transcript = await loop.run((UserMessage("use the tool"),), CancellationToken())
    await client.aclose()

    assert len(payloads) == 2
    assert tool.values == ["scene"]
    assert isinstance(transcript[1], AssistantMessage)
    assert [type(block) for block in transcript[1].content] == [ThinkingContent, ToolCall]
    assert isinstance(transcript[-1], AssistantMessage)
    assert [type(block) for block in transcript[-1].content] == [ThinkingContent, TextContent]
    assert transcript[-1].content[-1].text == "Finished after the tool."
