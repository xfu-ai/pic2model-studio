from __future__ import annotations

import pytest

from aipic_to_model.agent.core.events import CancellationToken
from aipic_to_model.agent.core.models import (
    AssistantMessage,
    ManagedAssetAttachment,
    ProviderEvent,
    ProviderEventType,
    SystemMessage,
    TextContent,
    UserMessage,
)
from aipic_to_model.agent.planning.models import ExecutionPlan
from aipic_to_model.agent.planning.prompts import PLANNING_SYSTEM_PROMPT
from aipic_to_model.agent.planning.service import PlanningService
from aipic_to_model.agent.providers.base import ModelProfile
from aipic_to_model.agent.providers.fake import FakeProvider, ScriptedResponse


def _response(text: str) -> ScriptedResponse:
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


@pytest.mark.asyncio
async def test_planning_service_uses_current_profile_without_tools_and_parses_plan() -> None:
    provider = FakeProvider(
        (
            _response(
                '{"goal":"Remove the background","deliverables":["transparent PNG"],'
                '"constraints":["preserve subject"],"acceptance_criteria":["has alpha"],'
                '"assumptions":[],"blocking_questions":[],"next_action":"execute",'
                '"steps":[{"id":"remove_background","label":"Remove background",'
                '"tool_name":"edit_image","input_source":"user attachment",'
                '"expected_output":"transparent PNG","verification_targets":["alpha"]}]}'
            ),
        )
    )
    profile = ModelProfile("deepseek", "model", "https://example.invalid", max_output_tokens=4096)
    service = PlanningService(lambda _profile: provider)

    plan = await service.prepare(
        profile,
        UserMessage("Remove this image background."),
        version=1,
        prior=None,
        request_transform=lambda request: request,
        cancellation=CancellationToken(),
    )

    assert provider.requests[0].profile.provider_id == profile.provider_id
    assert provider.requests[0].profile.model == profile.model
    assert provider.requests[0].tools == ()
    assert provider.requests[0].max_output_tokens == profile.max_output_tokens
    assert provider.requests[0].profile.timeout_seconds == profile.timeout_seconds
    assert plan.goal == "Remove the background"
    assert plan.current_step_id == "remove_background"
    assert plan.steps[0].tool_name == "edit_image"
    assert plan.acceptance_criteria == ("has alpha",)


@pytest.mark.parametrize(
    ("provider_id", "model"),
    (("deepseek", "deepseek-chat"), ("ollama", "qwen3-vl:8b")),
)
@pytest.mark.asyncio
async def test_planning_service_shares_2d_and_3d_few_shots_across_providers(
    provider_id: str, model: str
) -> None:
    provider = FakeProvider(
        (
            _response(
                '{"goal":"Inspect the request","deliverables":[],"constraints":[],'
                '"acceptance_criteria":[],"assumptions":[],"blocking_questions":[],'
                '"next_action":"respond","steps":[]}'
            ),
        )
    )
    service = PlanningService(lambda _profile: provider)

    await service.prepare(
        ModelProfile(provider_id, model, "https://example.invalid"),
        UserMessage("Plan this task."),
        version=1,
        prior=None,
        request_transform=lambda request: request,
        cancellation=CancellationToken(),
    )

    system_message = provider.requests[0].messages[0]
    assert isinstance(system_message, SystemMessage)
    assert system_message.content == PLANNING_SYSTEM_PROMPT
    assert "Few-shot A - component mother image" in system_message.content
    assert "Few-shot A2 - split a component mother image" in system_message.content
    assert "transform the complete mother image first" in system_message.content
    assert "Provider remove_background for gradients" in system_message.content
    assert "never\n   guess target_color or tolerance" in system_message.content
    assert "Few-shot D - one image to a textured 3D model" in system_message.content
    assert "default production workflow then prepares a managed" in system_message.content
    assert "Use single-image character generation only when" in system_message.content
    assert "Few-shot F - an existing managed 3D model" in system_message.content
    assert "Track every component independently" in system_message.content
    assert "does not complete the batch" in system_message.content
    assert "Never promote model-inferred details into hard user acceptance conditions" in (
        system_message.content
    )
    assert '"at least three tentacles"' in system_message.content
    assert "never be used to reject an\n   output" in system_message.content


