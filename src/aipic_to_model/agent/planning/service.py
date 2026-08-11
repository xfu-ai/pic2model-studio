"""No-tools LLM planning preflight with non-blocking degraded handling."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import replace
from time import monotonic

from ..core.errors import ProviderError
from ..core.events import CancellationToken
from ..core.models import Message, ProviderEventType, SystemMessage, TextContent, UserMessage
from ..providers.base import AgentModelProvider, ModelProfile, ModelRequest
from .models import ExecutionPlan, PlannerDiagnostic, PlanStep
from .prompts import PLANNING_SYSTEM_PROMPT

ProviderFactory = Callable[[ModelProfile], AgentModelProvider]
RequestTransform = Callable[[ModelRequest], ModelRequest]


class PlanningService:
    """Build a compact plan using the same frozen Profile as the executor."""

    def __init__(self, provider_factory: ProviderFactory) -> None:
        self._provider_factory = provider_factory

    async def prepare(
        self,
        profile: ModelProfile,
        message: UserMessage,
        *,
        version: int,
        prior: ExecutionPlan | None,
        request_transform: RequestTransform,
        cancellation: CancellationToken,
    ) -> ExecutionPlan:
        started_at = monotonic()
        request = ModelRequest(
            # Reuse the exact frozen conversation Profile. Qwen can emit visible
            # JSON only after a long reasoning trace, so a separate small planner
            # budget makes healthy plans appear malformed.
            profile=profile,
            messages=_planning_messages(message, prior),
            tools=(),
            max_output_tokens=profile.max_output_tokens,
        )
        text = ""
        try:
            transformed = request_transform(request)
            text = await asyncio.wait_for(
                _collect_text(self._provider_factory(profile), transformed, cancellation),
                timeout=profile.timeout_seconds if profile.timeout_seconds > 0 else None,
            )
            parsed = _parse(text, version, _message_text(message))
            if parsed is not None:
                parsed = _apply_character_multiview_default(parsed, message)
                issues = _plan_consistency_issues(parsed, message=message, prior=prior)
                if issues:
                    repaired = await _repair_plan_once(
                        self._provider_factory,
                        profile,
                        message,
                        prior,
                        parsed,
                        issues,
                        version=version,
                        request_transform=request_transform,
                        cancellation=cancellation,
                    )
                    if repaired is not None:
                        return repaired
                return parsed
            diagnostic_code = "empty_output" if not text else (
                "schema_invalid" if _json_object(text) is not None else "non_json_output"
            )
        except TimeoutError:
            cancellation.raise_if_cancelled()
            diagnostic_code = "provider_timeout"
        except ProviderError:
            cancellation.raise_if_cancelled()
            diagnostic_code = "provider_error"
        except Exception:  # noqa: BLE001 - planning remains fail-open.
            cancellation.raise_if_cancelled()
            diagnostic_code = "planner_internal_error"
        duration_ms = max(0, round((monotonic() - started_at) * 1000))
        return unavailable_plan(
            message,
            version,
            diagnostic=PlannerDiagnostic(
                code=diagnostic_code,
                duration_ms=duration_ms,
                output_characters=len(text),
                json_object_detected=_json_object(text) is not None,
            ),
        )


async def _collect_text(
    provider: AgentModelProvider, request: ModelRequest, cancellation: CancellationToken
) -> str:
    text_parts: list[str] = []
    async for event in provider.stream(request, cancellation):
        cancellation.raise_if_cancelled()
        if event.type is ProviderEventType.TEXT_DELTA and event.delta:
            text_parts.append(event.delta)
        elif event.type is ProviderEventType.MESSAGE_END and event.message and not text_parts:
            text_parts.extend(
                block.text for block in event.message.content if isinstance(block, TextContent)
            )
    return "".join(text_parts).strip()


async def _repair_plan_once(
    provider_factory: ProviderFactory,
    profile: ModelProfile,
    message: UserMessage,
    prior: ExecutionPlan | None,
    draft: ExecutionPlan,
    issues: tuple[str, ...],
    *,
    version: int,
    request_transform: RequestTransform,
    cancellation: CancellationToken,
) -> ExecutionPlan | None:
    """Give the same Planner one bounded chance to repair a contradictory draft."""

    request = ModelRequest(
        profile=profile,
        messages=_repair_messages(message, prior, draft, issues),
        tools=(),
        max_output_tokens=profile.max_output_tokens,
    )
    try:
        text = await asyncio.wait_for(
            _collect_text(
                provider_factory(profile), request_transform(request), cancellation
            ),
            timeout=profile.timeout_seconds if profile.timeout_seconds > 0 else None,
        )
    except (TimeoutError, ProviderError):
        cancellation.raise_if_cancelled()
        return None
    except Exception:  # noqa: BLE001 - a failed repair keeps the usable advisory draft.
        cancellation.raise_if_cancelled()
        return None
    repaired = _parse(text, version, _message_text(message))
    if repaired is not None:
        repaired = _apply_character_multiview_default(repaired, message)
    return (
        repaired
        if repaired is not None
        and not _plan_consistency_issues(repaired, message=message, prior=prior)
        else None
    )


def _planning_messages(message: UserMessage, prior: ExecutionPlan | None) -> tuple[Message, ...]:
    messages: list[Message] = [SystemMessage(PLANNING_SYSTEM_PROMPT)]
    if prior is not None:
        continuation = (
            "The user is answering a previous planning question."
            if prior.state == "waiting_user"
            else "The user is continuing, correcting, or replacing work after the latest plan."
        )
        messages.append(
            SystemMessage(
                continuation
                + " Treat the plan below as durable context, including confirmed goals, constraints, "
                "and prior user decisions. Preserve every compatible requirement while applying the "
                "current message as the newest authority. Replacing an attachment or changing step "
                "order does not discard an already confirmed style or deliverable. If the current "
                "message clearly starts an unrelated task, replace incompatible prior requirements. "
                "Progress states are historical context, not proof that pending deliverables are no "
                "longer requested. Update this prior plan:\n"
                + json.dumps(prior.to_dict(), ensure_ascii=False, separators=(",", ":"))
            )
        )
    messages.append(message)
    return tuple(messages)


def _repair_messages(
    message: UserMessage,
    prior: ExecutionPlan | None,
    draft: ExecutionPlan,
    issues: tuple[str, ...],
) -> tuple[Message, ...]:
    messages = list(_planning_messages(message, prior))
    issue_lines = "\n".join(f"- {issue}" for issue in issues)
    repair = SystemMessage(
        "The previous draft is structurally valid but internally inconsistent. Revise it once "
        "without changing compatible user requirements. Return exactly one replacement JSON "
        "object and no commentary.\nIssues:\n"
        + issue_lines
        + "\nDraft:\n"
        + json.dumps(draft.to_dict(), ensure_ascii=False, separators=(",", ":"))
    )
    messages.insert(len(messages) - 1, repair)
    return tuple(messages)


def _plan_consistency_issues(
    plan: ExecutionPlan,
    *,
    message: UserMessage | None = None,
    prior: ExecutionPlan | None = None,
) -> tuple[str, ...]:
    """Return only high-confidence dependency contradictions in a model-authored plan."""

    intent = " ".join(
        (plan.goal, *plan.deliverables, *plan.acceptance_criteria)
    ).casefold()
    requires_transparency = any(
        marker in intent for marker in ("transparent", "alpha", "透明", "阿尔法")
    )
    issues: list[str] = []
    if requires_transparency:
        removal_indexes = [
            index
            for index, step in enumerate(plan.steps)
            if _step_creates_transparency(step)
        ]
        split_indexes = [
            index
            for index, step in enumerate(plan.steps)
            if step.tool_name in {
                "split_image",
                "image.split_grid",
                "image.split_alpha_components",
                "element.split_semantic",
                "element.split_selection",
            }
            or (step.operation or "").startswith("split_")
        ]
        if not removal_indexes:
            issues.append(
                "The deliverable requires transparent output, but no step creates an alpha channel."
            )
        elif split_indexes and min(removal_indexes) > min(split_indexes):
            issues.append(
                "The plan splits components before the step that creates their transparent background."
            )

    model_indexes = [
        index
        for index, step in enumerate(plan.steps)
        if step.operation == "generate_model3d"
        or step.tool_name in {
            "generate_model3d",
            "model3d.generate_from_image",
            "model3d.generate_from_multiview",
        }
    ]
    if model_indexes:
        first_model = min(model_indexes)
        prep_indexes = [
            index
            for index, step in enumerate(plan.steps[:first_model])
            if step.operation in {"generate_image_from_prompt", "transform_from_reference"}
            or step.tool_name in {
                "image.generate_from_prompt",
                "image.generate_from_prompt_asset",
                "image.transform_from_reference",
                "image.inpaint_selection",
            }
        ]
        has_attachment = bool(message is not None and message.attachments)
        prior_has_visual_output = bool(
            prior is not None
            and any(
                step.state == "succeeded"
                and step.operation
                in {"generate_image_from_prompt", "transform_from_reference"}
                for step in prior.steps
            )
        )
        if not has_attachment and not prior_has_visual_output:
            if not prep_indexes:
                issues.append(
                    "A text-only 3D request has no supplied image; generate an image from the "
                    "user prompt before model3d generation instead of using project history."
                )
            else:
                verification_between = any(
                    step.operation in {"inspect_image", "verify_output"}
                    or step.tool_name
                    in {
                        "image.understand_for_agent",
                        "image.analyze_content",
                        "image.evaluate_3d_suitability",
                    }
                    for step in plan.steps[min(prep_indexes) + 1 : first_model]
                )
                if not verification_between:
                    issues.append(
                        "Verify the prompt-generated image against the requested subject and "
                        "constraints before model3d generation."
                    )

        request_text = (
            f"{_message_text(message)} {intent}".casefold()
            if message is not None
            else intent
        )
        edit_markers = (
            "remove ",
            "without ",
            "no weapon",
            "omit ",
            "delete ",
            "去掉",
            "移除",
            "删除",
            "不要武器",
            "无武器",
        )
        if (
            has_attachment
            and any(marker in request_text for marker in edit_markers)
            and not prep_indexes
        ):
            issues.append(
                "The user requests a visible edit to the supplied image; transform or edit "
                "that image and verify the edited output before model3d generation."
            )

    if message is not None and not message.attachments:
        first_created_image = next(
            (
                index
                for index, step in enumerate(plan.steps)
                if step.operation in {"generate_image_from_prompt", "transform_from_reference"}
            ),
            None,
        )
        for index, step in enumerate(plan.steps):
            if step.operation != "inspect_image":
                continue
            explicit_asset = any(
                marker in step.input_source.casefold()
                for marker in ("asset:", "explicit asset", "prior tool output")
            )
            if not explicit_asset and (
                first_created_image is None or index < first_created_image
            ):
                issues.append(
                    "The plan attempts image inspection without a current attachment, an explicit "
                    "asset, or a preceding image-producing step."
                )
                break
    return tuple(issues)


_CHARACTER_3D_MARKERS = (
    "character",
    "avatar",
    "humanoid",
    "human",
    "person",
    "creature",
    "monster",
    "boss",
    "warrior",
    "hero",
    "villain",
    "npc",
    "cthulhu",
    "角色",
    "人物",
    "人形",
    "人类",
    "生物",
    "怪物",
    "魔物",
    "战士",
    "英雄",
    "反派",
    "克苏鲁",
)
_SINGLE_IMAGE_OPT_OUT_MARKERS = (
    "single image",
    "single-image",
    "one image only",
    "skip multiview",
    "without multiview",
    "quick draft",
    "rough draft",
    "单图",
    "只用这张图",
    "仅用这张图",
    "不用三视图",
    "跳过三视图",
    "快速草模",
)


def _apply_character_multiview_default(
    plan: ExecutionPlan, message: UserMessage
) -> ExecutionPlan:
    """Apply the host-owned default that character-like 3D work uses multiview.

    This changes workflow routing only.  It deliberately adds no inferred
    visual acceptance criteria, so it cannot turn model-authored character
    details into hard requirements.
    """

    request_text = " ".join(
        (_message_text(message), plan.goal, *plan.deliverables)
    ).casefold()
    if not any(marker in request_text for marker in _CHARACTER_3D_MARKERS):
        return plan
    if any(marker in request_text for marker in _SINGLE_IMAGE_OPT_OUT_MARKERS):
        return plan
    model_index = next(
        (
            index
            for index, step in enumerate(plan.steps)
            if step.operation == "generate_model3d"
            or step.tool_name
            in {"model3d.generate_from_image", "model3d.generate_from_multiview"}
        ),
        None,
    )
    if model_index is None:
        return plan
    steps = list(plan.steps)
    model_step = steps[model_index]
    prior_multiview = next(
        (
            step
            for step in reversed(steps[:model_index])
            if step.operation == "prepare_multiview"
            or step.tool_name == "multiview.generate"
        ),
        None,
    )
    if prior_multiview is None:
        existing_ids = {step.id for step in steps}
        identifier = "prepare_character_multiview"
        suffix = 2
        while identifier in existing_ids:
            identifier = f"prepare_character_multiview_{suffix}"
            suffix += 1
        chinese = bool(re.search(r"[\u3400-\u9fff]", _message_text(message)))
        prior_multiview = PlanStep(
            id=identifier,
            label=(
                "生成并确认角色正面、侧面和背面三视图"
                if chinese
                else "Generate and confirm front, side, and back character views"
            ),
            tool_name="multiview.generate",
            input_source=(
                "前一步已核验的角色建模图"
                if chinese
                else "the previously verified character modeling image"
            ),
            expected_output=(
                "经用户确认的正面、侧面和背面三张独立裁图"
                if chinese
                else "three distinct user-confirmed front, side, and back crops"
            ),
            verification_targets=(),
            operation="prepare_multiview",
        )
        steps.insert(model_index, prior_multiview)
        model_index += 1
    else:
        # A generated sheet is an intermediate artifact. Do not let that Tool
        # result satisfy an expected output that requires persisted user crops.
        prior_index = steps.index(prior_multiview)
        steps[prior_index] = replace(
            prior_multiview,
            expected_output=(
                "可供用户框选正面、侧面和背面的三视图拼图"
                if re.search(r"[\u3400-\u9fff]", _message_text(message))
                else "a generated front-side-back sheet ready for user cropping"
            ),
        )
        prior_multiview = steps[prior_index]

    prior_index = steps.index(prior_multiview)
    steps[prior_index] = replace(
        prior_multiview,
        expected_output=(
            "可供用户框选正面、侧面和背面的三视图拼图"
            if re.search(r"[\u3400-\u9fff]", _message_text(message))
            else "a generated front-side-back sheet ready for user cropping"
        ),
    )
    prior_multiview = steps[prior_index]

    confirmation = next(
        (
            step
            for step in steps[:model_index]
            if step.operation == "confirm_multiview"
            or step.tool_name == "multiview.request_region_confirmation"
        ),
        None,
    )
    if confirmation is None:
        existing_ids = {step.id for step in steps}
        identifier = "confirm_character_multiview"
        suffix = 2
        while identifier in existing_ids:
            identifier = f"confirm_character_multiview_{suffix}"
            suffix += 1
        chinese = bool(re.search(r"[\u3400-\u9fff]", _message_text(message)))
        confirmation = PlanStep(
            id=identifier,
            label="确认角色正侧背裁图" if chinese else "Confirm character front, side, and back crops",
            tool_name="multiview.request_region_confirmation",
            input_source="前一步生成的三视图拼图" if chinese else "the generated multiview sheet",
            expected_output=(
                "持久化的三视图集及三张互不相同的用户确认裁图"
                if chinese
                else "a persisted multiview set with three distinct user-confirmed crops"
            ),
            verification_targets=(),
            operation="confirm_multiview",
        )
        steps.insert(model_index, confirmation)
        model_index += 1
    steps[model_index] = replace(
        model_step,
        tool_name="model3d.generate_from_multiview",
        input_source=(
            "前一步经用户确认的三视图裁图"
            if re.search(r"[\u3400-\u9fff]", _message_text(message))
            else "the preceding user-confirmed multiview crops"
        ),
    )
    current_step_id = plan.current_step_id
    if current_step_id == model_step.id:
        current_step_id = next(
            (
                step.id
                for step in steps[:model_index]
                if step.state in {"pending", "running"}
                and step.operation in {"prepare_multiview", "confirm_multiview"}
            ),
            confirmation.id,
        )
    return replace(plan, steps=tuple(steps), current_step_id=current_step_id)


def _step_creates_transparency(step: PlanStep) -> bool:
    if step.operation in {
        "remove_background_local",
        "remove_background_provider",
        "export_transparent_provider",
    }:
        return True
    if step.tool_name not in {
        "edit_image",
        "image.remove_background_local",
        "image.remove_background_provider",
        "element.export_transparent",
    }:
        return False
    description = f"{step.label} {step.expected_output}".casefold()
    return any(marker in description for marker in ("transparent", "alpha", "透明", "阿尔法"))


def _parse(text: str, version: int, user_goal: str) -> ExecutionPlan | None:
    decoded = _json_object(text)
    if decoded is None:
        return None
    goal = decoded.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        # Structural normalization of actual LLM JSON, not a deterministic
        # substitute planner. It avoids discarding useful steps for one missing field.
        goal = user_goal
    if not goal.strip():
        return None
    steps_value = decoded.get("steps")
    steps: list[PlanStep] = []
    if isinstance(steps_value, list):
        for position, value in enumerate(steps_value[:8], start=1):
            if not isinstance(value, dict):
                continue
            label = value.get("label")
            if not isinstance(label, str) or not label.strip():
                continue
            identifier = value.get("id")
            steps.append(
                PlanStep(
                    id=(identifier if isinstance(identifier, str) and identifier.strip() else f"step_{position}")[:80],
                    label=label.strip()[:240],
                    tool_name=(
                        value["tool_name"][:80]
                        if isinstance(value.get("tool_name"), str) and value["tool_name"].strip()
                        else None
                    ),
                    input_source=(value.get("input_source") if isinstance(value.get("input_source"), str) else "user request")[:240],
                    expected_output=(value.get("expected_output") if isinstance(value.get("expected_output"), str) else "requested result")[:240],
                    verification_targets=_string_tuple(value.get("verification_targets"), 8, 160),
                    operation=(
                        value["operation"][:80]
                        if isinstance(value.get("operation"), str) and value["operation"].strip()
                        else None
                    ),
                )
            )
    questions = _string_tuple(decoded.get("blocking_questions"), 3, 320)
    next_action = decoded.get("next_action")
    if questions:
        next_action = "ask_user"
    if next_action not in {"execute", "ask_user", "respond"}:
        next_action = "execute"
    state = "waiting_user" if next_action == "ask_user" else "executing"
    return ExecutionPlan(
        version=version,
        goal=goal.strip()[:500],
        deliverables=_string_tuple(decoded.get("deliverables"), 8, 240),
        constraints=_string_tuple(decoded.get("constraints"), 12, 240),
        acceptance_criteria=_string_tuple(decoded.get("acceptance_criteria"), 12, 240),
        assumptions=_string_tuple(decoded.get("assumptions"), 8, 240),
        blocking_questions=questions,
        steps=tuple(steps),
        current_step_id=steps[0].id if steps and next_action == "execute" else None,
        state=state,
        next_action=next_action,
    )


def unavailable_plan(
    message: UserMessage, version: int, *, diagnostic: PlannerDiagnostic | None = None
) -> ExecutionPlan:
    """Persist planner unavailability without presenting a fake execution plan."""

    content = message.display_content if isinstance(message.display_content, str) else _message_text(message)
    goal = content.strip() or "The requested work"
    return ExecutionPlan(
        version=version,
        goal=goal[:500],
        deliverables=(),
        constraints=(),
        acceptance_criteria=(),
        assumptions=(),
        blocking_questions=(),
        steps=(),
        current_step_id=None,
        state="executing",
        next_action="execute",
        fallback=True,
        planner_diagnostic=diagnostic,
    )


def _json_object(text: str) -> dict[str, object] | None:
    """Extract one JSON object despite harmless provider prose or fences."""

    candidate = re.sub(r"```(?:json)?", "", text.strip(), flags=re.IGNORECASE).replace("```", "")
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", candidate):
        try:
            value, _ = decoder.raw_decode(candidate[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _string_tuple(value: object, limit: int, item_limit: int) -> tuple[str, ...]:
    return tuple(
        item.strip()[:item_limit]
        for item in value[:limit]
        if isinstance(item, str) and item.strip()
    ) if isinstance(value, list) else ()


def _message_text(message: UserMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return "\n".join(block.text for block in message.content if isinstance(block, TextContent))
