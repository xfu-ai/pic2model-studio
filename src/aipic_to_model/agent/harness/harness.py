"""The durable, linear orchestration layer above :class:`Agent`."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol, cast

from ..core.agent import Agent
from ..core.agent_loop import (
    AfterToolCallContext,
    AgentLoopConfig,
    BeforeToolCallContext,
    BeforeToolCallResult,
)
from ..core.errors import AgentCoreError
from ..core.events import AgentEvent, AgentEventType
from ..core.models import (
    AssistantMessage,
    Message,
    SystemMessage,
    TextContent,
    ToolResult,
    UserMessage,
    message_from_dict,
)
from ..core.tool import ActiveToolSet, AgentTool, AgentToolCatalog
from ..extensions.registry import AgentExtension, ExtensionRegistry
from ..providers.base import AgentModelProvider, ModelProfile, ModelRequest
from ..session.sqlite import LinearSessionRepository
from ..skills.loader import Skill, SkillLoader
from .context import (
    DEFAULT_COMPACTION_SETTINGS,
    CompactionSettings,
    clamp_max_output_tokens,
    estimate_context_tokens,
    find_safe_cut,
    find_turn_prefix_cut,
    project_context,
    should_compact,
)


class HarnessPhase(StrEnum):
    IDLE = "idle"
    TURN = "turn"
    COMPACTION = "compaction"


@dataclass(frozen=True)
class TurnSnapshot:
    context: tuple[Message, ...]
    system_prompt: str
    skills: tuple[str, ...]
    tools: tuple[AgentTool, ...]
    profile: ModelProfile
    tool_context: dict[str, object]
    stream_options: dict[str, object]


@dataclass(frozen=True)
class CompactionInput:
    messages: tuple[Message, ...]
    previous_summary: str | None
    reason: str
    settings: CompactionSettings


class ContextSummarizer(Protocol):
    def __call__(self, value: CompactionInput) -> Awaitable[str] | str: ...


BeforeCompactHook = Callable[[CompactionInput], Awaitable[str | bool | None] | str | bool | None]
HarnessListener = Callable[[AgentEvent], Awaitable[None] | None]
SessionMessageSanitizer = Callable[[Message], Message]
ContextProjectionTransform = Callable[[tuple[Message, ...]], tuple[Message, ...]]
ProviderRequestTransform = Callable[[ModelRequest], ModelRequest]
BeforeToolCallGuard = Callable[
    [BeforeToolCallContext],
    Awaitable[BeforeToolCallResult | None] | BeforeToolCallResult | None,
]
ProviderResponseTransform = Callable[
    [AssistantMessage], Awaitable[AssistantMessage | None] | AssistantMessage | None
]


class AgentHarness:
    """Combines Agent, linear session state, snapshots, and compaction policy."""

    def __init__(
        self,
        provider: AgentModelProvider,
        profile: ModelProfile,
        repository: LinearSessionRepository,
        session_id: str,
        *,
        tools: tuple[AgentTool, ...] = (),
        system_prompt: str = "",
        skills: tuple[str, ...] = (),
        context_window: int = 128_000,
        compaction_settings: CompactionSettings = DEFAULT_COMPACTION_SETTINGS,
        summarizer: ContextSummarizer | None = None,
        session_before_compact: BeforeCompactHook | None = None,
        tool_context: dict[str, object] | None = None,
        stream_options: dict[str, object] | None = None,
        summarization_profile: ModelProfile | None = None,
        extensions: tuple[AgentExtension, ...] = (),
        skill_loader: SkillLoader | None = None,
        session_message_sanitizer: SessionMessageSanitizer | None = None,
        context_projection_transform: ContextProjectionTransform | None = None,
        provider_request_transform: ProviderRequestTransform | None = None,
        tool_catalog: AgentToolCatalog | None = None,
        active_tool_names: tuple[str, ...] | None = None,
        before_tool_call_guard: BeforeToolCallGuard | None = None,
        provider_response_transform: ProviderResponseTransform | None = None,
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._session_id = session_id
        self._context_window = context_window
        self._settings = compaction_settings.normalized(context_window)
        self._summarizer = summarizer or _default_summary
        self._before_compact = session_before_compact
        self._tool_context = dict(tool_context or {})
        self._stream_options = dict(stream_options or {})
        self._before_tool_call_guard = before_tool_call_guard
        self._provider_response_transform = provider_response_transform
        self.summarization_profile = summarization_profile or profile
        self.extensions = ExtensionRegistry()
        self.extensions.register(extensions)
        all_tools = tuple(tools) + self.extensions.tools
        if tool_catalog is not None:
            all_catalog_tools = (*tool_catalog.all(), *self.extensions.tools)
            self._tool_catalog = AgentToolCatalog(all_catalog_tools)
            initial_names = (
                tuple(active_tool_names)
                if active_tool_names is not None
                else tuple(tool.name for tool in tools)
            )
            initial_names = (*initial_names, *(tool.name for tool in self.extensions.tools))
        else:
            self._tool_catalog = AgentToolCatalog(all_tools)
            initial_names = tuple(tool.name for tool in all_tools)
        self._active_tools = ActiveToolSet(self._tool_catalog, (), initial_names)
        self._agent = Agent(
            provider,
            profile,
            self._active_tools.tools,
            system_prompt=system_prompt,
            loop_config=AgentLoopConfig(
                before_provider_request=self._before_provider_request,
                after_provider_response=self._after_provider_response,
                before_tool_call=self._before_tool_call,
                after_tool_call=self._after_tool_call,
            ),
        )
        self._skills = tuple(skills)
        self._skill_loader = skill_loader
        self._session_message_sanitizer = session_message_sanitizer
        self._context_projection_transform = context_projection_transform
        self._provider_request_transform = provider_request_transform
        self._active_skill_content: dict[str, Skill] = {}
        self.phase = HarnessPhase.IDLE
        self.events: list[AgentEvent] = []
        self._listeners: list[HarnessListener] = []
        self._lock = asyncio.Lock()

    @property
    def agent(self) -> Agent:
        return self._agent

    @property
    def settings(self) -> CompactionSettings:
        return self._settings

    @property
    def active_tool_names(self) -> tuple[str, ...]:
        return self._active_tools.names

    def activate_tools(self, names: tuple[str, ...]) -> tuple[str, ...]:
        """Append catalog Tools and persist their stable order for recovery."""

        added = self._active_tools.activate(names)
        if not added:
            return ()
        self._agent.update_tools(self._active_tools.tools)
        self._repository.update_config(
            self._session_id,
            active_tools_json=json.dumps(self._active_tools.names),
        )
        return added

    def subscribe(self, listener: HarnessListener) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def update_compaction_settings(self, settings: CompactionSettings) -> None:
        self._settings = settings.normalized(self._context_window)

    def snapshot(self) -> TurnSnapshot:
        session = self._repository.open(self._session_id)
        record = self._repository.latest_compaction(self._session_id)
        projection = project_context(
            session.messages,
            summary=record.summary if record else None,
            first_kept_sequence=record.first_kept_sequence if record else None,
        )
        context = (
            (SystemMessage(session.system_prompt),) if session.system_prompt else ()
        ) + projection.messages
        if self._context_projection_transform is not None:
            context = self._context_projection_transform(context)
        if self._active_skill_content:
            instructions = "\n\n".join(
                f'<skill name="{skill.name}">\n{skill.instructions or ""}\n</skill>'
                for skill in self._active_skill_content.values()
            )
            context = (SystemMessage(instructions),) + context
        return TurnSnapshot(
            self.extensions.transform_context(context),
            session.system_prompt,
            session.active_skills,
            self._agent.state.tools,
            self._agent.state.profile,
            dict(self._tool_context),
            dict(self._stream_options),
        )

    async def skill(self, name: str, user_input: str = "") -> str:
        if self._skill_loader is None:
            raise RuntimeError("No SkillLoader is configured for this harness.")
        if not self._skill_loader._skills:
            await self._skill_loader.discover()
        skill = await self._skill_loader.activate(
            name, tuple(tool.name for tool in self._agent.state.tools)
        )
        self._active_skill_content[name] = skill
        active = tuple(
            f"{item.name}@{item.version_hash}" for item in self._active_skill_content.values()
        )
        self._repository.update_config(self._session_id, active_skills_json=json.dumps(active))
        return f'<skill name="{skill.name}">\n{skill.instructions or ""}\n</skill>' + (
            f"\n\n{user_input}" if user_input else ""
        )

    async def prompt(self, message: Message | str) -> tuple[Message, ...]:
        user = UserMessage(message) if isinstance(message, str) else message
        if self._lock.locked() or self.phase is not HarnessPhase.IDLE:
            raise AgentCoreError("AgentHarness is already running.", "harness_busy")
        async with self._lock:
            await self._compact_if_needed("threshold")
            self._repository.append_message(self._session_id, user)
            return await self._attempt(user, retry=False)

    async def continue_run(self) -> tuple[Message, ...]:
        """Resume a suspended Tool Call from its durable Tool Result.

        This is intentionally distinct from ``prompt``: no synthetic user
        message is persisted or sent to the model. It is used only after the
        desktop has completed the original Tool Call's terminal result.
        """

        if self._lock.locked() or self.phase is not HarnessPhase.IDLE:
            raise AgentCoreError("AgentHarness is already running.", "harness_busy")
        async with self._lock:
            await self._compact_if_needed("threshold")
            return await self._attempt(None, retry=False)

    async def compact(self) -> bool:
        if self._lock.locked() or self.phase is not HarnessPhase.IDLE:
            raise AgentCoreError("AgentHarness is already running.", "harness_busy")
        async with self._lock:
            return await self._compact("manual")

    async def _attempt(self, user: Message | None, *, retry: bool) -> tuple[Message, ...]:
        self.phase = HarnessPhase.TURN
        await self.extensions.emit("before_agent_start", {"session_id": self._session_id})
        await self._emit(AgentEventType.ATTEMPT_START, retry=retry)
        operation = self._repository.start_operation(self._session_id)

        async def persist(event: AgentEvent) -> None:
            if event.type is AgentEventType.MESSAGE_END:
                await self.extensions.emit("session_message_append", dict(event.payload))
            persisted = event
            raw_message = event.payload.get("message")
            if self._session_message_sanitizer is not None and isinstance(raw_message, dict):
                safe_message = self._session_message_sanitizer(message_from_dict(raw_message))
                persisted = AgentEvent(
                    event.type, {**event.payload, "message": safe_message.to_dict()}
                )
            await self._repository.listener(self._session_id, operation, persisted)
            if event.type is AgentEventType.TURN_END:
                await self.extensions.emit("turn_end", dict(event.payload))
            await self._emit(event.type, **event.payload)

        unsubscribe = self._agent.subscribe(persist)
        try:
            snapshot = self.snapshot()
            # A fresh input is already durable but intentionally excluded from
            # the session snapshot so Agent adds it exactly once. A terminal
            # approval Tool Result, by contrast, is the exact final message of
            # a suspended transcript and must be retained for continue_run.
            context = (
                tuple(item for item in snapshot.context if item.id != user.id)
                if user is not None
                else snapshot.context
            )
            self._agent.state.messages = list(context)
            result = (
                await self._agent.prompt(user)
                if user is not None
                else await self._agent.continue_run()
            )
            await self._emit(AgentEventType.ATTEMPT_FINISHED, retry=retry, success=True)
            return result
        except AgentCoreError as error:
            await self._emit(AgentEventType.ATTEMPT_FINISHED, retry=retry, success=False)
            if not retry and _is_context_overflow(error):
                compacted = await self._compact("overflow")
                if compacted:
                    await self._emit(AgentEventType.RETRY_SCHEDULED, reason="overflow", attempt=1)
                    return await self._attempt(user, retry=True)
                raise AgentCoreError(
                    "Context overflow could not be resolved by compaction.",
                    "context_overflow",
                ) from error
            raise
        finally:
            unsubscribe()
            self._repository.finish_operation(operation)
            await self.extensions.emit("agent_end", {"session_id": self._session_id})
            self.phase = HarnessPhase.IDLE

    async def close(self) -> None:
        await self.extensions.close()

    async def _before_tool_call(
        self, context: BeforeToolCallContext, _cancellation: object
    ) -> BeforeToolCallResult | None:
        patch = await self.extensions.emit(
            "before_tool_call",
            {
                "tool_call_id": context.tool_call.id,
                "tool_name": context.tool_call.name,
                "arguments": context.arguments,
            },
        )
        if patch.get("block"):
            return BeforeToolCallResult(
                True, str(patch.get("reason") or "Tool blocked by extension.")
            )
        if self._before_tool_call_guard is not None:
            guarded = self._before_tool_call_guard(context)
            if asyncio.iscoroutine(guarded):
                guarded = await guarded
            if guarded is not None:
                return guarded
        return None

    async def _after_tool_call(
        self, context: AfterToolCallContext, _cancellation: object
    ) -> ToolResult | None:
        # Pi-style Tool additions change only the next provider request. Agent
        # queues the registry replacement while this run is active, and its
        # prepare_next_turn hook applies it after the current Tool Result has
        # been appended to the transcript.
        if context.result.added_tool_names:
            self.activate_tools(context.result.added_tool_names)
        patch = await self.extensions.emit(
            "after_tool_call",
            {
                "tool_call_id": context.tool_call.id,
                "tool_name": context.tool_call.name,
                "is_error": context.is_error,
            },
        )
        details = patch.get("details")
        if not isinstance(details, dict):
            return None
        base = context.result.details if isinstance(context.result.details, dict) else {}
        return ToolResult(
            context.result.content,
            details={**base, **details},
            usage=context.result.usage,
            is_error=context.result.is_error,
            added_tool_names=context.result.added_tool_names,
            terminate=context.result.terminate,
        )

    async def _before_provider_request(
        self, request: ModelRequest, _cancellation: object
    ) -> ModelRequest | None:
        original_request = request
        if self._provider_request_transform is not None:
            request = self._provider_request_transform(request)
        patch = await self.extensions.emit(
            "before_provider_request",
            {
                "request": request,
                "temperature": request.temperature,
                "max_output_tokens": request.max_output_tokens,
            },
        )
        temperature = patch.get("temperature", request.temperature)
        max_output = patch.get("max_output_tokens", request.max_output_tokens)
        if temperature is not None and not isinstance(temperature, int | float):
            raise AgentCoreError("Extension supplied an invalid temperature.", "extension_error")
        if max_output is not None and not isinstance(max_output, int):
            raise AgentCoreError("Extension supplied invalid max output tokens.", "extension_error")
        requested_output = max_output if isinstance(max_output, int) else request.profile.max_output_tokens
        if requested_output is None:
            updated = replace(
                request,
                temperature=float(temperature) if isinstance(temperature, int | float) else None,
            )
            return None if updated == original_request else updated
        clamped_output = clamp_max_output_tokens(
            request.messages,
            self._context_window,
            requested_output,
            request.tools,
        )
        updated = replace(
            request,
            temperature=float(temperature) if isinstance(temperature, int | float) else None,
            max_output_tokens=clamped_output,
        )
        return None if updated == original_request else updated

    async def _after_provider_response(
        self, message: AssistantMessage, _cancellation: object
    ) -> AssistantMessage | None:
        transformed: AssistantMessage | None = None
        if self._provider_response_transform is not None:
            candidate = self._provider_response_transform(message)
            if asyncio.iscoroutine(candidate):
                candidate = await candidate
            if candidate is not None:
                transformed = candidate
                message = candidate
        await self.extensions.emit("after_provider_response", {"message": message})
        return transformed

    async def _compact_if_needed(self, reason: str) -> bool:
        if not self._settings.enabled:
            return False
        estimate = estimate_context_tokens(self.snapshot().context, self._settings.image_token_cost)
        if should_compact(estimate.tokens, self._context_window, self._settings):
            return await self._compact(reason)
        return False

    async def _compact(self, reason: str) -> bool:
        if self._agent.state.pending_tool_calls:
            return False
        prior_phase = self.phase
        self.phase = HarnessPhase.COMPACTION
        snapshot = self.snapshot()
        record = self._repository.latest_compaction(self._session_id)
        estimate = estimate_context_tokens(snapshot.context, self._settings.image_token_cost)
        raw_session = self._repository.open(self._session_id)
        raw = raw_session.messages
        start_sequence = record.first_kept_sequence if record and record.first_kept_sequence else 1
        eligible = raw[start_sequence - 1 :]
        cut = find_safe_cut(eligible, self._settings.keep_recent_tokens)
        if cut == 0 and eligible:
            # A single oversized final turn cannot be retained whole. Summarize
            # its user-visible prefix and retain its model/tool suffix instead.
            prefix_cut = find_turn_prefix_cut(eligible)
            if prefix_cut is None:
                self.phase = prior_phase
                return False
            cut = prefix_cut
        old = eligible[:cut]
        tail = eligible[cut:]
        protected_outline = _latest_execution_outline(raw)
        input_value = CompactionInput(
            old, record.summary if record else None, reason, self._settings
        )
        await self._emit(AgentEventType.COMPACTION_START, reason=reason)
        try:
            override = await _call_before_compact(self._before_compact, input_value)
            if override is False:
                await self._emit(AgentEventType.COMPACTION_END, reason=reason, success=False)
                return False
            summary = (
                override
                if isinstance(override, str)
                else record.summary
                if not old and record and record.summary
                else await _call_summarizer(self._summarizer, input_value)
            )
            if not summary:
                raise AgentCoreError("Compaction summary was empty.", "compaction_failed")
            if protected_outline and protected_outline not in summary:
                # The outline is operational state, not prose for the
                # summarizer to reinterpret. Keep the source block verbatim.
                summary = f"{summary}\n\n{protected_outline}"
            compaction_id = self._repository.start_compaction(
                self._session_id,
                reason=reason,
                tokens_before=estimate.tokens,
                provider_id=self.summarization_profile.provider_id,
                model=self.summarization_profile.model,
                previous_compaction_id=record.id if record else None,
            )
            first_kept_sequence = start_sequence + cut
            projected = (SystemMessage(summary),) + tail
            tokens_after = estimate_context_tokens(
                projected, self._settings.image_token_cost
            ).tokens
            self._repository.commit_compaction(
                compaction_id,
                summary=summary,
                first_kept_sequence=first_kept_sequence,
                retained_tail=tail,
                tokens_after=tokens_after,
            )
            await self._emit(
                AgentEventType.CONTEXT_COMPACTED,
                reason=reason,
                tokens_before=estimate.tokens,
                tokens_after=tokens_after,
                first_kept_sequence=first_kept_sequence,
            )
            await self._emit(AgentEventType.COMPACTION_END, reason=reason, success=True)
            return True
        except Exception:
            await self._emit(AgentEventType.COMPACTION_END, reason=reason, success=False)
            if reason == "overflow":
                raise
            return False
        finally:
            self.phase = prior_phase

    async def _emit(self, event_type: AgentEventType, **payload: object) -> None:
        event = AgentEvent(event_type, payload)  # type: ignore[arg-type]
        self.events.append(event)
        for listener in tuple(self._listeners):
            result = listener(event)
            if result is not None:
                await result


def _is_context_overflow(error: AgentCoreError) -> bool:
    return (
        error.code == "context_overflow"
        or "context" in error.message.lower()
        and "overflow" in error.message.lower()
    )


async def _call_before_compact(
    function: BeforeCompactHook | None, value: CompactionInput
) -> str | bool | None:
    if function is None:
        return None
    result = function(value)
    if asyncio.iscoroutine(result):
        return await result
    return cast(str | bool | None, result)


async def _call_summarizer(function: ContextSummarizer, value: CompactionInput) -> str:
    result = function(value)
    if asyncio.iscoroutine(result):
        return await result
    return cast(str, result)


_EXECUTION_OUTLINE_RE = re.compile(
    r"<execution_outline>.*?</execution_outline>", re.DOTALL
)


def _latest_execution_outline(messages: tuple[Message, ...]) -> str | None:
    """Return the last complete outline exactly as the model emitted it."""

    latest: str | None = None
    for message in messages:
        if not isinstance(message, AssistantMessage | UserMessage | SystemMessage):
            continue
        content = message.content
        texts = [content] if isinstance(content, str) else [
            item.text for item in content if isinstance(item, TextContent)
        ]
        for text in texts:
            matches = _EXECUTION_OUTLINE_RE.findall(text)
            if matches:
                latest = matches[-1]
    return latest


def _default_summary(value: CompactionInput) -> str:
    """Offline-safe deterministic fallback; callers may install an LLM summarizer."""

    previous = value.previous_summary or "(none)"
    snippets = [
        item.content
        if isinstance(item, UserMessage) and isinstance(item.content, str)
        else item.role
        for item in value.messages
    ]
    progress = "; ".join(str(item)[:240] for item in snippets[-4:]) or "(none)"
    return (
        "## Goal\nContinue the current agent task.\n\n"
        "## Constraints\n- Preserve the linear transcript and tool results.\n\n"
        f"## Progress\n{progress}\n\n"
        "## Key Decisions\n- Context was compacted at a save-point.\n\n"
        "## Next Steps\n1. Continue from the retained context.\n\n"
        f"## Critical Context\nPrevious summary: {previous}"
    )