@pytest.mark.asyncio
async def test_planning_service_repairs_transparent_split_without_background_step() -> None:
    provider = FakeProvider(
        (
            _response(
                '{"goal":"Create separate transparent components",'
                '"deliverables":["12 transparent PNG components"],"constraints":[],'
                '"acceptance_criteria":["alpha channel"],"assumptions":[],'
                '"blocking_questions":[],"next_action":"execute","steps":['
                '{"id":"split","label":"Split grid","operation":"split_grid_local",'
                '"tool_name":"split_image","input_source":"user attachment",'
                '"expected_output":"12 transparent components","verification_targets":[]}]}'
            ),
            _response(
                '{"goal":"Create separate transparent components",'
                '"deliverables":["12 transparent PNG components"],"constraints":[],'
                '"acceptance_criteria":["alpha channel"],"assumptions":[],'
                '"blocking_questions":[],"next_action":"execute","steps":['
                '{"id":"remove","label":"Remove background",'
                '"operation":"remove_background_local","tool_name":"edit_image",'
                '"input_source":"user attachment","expected_output":"transparent mother image",'
                '"verification_targets":["alpha"]},'
                '{"id":"split","label":"Split grid","operation":"split_grid_local",'
                '"tool_name":"split_image","input_source":"prior tool output",'
                '"expected_output":"12 transparent components","verification_targets":[]}]}'
            ),
        )
    )
    service = PlanningService(lambda _profile: provider)

    plan = await service.prepare(
        ModelProfile("ollama", "qwen3-vl:8b", "http://127.0.0.1:11434/v1"),
        UserMessage("把组件母图拆成12个独立透明组件。"),
        version=1,
        prior=None,
        request_transform=lambda request: request,
        cancellation=CancellationToken(),
    )

    assert len(provider.requests) == 2
    assert [step.operation for step in plan.steps] == [
        "remove_background_local",
        "split_grid_local",
    ]
    repair_context = provider.requests[1].messages[-2]
    assert isinstance(repair_context, SystemMessage)
    assert "no step creates an alpha channel" in repair_context.content


@pytest.mark.asyncio
async def test_text_only_character_3d_plan_generates_image_and_multiview_before_modeling() -> None:
    provider = FakeProvider(
        (
            _response(
                '{"goal":"Create a dark warrior 3D model","deliverables":["3D model"],'
                '"constraints":[],"acceptance_criteria":["dark warrior"],"assumptions":[],'
                '"blocking_questions":[],"next_action":"execute","steps":['
                '{"id":"model","label":"Generate model","operation":"generate_model3d",'
                '"tool_name":"model3d.generate_from_image","input_source":"current image",'
                '"expected_output":"3D model","verification_targets":[]}]} '
            ),
            _response(
                '{"goal":"Create a dark warrior 3D model","deliverables":["3D model"],'
                '"constraints":[],"acceptance_criteria":["dark warrior"],"assumptions":[],'
                '"blocking_questions":[],"next_action":"execute","steps":['
                '{"id":"image","label":"Generate source image",'
                '"operation":"generate_image_from_prompt",'
                '"tool_name":"image.generate_from_prompt","input_source":"user prompt",'
                '"expected_output":"dark warrior image","verification_targets":[]},'
                '{"id":"verify","label":"Verify source image","operation":"inspect_image",'
                '"tool_name":"image.understand_for_agent","input_source":"prior tool output",'
                '"expected_output":"grounded image facts","verification_targets":["subject"]},'
                '{"id":"model","label":"Generate model","operation":"generate_model3d",'
                '"tool_name":"model3d.generate_from_image","input_source":"verified image output",'
                '"expected_output":"3D model","verification_targets":[]}]} '
            ),
        )
    )
    service = PlanningService(lambda _profile: provider)

    plan = await service.prepare(
        ModelProfile("ollama", "qwen3-vl:8b", "http://127.0.0.1:11434/v1"),
        UserMessage("生成一个暗黑战士的3D模型"),
        version=1,
        prior=None,
        request_transform=lambda request: request,
        cancellation=CancellationToken(),
    )

    assert [step.operation for step in plan.steps] == [
        "generate_image_from_prompt",
        "inspect_image",
        "prepare_multiview",
        "confirm_multiview",
        "generate_model3d",
    ]
    assert plan.steps[-1].tool_name == "model3d.generate_from_multiview"
    assert "text-only 3D request" in provider.requests[1].messages[-2].content


