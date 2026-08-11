from __future__ import annotations

import asyncio
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
    SystemMessage,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResult,
    ToolResultMessage,
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
from aipic_to_model.domain.tools import ToolResultV1


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


def _assistant_response(message: AssistantMessage) -> ScriptedResponse:
    return ScriptedResponse(
        (
            ProviderEvent(ProviderEventType.MESSAGE_START),
            ProviderEvent(ProviderEventType.MESSAGE_END, message=message),
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
    # Every natural-language turn has a no-tools planning request followed by
    # the regular execution request, both using the frozen DeepSeek profile.
    assert [profile.provider_id for profile in captured_profiles] == [
        "deepseek",
        "deepseek",
        "deepseek",
        "deepseek",
    ]
    assert [profile.model for profile in captured_profiles] == [
        "deepseek-v4-flash",
        "deepseek-v4-flash",
        "deepseek-v4-flash",
        "deepseek-v4-flash",
    ]
    assert session.profile["credential_ref"] == "agent/deepseek/default"
    assert session.profile["base_url"] == "https://api.deepseek.com"
    assert all(
        provider.requests[0].reasoning_effort is None for provider in captured_providers
    )


@pytest.mark.agent
@pytest.mark.asyncio
async def test_runtime_persists_a_no_tools_plan_and_injects_it_into_the_executor(
    tmp_path: Path,
) -> None:
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Plan runtime contract")
    dependencies.roots[project.id] = root
    providers: list[FakeProvider] = []

    def provider_factory(_profile: ModelProfile) -> FakeProvider:
        response = (
            _text_response("The background-removal request is ready to execute.")
            if not providers
            else _text_response(
                '{"goal":"Remove the image background","deliverables":["transparent PNG"],'
                '"constraints":["preserve subject"],"acceptance_criteria":["alpha channel"],'
                '"assumptions":[],"blocking_questions":[],"next_action":"execute",'
                '"steps":[{"id":"remove_background","label":"Remove background",'
                '"operation":"remove_background_local","tool_name":"image.remove_background_local",'
                '"input_source":"user attachment","expected_output":"transparent PNG",'
                '"verification_targets":["alpha channel"]},{"id":"split_grid",'
                '"label":"Split grid","operation":"split_grid_local",'
                '"tool_name":"image.split_grid","input_source":"prior tool output",'
                '"expected_output":"grid cells","verification_targets":["cell count"]}]}'
            )
        )
        provider = FakeProvider((response,))
        providers.append(provider)
        return provider

    runtime = AgentRuntime(
        dependencies.registry,
        lambda _project_id: root,
        provider_factory=provider_factory,
        runtime_context_provider=lambda _project_id: {
            "capabilities": {"model3d_generation": {"available": True}}
        },
        planner_context_provider=lambda _project_id: {
            "planner_capabilities": {"local_2d": ["remove_background_local"]}
        },
    )
    conversation_id = str(runtime.create(project.id)["id"])

    await runtime.send(
        project.id,
        conversation_id,
        "Remove this image background and split it as a grid.",
        wait=True,
    )

    assert len(providers) == 2
    requests = [provider.requests[0] for provider in providers]
    planner_request = next(request for request in requests if request.tools == ())
    executor_request = next(request for request in requests if request.tools != ())
    # The planner gets a shorter request timeout so its fail-open preflight cannot
    # stall execution, but it must retain the conversation's actual model route.
    assert planner_request.profile.provider_id == executor_request.profile.provider_id
    assert planner_request.profile.model == executor_request.profile.model
    assert planner_request.profile.base_url == executor_request.profile.base_url
    assert planner_request.profile.credential_ref == executor_request.profile.credential_ref
    assert planner_request.tools == ()
    assert any(
        isinstance(message, SystemMessage) and "planning stage" in message.content
        for message in planner_request.messages
    )
    planner_context = next(
        message.content
        for message in planner_request.messages
        if isinstance(message, SystemMessage) and message.content.startswith("Runtime context")
    )
    executor_context = next(
        message.content
        for message in executor_request.messages
        if isinstance(message, SystemMessage) and message.content.startswith("Runtime context")
    )
    assert "model-3d" not in planner_context
    assert "remove_background_local" in planner_context
    assert "current_model_ref" not in executor_context
    assert "assets.recent" not in executor_context
    assert any(
        isinstance(message, SystemMessage) and "Execution plan snapshot" in message.content
        for message in executor_request.messages
    )
    executor_tool_names = tuple(
        tool["function"]["name"] for tool in executor_request.tools
    )
    assert executor_tool_names[:10] == (
        "read",
        "write",
        "edit",
        "bash",
        "toolbox.status",
        "toolbox.load",
        "project.get_state",
        "image.understand_for_agent",
        "image.remove_background_local",
        "model3d.generate_from_image",
    )
    assert executor_tool_names[10:] == ("image.split_grid",)

    events = runtime.events(project.id, conversation_id, limit=100)["items"]
    plan_events = [item for item in events if item["event_type"] == "execution.plan.updated"]
    assert len(plan_events) == 1
    payload = plan_events[0]["payload"]
    assert payload["plan"]["goal"] == "Remove the image background"
    assert payload["plan"]["steps"][0]["tool_name"] == "image.remove_background_local"
    assert LinearSessionRepository(root / "agent.sqlite3").open(conversation_id).active_tools[-1] == "image.split_grid"


@pytest.mark.agent
@pytest.mark.asyncio
async def test_runtime_persists_safe_planner_fallback_diagnostic(tmp_path: Path) -> None:
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Planner diagnostic persistence")
    dependencies.roots[project.id] = root
    providers: list[FakeProvider] = []

    def provider_factory(_profile: ModelProfile) -> FakeProvider:
        response = _text_response("Execution can continue.") if not providers else _text_response(
            "not a plan"
        )
        provider = FakeProvider((response,))
        providers.append(provider)
        return provider

    runtime = AgentRuntime(
        dependencies.registry,
        lambda _project_id: root,
        provider_factory=provider_factory,
    )
    conversation_id = str(runtime.create(project.id)["id"])

    await runtime.send(project.id, conversation_id, "Resize this image.", wait=True)

    events = runtime.events(project.id, conversation_id, limit=100)["items"]
    payload = next(
        item["payload"]
        for item in events
        if item["event_type"] == "execution.plan.updated"
    )
    diagnostic = payload["plan"]["planner_diagnostic"]
    assert payload["plan"]["fallback"] is True
    assert diagnostic is not None
    assert set(diagnostic) == {
        "code",
        "duration_ms",
        "output_characters",
        "json_object_detected",
    }
    assert diagnostic["code"] == "non_json_output"
    assert isinstance(diagnostic["duration_ms"], int)
    assert diagnostic["duration_ms"] >= 0
    assert diagnostic["output_characters"] == len("not a plan")
    assert diagnostic["json_object_detected"] is False


@pytest.mark.agent
@pytest.mark.asyncio
async def test_runtime_persists_failed_and_successful_calls_as_step_attempts(
    tmp_path: Path,
) -> None:
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Plan attempt persistence")
    dependencies.roots[project.id] = root
    providers: list[FakeProvider] = []

    def provider_factory(_profile: ModelProfile) -> FakeProvider:
        responses = (
            (
                _assistant_response(
                    AssistantMessage(
                        (
                            ToolCall("call-first", "project.get_state", {"unexpected": True}),
                            ToolCall("call-second", "project.get_state", {}),
                        ),
                        stop_reason="tool_use",
                    )
                ),
                _text_response("Project state recovered."),
            )
            if not providers
            else (
                _text_response(
                    '{"goal":"Inspect the current project","deliverables":["project summary"],'
                    '"constraints":[],"acceptance_criteria":[],"assumptions":[],'
                    '"blocking_questions":[],"next_action":"execute","steps":['
                    '{"id":"inspect","label":"Inspect project",'
                    '"operation":null,"tool_name":"project.get_state",'
                    '"input_source":"current project","expected_output":"project summary",'
                    '"verification_targets":[]}]}'
                ),
            )
        )
        provider = FakeProvider(responses)
        providers.append(provider)
        return provider

    runtime = AgentRuntime(
        dependencies.registry,
        lambda _project_id: root,
        provider_factory=provider_factory,
    )
    conversation_id = str(runtime.create(project.id)["id"])

    await runtime.send(
        project.id,
        conversation_id,
        "Inspect the current project.",
        wait=True,
    )

    events = runtime.events(project.id, conversation_id, limit=100)["items"]
    plan_events = [item for item in events if item["event_type"] == "execution.plan.updated"]
    final_plan = plan_events[-1]["payload"]["plan"]
    step = final_plan["steps"][0]
    assert step["state"] == "succeeded"
    assert [(item["tool_call_id"], item["state"]) for item in step["attempts"]] == [
        ("call-first", "failed"),
        ("call-second", "succeeded"),
    ]
    assert step["attempts"][0]["warning"]
    assert "Latest attempt succeeded" in step["warning"]
    assert final_plan["state"] == "completed_with_warnings"
    assert final_plan["current_step_id"] is None
    assert final_plan["next_action"] == "respond"


@pytest.mark.agent
@pytest.mark.asyncio
async def test_approval_suspension_keeps_plan_step_running_without_an_attempt(
    tmp_path: Path,
) -> None:
    class AwaitingRegistry:
        def __init__(self) -> None:
            self.manifests: dict[tuple[str, str], object] = {}

        def execute(self, *args: object) -> ToolResultV1:
            return ToolResultV1(
                True,
                "awaiting_ui_action",
                "internal-approval-call",
                [],
                "Approval required.",
                [],
                expected_action={"type": "approval_required"},
                ui_action={
                    "action_id": "approval-one",
                    "type": "approval_required",
                    "workspace_mode": "working",
                },
            )

    root = tmp_path / "project"
    root.mkdir()
    providers: list[FakeProvider] = []

    def provider_factory(_profile: ModelProfile) -> FakeProvider:
        responses = (
            (
                _assistant_response(
                    AssistantMessage(
                        (ToolCall("call-approval", "project.get_state", {}),),
                        stop_reason="tool_use",
                    )
                ),
            )
            if not providers
            else (
                _text_response(
                    '{"goal":"Inspect the project","deliverables":["project summary"],'
                    '"constraints":[],"acceptance_criteria":[],"assumptions":[],'
                    '"blocking_questions":[],"next_action":"execute","steps":['
                    '{"id":"inspect","label":"Inspect project","operation":null,'
                    '"tool_name":"project.get_state","input_source":"current project",'
                    '"expected_output":"project summary","verification_targets":[]}]}'
                ),
            )
        )
        provider = FakeProvider(responses)
        providers.append(provider)
        return provider

    runtime = AgentRuntime(
        AwaitingRegistry(),  # type: ignore[arg-type]
        lambda _project_id: root,
        provider_factory=provider_factory,
    )
    conversation_id = str(runtime.create("project-one")["id"])

    await runtime.send(
        "project-one",
        conversation_id,
        "Inspect the current project.",
        wait=True,
    )

    events = runtime.events("project-one", conversation_id, limit=100)["items"]
    plan_events = [item for item in events if item["event_type"] == "execution.plan.updated"]
    final_plan = plan_events[-1]["payload"]["plan"]
    step = final_plan["steps"][0]
    assert step["state"] == "running"
    assert step["attempts"] == []
    assert final_plan["current_step_id"] == "inspect"
    assert final_plan["state"] == "executing"
    assert events[-1]["event_type"] == "conversation.suspended"


@pytest.mark.agent
@pytest.mark.asyncio
async def test_refresh_recovers_a_terminal_job_after_legacy_wait_timeout(
    tmp_path: Path,
) -> None:
    from aipic_to_model.agent.planning.models import ExecutionPlan, PlanStep

    class PassiveRegistry:
        def __init__(self) -> None:
            self.manifests: dict[tuple[str, str], object] = {}

    class TerminalBroker:
        async def wait_for_terminal(
            self,
            _database: Path,
            _job_id: str,
            *,
            timeout_seconds: float,
        ) -> object:
            assert timeout_seconds == 180.0
            return type(
                "TerminalJob",
                (),
                {
                    "status": "succeeded",
                    "result_asset_ids": ["model-asset"],
                    "job_type": "model3d.generate",
                    "provider": "tripo3d/default",
                },
            )()

    root = tmp_path / "project"
    root.mkdir()
    provider = FakeProvider((_text_response("Recovered the completed model."),))
    runtime = AgentRuntime(
        PassiveRegistry(),  # type: ignore[arg-type]
        lambda _project_id: root,
        provider_factory=lambda _profile: provider,
        job_completion_broker=TerminalBroker(),
    )
    conversation_id = str(runtime.create("project-one")["id"])
    repository = LinearSessionRepository(root / "agent.sqlite3")
    repository.append_message(
        conversation_id,
        AssistantMessage(
            (
                ToolCall(
                    "call-model",
                    "model3d.generate_from_image",
                    {"image_asset_ref": "source", "parameters": {}},
                ),
            ),
            stop_reason="tool_use",
        ),
    )
    repository.register_job_wait(
        conversation_id,
        project_id="project-one",
        run_id=conversation_id,
        tool_call_id="call-model",
        tool_name="model3d.generate_from_image",
    )
    assert repository.bind_job_wait(conversation_id, "call-model", "job-model")
    assert repository.complete_job_wait(
        conversation_id,
        "call-model",
        "waiting_external",
    )
    repository.append_message(
        conversation_id,
        ToolResultMessage(
            "call-model",
            "model3d.generate_from_image",
            ToolResult(
                (TextContent("The Job is still processing."),),
                details={"status": "waiting_external"},
            ),
        ),
    )
    plan = ExecutionPlan(
        version=1,
        goal="Generate a model",
        deliverables=("GLB",),
        constraints=(),
        acceptance_criteria=(),
        assumptions=(),
        blocking_questions=(),
        steps=(
            PlanStep(
                "generate",
                "Generate model",
                "model3d.generate_from_image",
                "source image",
                "GLB",
                (),
                state="running",
                operation="generate_model3d",
            ),
        ),
        current_step_id="generate",
        state="executing",
        next_action="execute",
    )
    repository.append_api_event(
        conversation_id,
        "execution.plan.updated",
        {"conversation_id": conversation_id, "plan": plan.to_dict()},
    )

    await runtime.resume_terminal_waits("project-one", conversation_id)
    for _attempt in range(100):
        await asyncio.sleep(0.01)
        event_types = [
            item["event_type"]
            for item in runtime.events("project-one", conversation_id, limit=100)["items"]
        ]
        if "conversation.completed" in event_types:
            break
    else:
        pytest.fail("Recovered terminal Job did not continue the Agent conversation.")

    messages = repository.open(conversation_id).messages
    results = [message for message in messages if isinstance(message, ToolResultMessage)]
    assert len(results) == 1
    assert results[0].result.details["status"] == "succeeded"
    assert results[0].result.details["output_asset_refs"] == ["model-asset"]
    events = runtime.events("project-one", conversation_id, limit=100)["items"]
    final_plan = [
        item["payload"]["plan"]
        for item in events
        if item["event_type"] == "execution.plan.updated"
    ][-1]
    assert final_plan["steps"][0]["state"] == "succeeded"
    assert final_plan["state"] == "completed"
    assert final_plan["next_action"] == "respond"


@pytest.mark.agent
@pytest.mark.asyncio
async def test_unknown_submission_recovery_waits_for_user_and_preloads_confirmation_tool(
    tmp_path: Path,
) -> None:
    from aipic_to_model.agent.planning.models import ExecutionPlan, PlanStep

    class PassiveRegistry:
        def __init__(self) -> None:
            self.manifests: dict[tuple[str, str], object] = {}

    class InterruptedBroker:
        async def wait_for_terminal(
            self,
            _database: Path,
            _job_id: str,
            *,
            timeout_seconds: float,
        ) -> object:
            assert timeout_seconds == 180.0
            return type(
                "InterruptedJob",
                (),
                {
                    "id": "job-model",
                    "status": "interrupted",
                    "result_asset_ids": [],
                    "job_type": "model3d.generate",
                    "provider": "tripo3d/default",
                    "error": {
                        "code": "JOB_UNKNOWN_SUBMISSION",
                        "safe_to_retry": False,
                    },
                },
            )()

    root = tmp_path / "project"
    root.mkdir()
    provider = FakeProvider((_text_response("Please confirm a new paid submission."),))
    runtime = AgentRuntime(
        PassiveRegistry(),  # type: ignore[arg-type]
        lambda _project_id: root,
        provider_factory=lambda _profile: provider,
        job_completion_broker=InterruptedBroker(),
    )
    conversation_id = str(runtime.create("project-one")["id"])
    repository = LinearSessionRepository(root / "agent.sqlite3")
    repository.append_message(
        conversation_id,
        AssistantMessage(
            (
                ToolCall(
                    "call-model",
                    "model3d.generate_from_multiview",
                    {"multiview_ref": "set", "view_asset_refs": {}, "parameters": {}},
                ),
            ),
            stop_reason="tool_use",
        ),
    )
    repository.register_job_wait(
        conversation_id,
        project_id="project-one",
        run_id=conversation_id,
        tool_call_id="call-model",
        tool_name="model3d.generate_from_multiview",
    )
    assert repository.bind_job_wait(conversation_id, "call-model", "job-model")
    assert repository.complete_job_wait(conversation_id, "call-model", "waiting_external")
    plan = ExecutionPlan(
        version=1,
        goal="Generate a character model",
        deliverables=("3D model",),
        constraints=(),
        acceptance_criteria=(),
        assumptions=(),
        blocking_questions=(),
        steps=(
            PlanStep(
                "generate",
                "Generate model",
                "model3d.generate_from_multiview",
                "confirmed views",
                "3D model",
                (),
                state="running",
                operation="generate_model3d",
            ),
        ),
        current_step_id="generate",
        state="executing",
        next_action="execute",
    )
    repository.append_api_event(
        conversation_id,
        "execution.plan.updated",
        {"conversation_id": conversation_id, "plan": plan.to_dict()},
    )

    await runtime.resume_terminal_waits("project-one", conversation_id)
    for _attempt in range(100):
        await asyncio.sleep(0.01)
        events = runtime.events("project-one", conversation_id, limit=100)["items"]
        if any(item["event_type"] == "conversation.completed" for item in events):
            break
    else:
        pytest.fail("Interrupted Job did not continue to a recovery response.")

    final_plan = [
        item["payload"]["plan"]
        for item in events
        if item["event_type"] == "execution.plan.updated"
    ][-1]
    assert final_plan["steps"][0]["state"] == "failed"
    assert final_plan["state"] == "waiting_user"
    assert final_plan["next_action"] == "ask_user"
    assert "job.confirm_new_submission" in repository.open(conversation_id).active_tools
    assert any(
        tool["function"]["name"] == "job.confirm_new_submission"
        for tool in provider.requests[0].tools
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
            {
                "type": "function",
                "function": {
                    "name": "image.split_grid",
                    "description": "newly activated grid splitter",
                    "parameters": {
                        "type": "object",
                        "properties": {"columns": {"type": "integer"}},
                    },
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
    assert payload["tools"][1]["function"]["name"] == "image_split_grid"
    assert payload["tools"][1]["function"]["parameters"]["properties"] == {
        "columns": {"type": "integer"}
    }
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
