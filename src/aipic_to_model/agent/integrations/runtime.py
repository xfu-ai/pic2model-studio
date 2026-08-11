"""Application-facing Agent runtime with durable, safe event projections."""

from __future__ import annotations

import asyncio
import base64
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from io import BytesIO
from pathlib import Path
from typing import Any, cast

from PIL import Image

from ...application.tools import ToolRegistry as AIPicToolRegistry
from ...infrastructure.sqlite.multiview_repository import MultiviewRepository
from ..core.agent_loop import BeforeToolCallContext, BeforeToolCallResult
from ..core.errors import AgentCancelledError, ProviderError
from ..core.events import AgentEvent, AgentEventType, CancellationToken
from ..core.models import (
    AssistantMessage,
    CustomMessage,
    ImageContent,
    ManagedAssetAttachment,
    Message,
    SystemMessage,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResult,
    ToolResultMessage,
    UserMessage,
    message_from_dict,
)
from ..core.tool import AgentTool
from ..execution import LocalExecutionEnv
from ..execution.approved_job_wait import ApprovedToolJobWait
from ..harness import AgentHarness
from ..planning import ExecutionPlan, PlanningService
from ..providers.api.openai_completions import OpenAICompletionsProvider
from ..providers.base import AgentModelProvider, ModelProfile, ModelRequest
from ..providers.deepseek import (
    create_deepseek_credential_resolver,
    create_deepseek_profile,
    deepseek_context_window,
)
from ..providers.qwen3_vl import (
    OLLAMA_PROVIDER_ID,
    QWEN3_VL_DEFAULT_TIMEOUT_SECONDS,
    QWEN3_VL_SUPPORTED_MODELS,
    create_ollama_credential_resolver,
    create_qwen3_vl_profile,
    qwen3_vl_context_window,
)
from ..session.sqlite import LinearSessionRepository
from ..skills.loader import SkillLoader
from ..tools import BashTool, EditTool, ReadTool, WriteTool
from .aipic_tools import AIPicToolInvocation
from .facade_tools import PromptCreator
from .progressive_tools import (
    PERMANENT_TOOL_NAMES,
    build_progressive_tool_catalog,
    planner_tool_names,
)

ProviderFactory = Callable[[ModelProfile], AgentModelProvider]
RuntimeContextProvider = Callable[[str], dict[str, object]]
PlannerContextProvider = Callable[[str], dict[str, object]]
AttachmentProvider = Callable[[str, str], dict[str, object]]
AgentModelSelector = Callable[[], str | None]
AttachmentContentProvider = Callable[[str, str], tuple[bytes, str]]
_MODEL_TOOL_RESULT_TEXT_LIMIT = 4_096
_AGENT_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
_AGENT_IMAGE_FORMATS = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
}
_MAX_AGENT_IMAGE_ATTACHMENTS = 4
_MAX_AGENT_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_AGENT_IMAGE_REQUEST_BYTES = 24 * 1024 * 1024
_MAX_AGENT_IMAGE_DIMENSION = 8_192
_MAX_AGENT_IMAGE_PIXELS = 40_000_000
_NONTERMINAL_TOOL_STATUSES = frozenset(
    {"awaiting_ui_action", "queued", "running", "waiting_external"}
)
_LEGACY_QWEN3_VL_MAX_OUTPUT_TOKENS = {2_048, 16_384}
_LEGACY_QWEN3_VL_TIMEOUT_SECONDS = 120.0
_QWEN3_VL_DEFAULT_THINKING_LEVEL = "medium"
_FINAL_RESPONSE_CONTRACT = (
    "Completion contract: do not finish a user turn until the provider has "
    "returned a terminal finish_reason. After one or more tool calls have "
    "finished, always send a concise, user-facing final summary of the "
    "completed work, results, and any warnings or next action. Never end a "
    "turn with an empty assistant message: the terminal response must contain "
    "non-whitespace text. Reply in the language of the latest natural-language "
    "user request: Chinese input receives Chinese output, English input receives "
    "English output, and Chinese is the default when ambiguous. Internal tool and "
    "task-event text does not change that user-facing language. Completion facts contract: base "
    "claims about output count, format, size, transparency, and success only on structured Tool "
    "Result facts. A queued, running, failed, empty, or waiting_external result must be described "
    "as waiting, failed, empty, or uncertain as applicable; never claim it succeeded."
)
_EXECUTION_OUTLINE_CONTRACT = (
    "Execution-outline contract: before the first business Tool in a multi-step, asynchronous, "
    "or multi-asset task, write a short visible <execution_outline> with goal, ordered steps, each "
    "step's input source, current step, and critical constraints. Keep it operational rather than "
    "speculative: include every requested deliverable and preserve unchanged constraints. It is a "
    "working aid, not a permission gate: a malformed or omitted outline never blocks a valid Tool "
    "call. Execute dependent Tools "
    "serially. After each Tool result, use its exact output_asset_refs for the next dependent Tool; "
    "do not use asset.list, newest assets, or filenames to rediscover a known output. Direct "
    "background removal, splitting, resizing, or generation is a valid one-step task with no invented "
    "prerequisites. When the user changes one constraint, preserve all unchanged inputs and constraints; "
    "do not change a reference-image transformation from from_image to from_prompt unless the user "
    "explicitly removes that image reference."
)
_ARTIFACT_VERIFICATION_CONTRACT = (
    "Artifact-verification contract: a Tool Result may include verification facts and advisory "
    "checks produced by the application. Treat those facts as authoritative for every provider, "
    "including DeepSeek and Qwen. Tool status=succeeded means the operation ran; it does not by "
    "itself prove the user's requested visual or artifact outcome. Before deciding the next step "
    "or claiming completion, inspect the verification disposition. If it is review_required, warn, "
    "or unknown, explain the evidence accurately and choose the next safe action using the user's "
    "goal; do not claim the requested result is verified and do not blindly repeat the same tool. "
    "Verification is advisory and never blocks a valid Tool call, requires a retry, or changes a "
    "Tool status. If verification is absent or not applicable, continue normally but only claim "
    "facts returned by structured Tool Results."
)
_PLANNING_EXECUTION_CONTRACT = (
    "Planning-state contract: every natural-language user request is first reduced to an "
    "advisory execution plan containing goal, deliverables, constraints, acceptance criteria, "
    "and ordered steps. The current plan snapshot is read-only host truth. Use it to preserve "
    "the requested outcome and inspect its current-step warnings, but it is never a tool-call "
    "permission gate. When a managed Tool advances a Plan step, pass that step's exact plan_step_id; "
    "omit it for support work that does not advance a step. This binding updates display progress "
    "only and never permits or blocks execution. When Tool Results or user instructions materially "
    "change the work, update the plan rather than silently discarding unchanged constraints."
)
_ASYNC_JOB_CONTRACT = (
    "Async-job contract: call job.get_status at most once for "
    "a job in one Agent run. Do not repeatedly call asset.list, sleep, or poll files "
    "to wait for asynchronous output. If a job is queued, running, or waiting, tell "
    "the user it is waiting and end with a concise visible summary. An interrupted "
    "result with an error is terminal incomplete work, never background progress or "
    "task completion. Explain its recovery fields and ask for any explicit user "
    "confirmation they require. When requires_user_confirmation=true, ask the user "
    "directly and stop: do not search the toolbox or call a recovery Tool in that "
    "same turn. The recommended recovery Tool is preloaded for the later confirmed "
    "turn. "
    "The desktop will send a new completion event when the job reaches a terminal state."
)
_ASYNC_JOB_CONTRACT = (
    _ASYNC_JOB_CONTRACT
    + " Binding clarification: after a Tool returns a fresh queued, running, or waiting "
    "Job result, do not call job.get_status, asset.list, sleep, or "
    "poll files to wait for it. This overrides any earlier at-most-once allowance. Tell "
    "the user the background task started and stop; the desktop will send the terminal "
    "event. Use job.get_status only when the user explicitly asks for progress "
    "and no fresh Job event is already present in the current context."
)
_IMAGE_PRESENTATION_CONTRACT = (
    "Image-presentation contract: images are attached to the final user-facing "
    "assistant answer, never to a tool explanation. When presenting an existing "
    "image found through asset.list, inspect that asset with "
    "asset.get_metadata before the final answer so the desktop "
    "can attach that managed image. To place a managed image at a specific position "
    "inside the final Markdown answer, write ![short descriptive label]"
    "(asset:<exact_output_asset_ref>) using only an exact output_asset_ref from a "
    "managed Tool result. Never use a filename, local path, blob URL, or remote URL "
    "for a managed image; the desktop resolves the special reference. "
    "Do not expose raw asset IDs in user-facing text."
)
_TOOL_SELECTION_CONTRACT = (
    "Tool-selection contract: the active Tool area starts with a small permanent set. Use "
    "toolbox.status to search the single-operation catalog and toolbox.load to append missing "
    "Tool schemas; newly loaded Tools are callable only on the next model turn. Use managed Tools for project state, managed "
    "assets, selections, prompts, jobs, approvals, provider work, and exports. "
    "Use read, write, edit, and bash only for workspace-local text files, scripts, "
    "and diagnostics that the user requested. Never use filesystem or shell tools "
    "to edit project databases, mutate managed asset files, call provider endpoints, "
    "read secrets, or bypass an AIPic approval or UI action."
)
_IMAGE_TOOL_DECISION_CONTRACT = (
    "Image-tool decision contract: for ordinary generation, variants, transforms, multiview, "
    "and clearly understandable 3D requests, understand a directly attached image yourself; "
    "for a managed image reference, use image.understand_for_agent only when a concrete visual fact is "
    "needed. It returns transient text and does not create a project analysis. Call "
    "image.analyze_content only when the user asks for an explainable or reusable content "
    "specification; call image.analyze_style only when the user asks to analyze, preserve, "
    "compare, or reuse a style; and call image.evaluate_3d_suitability only when the user asks "
    "for, or the task is genuinely uncertain about, 3D readiness. Do not call content and style "
    "analysis together unless the user has distinct requirements. Set refresh=true only when the "
    "user explicitly asks to reanalyze. Do not call a persisted analysis after transient understanding for the "
    "same purpose, or transient understanding after persisted analysis, unless the user asks a new distinct "
    "question. Never use either image-understanding path to locate three-view crop boxes. "
    "Choose an existing background-removal route from the image evidence: image.remove_background_local "
    "with color_key for a flat keyed background, local channel for a "
    "separable channel/luminance/saturation range, Provider remove_background for gradients, "
    "shadows, texture, or foreground-like background colors, and export_transparent for an "
    "already extracted element. Provider operations still require their normal capability and "
    "approval flow and must never silently replace a local operation. Unless exact pixel values "
    "are available from structured evidence, do not guess target_color or tolerance from visual "
    "appearance; omit target_color so color_key derives it from image corners. If background "
    "removal returns review_required because opaque border background remains, choose another "
    "suitable removal attempt or explain the limitation; do not split that result or claim that "
    "transparency is verified."
)
_MANAGED_ATTACHMENT_CONTRACT = (
    "Managed-attachment contract: every attached image is already stored in the current "
    "project. A multimodal model also receives the image pixels in this user message and must "
    "understand them directly. A text-only model receives only these managed references and "
    "must call image.understand_for_agent before making claims about visual content. Infer the role of "
    "each image from the user's request and use the exact source_asset_ref values with managed "
    "managed single-operation Tools as needed. There is no implicit primary image; if the intended mapping is "
    "materially ambiguous, ask the user. Never use read, bash, or a filesystem path for "
    "attachments, and never expose opaque references in user-facing text."
)