@pytest.mark.asyncio
async def test_character_3d_plan_applies_multiview_default_without_new_visual_criteria() -> None:
    provider = FakeProvider(
        (
            _response(
                '{"goal":"Create a Cthulhu boss character model",'
                '"deliverables":["3D character"],"constraints":[],'
                '"acceptance_criteria":["Cthulhu style"],"assumptions":[],'
                '"blocking_questions":[],"next_action":"execute","steps":['
                '{"id":"image","label":"Generate source image",'
                '"operation":"generate_image_from_prompt",'
                '"tool_name":"image.generate_from_prompt","input_source":"user prompt",'
                '"expected_output":"source image","verification_targets":[]},'
                '{"id":"verify","label":"Verify image","operation":"inspect_image",'
                '"tool_name":"image.understand_for_agent","input_source":"prior output",'
                '"expected_output":"verified image","verification_targets":["Cthulhu style"]},'
                '{"id":"model","label":"Generate model","operation":"generate_model3d",'
                '"tool_name":"model3d.generate_from_image","input_source":"verified image",'
                '"expected_output":"3D character","verification_targets":["Cthulhu style"]}]}'
            ),
        )
    )
    service = PlanningService(lambda _profile: provider)

    plan = await service.prepare(
        ModelProfile("deepseek", "model", "https://example.invalid"),
        UserMessage("Create a Cthulhu boss character 3D model."),
        version=1,
        prior=None,
        request_transform=lambda request: request,
        cancellation=CancellationToken(),
    )

    assert len(provider.requests) == 1
    assert [step.operation for step in plan.steps] == [
        "generate_image_from_prompt",
        "inspect_image",
        "prepare_multiview",
        "confirm_multiview",
        "generate_model3d",
    ]
    assert plan.steps[-1].tool_name == "model3d.generate_from_multiview"
    assert plan.steps[2].verification_targets == ()
    assert plan.steps[3].tool_name == "multiview.request_region_confirmation"
    assert plan.steps[3].verification_targets == ()
    assert plan.acceptance_criteria == ("Cthulhu style",)


@pytest.mark.asyncio
async def test_explicit_single_image_character_request_opts_out_of_multiview_default() -> None:
    provider = FakeProvider(
        (
            _response(
                '{"goal":"Create a quick single-image character draft",'
                '"deliverables":["3D character"],"constraints":["single image"],'
                '"acceptance_criteria":[],"assumptions":[],"blocking_questions":[],'
                '"next_action":"execute","steps":['
                '{"id":"model","label":"Generate model","operation":"generate_model3d",'
                '"tool_name":"model3d.generate_from_image","input_source":"user attachment",'
                '"expected_output":"3D character","verification_targets":[]}]}'
            ),
        )
    )
    service = PlanningService(lambda _profile: provider)

    plan = await service.prepare(
        ModelProfile("deepseek", "model", "https://example.invalid"),
        UserMessage(
            "Use this single image only for a quick character draft.",
            attachments=(ManagedAssetAttachment("image-1", "source.png", "image/png"),),
        ),
        version=1,
        prior=None,
        request_transform=lambda request: request,
        cancellation=CancellationToken(),
    )

    assert [step.operation for step in plan.steps] == ["generate_model3d"]
    assert plan.steps[0].tool_name == "model3d.generate_from_image"


