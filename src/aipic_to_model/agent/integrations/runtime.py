"""Application-facing Agent runtime with durable, safe event projections."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from ...application.tools import ToolRegistry as AIPicToolRegistry
from ..core.errors import AgentCancelledError
from ..core.events import AgentEvent, AgentEventType
from ..core.models import (
    AssistantMessage,
    CustomMessage,
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
from ..harness import AgentHarness
from ..providers.api.openai_completions import OpenAICompletionsProvider
from ..providers.base import AgentModelProvider, ModelProfile, ModelRequest
from ..providers.deepseek import (
    create_deepseek_credential_resolver,
    create_deepseek_profile,
    deepseek_context_window,
)
from ..session.sqlite import LinearSessionRepository
from ..skills.loader import SkillLoader
from ..tools import BashTool, EditTool, ReadTool, WriteTool
from .aipic_tools import AIPicToolInvocation
from .facade_tools import FACADE_TOOL_NAMES, facade_tools

ProviderFactory = Callable[[ModelProfile], AgentModelProvider]
RuntimeContextProvider = Callable[[str], dict[str, object]]
AttachmentProvider = Callable[[str, str], dict[str, object]]
_MODEL_TOOL_RESULT_TEXT_LIMIT = 4_096
_FINAL_RESPONSE_CONTRACT = (
    "Completion contract: do not finish a user turn until the provider has "
    "returned a terminal finish_reason. After one or more tool calls have "
    "finished, always send a concise, user-facing final summary of the "
    "completed work, results, and any warnings or next action. Never end a "
    "turn with an empty assistant message: the terminal response must contain "
    "non-whitespace text."
)
_ASYNC_JOB_CONTRACT = (
    "Async-job contract: call control_job with action=status at most once for "
    "a job in one Agent run. Do not repeatedly call inspect_workspace, sleep, or poll files "
    "to wait for asynchronous output. If a job is queued, running, waiting, or "
    "interrupted, tell the user it is waiting and end with a concise visible summary. "
    "The desktop will send a new completion event when the job reaches a terminal state."
)
_ASYNC_JOB_CONTRACT = (
    _ASYNC_JOB_CONTRACT
    + " Binding clarification: after a Tool returns a fresh queued, running, waiting, "
    "or interrupted Job result, do not call control_job, inspect_workspace, sleep, or "
    "poll files to wait for it. This overrides any earlier at-most-once allowance. Tell "
    "the user the background task started and stop; the desktop will send the terminal "
    "event. Use control_job action=status only when the user explicitly asks for progress "
    "and no fresh Job event is already present in the current context."
)
_IMAGE_PRESENTATION_CONTRACT = (
    "Image-presentation contract: images are attached to the final user-facing "
    "assistant answer, never to a tool explanation. When presenting an existing "
    "image found through inspect_workspace, inspect that asset with "
    "inspect_workspace(view=asset_details) before the final answer so the desktop "
    "can attach that managed image. "
    "Do not expose raw asset IDs in user-facing text."
)
_TOOL_SELECTION_CONTRACT = (
    "Tool-selection contract: use the fixed managed facade tools for project state, managed "
    "assets, selections, prompts, jobs, approvals, provider work, and exports. "
    "Use read, write, edit, and bash only for workspace-local text files, scripts, "
    "and diagnostics that the user requested. Never use filesystem or shell tools "
    "to edit project databases, mutate managed asset files, call provider endpoints, "
    "read secrets, or bypass an AIPic approval or UI action."
)
_MANAGED_ATTACHMENT_CONTRACT = (
    "Managed-attachment contract: every attached image is already stored in the current "
    "project. Infer the role of each image from the user's request and use the exact "
    "source_asset_ref values with managed facade tools as needed. There is no implicit "
    "primary image; if the intended mapping is materially ambiguous, ask the user. Never "
    "use read, bash, or a filesystem path for attachments, and never expose opaque "
    "references in user-facing text."
)


@dataclass
class _Conversation:
    harness: AgentHarness
    repository: LinearSessionRepository
    task: asyncio.Task[tuple[Message, ...]] | None = None
    error_code: str | None = None


class AgentRuntime:
    """Owns live Agent tasks while SQLite remains the recovery source of truth."""

    def __init__(
        self,
        registry: AIPicToolRegistry,
        root_for: Callable[[str], Path],
        *,
        provider_factory: ProviderFactory | None = None,
        runtime_context_provider: RuntimeContextProvider | None = None,
        attachment_provider: AttachmentProvider | None = None,
    ) -> None:
        self._registry = registry
        self._root_for = root_for
        self._provider_factory = provider_factory or _default_provider
        self._runtime_context_provider = runtime_context_provider or _empty_runtime_context
        self._attachment_provider = attachment_provider
        self._conversations: dict[tuple[str, str], _Conversation] = {}

    def create(
        self, project_id: str, *, system_prompt: str = "", model: str | None = None
    ) -> dict[str, object]:
        root = self._root_for(project_id)
        repository = LinearSessionRepository(root / "agent.sqlite3")
        profile = create_deepseek_profile(model=model)
        session = repository.create(
            system_prompt=_system_prompt_for_project(system_prompt, project_id),
            profile=_profile_dict(profile),
            active_tools=("read", "write", "edit", "bash", *FACADE_TOOL_NAMES),
        )
        conversation = self._build(project_id, session.id, repository, profile)
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
                conversation.error_code if conversation and conversation.error_code else repository.terminal_error_code(str(summary["id"]))
            )
            summary["state"] = (
                "error"
                if error_code is not None
                else "running"
                if conversation is not None and conversation.task is not None and not conversation.task.done()
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
        attachments = self._resolve_attachments(project_id, asset_refs)
        display_content = _safe_text(content)
        model_content = _model_content_with_attachments(display_content, attachments)
        self._ensure_final_response_contract(conversation.repository, conversation_id)
        conversation.error_code = None
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
                UserMessage(
                    model_content,
                    display_content=display_content,
                    attachments=attachments,
                ),
            ),
            name=f"agent-{conversation_id}",
        )
        if wait:
            await conversation.task
        return self.status(project_id, conversation_id)

    def _resolve_attachments(
        self, project_id: str, asset_refs: tuple[str, ...]
    ) -> tuple[ManagedAssetAttachment, ...]:
        if not asset_refs:
            return ()
        if self._attachment_provider is None:
            raise RuntimeError("agent_attachment_not_found")
        attachments: list[ManagedAssetAttachment] = []
        for asset_ref in asset_refs:
            try:
                asset = self._attachment_provider(project_id, asset_ref)
            except Exception as error:
                raise RuntimeError("agent_attachment_not_found") from error
            mime_type = str(asset.get("mime_type", ""))
            asset_type = str(asset.get("asset_type", ""))
            if (
                not mime_type.startswith("image/")
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
        return [
            _message_dto(item) for item in messages
        ]

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
        messages, next_before, has_more = conversation.repository.message_page(
            conversation_id,
            before=before,
            limit=limit,
        )
        return {
            "items": [_message_dto(item) for item in messages],
            "next_before": next_before,
            "has_more": has_more,
        }

    def event_cursor(self, project_id: str, conversation_id: str) -> int:
        return self._get(project_id, conversation_id).repository.api_event_cursor(
            conversation_id
        )

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
            result = await conversation.harness.prompt(message)
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
            self._append_event(
                conversation.repository,
                conversation_id,
                "conversation.completed",
                {"conversation_id": conversation_id},
            )
            return result
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

    def _build(
        self,
        project_id: str,
        conversation_id: str,
        repository: LinearSessionRepository,
        profile: ModelProfile,
    ) -> _Conversation:
        root = self._root_for(project_id)
        env = LocalExecutionEnv((root,))
        request_id = f"agent-{conversation_id}"
        # The model sees four generic workspace tools plus eleven stable AIPic
        # facades. Atomic B01/B02 manifests remain internal execution contracts.
        tools = (
            ReadTool(env),
            WriteTool(env),
            EditTool(env),
            BashTool(env),
        ) + facade_tools(
            self._registry,
            lambda: AIPicToolInvocation(root, project_id, request_id, run_id=conversation_id),
            lambda: self._runtime_context_provider(project_id),
        )
        loader = SkillLoader(env, project_roots=(root / ".agent-skills",))
        harness = AgentHarness(
            self._provider_factory(profile),
            profile,
            repository,
            conversation_id,
            tools=cast(tuple[AgentTool, ...], tools),
            skill_loader=loader,
            session_message_sanitizer=_sanitize_message,
            context_projection_transform=_project_model_context,
            provider_request_transform=lambda request: _with_runtime_context(
                request, self._runtime_context_provider(project_id)
            ),
            context_window=deepseek_context_window(profile.model),
        )
        conversation = _Conversation(harness, repository)

        async def persist(event: AgentEvent) -> None:
            projected = _event_dto(conversation_id, event)
            if projected is not None:
                self._append_event(repository, conversation_id, projected[0], projected[1])

        harness.subscribe(persist)
        return conversation

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
        if profile_updated:
            repository.update_config(
                conversation_id,
                profile_json=json.dumps(_profile_dict(profile)),
            )
        conversation = self._build(project_id, conversation_id, repository, profile)
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
            and _IMAGE_PRESENTATION_CONTRACT in session.system_prompt
            and _TOOL_SELECTION_CONTRACT in session.system_prompt
        ):
            return
        prompt = session.system_prompt.strip()
        additions = tuple(
            instruction
            for instruction in (
                _ASYNC_JOB_CONTRACT,
                _IMAGE_PRESENTATION_CONTRACT,
                _TOOL_SELECTION_CONTRACT,
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


def _default_provider(_profile: ModelProfile) -> AgentModelProvider:
    return OpenAICompletionsProvider(create_deepseek_credential_resolver())


def _empty_runtime_context(_project_id: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "configuration_state": "unavailable",
        "facade_tools": list(FACADE_TOOL_NAMES),
        "capabilities": {},
        "jobs": {"nonterminal": []},
    }


def _with_runtime_context(
    request: ModelRequest, snapshot: dict[str, object]
) -> ModelRequest:
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
        "host capability ID, credential, URL, or approval decision to a managed facade tool. "
        f"The host-only project binding is {project_id}; do not repeat it in user-facing text."
    )
    instructions = (
        f"{project_instruction}\n\n{_ASYNC_JOB_CONTRACT}\n\n"
        f"{_IMAGE_PRESENTATION_CONTRACT}\n\n{_TOOL_SELECTION_CONTRACT}\n\n"
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
    """Replace the former hard-coded 256-token DeepSeek budget on recovery."""

    if profile.provider_id != "deepseek" or profile.max_output_tokens != 256:
        return profile, False
    return (
        create_deepseek_profile(
            base_url=profile.base_url,
            model=profile.model,
            timeout_seconds=profile.timeout_seconds,
        ),
        True,
    )


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
            "content": [_content_block_dto(item) for item in message.content],
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


def _tool_call_dto_from_raw(value: dict[str, object]) -> dict[str, object]:
    try:
        return _content_block_dto(
            ToolCall(
                str(value.get("id", "")),
                str(value.get("name", "")),
                cast(dict[str, Any], value.get("arguments", {})),
            )
        )
    except (TypeError, ValueError):
        return {"type": "tool_call", "id": "", "name": "", "arguments": {}}


def _tool_result_dto_from_raw(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    content = value.get("content", [])
    if not isinstance(content, list):
        content = []
    return {
        "content": [
            _content_block_dto(TextContent(str(item.get("text", ""))))
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ],
        "details": _safe_json(value.get("details")),
        "is_error": bool(value.get("is_error", False)),
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


def _failure_terminal_message() -> AssistantMessage:
    return _sanitize_message(
        AssistantMessage(
            (
                TextContent(
                    "I completed the available tool steps, but I could not finish the final response. Please try again; your completed tool results are still available in this conversation."
                ),
            ),
            stop_reason="error",
            error_message="The Agent stopped before it could finish its response.",
        )
    )


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
    text = "\n".join(
        block.text for block in message.content if isinstance(block, TextContent)
    )
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
    if tool_name in {"asset.list", "inspect_workspace"}:
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
                    "inspect_workspace(view=asset_details) when an individual entry is needed."
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