@dataclass
class _Conversation:
    harness: AgentHarness
    repository: LinearSessionRepository
    project_id: str
    profile: ModelProfile
    task: asyncio.Task[tuple[Message, ...]] | None = None
    error_code: str | None = None
    planning_cancellation: CancellationToken | None = None


class AgentRuntime:
    """Owns live Agent tasks while SQLite remains the recovery source of truth."""

    def __init__(
        self,
        registry: AIPicToolRegistry,
        root_for: Callable[[str], Path],
        *,
        provider_factory: ProviderFactory | None = None,
        runtime_context_provider: RuntimeContextProvider | None = None,
        planner_context_provider: PlannerContextProvider | None = None,
        attachment_provider: AttachmentProvider | None = None,
        attachment_content_provider: AttachmentContentProvider | None = None,
        agent_model_selector: AgentModelSelector | None = None,
        prompt_creator: PromptCreator | None = None,
        job_completion_broker: Any | None = None,
    ) -> None:
        self._registry = registry
        self._root_for = root_for
        self._provider_factory = provider_factory or _default_provider
        self._runtime_context_provider = runtime_context_provider or _empty_runtime_context
        self._planner_context_provider = planner_context_provider or _empty_planner_context
        self._attachment_provider = attachment_provider
        self._attachment_content_provider = attachment_content_provider
        self._agent_model_selector = agent_model_selector or (lambda: None)
        self._prompt_creator = prompt_creator
        self._job_completion_broker = job_completion_broker
        self._planning = PlanningService(self._provider_factory)
        self._conversations: dict[tuple[str, str], _Conversation] = {}
        self._job_wait_tasks: dict[tuple[str, str], asyncio.Task[Any]] = {}

    def create(
        self, project_id: str, *, system_prompt: str = "", model: str | None = None
    ) -> dict[str, object]:
        root = self._root_for(project_id)
        repository = LinearSessionRepository(root / "agent.sqlite3")
        selected_model = model if model is not None else self._agent_model_selector()
        # Qwen3-VL is the configured local Agent by default. A caller may
        # explicitly select DeepSeek for a new conversation; the selected
        # profile is frozen in that conversation and never silently changed.
        profile = (
            create_qwen3_vl_profile(model=selected_model)
            if selected_model in QWEN3_VL_SUPPORTED_MODELS
            else create_deepseek_profile(model=selected_model)
        )
        session = repository.create(
            system_prompt=_system_prompt_for_project(system_prompt, project_id),
            profile=_profile_dict(profile),
            thinking_level=(
                _QWEN3_VL_DEFAULT_THINKING_LEVEL
                if profile.provider_id == OLLAMA_PROVIDER_ID
                else "off"
            ),
            active_tools=PERMANENT_TOOL_NAMES,
        )
        conversation = self._build(
            project_id, session.id, repository, profile, session.thinking_level
        )
        self._conversations[(project_id, session.id)] = conversation
        self._append_event(
            repository, session.id, "conversation.created", {"conversation_id": session.id}
        )
        return self.status(project_id, session.id)

    def status(self, project_id: str, conversation_id: str) -> dict[str, object]:
        conversation = self._get(project_id, conversation_id)
        session = conversation.repository.open(conversation_id)
        durable_error = conversation.repository.terminal_error_code(conversation_id)
        state = (
            "running" if conversation.task is not None and not conversation.task.done() else "idle"
        )
        error_code = conversation.error_code or durable_error
        if error_code is not None and state != "running":
            state = "error"
        return {
            "id": session.id,
            "project_id": project_id,
            "state": state,
            "message_count": len(session.messages),
            "active_tools": list(session.active_tools),
            "active_skills": list(session.active_skills),
            "error_code": error_code,
        }

    def conversations(self, project_id: str, limit: int | None = None) -> list[dict[str, object]]:
        """List Pi-style durable conversation summaries for the active project."""

        repository = LinearSessionRepository(self._root_for(project_id) / "agent.sqlite3")
        summaries = repository.recent_sessions(limit)
        for summary in summaries:
            conversation = self._conversations.get((project_id, str(summary["id"])))
            error_code = (
                conversation.error_code
                if conversation and conversation.error_code
                else repository.terminal_error_code(str(summary["id"]))
            )
            summary["state"] = (
                "error"
                if error_code is not None
                else "running"
                if conversation is not None
                and conversation.task is not None
                and not conversation.task.done()
                else "idle"
            )
            summary["error_code"] = error_code
            summary["project_id"] = project_id
            summary["preview"] = _safe_text(str(summary["preview"]))[:240]
        return summaries

    async def send(
        self,
        project_id: str,
        conversation_id: str,
        content: str,
        *,
        asset_refs: tuple[str, ...] = (),
        wait: bool = False,
    ) -> dict[str, object]:
        conversation = self._get(project_id, conversation_id)
        if conversation.task is not None and not conversation.task.done():
            raise RuntimeError("conversation_busy")
        wait_service = ApprovedToolJobWait(
            conversation.repository, self._job_completion_broker
        )
        for pending in conversation.repository.active_job_waits(project_id):
            if (
                pending.get("session_id") == conversation_id
                and pending.get("state") == "awaiting_ui_action"
            ):
                wait_service.complete_superseded(pending)
        attachments = self._resolve_attachments(project_id, asset_refs)
        display_content = _safe_text(content)
        model_content = _model_content_with_attachments(display_content, attachments)
        user_message = UserMessage(
            model_content,
            display_content=display_content,
            attachments=attachments,
        )
        self._ensure_final_response_contract(conversation.repository, conversation_id)
        conversation.error_code = None
        # Persist the user turn before the asynchronous Planner starts. This is
        # both the durable source of truth and prevents a message-page refresh
        # from replacing the frontend's optimistic user bubble with an empty page.
        conversation.repository.append_message(conversation_id, user_message)
        self._append_event(
            conversation.repository,
            conversation_id,
            "message.completed",
            {"conversation_id": conversation_id, "message": _message_dto(user_message)},
        )
        self._append_event(
            conversation.repository,
            conversation_id,
            "message.accepted",
            {"conversation_id": conversation_id},
        )
        conversation.task = asyncio.create_task(
            self._run(
                conversation,
                conversation_id,
                user_message,
            ),
            name=f"agent-{conversation_id}",
        )
        # Most desktop sends are intentionally fire-and-poll (``wait=False``).
        # ``_run`` persists a durable failure before re-raising, so consume the
        # finished Task's exception here as well; otherwise asyncio reports
        # "Task exception was never retrieved" even though the UI has already
        # received the matching ``conversation.failed`` event.  Awaiting the
        # same Task for ``wait=True`` still raises normally.
        conversation.task.add_done_callback(_consume_task_exception)
        if wait:
            await conversation.task
        return self.status(project_id, conversation_id)

    def _resolve_attachments(
        self, project_id: str, asset_refs: tuple[str, ...]
    ) -> tuple[ManagedAssetAttachment, ...]:
        if not asset_refs:
            return ()
        if len(asset_refs) > _MAX_AGENT_IMAGE_ATTACHMENTS or len(set(asset_refs)) != len(
            asset_refs
        ):
            raise RuntimeError("agent_attachment_invalid")
        if self._attachment_provider is None:
            raise RuntimeError("agent_attachment_not_found")
        attachments: list[ManagedAssetAttachment] = []
        total_bytes = 0
        for asset_ref in asset_refs:
            try:
                asset = self._attachment_provider(project_id, asset_ref)
            except Exception as error:
                raise RuntimeError("agent_attachment_not_found") from error
            mime_type = str(asset.get("mime_type", ""))
            asset_type = str(asset.get("asset_type", ""))
            if (
                mime_type not in _AGENT_IMAGE_MIME_TYPES
                or asset_type
                not in {
                    "source_image",
                    "generated_image",
                    "annotation",
                    "crop",
                    "multiview",
                    "preview",
                    "texture",
                }
                or asset.get("trashed_at") is not None
            ):
                raise RuntimeError("agent_attachment_not_image")
            size_bytes = asset.get("size_bytes")
            if (
                not isinstance(size_bytes, int)
                or isinstance(size_bytes, bool)
                or not 0 < size_bytes <= _MAX_AGENT_IMAGE_BYTES
            ):
                raise RuntimeError("agent_attachment_invalid")
            total_bytes += size_bytes
            if total_bytes > _MAX_AGENT_IMAGE_REQUEST_BYTES:
                raise RuntimeError("agent_attachment_invalid")
            attachments.append(
                ManagedAssetAttachment(
                    asset_id=asset_ref,
                    name=_safe_text(str(asset.get("name", "Attached image"))),
                    mime_type=mime_type,
                )
            )
        return tuple(attachments)

    async def abort(self, project_id: str, conversation_id: str) -> dict[str, object]:
        conversation = self._get(project_id, conversation_id)
        if conversation.planning_cancellation is not None:
            conversation.planning_cancellation.cancel("Agent run aborted.")
        conversation.harness.agent.abort()
        self._append_event(
            conversation.repository,
            conversation_id,
            "conversation.abort_requested",
            {"conversation_id": conversation_id},
        )
        self._append_event(
            conversation.repository,
            conversation_id,
            "conversation.cancelled",
            {"conversation_id": conversation_id},
        )
        if conversation.task is not None:
            try:
                await conversation.task
            except AgentCancelledError, asyncio.CancelledError:
                pass
        return self.status(project_id, conversation_id)

    def queue(
        self, project_id: str, conversation_id: str, content: str, kind: str
    ) -> dict[str, object]:
        conversation = self._get(project_id, conversation_id)
        if conversation.task is None or conversation.task.done():
            raise RuntimeError("conversation_idle")
        if kind == "steer":
            conversation.harness.agent.steer(content)
        else:
            conversation.harness.agent.follow_up(content)
        return self.status(project_id, conversation_id)

    async def activate_skill(
        self, project_id: str, conversation_id: str, name: str
    ) -> dict[str, object]:
        conversation = self._get(project_id, conversation_id)
        await conversation.harness.skill(name)
        return self.status(project_id, conversation_id)

    def skills(self, project_id: str, conversation_id: str) -> list[dict[str, str]]:
        conversation = self._get(project_id, conversation_id)
        loader = conversation.harness._skill_loader
        if loader is None:
            return []
        return [
            {"name": skill.name, "description": skill.description, "source": skill.source}
            for skill in loader._skills.values()
        ]

    def extensions(self, project_id: str, conversation_id: str) -> dict[str, object]:
        conversation = self._get(project_id, conversation_id)
        registry = conversation.harness.extensions
        return {"disabled": sorted(registry.disabled), "diagnostics": list(registry.diagnostics)}

    def messages(
        self, project_id: str, conversation_id: str, *, limit: int | None = None
    ) -> list[dict[str, object]]:
        conversation = self._get(project_id, conversation_id)
        self._repair_empty_completed_turn(conversation, conversation_id)
        self._ensure_failure_terminal_message(conversation, conversation_id)
        messages = conversation.repository.open(conversation_id).messages
        if limit is not None:
            messages = messages[-limit:]
        return [_message_dto(item) for item in messages]

    def message_page(
        self,
        project_id: str,
        conversation_id: str,
        *,
        limit: int,
        before: int | None = None,
    ) -> dict[str, object]:
        conversation = self._get(project_id, conversation_id)
        self._repair_empty_completed_turn(conversation, conversation_id)
        self._ensure_failure_terminal_message(conversation, conversation_id)
        self._repair_completed_plan(conversation.repository, conversation_id)
        messages, next_before, has_more = conversation.repository.message_page(
            conversation_id,
            before=before,
            limit=limit,
        )
        return {
            "items": [_message_dto(item) for item in messages],
            "next_before": next_before,
            "has_more": has_more,
            "execution_plan": (
                plan.to_dict()
                if (plan := _latest_execution_plan(conversation.repository, conversation_id))
                is not None
                else None
            ),
            # This is a desktop-only control projection. It intentionally is
            # not a ToolResultMessage: the original Tool Call remains open
            # until the approval decision and Job wait return its one terminal
            # model-visible result.
            "pending_ui_actions": conversation.repository.pending_ui_actions(conversation_id),
        }

    async def resume_terminal_waits(self, project_id: str, conversation_id: str) -> None:
        """Recover approved waits without inventing user input or resubmitting work."""

        conversation = self._get(project_id, conversation_id)
        for wait in conversation.repository.resumable_job_waits(conversation_id):
            if wait["state"] == "terminal_returned":
                self._start_terminal_continuation(project_id, wait)
            elif self._job_completion_broker is not None:
                self._start_job_wait_monitor(
                    project_id,
                    ApprovedToolJobWait(
                        conversation.repository,
                        self._job_completion_broker,
                    ),
                    wait,
                )

    def event_cursor(self, project_id: str, conversation_id: str) -> int:
        return self._get(project_id, conversation_id).repository.api_event_cursor(conversation_id)

    def events(
        self, project_id: str, conversation_id: str, after: int = 0, limit: int = 100
    ) -> dict[str, object]:
        conversation = self._get(project_id, conversation_id)
        rows = conversation.repository.api_events(conversation_id, after, limit)
        items = [
            {
                "sequence_no": row["sequence_no"],
                "event_type": row["event_type"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
        return {"items": items, "next_cursor": items[-1]["sequence_no"] if items else after}

    def health(self) -> dict[str, object]:
        return {
            "agent": {
                "state": "ok",
                "conversations": len(self._conversations),
                "provider": "configured",
                "skills": "available",
                "extensions": "available",
                "tools": "available",
            }
        }

    async def _run(
        self, conversation: _Conversation, conversation_id: str, message: UserMessage
    ) -> tuple[Message, ...]:
        try:
            prior_plan = _latest_execution_plan(conversation.repository, conversation_id)
            self._append_event(
                conversation.repository,
                conversation_id,
                "execution.planning.started",
                {"conversation_id": conversation_id},
            )
            planning_cancellation = CancellationToken()
            conversation.planning_cancellation = planning_cancellation
            try:
                plan = await self._planning.prepare(
                    conversation.profile,
                    message,
                    version=(prior_plan.version + 1) if prior_plan is not None else 1,
                    prior=prior_plan,
                    request_transform=lambda request: _with_request_images(
                        _with_reasoning_effort(
                            _with_runtime_context(
                                request,
                                self._planner_context_provider(conversation.project_id),
                            ),
                            conversation.harness.agent.state.thinking_level,
                        ),
                        conversation.project_id,
                        self._attachment_content_provider,
                    ),
                    cancellation=planning_cancellation,
                )
            finally:
                conversation.planning_cancellation = None
            self._append_event(
                conversation.repository,
                conversation_id,
                "execution.plan.updated",
                _plan_event_payload(conversation_id, plan),
            )
            if plan.next_action == "ask_user":
                return self._persist_planning_question(conversation, conversation_id, plan)
            conversation.harness.activate_tools(planner_tool_names(plan))
            result = await conversation.harness.prompt(message)
            return self._finish_harness_result(conversation, conversation_id, result)
        except Exception as error:
            if _safe_error_code(error) == "cancelled":
                return ()
            conversation.error_code = _safe_error_code(error)
            self._ensure_failure_terminal_message(conversation, conversation_id, force=True)
            failure_payload: dict[str, object] = {
                "conversation_id": conversation_id,
                "code": conversation.error_code,
            }
            provider_reason = _safe_provider_reason(error)
            if provider_reason is not None:
                failure_payload["reason"] = provider_reason
            self._append_event(
                conversation.repository,
                conversation_id,
                "conversation.failed",
                failure_payload,
            )
            raise

    def _persist_planning_question(
        self, conversation: _Conversation, conversation_id: str, plan: ExecutionPlan
    ) -> tuple[Message, ...]:
        question_lines = "\n".join(f"- {item}" for item in plan.blocking_questions)
        response = AssistantMessage(
            (
                TextContent(
                    "为确保结果符合你的要求，我需要先确认：\n" + question_lines
                ),
            )
        )
        conversation.repository.append_message(conversation_id, response)
        self._append_event(
            conversation.repository,
            conversation_id,
            "message.completed",
            {"conversation_id": conversation_id, "message": _message_dto(response)},
        )
        return self._finish_harness_result(conversation, conversation_id, (response,))

    def _finish_harness_result(
        self,
        conversation: _Conversation,
        conversation_id: str,
        result: tuple[Message, ...],
    ) -> tuple[Message, ...]:
        if _has_suspended_tool_call(result):
            # Approval is a sideband desktop control event. The matching Tool
            # Result is appended later by ApprovedToolJobWait; no synthetic
            # user message is allowed here.
            self._append_event(
                conversation.repository,
                conversation_id,
                "conversation.suspended",
                {"conversation_id": conversation_id},
            )
            return result
        if not _has_visible_final_response(result):
            conversation.error_code = "empty_final_response"
            self._ensure_failure_terminal_message(conversation, conversation_id, force=True)
            self._append_event(
                conversation.repository,
                conversation_id,
                "conversation.failed",
                {"conversation_id": conversation_id, "code": conversation.error_code},
            )
            return result
        current_plan = _latest_execution_plan(conversation.repository, conversation_id)
        if current_plan is not None:
            completed_plan = current_plan.with_final_response()
            if completed_plan != current_plan:
                self._append_event(
                    conversation.repository,
                    conversation_id,
                    "execution.plan.updated",
                    _plan_event_payload(conversation_id, completed_plan),
                )
        self._append_event(
            conversation.repository,
            conversation_id,
            "conversation.completed",
            {"conversation_id": conversation_id},
        )
        return result

    def _repair_completed_plan(
        self, repository: LinearSessionRepository, conversation_id: str
    ) -> None:
        """Apply final-response plan closure to conversations saved before this rule existed."""

        if repository.terminal_outcome(conversation_id) != "conversation.completed":
            return
        current_plan = _latest_execution_plan(repository, conversation_id)
        if current_plan is None:
            return
        completed_plan = current_plan.with_final_response()
        if completed_plan != current_plan:
            self._append_event(
                repository,
                conversation_id,
                "execution.plan.updated",
                _plan_event_payload(conversation_id, completed_plan),
            )

    def _build(
        self,
        project_id: str,
        conversation_id: str,
        repository: LinearSessionRepository,
        profile: ModelProfile,
        thinking_level: str,
    ) -> _Conversation:
        root = self._root_for(project_id)
        env = LocalExecutionEnv((root,))
        request_id = f"agent-{conversation_id}"
        builtin_tools = cast(
            tuple[AgentTool, ...],
            (
            ReadTool(env),
            WriteTool(env),
            EditTool(env),
            BashTool(env),
            ),
        )
        session = repository.open(conversation_id)
        pinned_tool_names = tuple(
            str(wait["tool_name"])
            for wait in repository.active_job_waits(project_id)
            if wait.get("session_id") == conversation_id
            and isinstance(wait.get("tool_name"), str)
        )
        harness_box: list[AgentHarness] = []
        catalog = build_progressive_tool_catalog(
            builtin_tools,
            self._registry,
            lambda: AIPicToolInvocation(root, project_id, request_id, run_id=conversation_id),
            lambda: self._runtime_context_provider(project_id),
            self._prompt_creator,
            self._job_completion_broker,
            active_names=lambda: (
                harness_box[0].active_tool_names
                if harness_box
                else (*PERMANENT_TOOL_NAMES, *session.active_tools, *pinned_tool_names)
            ),
        )
        active_tool_names = (*PERMANENT_TOOL_NAMES, *session.active_tools, *pinned_tool_names)
        loader = SkillLoader(env, project_roots=(root / ".agent-skills",))
        harness = AgentHarness(
            self._provider_factory(profile),
            profile,
            repository,
            conversation_id,
            tools=(),
            tool_catalog=catalog,
            active_tool_names=active_tool_names,
            skill_loader=loader,
            session_message_sanitizer=_sanitize_message,
            context_projection_transform=_project_model_context,
            provider_request_transform=lambda request: _with_request_images(
                _with_reasoning_effort(
                    _with_plan_context(
                        _with_runtime_context(request, self._runtime_context_provider(project_id)),
                        _latest_execution_plan(repository, conversation_id),
                    ),
                    thinking_level,
                ),
                project_id,
                self._attachment_content_provider,
            ),
            before_tool_call_guard=lambda context: _planned_tool_dependency_guard(
                context,
                _latest_execution_plan(repository, conversation_id),
            ),
            provider_response_transform=lambda message: _ground_model3d_final_response(
                message,
                repository.open(conversation_id).messages,
            ),
            context_window=_model_context_window(profile),
        )
        harness_box.append(harness)
        if harness.active_tool_names != session.active_tools:
            repository.update_config(
                conversation_id,
                active_tools_json=json.dumps(harness.active_tool_names),
            )
        harness.agent.state.thinking_level = thinking_level
        conversation = _Conversation(harness, repository, project_id, profile)
        plan_steps_by_tool_call: dict[str, str] = {}

        async def persist(event: AgentEvent) -> None:
            if event.type is AgentEventType.TOOL_EXECUTION_START:
                tool_call_id = event.payload.get("tool_call_id")
                tool_name = event.payload.get("tool_name")
                arguments = event.payload.get("arguments")
                plan_step_id = (
                    arguments.get("plan_step_id") if isinstance(arguments, dict) else None
                )
                current_plan = _latest_execution_plan(repository, conversation_id)
                if current_plan is not None and not current_plan.fallback:
                    updated_plan = (
                        current_plan.with_step_running(plan_step_id, tool_name=tool_name)
                        if isinstance(plan_step_id, str) and isinstance(tool_name, str)
                        else current_plan.with_tool_running(tool_name)
                        if isinstance(tool_name, str)
                        else current_plan
                    )
                    if (
                        isinstance(tool_call_id, str)
                        and isinstance(plan_step_id, str)
                        and updated_plan != current_plan
                    ):
                        plan_steps_by_tool_call[tool_call_id] = plan_step_id
                    if updated_plan != current_plan:
                        self._append_event(
                            repository,
                            conversation_id,
                            "execution.plan.updated",
                            _plan_event_payload(conversation_id, updated_plan),
                        )
            if event.type is AgentEventType.TOOL_EXECUTION_END:
                raw = event.payload.get("result")
                details = raw.get("details") if isinstance(raw, dict) else None
                tool_call_id = event.payload.get("tool_call_id")
                tool_name = event.payload.get("tool_name")
                status = details.get("status") if isinstance(details, dict) else None
                failed = bool(event.payload.get("is_error", False)) or status in {
                    "failed",
                    "cancelled",
                    "declined",
                    "interrupted",
                }
                attempt_warning = _tool_attempt_warning(raw) if failed else None
                waiting_for_user = _tool_result_waits_for_user(details)
                plan_step_id = (
                    plan_steps_by_tool_call.pop(tool_call_id, None)
                    if isinstance(tool_call_id, str)
                    else None
                )
                current_plan = _latest_execution_plan(repository, conversation_id)
                if current_plan is not None and not current_plan.fallback:
                    verification = details.get("verification") if isinstance(details, dict) else None
                    if status in _NONTERMINAL_TOOL_STATUSES:
                        updated_plan = (
                            current_plan.with_step_running(plan_step_id, tool_name=tool_name)
                            if plan_step_id is not None and isinstance(tool_name, str)
                            else current_plan.with_tool_running(tool_name)
                            if isinstance(tool_name, str)
                            else current_plan
                        )
                    elif plan_step_id is not None:
                        updated_plan = current_plan.with_step_result(
                            plan_step_id,
                            failed=failed,
                            verification=verification if isinstance(verification, dict) else None,
                            tool_name=tool_name if isinstance(tool_name, str) else None,
                            tool_call_id=(
                                tool_call_id if isinstance(tool_call_id, str) else None
                            ),
                            attempt_warning=attempt_warning,
                            waiting_for_user=waiting_for_user,
                        )
                    else:
                        updated_plan = (
                            current_plan.with_tool_result(
                                tool_name,
                                failed=failed,
                                verification=(
                                    verification if isinstance(verification, dict) else None
                                ),
                                tool_call_id=(
                                    tool_call_id if isinstance(tool_call_id, str) else None
                                ),
                                attempt_warning=attempt_warning,
                                waiting_for_user=waiting_for_user,
                            )
                            if isinstance(tool_name, str)
                            else current_plan
                        )
                    if updated_plan != current_plan:
                        self._append_event(
                            repository,
                            conversation_id,
                            "execution.plan.updated",
                            _plan_event_payload(conversation_id, updated_plan),
                        )
                if isinstance(details, dict) and details.get("status") == "awaiting_ui_action":
                    tool_call_id = event.payload.get("tool_call_id")
                    tool_name = event.payload.get("tool_name")
                    if isinstance(tool_call_id, str) and isinstance(tool_name, str):
                        repository.register_job_wait(
                            conversation_id,
                            project_id=project_id,
                            run_id=conversation_id,
                            tool_call_id=tool_call_id,
                            tool_name=tool_name,
                        )
            projected = _event_dto(conversation_id, event)
            if projected is not None:
                self._append_event(repository, conversation_id, projected[0], projected[1])

        harness.subscribe(persist)
        return conversation

    async def resolve_approval(
        self, project_id: str, approval_id: str, result: Any
    ) -> None:
        """Bind an approval decision to its suspended Tool Call, if it is Agent-owned."""

        repository = LinearSessionRepository(self._root_for(project_id) / "agent.sqlite3")
        wait = repository.job_wait_for_approval(project_id, approval_id)
        if wait is None:
            return
        tool_call_id = str(wait["tool_call_id"])
        service = ApprovedToolJobWait(repository, self._job_completion_broker)
        if getattr(result, "status", None) == "queued":
            job = getattr(result, "job", None) or {}
            job_id = job.get("job_id") if isinstance(job, dict) else None
            if not isinstance(job_id, str):
                return
            if wait["state"] == "terminal_returned":
                self._start_terminal_continuation(project_id, wait)
                return
            if not repository.bind_job_wait(str(wait["session_id"]), tool_call_id, job_id):
                return
            updated = repository.job_wait_for_tool(project_id, tool_call_id)
            if updated is not None and self._job_completion_broker is not None:
                self._start_job_wait_monitor(project_id, service, updated)
            return
        if getattr(result, "status", None) == "failed":
            service.complete_declined(wait)
            updated = repository.job_wait_for_tool(project_id, tool_call_id)
            if updated is not None:
                self._start_terminal_continuation(project_id, updated)

    async def complete_multiview_ui_action(
        self,
        project_id: str,
        action_id: str,
        multiview_ref: str,
        view_asset_refs: dict[str, str],
    ) -> dict[str, object]:
        """Close one suspended region-confirmation call from persisted desktop state."""

        if set(view_asset_refs) != {"front", "side", "back"} or len(
            set(view_asset_refs.values())
        ) != 3:
            raise ValueError("three distinct view assets are required")
        project_root = self._root_for(project_id)
        multiviews = MultiviewRepository()
        if not multiviews.is_ready_for_submission(
            project_root / "project.sqlite3",
            set_id=multiview_ref,
            members=view_asset_refs,
        ):
            raise ValueError("multiview crops are not persistently confirmed")
        repository = LinearSessionRepository(project_root / "agent.sqlite3")
        wait = repository.job_wait_for_ui_action(
            project_id, action_id, "confirm_multiview_regions"
        )
        if wait is None:
            raise KeyError(action_id)
        session_id = str(wait["session_id"])
        tool_call_id = str(wait["tool_call_id"])
        tool_name = str(wait["tool_name"])
        if not repository.complete_job_wait(session_id, tool_call_id, "terminal_returned"):
            raise ValueError("desktop action is no longer pending")
        output_refs = [view_asset_refs[name] for name in ("front", "side", "back")]
        details: dict[str, object] = {
            "status": "succeeded",
            "output_asset_ids": output_refs,
            "output_asset_refs": output_refs,
            "multiview_ref": multiview_ref,
            "view_asset_refs": dict(view_asset_refs),
            "persisted_confirmation": True,
        }
        model_binding = json.dumps(
            {
                "multiview_ref": multiview_ref,
                "view_asset_refs": view_asset_refs,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        repository.append_message(
            session_id,
            ToolResultMessage(
                tool_call_id,
                tool_name,
                ToolResult(
                    (
                        TextContent(
                            "The user confirmed the persisted multiview crops. "
                            "This is the authoritative binding for the next "
                            "model3d.generate_from_multiview call; use these exact values directly "
                            "and do not rediscover, substitute, or repair them through project.get_state, "
                            "asset.list, filenames, or image analysis.\n"
                            f"confirmed_multiview={model_binding}"
                        ),
                    ),
                    details=details,
                ),
            ),
        )
        updated = repository.job_wait_for_tool(project_id, tool_call_id)
        if updated is not None:
            self._start_terminal_continuation(project_id, updated)
        return {"status": "succeeded", **details}

    def _start_job_wait_monitor(
        self,
        project_id: str,
        service: ApprovedToolJobWait,
        wait: dict[str, object],
    ) -> None:
        tool_call_id = str(wait["tool_call_id"])
        key = (project_id, tool_call_id)
        existing = self._job_wait_tasks.get(key)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(
            self._wait_and_continue_after_approval(project_id, service, wait),
            name=f"agent-approved-job-wait-{wait.get('job_id') or tool_call_id}",
        )
        self._job_wait_tasks[key] = task

        def completed(done: asyncio.Task[Any]) -> None:
            if self._job_wait_tasks.get(key) is done:
                self._job_wait_tasks.pop(key, None)
            _consume_task_exception(done)

        task.add_done_callback(completed)

    async def _wait_and_continue_after_approval(
        self, project_id: str, service: ApprovedToolJobWait, wait: dict[str, object]
    ) -> None:
        while True:
            await service.wait_and_complete(wait, project_root=self._root_for(project_id))
            repository = LinearSessionRepository(self._root_for(project_id) / "agent.sqlite3")
            updated = repository.job_wait_for_tool(project_id, str(wait["tool_call_id"]))
            if updated is None:
                return
            if updated["state"] == "terminal_returned":
                self._start_terminal_continuation(project_id, updated)
                return
            if updated["state"] != "waiting_external":
                return
            # Re-arm another bounded repository wait. The durable record remains
            # recoverable across application restarts and there is no overall
            # time limit on a valid approved Job.
            wait = updated

    def _start_terminal_continuation(self, project_id: str, wait: dict[str, object]) -> None:
        conversation_id = str(wait["session_id"])
        tool_call_id = str(wait["tool_call_id"])
        conversation = self._get(project_id, conversation_id)
        if conversation.task is not None and not conversation.task.done():
            return
        messages = conversation.repository.open(conversation_id).messages
        if not messages or not isinstance(messages[-1], ToolResultMessage):
            return
        if messages[-1].tool_call_id != tool_call_id:
            return
        details = messages[-1].result.details
        recommended_action = (
            details.get("recommended_action") if isinstance(details, dict) else None
        )
        if recommended_action in {"job.retry", "job.confirm_new_submission"}:
            conversation.harness.activate_tools((str(recommended_action),))
        self._project_terminal_result_to_plan(
            conversation.repository,
            conversation_id,
            messages[-1],
        )
        conversation.task = asyncio.create_task(
            self._continue_after_terminal_result(conversation, conversation_id),
            name=f"agent-terminal-tool-result-{tool_call_id}",
        )
        conversation.task.add_done_callback(_consume_task_exception)

    def _project_terminal_result_to_plan(
        self,
        repository: LinearSessionRepository,
        conversation_id: str,
        message: ToolResultMessage,
    ) -> None:
        current_plan = _latest_execution_plan(repository, conversation_id)
        if current_plan is None or current_plan.fallback:
            return
        details = message.result.details
        verification = details.get("verification") if isinstance(details, dict) else None
        status = details.get("status") if isinstance(details, dict) else None
        failed = message.is_error or status in {
            "failed",
            "cancelled",
            "declined",
            "interrupted",
        }
        updated_plan = current_plan.with_tool_result(
            message.tool_name,
            failed=failed,
            verification=verification if isinstance(verification, dict) else None,
            tool_call_id=message.tool_call_id,
            attempt_warning=(
                _tool_attempt_warning(message.result.to_dict()) if failed else None
            ),
            waiting_for_user=_tool_result_waits_for_user(details),
        )
        if updated_plan != current_plan:
            self._append_event(
                repository,
                conversation_id,
                "execution.plan.updated",
                _plan_event_payload(conversation_id, updated_plan),
            )

    async def _continue_after_terminal_result(
        self, conversation: _Conversation, conversation_id: str
    ) -> tuple[Message, ...]:
        try:
            result = await conversation.harness.continue_run()
            return self._finish_harness_result(conversation, conversation_id, result)
        except Exception as error:
            if _safe_error_code(error) == "cancelled":
                return ()
            conversation.error_code = _safe_error_code(error)
            self._ensure_failure_terminal_message(conversation, conversation_id, force=True)
            self._append_event(
                conversation.repository,
                conversation_id,
                "conversation.failed",
                {"conversation_id": conversation_id, "code": conversation.error_code},
            )
            raise

    def _repair_empty_completed_turn(
        self, conversation: _Conversation, conversation_id: str
    ) -> None:
        """Repair earlier successful events whose final assistant message was empty."""

        if conversation.task is not None and not conversation.task.done():
            return
        if conversation.repository.terminal_outcome(conversation_id) != "conversation.completed":
            return
        messages = conversation.repository.open(conversation_id).messages
        if _has_visible_final_response(messages):
            return
        conversation.error_code = "empty_final_response"
        self._ensure_failure_terminal_message(conversation, conversation_id)
        self._append_event(
            conversation.repository,
            conversation_id,
            "conversation.failed",
            {"conversation_id": conversation_id, "code": conversation.error_code},
        )

    def _ensure_failure_terminal_message(
        self, conversation: _Conversation, conversation_id: str, *, force: bool = False
    ) -> None:
        """Give interrupted transcripts a durable, user-visible final turn.

        Older runs can have a terminal ``conversation.failed`` event after the
        core loop's idle event, leaving their last persisted message as a tool
        result.  Repair those transcripts lazily on recovery as well as during
        new failures, so reopening an existing conversation never looks stuck.
        """

        if not force and conversation.task is not None and not conversation.task.done():
            return
        if (
            conversation.error_code is None
            and conversation.repository.terminal_error_code(conversation_id) is None
        ):
            return
        messages = conversation.repository.open(conversation_id).messages
        if (
            messages
            and isinstance(messages[-1], AssistantMessage)
            and messages[-1].stop_reason == "error"
        ):
            return
        terminal_message = _failure_terminal_message()
        conversation.repository.append_message(conversation_id, terminal_message)
        self._append_event(
            conversation.repository,
            conversation_id,
            "message.completed",
            {"conversation_id": conversation_id, "message": _message_dto(terminal_message)},
        )

    def _get(self, project_id: str, conversation_id: str) -> _Conversation:
        key = (project_id, conversation_id)
        existing = self._conversations.get(key)
        if existing is not None:
            return existing
        root = self._root_for(project_id)
        repository = LinearSessionRepository(root / "agent.sqlite3")
        session = repository.open(conversation_id)
        profile, profile_updated = _upgrade_profile_defaults(_profile_from_dict(session.profile))
        thinking_level = session.thinking_level
        thinking_updated = False
        if profile.provider_id == OLLAMA_PROVIDER_ID and thinking_level == "off":
            # Qwen thinking was previously enabled implicitly by Ollama while this
            # durable field was never wired to the request. Preserve that behavior
            # explicitly for recovered local-model conversations.
            thinking_level = _QWEN3_VL_DEFAULT_THINKING_LEVEL
            thinking_updated = True
        if profile_updated or thinking_updated:
            repository.update_config(
                conversation_id,
                **(
                    {"profile_json": json.dumps(_profile_dict(profile))}
                    if profile_updated
                    else {}
                ),
                **({"thinking_level": thinking_level} if thinking_updated else {}),
            )
        conversation = self._build(
            project_id, conversation_id, repository, profile, thinking_level
        )
        self._conversations[key] = conversation
        return conversation

    @staticmethod
    def _ensure_final_response_contract(
        repository: LinearSessionRepository, conversation_id: str
    ) -> None:
        """Upgrade recovered conversations before their next provider request."""

        session = repository.open(conversation_id)
        if (
            _FINAL_RESPONSE_CONTRACT in session.system_prompt
            and _ASYNC_JOB_CONTRACT in session.system_prompt
            and _ARTIFACT_VERIFICATION_CONTRACT in session.system_prompt
            and _PLANNING_EXECUTION_CONTRACT in session.system_prompt
            and _IMAGE_PRESENTATION_CONTRACT in session.system_prompt
            and _TOOL_SELECTION_CONTRACT in session.system_prompt
            and _IMAGE_TOOL_DECISION_CONTRACT in session.system_prompt
        ):
            return
        prompt = session.system_prompt.strip()
        additions = tuple(
            instruction
            for instruction in (
                _ASYNC_JOB_CONTRACT,
                _ARTIFACT_VERIFICATION_CONTRACT,
                _PLANNING_EXECUTION_CONTRACT,
                _IMAGE_PRESENTATION_CONTRACT,
                _TOOL_SELECTION_CONTRACT,
                _IMAGE_TOOL_DECISION_CONTRACT,
                _FINAL_RESPONSE_CONTRACT,
            )
            if instruction not in prompt
        )
        repository.update_config(
            conversation_id,
            system_prompt="\n\n".join((prompt, *additions)) if prompt else "\n\n".join(additions),
        )

    @staticmethod
    def _append_event(
        repository: LinearSessionRepository,
        conversation_id: str,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        repository.append_api_event(conversation_id, event_type, payload)


def _planned_tool_dependency_guard(
    context: BeforeToolCallContext,
    plan: ExecutionPlan | None,
) -> BeforeToolCallResult | None:
    """Prevent 3D dispatch while the plan's image preparation is unfinished."""

    if context.tool_call.name not in {
        "model3d.generate_from_image",
        "model3d.generate_from_multiview",
        "generate_model3d",
    } or plan is None or plan.fallback:
        return None
    target_index = next(
        (
            index
            for index, step in enumerate(plan.steps)
            if step.tool_name == context.tool_call.name
            or step.operation == "generate_model3d"
        ),
        None,
    )
    if target_index is None:
        return None
    incomplete = [
        step
        for step in plan.steps[:target_index]
        if step.state != "succeeded" and step.operation != "ask_user"
    ]
    if not incomplete:
        return None
    labels = ", ".join(step.label for step in incomplete[:3])
    return BeforeToolCallResult(
        True,
        "3D generation is blocked until the planned image preparation and verification "
        f"steps succeed: {labels}. Use the exact output asset from those steps.",
    )


def _ground_model3d_final_response(
    message: AssistantMessage,
    prior_messages: list[Message] | tuple[Message, ...],
) -> AssistantMessage | None:
    """Replace unsupported semantic claims after a newly completed 3D Job."""

    if message.stop_reason != "stop" or any(
        isinstance(block, ToolCall) for block in message.content
    ):
        return None
    last_user_index = max(
        (
            index
            for index, item in enumerate(prior_messages)
            if isinstance(item, UserMessage)
        ),
        default=-1,
    )
    completed: ToolResultMessage | None = None
    facts: dict[str, object] | None = None
    for item in prior_messages[last_user_index + 1 :]:
        if not isinstance(item, ToolResultMessage) or item.tool_name not in {
            "model3d.generate_from_image",
            "model3d.generate_from_multiview",
            "generate_model3d",
        }:
            continue
        details = item.result.details
        candidate = details.get("artifact_facts") if isinstance(details, dict) else None
        status = details.get("status") if isinstance(details, dict) else None
        if not item.is_error and status == "succeeded" and isinstance(candidate, dict):
            completed = item
            facts = candidate
    if completed is None or facts is None or facts.get("semantic_match") == "verified":
        return None
    details = completed.result.details
    refs = details.get("output_asset_ids") if isinstance(details, dict) else None
    output_ref = next(
        (item for item in refs if isinstance(item, str) and item),
        None,
    ) if isinstance(refs, list) else None
    user = prior_messages[last_user_index] if last_user_index >= 0 else None
    user_text = (
        user.display_content or (user.content if isinstance(user.content, str) else "")
        if isinstance(user, UserMessage)
        else ""
    )
    chinese = bool(re.search(r"[\u3400-\u9fff]", user_text))
    local_vertex_color = (
        facts.get("pbr") is False and facts.get("material_mode") == "vertex_color"
    )
    if chinese:
        text = (
            "3D 生成任务已完成并创建了模型资产。当前工具证据只能确认文件生成成功；"
            "主体是否匹配、风格是否符合，以及去除武器等局部修改是否准确，尚未经过视觉验证。"
        )
        if local_vertex_color:
            text += "本次本地 TripoSR 输出使用顶点颜色，不包含 PBR 材质贴图。"
        if output_ref is not None:
            text += f"\n\n![3D 模型](asset:{output_ref})"
    else:
        text = (
            "The 3D generation job completed and created a model asset. Tool evidence currently "
            "confirms file creation only; subject identity, style fidelity, and localized edits "
            "such as weapon removal have not been visually verified."
        )
        if local_vertex_color:
            text += " This local TripoSR output uses vertex colors and does not include PBR maps."
        if output_ref is not None:
            text += f"\n\n![3D model](asset:{output_ref})"
    return replace(message, content=(TextContent(text),))


def _latest_execution_plan(
    repository: LinearSessionRepository, conversation_id: str
) -> ExecutionPlan | None:
    payload = repository.latest_api_event_payload(conversation_id, "execution.plan.updated")
    return ExecutionPlan.from_dict(payload.get("plan")) if isinstance(payload, dict) else None


def _plan_event_payload(conversation_id: str, plan: ExecutionPlan) -> dict[str, object]:
    return {"conversation_id": conversation_id, "plan": plan.to_dict()}


def _execution_outline_from_message(value: object) -> dict[str, object] | None:
    """Extract a model-authored outline without making it a protocol requirement."""

    if not isinstance(value, dict) or value.get("role") != "assistant":
        return None
    content = value.get("content")
    texts: list[str] = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(
                item.get("text"), str
            ):
                texts.append(item["text"])
    for text in texts:
        matched = re.search(
            r"<execution_outline>\s*(?P<body>.*?)\s*</execution_outline>",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if matched is None:
            continue
        body = matched.group("body").strip()
        if not body:
            continue
        fields: dict[str, str] = {}
        for line in body.splitlines():
            key, separator, field_value = line.partition(":")
            if separator and key.strip().casefold() in {
                "goal",
                "steps",
                "current",
                "constraints",
            }:
                fields[key.strip().casefold()] = field_value.strip()
        return {
            "schema_version": 1,
            "raw": body[:4_096],
            "fields": fields,
            "parse_state": "structured" if fields else "unstructured",
        }
    return None


def _default_provider(profile: ModelProfile) -> AgentModelProvider:
    if profile.provider_id == OLLAMA_PROVIDER_ID:
        return OpenAICompletionsProvider(
            create_ollama_credential_resolver(),
            include_stream_usage=False,
            enforce_loopback=True,
        )
    if profile.provider_id == "deepseek":
        return OpenAICompletionsProvider(create_deepseek_credential_resolver())
    raise ProviderError(f"Unsupported Agent provider profile: {profile.provider_id}")


def _model_context_window(profile: ModelProfile) -> int:
    if profile.provider_id == OLLAMA_PROVIDER_ID:
        return qwen3_vl_context_window(profile.model)
    if profile.provider_id == "deepseek":
        return deepseek_context_window(profile.model)
    raise ProviderError(f"Unsupported Agent provider profile: {profile.provider_id}")


def _profile_supports_direct_image_input(profile: ModelProfile) -> bool:
    """Return whether this frozen conversation profile accepts image content directly."""

    return (
        profile.provider_id == OLLAMA_PROVIDER_ID
        and profile.model in QWEN3_VL_SUPPORTED_MODELS
    )


def _empty_runtime_context(_project_id: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "configuration_state": "unavailable",
        "capabilities": {},
        "jobs": {"nonterminal": []},
    }


def _empty_planner_context(_project_id: str) -> dict[str, object]:
    """Return neutral planning capability hints, never a project-history dump."""

    return {
        "schema_version": 1,
        "configuration_state": "current",
        "planner_capabilities": {
            "local_2d": [
                "remove_background_local",
                "split_grid_local",
                "split_alpha_components_local",
            ],
            "reference_image_transform": "preserve the supplied input image",
        },
    }


def _consume_task_exception(task: asyncio.Task[Any]) -> None:
    """Mark a completed background Agent failure as observed by asyncio."""

    if task.cancelled():
        return
    task.exception()


def _with_runtime_context(request: ModelRequest, snapshot: dict[str, object]) -> ModelRequest:
    """Add a fresh, non-persistent host snapshot to every provider request."""

    payload = json.dumps(
        _safe_json(snapshot),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    runtime_message = SystemMessage(
        "Runtime context (read-only host truth; never guess missing values or expose opaque "
        f"references to the user):\n{payload}"
    )
    messages = request.messages
    if messages and isinstance(messages[0], SystemMessage):
        messages = (messages[0], runtime_message, *messages[1:])
    else:
        messages = (runtime_message, *messages)
    return replace(request, messages=messages)


def _with_plan_context(request: ModelRequest, plan: ExecutionPlan | None) -> ModelRequest:
    """Inject durable plan state on every executor turn without altering transcripts."""

    if plan is None or plan.fallback:
        return request
    payload = json.dumps(plan.to_dict(), ensure_ascii=False, separators=(",", ":"))
    plan_message = SystemMessage(
        "Execution plan snapshot (read-only host truth; preserve its constraints and use "
        f"Tool Result facts to update its outcome):\n{payload}"
    )
    messages = request.messages
    if messages and isinstance(messages[0], SystemMessage):
        messages = (messages[0], plan_message, *messages[1:])
    else:
        messages = (plan_message, *messages)
    return replace(request, messages=messages)


def _with_reasoning_effort(request: ModelRequest, thinking_level: str) -> ModelRequest:
    """Enable Ollama reasoning without changing DeepSeek's wire contract."""

    if request.profile.provider_id != OLLAMA_PROVIDER_ID:
        return request
    normalized = thinking_level.strip().lower()
    effort = {
        "off": "none",
        "none": "none",
        "low": "low",
        "medium": "medium",
        "high": "high",
        "max": "max",
    }.get(normalized, _QWEN3_VL_DEFAULT_THINKING_LEVEL)
    return replace(request, reasoning_effort=cast(Any, effort))


def _with_request_images(
    request: ModelRequest,
    project_id: str,
    content_provider: AttachmentContentProvider | None,
) -> ModelRequest:
    """Hydrate managed image metadata only for a single local Provider request."""

    if not _profile_supports_direct_image_input(request.profile):
        return request
    attachment_count = sum(
        len(message.attachments) for message in request.messages if isinstance(message, UserMessage)
    )
    if attachment_count == 0:
        return request
    if attachment_count > _MAX_AGENT_IMAGE_ATTACHMENTS or content_provider is None:
        raise RuntimeError("agent_attachment_invalid")

    total_bytes = 0
    hydrated: list[Message] = []
    for message in request.messages:
        if not isinstance(message, UserMessage) or not message.attachments:
            hydrated.append(message)
            continue
        blocks: list[TextContent | ImageContent] = []
        if isinstance(message.content, str):
            blocks.append(TextContent(message.content))
        else:
            blocks.extend(item for item in message.content if isinstance(item, TextContent))
        for attachment in message.attachments:
            try:
                image_bytes, mime_type = content_provider(project_id, attachment.asset_id)
            except Exception as error:
                raise RuntimeError("agent_attachment_not_found") from error
            if (
                mime_type != attachment.mime_type
                or mime_type not in _AGENT_IMAGE_MIME_TYPES
                or not 0 < len(image_bytes) <= _MAX_AGENT_IMAGE_BYTES
            ):
                raise RuntimeError("agent_attachment_invalid")
            total_bytes += len(image_bytes)
            if total_bytes > _MAX_AGENT_IMAGE_REQUEST_BYTES:
                raise RuntimeError("agent_attachment_invalid")
            _validate_agent_image(image_bytes, mime_type)
            blocks.append(
                ImageContent(
                    base64.b64encode(image_bytes).decode("ascii"),
                    mime_type,
                )
            )
        hydrated.append(replace(message, content=tuple(blocks)))
    return replace(request, messages=tuple(hydrated))


def _validate_agent_image(image_bytes: bytes, mime_type: str) -> None:
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            width, height = image.size
            if (
                image.format != _AGENT_IMAGE_FORMATS[mime_type]
                or width <= 0
                or height <= 0
                or width > _MAX_AGENT_IMAGE_DIMENSION
                or height > _MAX_AGENT_IMAGE_DIMENSION
                or width * height > _MAX_AGENT_IMAGE_PIXELS
            ):
                raise RuntimeError("agent_attachment_invalid")
            image.verify()
    except RuntimeError:
        raise
    except (Image.DecompressionBombError, OSError, ValueError) as error:
        raise RuntimeError("agent_attachment_invalid") from error


def _system_prompt_for_project(system_prompt: str, project_id: str) -> str:
    """Add non-optional runtime rules to the caller-supplied system prompt.

    The runtime already scopes every AIPic invocation to ``project_id``.  Some
    read tools nevertheless retain ``project_id`` in their public schemas so
    that the same manifest can be used by the HTTP API.  Without this context
    the model guesses values such as ``default``; ``project.get_state`` then
    fails before a multi-turn workflow can make progress.
    """

    base = _safe_text(system_prompt).strip()
    project_instruction = (
        "This conversation is already bound to the current managed project. "
        "Never invent or pass a project ID, provider profile, provider model, local path, "
        "host capability ID, credential, URL, or approval decision to a managed Tool. "
        f"The host-only project binding is {project_id}; do not repeat it in user-facing text."
    )
    instructions = (
        f"{project_instruction}\n\n{_PLANNING_EXECUTION_CONTRACT}\n\n{_ARTIFACT_VERIFICATION_CONTRACT}\n\n{_ASYNC_JOB_CONTRACT}\n\n"
        f"{_IMAGE_PRESENTATION_CONTRACT}\n\n{_TOOL_SELECTION_CONTRACT}\n\n"
        f"{_IMAGE_TOOL_DECISION_CONTRACT}\n\n"
        f"{_FINAL_RESPONSE_CONTRACT}"
    )
    return f"{base}\n\n{instructions}" if base else instructions


def _profile_dict(profile: ModelProfile) -> dict[str, object]:
    return {
        "provider_id": profile.provider_id,
        "model": profile.model,
        "base_url": profile.base_url,
        "credential_ref": profile.credential_ref,
        "timeout_seconds": profile.timeout_seconds,
        "max_output_tokens": profile.max_output_tokens,
    }


def _profile_from_dict(value: dict[str, Any]) -> ModelProfile:
    return ModelProfile(
        provider_id=str(value["provider_id"]),
        model=str(value["model"]),
        base_url=str(value["base_url"]),
        credential_ref=str(value["credential_ref"]) if value.get("credential_ref") else None,
        timeout_seconds=float(value.get("timeout_seconds", 60)),
        max_output_tokens=int(value["max_output_tokens"])
        if value.get("max_output_tokens") is not None
        else None,
    )


def _upgrade_profile_defaults(profile: ModelProfile) -> tuple[ModelProfile, bool]:
    """Replace obsolete Provider token budgets when reopening durable sessions."""

    if profile.provider_id == "deepseek" and profile.max_output_tokens == 256:
        return (
            create_deepseek_profile(
                base_url=profile.base_url,
                model=profile.model,
                timeout_seconds=profile.timeout_seconds,
            ),
            True,
        )
    if (
        profile.provider_id == OLLAMA_PROVIDER_ID
        and profile.model in QWEN3_VL_SUPPORTED_MODELS
        and profile.max_output_tokens in _LEGACY_QWEN3_VL_MAX_OUTPUT_TOKENS
    ):
        return (
            create_qwen3_vl_profile(
                base_url=profile.base_url,
                model=profile.model,
                timeout_seconds=(
                    QWEN3_VL_DEFAULT_TIMEOUT_SECONDS
                    if profile.timeout_seconds == _LEGACY_QWEN3_VL_TIMEOUT_SECONDS
                    else profile.timeout_seconds
                ),
            ),
            True,
        )
    return profile, False


def _event_dto(conversation_id: str, event: AgentEvent) -> tuple[str, dict[str, object]] | None:
    payload = event.payload
    base: dict[str, object] = {"conversation_id": conversation_id}
    if event.type is AgentEventType.MESSAGE_START:
        provider = payload.get("provider_event")
        if isinstance(provider, dict) and provider.get("type") == "message_start":
            return "message.started", base
    if event.type is AgentEventType.MESSAGE_UPDATE:
        provider = payload.get("provider_event")
        if not isinstance(provider, dict):
            return None
        if provider.get("type") == "text_delta":
            return "message.delta", {**base, "text": _safe_text(str(provider.get("delta", "")))}
        if provider.get("type") == "reasoning_start":
            return "reasoning.started", base
        if provider.get("type") == "reasoning_delta":
            return "reasoning.delta", {
                **base,
                "text": _safe_text(str(provider.get("delta", ""))),
            }
        if provider.get("type") == "reasoning_end":
            return "reasoning.completed", base
        if provider.get("type") in {
            "tool_call_start",
            "tool_call_arguments_delta",
            "tool_call_end",
        }:
            call = provider.get("tool_call")
            if isinstance(call, dict):
                return "tool.call", {
                    **base,
                    "phase": str(provider["type"]),
                    "tool_call": _tool_call_dto_from_raw(call),
                }
    if event.type is AgentEventType.MESSAGE_END and isinstance(payload.get("message"), dict):
        return "message.completed", {**base, "message": _message_dto_from_raw(payload["message"])}
    if event.type in {AgentEventType.TOOL_EXECUTION_START, AgentEventType.TOOL_EXECUTION_UPDATE}:
        return "tool.running", {
            **base,
            "tool_call_id": str(payload.get("tool_call_id", "")),
            "tool_name": str(payload.get("tool_name", "")),
            "arguments": _safe_json(payload.get("arguments", {})),
        }
    if event.type is AgentEventType.TOOL_EXECUTION_END:
        return "tool.completed", {
            **base,
            "tool_call_id": str(payload.get("tool_call_id", "")),
            "tool_name": str(payload.get("tool_name", "")),
            "is_error": bool(payload.get("is_error", False)),
            "result": _tool_result_dto_from_raw(payload.get("result")),
        }
    if event.type is AgentEventType.AGENT_END:
        return "agent.idle", base
    return None


def _message_dto(message: Message) -> dict[str, object]:
    if isinstance(message, UserMessage):
        content = (
            _safe_text(message.display_content)
            if message.display_content is not None
            else _safe_content(message.content)
        )
        return {
            "id": message.id,
            "role": "user",
            "content": content,
            "attachments": [
                {
                    "asset_id": item.asset_id,
                    "name": _safe_text(item.name),
                    "mime_type": item.mime_type,
                }
                for item in message.attachments
            ],
        }
    if isinstance(message, AssistantMessage):
        return {
            "id": message.id,
            "role": "assistant",
            # Keep provider reasoning in the private transcript for Tool-call
            # signatures, but never expose it through the desktop API.
            "content": [
                _content_block_dto(item)
                for item in message.content
                if not isinstance(item, ThinkingContent)
            ],
            "stop_reason": message.stop_reason,
            "error_message": _safe_text(message.error_message) if message.error_message else None,
        }
    if isinstance(message, ToolResultMessage):
        return {
            "id": message.id,
            "role": "tool_result",
            "tool_call_id": message.tool_call_id,
            "tool_name": message.tool_name,
            "content": [_content_block_dto(item) for item in message.content],
            "is_error": message.is_error,
            "details": _safe_json(message.result.details),
            **(
                {"added_tool_names": list(message.result.added_tool_names)}
                if message.result.added_tool_names
                else {}
            ),
        }
    return {"id": message.id, "role": message.role, "content": ""}


def _message_dto_from_raw(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {"role": "unknown", "content": ""}
    return _message_dto(message_from_dict(cast(dict[str, Any], value)))


def _content_block_dto(item: object) -> dict[str, object]:
    if isinstance(item, TextContent):
        return {"type": "text", "text": _safe_text(item.text)}
    if isinstance(item, ThinkingContent):
        return {
            "type": "thinking",
            "thinking": _safe_text(item.thinking),
            "redacted": bool(item.redacted),
        }
    if isinstance(item, ToolCall):
        return {
            "type": "tool_call",
            "id": item.id,
            "name": item.name,
            "arguments": _safe_json(item.arguments),
        }
    return {"type": "unknown"}


def _tool_call_dto_from_raw(value: Mapping[str, object]) -> dict[str, object]:
    try:
        return _content_block_dto(
            ToolCall(
                str(value.get("id", "")),
                str(value.get("name", "")),
                cast(dict[str, Any], value.get("arguments", {})),
            )
        )
    except TypeError, ValueError:
        return {"type": "tool_call", "id": "", "name": "", "arguments": {}}


def _tool_result_dto_from_raw(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    content = value.get("content", [])
    if not isinstance(content, list):
        content = []
    added = value.get("added_tool_names")
    return {
        "content": [
            _content_block_dto(TextContent(str(item.get("text", ""))))
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ],
        "details": _safe_json(value.get("details")),
        "is_error": bool(value.get("is_error", False)),
        **(
            {"added_tool_names": [str(name) for name in added if isinstance(name, str)]}
            if isinstance(added, list) and added
            else {}
        ),
    }


def _safe_content(content: object) -> str | list[str]:
    if isinstance(content, str):
        return _safe_text(content)
    if not isinstance(content, tuple):
        return []
    return [_safe_text(item.text) for item in content if isinstance(item, TextContent)]


def _safe_error_code(error: Exception) -> str:
    code = getattr(error, "code", None)
    return str(code) if isinstance(code, str) and code else "agent_error"


def _safe_provider_reason(error: Exception) -> str | None:
    """Return only an allowlisted Provider diagnostic, never raw response data."""

    if _safe_error_code(error) != "provider_error":
        return None
    details = getattr(error, "details", None)
    reason = details.get("error_code") if isinstance(details, dict) else None
    allowed = {
        "context_overflow",
        "model_load_failed",
        "provider_internal",
        "request_format",
        "resource_exhausted",
        "runner_unavailable",
        "vision_request",
    }
    return reason if isinstance(reason, str) and reason in allowed else None


def _failure_terminal_message() -> AssistantMessage:
    return cast(
        AssistantMessage,
        _sanitize_message(
            AssistantMessage(
                (
                    TextContent(
                        "I completed the available tool steps, but I could not finish the final response. Please try again; your completed tool results are still available in this conversation."
                    ),
                ),
                stop_reason="error",
                error_message="The Agent stopped before it could finish its response.",
            )
        ),
    )


def _has_suspended_tool_call(messages: tuple[Message, ...]) -> bool:
    """Whether the last Agent turn intentionally left an approval Tool Call open."""

    calls = {
        block.id
        for message in messages
        if isinstance(message, AssistantMessage)
        for block in message.content
        if isinstance(block, ToolCall)
    }
    results = {
        message.tool_call_id for message in messages if isinstance(message, ToolResultMessage)
    }
    return bool(calls - results)


def _has_visible_final_response(messages: tuple[Message, ...]) -> bool:
    if not messages or not isinstance(messages[-1], AssistantMessage):
        return False
    return any(
        isinstance(block, TextContent) and bool(block.text.strip())
        for block in messages[-1].content
    )


def _sanitize_message(message: Message) -> Message:
    if isinstance(message, UserMessage):
        return UserMessage(
            _safe_content_for_message(message.content),
            message.id,
            message.timestamp,
            display_content=(
                _safe_text(message.display_content) if message.display_content is not None else None
            ),
            attachments=tuple(
                ManagedAssetAttachment(
                    item.asset_id,
                    _safe_text(item.name),
                    item.mime_type,
                )
                for item in message.attachments
            ),
        )
    if isinstance(message, SystemMessage):
        return SystemMessage(
            _safe_content_for_message(message.content), message.id, message.timestamp
        )
    if isinstance(message, AssistantMessage):
        blocks = tuple(
            TextContent(_safe_text(item.text), text_signature=item.text_signature)
            if isinstance(item, TextContent)
            else ToolCall(
                item.id, item.name, _safe_json(item.arguments), item.type, item.thought_signature
            )
            if isinstance(item, ToolCall)
            else ThinkingContent(
                _safe_text(item.thinking),
                item.type,
                item.thinking_signature,
                item.redacted,
            )
            if isinstance(item, ThinkingContent)
            else item
            for item in message.content
        )
        return AssistantMessage(
            blocks,
            message.api,
            message.provider,
            message.model,
            message.usage,
            message.stop_reason,
            message.id,
            message.timestamp,
            message.response_model,
            message.response_id,
            _safe_text(message.error_message) if message.error_message else None,
        )
    if isinstance(message, ToolResultMessage):
        content = tuple(
            TextContent(_safe_text(item.text), text_signature=item.text_signature)
            for item in message.content
            if isinstance(item, TextContent)
        )
        return ToolResultMessage(
            message.tool_call_id,
            message.tool_name,
            ToolResult(
                content,
                details=_safe_json(message.result.details),
                usage=message.result.usage,
                is_error=message.is_error,
                added_tool_names=message.result.added_tool_names,
                terminate=message.result.terminate,
            ),
            message.id,
            message.timestamp,
        )
    if isinstance(message, CustomMessage):
        return CustomMessage(
            message.name, _safe_json(message.payload), message.id, message.timestamp
        )
    return message


def _project_model_context(messages: tuple[Message, ...]) -> tuple[Message, ...]:
    """Keep durable tool output intact while bounding it for later model turns.

    The first response after a tool call receives the complete result.  On a
    later turn the result is recalled from SQLite; forwarding an entire asset
    inventory (including provenance for every item) can consume the model's
    remaining context and yield an empty ``length`` response.  Pi keeps the
    transcript durable but projects its working context, so apply the same
    separation here.  The UI DTO and its expandable raw tool output still use
    the original persisted message.
    """

    return tuple(_project_tool_result_for_model(message) for message in messages)


def _project_tool_result_for_model(message: Message) -> Message:
    if not isinstance(message, ToolResultMessage):
        return message
    text = "\n".join(block.text for block in message.content if isinstance(block, TextContent))
    if len(text) <= _MODEL_TOOL_RESULT_TEXT_LIMIT:
        return message
    return ToolResultMessage(
        message.tool_call_id,
        message.tool_name,
        ToolResult(
            (TextContent(_tool_result_context_summary(message.tool_name, text)),),
            details=message.result.details,
            usage=message.result.usage,
            is_error=message.is_error,
            added_tool_names=message.result.added_tool_names,
            terminate=message.result.terminate,
        ),
        message.id,
        message.timestamp,
    )


def _tool_result_context_summary(tool_name: str, text: str) -> str:
    if tool_name == "asset.list":
        try:
            assets = json.loads(text)
            if isinstance(assets, list):
                current = next(
                    (
                        item.get("name")
                        for item in assets
                        if isinstance(item, dict) and item.get("is_current")
                    ),
                    None,
                )
                return (
                    f"{tool_name} returned {len(assets)} managed assets. "
                    f"Current asset: {current or 'none'}. "
                    "The full inventory is retained locally; use "
                    "asset.get_metadata when an individual entry is needed."
                )
        except json.JSONDecodeError:
            pass
    preview = text[:_MODEL_TOOL_RESULT_TEXT_LIMIT]
    return (
        f"{tool_name} returned {len(text)} characters. "
        "The complete result is retained locally; call the tool again if more detail is needed.\n\n"
        f"Preview:\n{preview}"
    )


def _safe_content_for_message(
    content: str | tuple[TextContent, ...] | tuple[object, ...],
) -> str | tuple[TextContent, ...]:
    if isinstance(content, str):
        return _safe_text(content)
    return tuple(
        TextContent(_safe_text(item.text), text_signature=item.text_signature)
        for item in content
        if isinstance(item, TextContent)
    )


def _model_content_with_attachments(
    display_content: str,
    attachments: tuple[ManagedAssetAttachment, ...],
) -> str:
    if not attachments:
        return display_content
    attachment_lines = "\n".join(
        f'- mime_type="{item.mime_type}", source_asset_ref="{item.asset_id}"'
        for item in attachments
    )
    return (
        f"{display_content}\n\n"
        "<managed-image-attachments>\n"
        f"{_MANAGED_ATTACHMENT_CONTRACT}\n"
        f"{attachment_lines}\n"
        "</managed-image-attachments>"
    )


def _safe_json(value: object) -> Any:
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, list):
        return [_safe_json(item) for item in value]
    if isinstance(value, tuple):
        return [_safe_json(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in value.items()}
    return value


_SECRET = re.compile(r"(?i)(authorization|api[_-]?key|token)\s*[:=]\s*(?:bearer\s+)?\S+")
_ABSOLUTE_PATH = re.compile(r"(?<!\w)(?:[A-Za-z]:[\\/][^\s]*|/(?:[^\s/]+/)+[^\s]*)")


def _safe_text(value: str) -> str:
    value = _SECRET.sub("[REDACTED]", value)
    return _ABSOLUTE_PATH.sub("<workspace-path>", value)


def _tool_result_waits_for_user(details: object) -> bool:
    """Return whether terminal recovery needs an explicit user decision."""

    if not isinstance(details, dict):
        return False
    return details.get("status") == "interrupted" and bool(
        details.get("requires_user_confirmation", True)
    )


def _tool_attempt_warning(value: object) -> str:
    """Extract a short, user-safe reason from a failed Tool result."""

    if isinstance(value, dict):
        details = value.get("details")
        if isinstance(details, dict):
            error = details.get("error")
            if isinstance(error, dict):
                user_message = error.get("user_message")
                if isinstance(user_message, str) and user_message.strip():
                    return _safe_text(user_message.strip())[:320]
        content = value.get("content")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "text":
                    continue
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    return _safe_text(text.strip())[:320]
    return "Tool attempt failed."