@pytest.mark.asyncio
async def test_attached_image_weapon_removal_is_planned_before_modeling() -> None:
    provider = FakeProvider(
        (
            _response(
                '{"goal":"Create a weapon-free Guan Yu model","deliverables":["3D model"],'
                '"constraints":["remove weapon"],"acceptance_criteria":[],"assumptions":[],'
                '"blocking_questions":[],"next_action":"execute","steps":['
                '{"id":"model","label":"Generate model","operation":"generate_model3d",'
                '"tool_name":"model3d.generate_from_image","input_source":"edited source",'
                '"expected_output":"3D model","verification_targets":[]}]} '
            ),
            _response(
                '{"goal":"Create a weapon-free Guan Yu model","deliverables":["3D model"],'
                '"constraints":["remove weapon"],"acceptance_criteria":[],"assumptions":[],'
                '"blocking_questions":[],"next_action":"execute","steps":['
                '{"id":"edit","label":"Remove weapon from image",'
                '"operation":"transform_from_reference",'
                '"tool_name":"image.transform_from_reference","input_source":"user attachment",'
                '"expected_output":"weapon-free image","verification_targets":["no weapon"]},'
                '{"id":"model","label":"Generate model","operation":"generate_model3d",'
                '"tool_name":"model3d.generate_from_image","input_source":"prior tool output",'
                '"expected_output":"3D model","verification_targets":[]}]} '
            ),
        )
    )
    service = PlanningService(lambda _profile: provider)
    message = UserMessage(
        "把图里的武器去掉，再生成3D模型",
        attachments=(ManagedAssetAttachment("image-1", "source.png", "image/png"),),
    )

    plan = await service.prepare(
        ModelProfile("ollama", "qwen3-vl:8b", "http://127.0.0.1:11434/v1"),
        message,
        version=1,
        prior=None,
        request_transform=lambda request: request,
        cancellation=CancellationToken(),
    )

    assert [step.operation for step in plan.steps] == [
        "transform_from_reference",
        "generate_model3d",
    ]


@pytest.mark.parametrize(
    "prior_state",
    ("waiting_user", "executing", "completed", "completed_with_warnings"),
)
@pytest.mark.asyncio
async def test_planning_service_always_inherits_the_latest_plan_context(
    prior_state: str,
) -> None:
    provider = FakeProvider(
        (
            _response(
                '{"goal":"Continue the cyberpunk component workflow",'
                '"deliverables":["transparent components"],"constraints":[],'
                '"acceptance_criteria":[],"assumptions":[],"blocking_questions":[],'
                '"next_action":"execute","steps":[]}'
            ),
        )
    )
    service = PlanningService(lambda _profile: provider)
    prior = ExecutionPlan(
        version=2,
        goal="Change the component mother image to cyberpunk style",
        deliverables=("separate transparent components",),
        constraints=("preserve component count",),
        acceptance_criteria=("consistent cyberpunk style",),
        assumptions=(),
        blocking_questions=(),
        steps=(),
        current_step_id=None,
        state=prior_state,
        next_action="execute",
    )

    await service.prepare(
        ModelProfile("ollama", "qwen3-vl:8b", "http://127.0.0.1:11434/v1"),
        UserMessage("Use this corrected image and change the operation order."),
        version=3,
        prior=prior,
        request_transform=lambda request: request,
        cancellation=CancellationToken(),
    )

    prior_context = provider.requests[0].messages[1]
    assert isinstance(prior_context, SystemMessage)
    assert "Change the component mother image to cyberpunk style" in prior_context.content
    assert "separate transparent components" in prior_context.content
    assert "Replacing an attachment or changing step order" in prior_context.content


@pytest.mark.asyncio
async def test_planning_service_marks_unavailable_without_faking_a_plan() -> None:
    provider = FakeProvider((_response("not a plan"),))
    profile = ModelProfile("deepseek", "model", "https://example.invalid")
    service = PlanningService(lambda _profile: provider)

    plan = await service.prepare(
        profile,
        UserMessage("Resize this image."),
        version=3,
        prior=None,
        request_transform=lambda request: request,
        cancellation=CancellationToken(),
    )

    assert plan.fallback is True
    assert plan.version == 3
    assert plan.next_action == "execute"
    assert plan.steps == ()
    assert plan.planner_diagnostic is not None
    assert plan.planner_diagnostic.code == "non_json_output"
    assert plan.planner_diagnostic.output_characters == len("not a plan")
    assert plan.planner_diagnostic.json_object_detected is False


