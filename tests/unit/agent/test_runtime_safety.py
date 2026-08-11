from __future__ import annotations

from aipic_to_model.agent.core.agent_loop import BeforeToolCallContext
from aipic_to_model.agent.core.models import (
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResult,
    ToolResultMessage,
    UserMessage,
)
from aipic_to_model.agent.integrations.runtime import (
    _ground_model3d_final_response,
    _planned_tool_dependency_guard,
)
from aipic_to_model.agent.planning.models import ExecutionPlan, PlanStep


def _model_plan(image_state: str = "pending") -> ExecutionPlan:
    return ExecutionPlan(
        version=1,
        goal="Create a 3D warrior",
        deliverables=("3D model",),
        constraints=(),
        acceptance_criteria=(),
        assumptions=(),
        blocking_questions=(),
        steps=(
            PlanStep(
                "image",
                "Generate source image",
                "image.generate_from_prompt",
                "user prompt",
                "source image",
                (),
                state=image_state,  # type: ignore[arg-type]
                operation="generate_image_from_prompt",
            ),
            PlanStep(
                "model",
                "Generate 3D model",
                "model3d.generate_from_image",
                "prior tool output",
                "3D model",
                (),
                operation="generate_model3d",
            ),
        ),
        current_step_id="image" if image_state == "pending" else "model",
        state="executing",
        next_action="execute",
    )


def test_model_generation_is_blocked_until_planned_image_step_succeeds() -> None:
    call = ToolCall(
        "call-model",
        "model3d.generate_from_image",
        {"image_asset_ref": "wrong-history-image", "parameters": {}},
    )
    assistant = AssistantMessage((call,), stop_reason="tool_use")
    context = BeforeToolCallContext(assistant, call, dict(call.arguments), ())

    blocked = _planned_tool_dependency_guard(context, _model_plan())
    allowed = _planned_tool_dependency_guard(context, _model_plan("succeeded"))

    assert blocked is not None and blocked.block is True
    assert "Generate source image" in str(blocked.reason)
    assert allowed is None


def test_unverified_local_model_claim_is_replaced_with_tool_evidence() -> None:
    tool_result = ToolResultMessage(
        "call-model",
        "model3d.generate_from_image",
        ToolResult(
            (TextContent("completed"),),
            details={
                "status": "succeeded",
                "output_asset_ids": ["model-1"],
                "artifact_facts": {
                    "file_created": True,
                    "semantic_match": "not_verified",
                    "pbr": False,
                    "material_mode": "vertex_color",
                },
            },
        ),
    )
    unsupported = AssistantMessage(
        (TextContent("已确认武器完全移除，PBR 模型符合要求。"),)
    )

    grounded = _ground_model3d_final_response(
        unsupported,
        [UserMessage("去掉武器并生成3D"), tool_result],
    )

    assert grounded is not None
    text = grounded.content[0].text
    assert "尚未经过视觉验证" in text
    assert "不包含 PBR" in text
    assert "武器完全移除" not in text
    assert "asset:model-1" in text
