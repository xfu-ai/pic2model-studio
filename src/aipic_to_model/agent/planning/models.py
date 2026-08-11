"""Small, provider-neutral planning models.

Plans are advisory working state, never a tool permission boundary.  The
objects intentionally model an ordered list instead of a generic DAG: Agent
tools already execute serially and each dependent operation receives exact
output references from its preceding Tool Result.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

PlanState = Literal["executing", "waiting_user", "completed", "completed_with_warnings"]
StepState = Literal["pending", "running", "succeeded", "review_required", "failed"]
AttemptState = Literal["succeeded", "review_required", "failed"]
NextAction = Literal["execute", "ask_user", "respond"]
PlannerDiagnosticCode = Literal[
    "provider_timeout",
    "provider_error",
    "empty_output",
    "non_json_output",
    "schema_invalid",
    "planner_internal_error",
]


@dataclass(frozen=True)
class PlannerDiagnostic:
    """Safe, durable metadata for a degraded planning attempt.

    It intentionally excludes provider messages and model text, which may
    contain user content or credentials echoed by a provider.
    """

    code: PlannerDiagnosticCode
    duration_ms: int
    output_characters: int = 0
    json_object_detected: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "duration_ms": self.duration_ms,
            "output_characters": self.output_characters,
            "json_object_detected": self.json_object_detected,
        }

    @classmethod
    def from_dict(cls, value: object) -> PlannerDiagnostic | None:
        if not isinstance(value, dict):
            return None
        code = value.get("code")
        if code not in {
            "provider_timeout",
            "provider_error",
            "empty_output",
            "non_json_output",
            "schema_invalid",
            "planner_internal_error",
        }:
            return None
        return cls(
            code=code,
            duration_ms=max(0, int(value.get("duration_ms", 0)))
            if isinstance(value.get("duration_ms", 0), int)
            else 0,
            output_characters=max(0, int(value.get("output_characters", 0)))
            if isinstance(value.get("output_characters", 0), int)
            else 0,
            json_object_detected=bool(value.get("json_object_detected", False)),
        )


@dataclass(frozen=True)
class PlanAttempt:
    """One durable Tool attempt made for a Plan step."""

    tool_name: str
    state: AttemptState
    tool_call_id: str | None = None
    warning: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "tool_name": self.tool_name,
            "state": self.state,
            "tool_call_id": self.tool_call_id,
            "warning": self.warning,
        }

    @classmethod
    def from_dict(cls, value: object) -> PlanAttempt | None:
        if not isinstance(value, dict):
            return None
        tool_name = value.get("tool_name")
        state = value.get("state")
        if (
            not isinstance(tool_name, str)
            or not tool_name
            or state not in {"succeeded", "review_required", "failed"}
        ):
            return None
        tool_call_id = value.get("tool_call_id")
        warning = value.get("warning")
        return cls(
            tool_name=tool_name[:80],
            state=state,
            tool_call_id=(
                tool_call_id[:120]
                if isinstance(tool_call_id, str) and tool_call_id
                else None
            ),
            warning=warning[:320] if isinstance(warning, str) and warning else None,
        )


@dataclass(frozen=True)
class PlanStep:
    id: str
    label: str
    tool_name: str | None
    input_source: str
    expected_output: str
    verification_targets: tuple[str, ...]
    state: StepState = "pending"
    warning: str | None = None
    # This captures the user-outcome operation, not a command for the Executor.
    operation: str | None = None
    attempts: tuple[PlanAttempt, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "tool_name": self.tool_name,
            "input_source": self.input_source,
            "expected_output": self.expected_output,
            "verification_targets": list(self.verification_targets),
            "state": self.state,
            "warning": self.warning,
            "operation": self.operation,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
        }

    @classmethod
    def from_dict(cls, value: object) -> PlanStep | None:
        if not isinstance(value, dict):
            return None
        identifier = value.get("id")
        label = value.get("label")
        if not isinstance(identifier, str) or not identifier or not isinstance(label, str) or not label:
            return None
        tool_name = value.get("tool_name")
        input_source = value.get("input_source")
        expected_output = value.get("expected_output")
        targets = value.get("verification_targets")
        state = value.get("state", "pending")
        warning = value.get("warning")
        operation = value.get("operation")
        attempts_value = value.get("attempts")
        return cls(
            id=identifier[:80],
            label=label[:240],
            tool_name=tool_name[:80] if isinstance(tool_name, str) and tool_name else None,
            input_source=input_source[:240] if isinstance(input_source, str) else "user request",
            expected_output=expected_output[:240]
            if isinstance(expected_output, str)
            else "requested result",
            verification_targets=tuple(
                item[:160] for item in targets[:8] if isinstance(item, str) and item.strip()
            )
            if isinstance(targets, list)
            else (),
            state=state
            if state in {"pending", "running", "succeeded", "review_required", "failed"}
            else "pending",
            warning=warning[:320] if isinstance(warning, str) and warning else None,
            operation=operation[:80] if isinstance(operation, str) and operation else None,
            attempts=tuple(
                attempt
                for item in attempts_value[-20:]
                if (attempt := PlanAttempt.from_dict(item)) is not None
            )
            if isinstance(attempts_value, list)
            else (),
        )


@dataclass(frozen=True)
class ExecutionPlan:
    """The durable, user-safe snapshot for one requested outcome."""

    version: int
    goal: str
    deliverables: tuple[str, ...]
    constraints: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    assumptions: tuple[str, ...]
    blocking_questions: tuple[str, ...]
    steps: tuple[PlanStep, ...]
    current_step_id: str | None
    state: PlanState
    next_action: NextAction
    fallback: bool = False
    planner_diagnostic: PlannerDiagnostic | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "version": self.version,
            "goal": self.goal,
            "deliverables": list(self.deliverables),
            "constraints": list(self.constraints),
            "acceptance_criteria": list(self.acceptance_criteria),
            "assumptions": list(self.assumptions),
            "blocking_questions": list(self.blocking_questions),
            "steps": [step.to_dict() for step in self.steps],
            "current_step_id": self.current_step_id,
            "state": self.state,
            "next_action": self.next_action,
            "fallback": self.fallback,
            "planner_diagnostic": self.planner_diagnostic.to_dict()
            if self.planner_diagnostic is not None
            else None,
        }

    @classmethod
    def from_dict(cls, value: object) -> ExecutionPlan | None:
        if not isinstance(value, dict):
            return None
        goal = value.get("goal")
        if not isinstance(goal, str) or not goal.strip():
            return None
        steps_value = value.get("steps")
        steps = tuple(
            step for item in steps_value if (step := PlanStep.from_dict(item)) is not None
        ) if isinstance(steps_value, list) else ()
        current_step_id = value.get("current_step_id")
        state = value.get("state", "executing")
        next_action = value.get("next_action", "execute")
        return cls(
            version=max(1, int(value.get("version", 1)))
            if isinstance(value.get("version", 1), int)
            else 1,
            goal=goal.strip()[:500],
            deliverables=_strings(value.get("deliverables"), 8, 240),
            constraints=_strings(value.get("constraints"), 12, 240),
            acceptance_criteria=_strings(value.get("acceptance_criteria"), 12, 240),
            assumptions=_strings(value.get("assumptions"), 8, 240),
            blocking_questions=_strings(value.get("blocking_questions"), 3, 320),
            steps=steps,
            current_step_id=current_step_id[:80]
            if isinstance(current_step_id, str) and current_step_id
            else None,
            state=state
            if state in {"executing", "waiting_user", "completed", "completed_with_warnings"}
            else "executing",
            next_action=next_action if next_action in {"execute", "ask_user", "respond"} else "execute",
            fallback=bool(value.get("fallback", False)),
            planner_diagnostic=PlannerDiagnostic.from_dict(value.get("planner_diagnostic")),
        )

    def with_tool_result(
        self,
        tool_name: str,
        *,
        failed: bool,
        verification: dict[str, object] | None,
        tool_call_id: str | None = None,
        attempt_warning: str | None = None,
        waiting_for_user: bool = False,
    ) -> ExecutionPlan:
        """Bind a Tool result to the current step or a matching retryable step."""

        current = next(
            (step for step in self.steps if step.id == self.current_step_id),
            None,
        )
        target = (
            current
            if current is not None and _step_matches_tool(current, tool_name)
            else next(
                (
                    step
                    for step in reversed(self.steps)
                    if step.state in {"failed", "review_required", "succeeded"}
                    and _step_matches_tool(step, tool_name)
                ),
                None,
            )
        )
        if target is None:
            return self
        return self.with_step_result(
            target.id,
            tool_name=tool_name,
            failed=failed,
            verification=verification,
            tool_call_id=tool_call_id,
            attempt_warning=attempt_warning,
            waiting_for_user=waiting_for_user,
        )

    def with_tool_running(self, tool_name: str) -> ExecutionPlan:
        """Keep the matching current step active for a non-terminal Tool result."""

        current = next(
            (
                step
                for step in self.steps
                if step.id == self.current_step_id and _step_matches_tool(step, tool_name)
            ),
            None,
        )
        return self.with_step_running(current.id) if current is not None else self

    def with_step_running(
        self, step_id: str, *, tool_name: str | None = None
    ) -> ExecutionPlan:
        """Mark a pending Plan step active without recording a completed attempt."""

        index = next(
            (position for position, step in enumerate(self.steps) if step.id == step_id),
            None,
        )
        if (
            index is None
            or self.steps[index].state not in {"pending", "running"}
            or (
                tool_name is not None
                and not _step_matches_tool(self.steps[index], tool_name)
            )
        ):
            return self
        updated_steps = list(self.steps)
        updated_steps[index] = replace(updated_steps[index], state="running")
        return replace(
            self,
            steps=tuple(updated_steps),
            current_step_id=step_id,
            state="executing",
            next_action="execute",
        )

    def with_step_result(
        self,
        step_id: str,
        *,
        failed: bool,
        verification: dict[str, object] | None,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        attempt_warning: str | None = None,
        waiting_for_user: bool = False,
    ) -> ExecutionPlan:
        """Record one attempt and derive the step outcome from all its attempts."""

        disposition = verification.get("disposition") if isinstance(verification, dict) else None
        warning = attempt_warning or _verification_warning(verification)
        attempt_state: AttemptState = (
            "failed"
            if failed
            else "review_required"
            if disposition in {"review_required", "warn", "unknown"}
            else "succeeded"
        )
        index = next(
            (position for position, step in enumerate(self.steps) if step.id == step_id),
            None,
        )
        if index is None:
            return self
        previous = self.steps[index]
        if tool_name is not None and not _step_matches_tool(previous, tool_name):
            return self
        if tool_call_id is not None and any(
            item.tool_call_id == tool_call_id for item in previous.attempts
        ):
            return self
        attempt = PlanAttempt(
            tool_name=(tool_name or previous.tool_name or previous.operation or "managed_tool")[:80],
            state=attempt_state,
            tool_call_id=tool_call_id,
            warning=warning,
        )
        attempts = (*previous.attempts, attempt)[-20:]
        # Once any attempt succeeded, a later failed experiment does not erase
        # the usable result. Conversely, a successful retry recovers a failed
        # step while retaining the earlier failure in ``attempts``.
        has_success = attempt_state == "succeeded" or any(
            item.state == "succeeded" for item in previous.attempts
        )
        state: StepState = "succeeded" if has_success else attempt_state
        step_warning = _attempt_history_warning(attempts, state)
        updated_steps = list(self.steps)
        updated_steps[index] = replace(
            previous,
            state=state,
            warning=step_warning,
            attempts=attempts,
        )
        # A failed operation or an advisory verification warning is a pause point,
        # not evidence that the following step has started. Keeping the plan in
        # ``executing`` used to advance ``current_step_id`` to the next pending
        # step, which made the desktop show a spinner after the Agent had already
        # stopped to ask for review.
        if state in {"failed", "review_required"}:
            return replace(
                self,
                steps=tuple(updated_steps),
                current_step_id=None,
                state="waiting_user" if waiting_for_user else "completed_with_warnings",
                next_action="ask_user" if waiting_for_user else self.next_action,
            )
        existing_current = next(
            (
                step.id
                for step in updated_steps
                if step.id == self.current_step_id and step.state in {"pending", "running"}
            ),
            None,
        )
        next_pending = existing_current or next(
            (step.id for step in updated_steps[index + 1 :] if step.state == "pending"), None
        )
        plan_state: PlanState = "executing"
        if next_pending is None:
            plan_state = (
                "completed_with_warnings"
                if _steps_have_attempt_warnings(tuple(updated_steps))
                else "completed"
            )
        return replace(
            self,
            steps=tuple(updated_steps),
            current_step_id=next_pending,
            state=plan_state,
        )

    def with_final_response(self) -> ExecutionPlan:
        """Close response-level verification after a visible final answer.

        A final assistant answer can itself perform the plan's user-facing
        verification summary, so a pending ``verify_output`` step must not
        leave an otherwise completed task looking active forever.  Real failed
        work, blocking questions, and pending operational steps remain open.
        """

        if self.state == "waiting_user" or any(step.state == "failed" for step in self.steps):
            return self
        updated_steps = tuple(
            replace(step, state="succeeded")
            if step.state in {"pending", "running"} and step.operation == "verify_output"
            else step
            for step in self.steps
        )
        remaining = next(
            (step.id for step in updated_steps if step.state in {"pending", "running"}), None
        )
        if remaining is not None:
            return replace(self, steps=updated_steps, current_step_id=remaining)
        has_warnings = _steps_have_attempt_warnings(updated_steps)
        return replace(
            self,
            steps=updated_steps,
            current_step_id=None,
            state="completed_with_warnings" if has_warnings else "completed",
            next_action="respond",
        )


def _strings(value: object, limit: int, item_limit: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        item.strip()[:item_limit]
        for item in value[:limit]
        if isinstance(item, str) and item.strip()
    )


def _verification_warning(verification: dict[str, object] | None) -> str | None:
    if not isinstance(verification, dict):
        return None
    checks = verification.get("checks")
    if isinstance(checks, list):
        for check in checks:
            if not isinstance(check, dict):
                continue
            if check.get("passed") is False or check.get("outcome") in {"warn", "fail"}:
                message = check.get("message") or check.get("name")
                if isinstance(message, str) and message.strip():
                    return message.strip()[:320]
    return "The produced artifact requires review." if verification.get("disposition") == "review_required" else None


def _attempt_history_warning(
    attempts: tuple[PlanAttempt, ...], state: StepState
) -> str | None:
    earlier_problems = [
        attempt for attempt in attempts[:-1] if attempt.state in {"failed", "review_required"}
    ]
    latest = attempts[-1]
    if state == "succeeded" and earlier_problems:
        detail = next(
            (attempt.warning for attempt in earlier_problems if attempt.warning),
            None,
        )
        prefix = f"Earlier attempt: {detail}" if detail else "An earlier attempt failed."
        return f"{prefix} Latest attempt succeeded."[:320]
    if latest.state in {"failed", "review_required"}:
        return latest.warning or (
            "Tool attempt failed."
            if latest.state == "failed"
            else "The produced artifact requires review."
        )
    return None


def _steps_have_attempt_warnings(steps: tuple[PlanStep, ...]) -> bool:
    return any(
        step.state == "review_required"
        or any(attempt.state in {"failed", "review_required"} for attempt in step.attempts)
        for step in steps
    )


def _step_matches_tool(step: PlanStep, tool_name: str) -> bool:
    """Map stable planner operations to the facade that reports their result."""

    if step.tool_name == tool_name:
        return True
    return tool_name in {
        "inspect_image": {
            "inspect_workspace",
            "understand_image",
            "analyze_image",
            "image.understand_for_agent",
            "image.analyze_content",
            "image.analyze_style",
            "image.evaluate_3d_suitability",
        },
        "remove_background_local": {"edit_image", "image.remove_background_local"},
        "generate_image_from_prompt": {
            "generate_images",
            "image.generate_from_prompt",
            "image.generate_from_prompt_asset",
        },
        "transform_from_reference": {"generate_images", "image.transform_from_reference"},
        "split_grid_local": {"split_image", "image.split_grid"},
        "split_alpha_components_local": {"split_image", "image.split_alpha_components"},
        "normalize_components_local": {"edit_image", "image.trim_transparent", "image.normalize"},
        "resize_image_local": {"edit_image", "image.normalize"},
        "upscale_image_local": {"edit_image", "image.upscale_local"},
        "upscale_image_provider": {"edit_image", "image.upscale_provider"},
        "prepare_multiview": {
            "prepare_multiview",
            "multiview.generate",
            "multiview.detect_regions",
            "multiview.regenerate_view",
        },
        "confirm_multiview": {
            "prepare_multiview",
            "multiview.request_region_confirmation",
        },
        "verify_output": {
            "inspect_workspace",
            "understand_image",
            "asset.get_metadata",
            "image.understand_for_agent",
        },
    }.get(step.operation or "", set())