@pytest.mark.asyncio
async def test_planning_service_accepts_json_after_qwen_style_prose_and_normalizes_missing_goal() -> None:
    provider = FakeProvider(
        (
            _response(
                'I will now return the requested object.\n```json\n'
                '{"steps":[{"id":"remove","label":"Remove the background",'
                '"operation":"remove_background_local","tool_name":"edit_image"}],'
                '"blocking_questions":[],"next_action":"execute"}\n```'
            ),
        )
    )
    service = PlanningService(lambda _profile: provider)

    plan = await service.prepare(
        ModelProfile("ollama", "qwen3-vl:8b", "http://127.0.0.1:11434/v1"),
        UserMessage("Remove this image background."),
        version=1,
        prior=None,
        request_transform=lambda request: request,
        cancellation=CancellationToken(),
    )

    assert plan.fallback is False
    assert plan.goal == "Remove this image background."
    assert plan.steps[0].operation == "remove_background_local"


def test_plan_marks_matching_step_for_review_without_changing_tool_success() -> None:
    from aipic_to_model.agent.planning.models import ExecutionPlan, PlanStep

    plan = ExecutionPlan(
        version=1,
        goal="Remove background",
        deliverables=("transparent PNG",),
        constraints=(),
        acceptance_criteria=("has alpha",),
        assumptions=(),
        blocking_questions=(),
        steps=(
            PlanStep(
                "remove_background",
                "Remove background",
                "edit_image",
                "user attachment",
                "transparent PNG",
                ("has alpha",),
            ),
        ),
        current_step_id="remove_background",
        state="executing",
        next_action="execute",
    )

    updated = plan.with_tool_result(
        "edit_image",
        failed=False,
        verification={"disposition": "review_required", "checks": []},
    )

    assert updated.steps[0].state == "review_required"
    assert updated.state == "completed_with_warnings"


def test_final_response_closes_response_level_verification_and_keeps_warnings() -> None:
    from aipic_to_model.agent.planning.models import ExecutionPlan, PlanStep

    plan = ExecutionPlan(
        version=1,
        goal="Split and normalize components",
        deliverables=("normalized components",),
        constraints=(),
        acceptance_criteria=("consistent output",),
        assumptions=(),
        blocking_questions=(),
        steps=(
            PlanStep(
                "split",
                "Split components",
                "image.split_alpha_components",
                "transparent atlas",
                "components",
                ("one subject per output",),
                state="review_required",
                warning="Split outputs have different dimensions.",
                operation="split_alpha_components_local",
            ),
            PlanStep(
                "normalize",
                "Normalize components",
                "image.normalize",
                "split outputs",
                "normalized components",
                ("same canvas",),
                state="succeeded",
                operation="normalize_components_local",
            ),
            PlanStep(
                "verify",
                "Verify final output",
                None,
                "normalized components",
                "verification summary",
                ("transparent", "consistent canvas"),
                operation="verify_output",
            ),
        ),
        current_step_id="verify",
        state="completed_with_warnings",
        next_action="execute",
    )

    completed = plan.with_final_response()

    assert completed.steps[-1].state == "succeeded"
    assert completed.current_step_id is None
    assert completed.state == "completed_with_warnings"
    assert completed.next_action == "respond"


def test_plan_pauses_after_a_failed_step_instead_of_advancing_the_current_step() -> None:
    from aipic_to_model.agent.planning.models import ExecutionPlan, PlanStep

    plan = ExecutionPlan(
        version=1,
        goal="Make components transparent and split them",
        deliverables=(),
        constraints=(),
        acceptance_criteria=(),
        assumptions=(),
        blocking_questions=(),
        steps=(
            PlanStep(
                "remove_background",
                "Remove background",
                "edit_image",
                "user attachment",
                "transparent PNG",
                ("has alpha",),
                operation="remove_background_local",
            ),
            PlanStep(
                "split_components",
                "Split alpha components",
                "split_image",
                "prior tool output",
                "separate PNGs",
                ("one component per output",),
                operation="split_alpha_components_local",
            ),
        ),
        current_step_id="remove_background",
        state="executing",
        next_action="execute",
    )

    updated = plan.with_tool_result("edit_image", failed=True, verification=None)

    assert updated.steps[0].state == "failed"
    assert updated.steps[1].state == "pending"
    assert updated.current_step_id is None
    assert updated.state == "completed_with_warnings"


def test_interrupted_step_waits_for_user_instead_of_completing_plan() -> None:
    from aipic_to_model.agent.planning.models import ExecutionPlan, PlanStep

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
                "generate_model",
                "Generate model",
                "model3d.generate_from_multiview",
                "confirmed views",
                "3D model",
                (),
                operation="generate_model3d",
            ),
        ),
        current_step_id="generate_model",
        state="executing",
        next_action="execute",
    )

    updated = plan.with_tool_result(
        "model3d.generate_from_multiview",
        failed=True,
        verification=None,
        tool_call_id="call-interrupted",
        attempt_warning="Submission state is unknown.",
        waiting_for_user=True,
    )

    assert updated.steps[0].state == "failed"
    assert updated.state == "waiting_user"
    assert updated.current_step_id is None
    assert updated.next_action == "ask_user"
    assert updated.with_final_response() == updated


def test_plan_progress_updates_only_the_ai_bound_step_id() -> None:
    from aipic_to_model.agent.planning.models import ExecutionPlan, PlanStep

    plan = ExecutionPlan(
        version=1,
        goal="Inspect and verify an image",
        deliverables=(),
        constraints=(),
        acceptance_criteria=(),
        assumptions=(),
        blocking_questions=(),
        steps=(
            PlanStep(
                "inspect_source",
                "Inspect source",
                None,
                "user attachment",
                "source facts",
                (),
                operation="inspect_image",
            ),
            PlanStep(
                "verify_output",
                "Verify output",
                None,
                "prior output",
                "verification",
                (),
                operation="verify_output",
            ),
        ),
        current_step_id="inspect_source",
        state="executing",
        next_action="execute",
    )

    updated = plan.with_step_result("inspect_source", failed=False, verification=None)

    assert updated.steps[0].state == "succeeded"
    assert updated.steps[1].state == "pending"
    assert updated.current_step_id == "verify_output"


def test_legacy_tool_projection_does_not_scan_a_later_matching_step() -> None:
    from aipic_to_model.agent.planning.models import ExecutionPlan, PlanStep

    plan = ExecutionPlan(
        version=1,
        goal="Edit then verify",
        deliverables=(),
        constraints=(),
        acceptance_criteria=(),
        assumptions=(),
        blocking_questions=(),
        steps=(
            PlanStep("edit", "Edit", "edit_image", "source", "image", ()),
            PlanStep(
                "verify",
                "Verify",
                None,
                "image",
                "verified image",
                (),
                operation="verify_output",
            ),
        ),
        current_step_id="edit",
        state="executing",
        next_action="execute",
    )

    updated = plan.with_tool_result("inspect_workspace", failed=False, verification=None)

    assert updated == plan


def test_successful_retry_recovers_failed_step_and_preserves_attempt_warning() -> None:
    from aipic_to_model.agent.planning.models import ExecutionPlan, PlanStep

    plan = ExecutionPlan(
        version=1,
        goal="Generate one model",
        deliverables=("GLB model",),
        constraints=(),
        acceptance_criteria=("model asset exists",),
        assumptions=(),
        blocking_questions=(),
        steps=(
            PlanStep(
                "generate_model",
                "Generate model",
                "model3d.generate_from_image",
                "generated image",
                "GLB model",
                ("model asset exists",),
                operation="generate_model3d",
            ),
        ),
        current_step_id="generate_model",
        state="executing",
        next_action="execute",
    )

    failed = plan.with_tool_result(
        "model3d.generate_from_image",
        failed=True,
        verification=None,
        tool_call_id="call-first",
        attempt_warning="0 is less than the minimum of 1",
    )
    recovered = failed.with_tool_result(
        "model3d.generate_from_image",
        failed=False,
        verification=None,
        tool_call_id="call-second",
    )
    completed = recovered.with_final_response()

    step = completed.steps[0]
    assert step.state == "succeeded"
    assert [(item.tool_call_id, item.state) for item in step.attempts] == [
        ("call-first", "failed"),
        ("call-second", "succeeded"),
    ]
    assert step.attempts[0].warning == "0 is less than the minimum of 1"
    assert step.warning == (
        "Earlier attempt: 0 is less than the minimum of 1 Latest attempt succeeded."
    )
    assert completed.current_step_id is None
    assert completed.state == "completed_with_warnings"
    assert completed.next_action == "respond"


def test_attempt_history_round_trips_through_plan_serialization() -> None:
    from aipic_to_model.agent.planning.models import ExecutionPlan, PlanAttempt, PlanStep

    original = ExecutionPlan(
        version=3,
        goal="Inspect project",
        deliverables=(),
        constraints=(),
        acceptance_criteria=(),
        assumptions=(),
        blocking_questions=(),
        steps=(
            PlanStep(
                "inspect",
                "Inspect project",
                "project.get_state",
                "current project",
                "project summary",
                (),
                state="succeeded",
                attempts=(
                    PlanAttempt(
                        "project.get_state",
                        "failed",
                        "call-first",
                        "Invalid arguments.",
                    ),
                    PlanAttempt("project.get_state", "succeeded", "call-second"),
                ),
            ),
        ),
        current_step_id=None,
        state="completed_with_warnings",
        next_action="respond",
    )

    restored = ExecutionPlan.from_dict(original.to_dict())

    assert restored == original


def test_nonterminal_tool_keeps_plan_step_running_without_attempt() -> None:
    from aipic_to_model.agent.planning.models import ExecutionPlan, PlanStep

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
                operation="generate_model3d",
            ),
            PlanStep(
                "inspect",
                "Inspect model",
                "model3d.inspect",
                "GLB",
                "inspection",
                (),
                operation="inspect_model3d",
            ),
        ),
        current_step_id="generate",
        state="executing",
        next_action="execute",
    )

    running = plan.with_step_running("generate")

    assert running.steps[0].state == "running"
    assert running.steps[0].attempts == ()
    assert running.steps[1].state == "pending"
    assert running.current_step_id == "generate"
    assert running.state == "executing"


def test_explicit_plan_step_id_cannot_bind_an_unrelated_support_tool() -> None:
    from aipic_to_model.agent.planning.models import ExecutionPlan, PlanStep

    plan = ExecutionPlan(
        version=1,
        goal="Prepare confirmed multiview crops",
        deliverables=("confirmed crops",),
        constraints=(),
        acceptance_criteria=(),
        assumptions=(),
        blocking_questions=(),
        steps=(
            PlanStep(
                "confirm_views",
                "Confirm views",
                "multiview.request_region_confirmation",
                "generated sheet",
                "confirmed crops",
                (),
                operation="confirm_multiview",
            ),
        ),
        current_step_id="confirm_views",
        state="executing",
        next_action="execute",
    )

    assert plan.with_step_running(
        "confirm_views", tool_name="project.get_state"
    ) == plan
    assert plan.with_step_result(
        "confirm_views",
        failed=False,
        verification=None,
        tool_name="project.get_state",
        tool_call_id="support-query",
    ) == plan


def test_same_terminal_tool_call_is_not_recorded_twice() -> None:
    from aipic_to_model.agent.planning.models import ExecutionPlan, PlanStep

    plan = ExecutionPlan(
        version=1,
        goal="Generate a model",
        deliverables=(),
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
                operation="generate_model3d",
            ),
        ),
        current_step_id="generate",
        state="executing",
        next_action="execute",
    )

    completed = plan.with_tool_result(
        "model3d.generate_from_image",
        failed=False,
        verification=None,
        tool_call_id="call-one",
    )
    replayed = completed.with_tool_result(
        "model3d.generate_from_image",
        failed=False,
        verification=None,
        tool_call_id="call-one",
    )

    assert replayed == completed
    assert len(replayed.steps[0].attempts) == 1
