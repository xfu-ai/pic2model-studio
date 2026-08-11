"""SQLite repository for a deliberately linear Agent transcript."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..core.events import AgentEvent, AgentEventType
from ..core.models import (
    Message,
    TextContent,
    ToolResultMessage,
    Usage,
    UserMessage,
    message_from_dict,
    message_from_json,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class LinearSession:
    id: str
    messages: tuple[Message, ...]
    system_prompt: str
    profile: dict[str, Any]
    thinking_level: str
    active_tools: tuple[str, ...]
    active_skills: tuple[str, ...]
    compaction: dict[str, Any] | None


@dataclass(frozen=True)
class CompactionRecord:
    id: str
    state: str
    reason: str
    summary: str | None
    first_kept_sequence: int | None
    retained_tail: tuple[Message, ...]
    tokens_before: int
    tokens_after: int | None
    usage: dict[str, Any] | None
    provider_id: str | None
    model: str | None
    previous_compaction_id: str | None
    created_at: str


class LinearSessionRepository:
    def __init__(self, path: Path) -> None:
        self._path = path

    def migrate(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        script = (Path(__file__).parent / "migrations" / "0003_agent_linear.sql").read_bytes()
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS agent_schema_migrations(version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
            )
            self._apply_migration(connection, 3, script)
            script4 = (
                Path(__file__).parent / "migrations" / "0004_agent_compaction.sql"
            ).read_bytes()
            self._apply_migration(connection, 4, script4)
            script5 = (
                Path(__file__).parent / "migrations" / "0005_agent_api_events.sql"
            ).read_bytes()
            self._apply_migration(connection, 5, script5)
            script6 = (
                Path(__file__).parent / "migrations" / "0006_agent_job_waits.sql"
            ).read_bytes()
            self._apply_migration(connection, 6, script6)

    def create(
        self,
        *,
        session_id: str | None = None,
        system_prompt: str = "",
        profile: dict[str, Any] | None = None,
        thinking_level: str = "off",
        active_tools: tuple[str, ...] = (),
        active_skills: tuple[str, ...] = (),
    ) -> LinearSession:
        self.migrate()
        identifier, now = session_id or _id(), _now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO agent_sessions VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    now,
                    now,
                    system_prompt,
                    json.dumps(profile or {}),
                    thinking_level,
                    json.dumps(active_tools),
                    json.dumps(active_skills),
                    None,
                ),
            )
        return self.open(identifier)

    def open(self, session_id: str) -> LinearSession:
        self.migrate()
        with self._connect() as connection:
            self._interrupt_unfinished(connection, session_id)
            self._interrupt_unfinished_compactions(connection, session_id)
            row = connection.execute(
                "SELECT * FROM agent_sessions WHERE id=?", (session_id,)
            ).fetchone()
            if row is None:
                raise KeyError(session_id)
            messages = tuple(
                message_from_json(item[0])
                for item in connection.execute(
                    "SELECT message_json FROM agent_messages WHERE session_id=? ORDER BY sequence_no",
                    (session_id,),
                )
            )
            record = self._latest_compaction(connection, session_id)
        return LinearSession(
            session_id,
            messages,
            row["system_prompt"],
            json.loads(row["profile_json"]),
            row["thinking_level"],
            tuple(json.loads(row["active_tools_json"])),
            tuple(json.loads(row["active_skills_json"])),
            self._record_to_dict(record) if record is not None else None,
        )

    def message_page(
        self,
        session_id: str,
        *,
        before: int | None,
        limit: int,
    ) -> tuple[tuple[Message, ...], int | None, bool]:
        """Return one chronological page from the end of a transcript."""

        self.migrate()
        safe_limit = min(max(limit, 1), 100)
        with self._connect() as connection:
            if before is None:
                rows = list(
                    connection.execute(
                        "SELECT sequence_no,message_json FROM agent_messages "
                        "WHERE session_id=? ORDER BY sequence_no DESC LIMIT ?",
                        (session_id, safe_limit + 1),
                    )
                )
            else:
                rows = list(
                    connection.execute(
                        "SELECT sequence_no,message_json FROM agent_messages "
                        "WHERE session_id=? AND sequence_no<? "
                        "ORDER BY sequence_no DESC LIMIT ?",
                        (session_id, before, safe_limit + 1),
                    )
                )
        has_more = len(rows) > safe_limit
        selected = rows[:safe_limit]
        selected.reverse()
        messages = tuple(message_from_json(str(row["message_json"])) for row in selected)
        next_before = int(selected[0]["sequence_no"]) if has_more and selected else None
        return messages, next_before, has_more

    def recent_sessions(self, limit: int | None = None) -> list[dict[str, object]]:
        """Return Pi-style durable session summaries for one project store.

        The first user message is kept local until the runtime applies its normal
        safe-text projection.  Tool output and model thinking are deliberately
        not used for the list preview.  Empty bootstrap drafts are intentionally
        excluded: they have no transcript to resume and otherwise make a
        project's history noisy after development-mode remounts.
        """

        self.migrate()
        safe_limit = min(max(limit, 1), 500) if limit is not None else None
        with self._connect() as connection:
            query = (
                "SELECT sessions.id, sessions.created_at, sessions.updated_at, "
                "COUNT(messages.sequence_no) AS message_count, "
                "(SELECT first.message_json FROM agent_messages AS first "
                " WHERE first.session_id=sessions.id AND first.role='user' "
                " ORDER BY first.sequence_no LIMIT 1) AS first_user_message "
                "FROM agent_sessions AS sessions "
                "LEFT JOIN agent_messages AS messages ON messages.session_id=sessions.id "
                "GROUP BY sessions.id "
                "HAVING COUNT(messages.sequence_no)>0 "
                "ORDER BY "
                "sessions.updated_at DESC, sessions.created_at DESC"
            )
            if safe_limit is not None:
                query += " LIMIT ?"
            rows = connection.execute(query, (safe_limit,) if safe_limit is not None else ()).fetchall()
        summaries: list[dict[str, object]] = []
        for row in rows:
            preview = ""
            if row["first_user_message"]:
                try:
                    message = message_from_json(str(row["first_user_message"]))
                    if isinstance(message, UserMessage):
                        if message.display_content is not None:
                            preview = message.display_content
                        elif isinstance(message.content, str):
                            preview = message.content
                        else:
                            preview = " ".join(
                                item.text for item in message.content if isinstance(item, TextContent)
                            )
                except (TypeError, ValueError, KeyError, json.JSONDecodeError):
                    # A corrupt historical message must not prevent the rest of
                    # the project's conversations from being recovered.
                    preview = ""
            summaries.append(
                {
                    "id": str(row["id"]),
                    "created_at": str(row["created_at"]),
                    "updated_at": str(row["updated_at"]),
                    "message_count": int(row["message_count"]),
                    "preview": preview,
                }
            )
        return summaries

    def recent_session_ids(self, limit: int = 20) -> list[str]:
        """Compatibility helper for callers that only need durable IDs."""

        return [str(item["id"]) for item in self.recent_sessions(limit)]

    def start_compaction(
        self,
        session_id: str,
        *,
        reason: str,
        tokens_before: int,
        provider_id: str | None,
        model: str | None,
        previous_compaction_id: str | None,
    ) -> str:
        identifier = _id()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO agent_compactions(id,session_id,state,reason,tokens_before,provider_id,model,previous_compaction_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    session_id,
                    "started",
                    reason,
                    tokens_before,
                    provider_id,
                    model,
                    previous_compaction_id,
                    _now(),
                ),
            )
        return identifier

    def commit_compaction(
        self,
        compaction_id: str,
        *,
        summary: str,
        first_kept_sequence: int,
        retained_tail: tuple[Message, ...],
        tokens_after: int,
        usage: Usage | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE agent_compactions SET state='committed',summary=?,first_kept_sequence=?,retained_tail_json=?,tokens_after=?,usage_json=?,committed_at=? WHERE id=? AND state='started'",
                (
                    summary,
                    first_kept_sequence,
                    json.dumps([item.to_dict() for item in retained_tail]),
                    tokens_after,
                    usage.to_json() if usage else None,
                    _now(),
                    compaction_id,
                ),
            )
            if connection.total_changes != 1:
                raise RuntimeError("Compaction was not in started state")

    def latest_compaction(self, session_id: str) -> CompactionRecord | None:
        self.migrate()
        with self._connect() as connection:
            self._interrupt_unfinished_compactions(connection, session_id)
            return self._latest_compaction(connection, session_id)

    def update_config(self, session_id: str, **changes: Any) -> None:
        allowed = {
            "system_prompt",
            "profile_json",
            "thinking_level",
            "active_tools_json",
            "active_skills_json",
            "compaction_json",
        }
        values = {key: value for key, value in changes.items() if key in allowed}
        if not values:
            return
        columns = ", ".join(f"{key}=?" for key in values)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE agent_sessions SET {columns}, updated_at=? WHERE id=?",
                (*values.values(), _now(), session_id),
            )

    def start_operation(self, session_id: str) -> str:
        operation_id = _id()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO agent_operations VALUES(?,?,?,?,?)",
                (operation_id, session_id, "running", _now(), None),
            )
        return operation_id

    def finish_operation(self, operation_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE agent_operations SET state='completed', ended_at=? WHERE id=?",
                (_now(), operation_id),
            )

    def append_message(self, session_id: str, message: Message) -> None:
        tool_call_id = message.tool_call_id if isinstance(message, ToolResultMessage) else None
        with self._connect() as connection:
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence_no),0)+1 FROM agent_messages WHERE session_id=?",
                (session_id,),
            ).fetchone()[0]
            inserted = connection.execute(
                "INSERT OR IGNORE INTO agent_messages VALUES(?,?,?,?,?,?)",
                (session_id, sequence, message.id, message.role, message.to_json(), tool_call_id),
            )
            if inserted.rowcount:
                connection.execute(
                    "UPDATE agent_sessions SET updated_at=? WHERE id=?", (_now(), session_id)
                )

    def append_or_replace_waiting_tool_result(
        self, session_id: str, message: ToolResultMessage
    ) -> bool:
        """Persist exactly one terminal result for an approval-suspended Tool Call.

        Builds before durable wait recovery wrote a non-terminal ``waiting_external``
        result into the transcript. Replace that legacy placeholder in place so a
        recovered conversation never contains two results for one Tool Call.
        """

        with self._connect() as connection:
            row = connection.execute(
                """SELECT sequence_no,message_json FROM agent_messages
                WHERE session_id=? AND tool_call_id=? ORDER BY sequence_no DESC LIMIT 1""",
                (session_id, message.tool_call_id),
            ).fetchone()
            if row is not None:
                existing = message_from_json(str(row["message_json"]))
                if not isinstance(existing, ToolResultMessage):
                    return False
                details = existing.result.details
                status = details.get("status") if isinstance(details, dict) else None
                if status != "waiting_external":
                    return False
                replacement = replace(
                    message,
                    id=existing.id,
                    timestamp=existing.timestamp,
                )
                connection.execute(
                    """UPDATE agent_messages SET message_json=?
                    WHERE session_id=? AND sequence_no=?""",
                    (replacement.to_json(), session_id, row["sequence_no"]),
                )
                connection.execute(
                    "UPDATE agent_sessions SET updated_at=? WHERE id=?",
                    (_now(), session_id),
                )
                return True
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence_no),0)+1 FROM agent_messages WHERE session_id=?",
                (session_id,),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO agent_messages VALUES(?,?,?,?,?,?)",
                (
                    session_id,
                    sequence,
                    message.id,
                    message.role,
                    message.to_json(),
                    message.tool_call_id,
                ),
            )
            connection.execute(
                "UPDATE agent_sessions SET updated_at=? WHERE id=?", (_now(), session_id)
            )
            return True

    def register_job_wait(
        self,
        session_id: str,
        *,
        project_id: str,
        run_id: str,
        tool_call_id: str,
        tool_name: str,
    ) -> None:
        """Persist an approval-sideband Tool call without inventing a TaskPlan."""

        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO agent_job_waits(
                session_id,project_id,run_id,tool_call_id,tool_name,state,created_at
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    session_id,
                    project_id,
                    run_id,
                    tool_call_id,
                    tool_name,
                    "awaiting_ui_action",
                    _now(),
                ),
            )

    def bind_job_wait(self, session_id: str, tool_call_id: str, job_id: str) -> bool:
        with self._connect() as connection:
            changed = connection.execute(
                """UPDATE agent_job_waits SET job_id=?,state='waiting',resumed_at=?
                WHERE session_id=? AND tool_call_id=? AND state='awaiting_ui_action'""",
                (job_id, _now(), session_id, tool_call_id),
            )
            return changed.rowcount == 1

    def complete_job_wait(self, session_id: str, tool_call_id: str, state: str) -> bool:
        if state not in {"terminal_returned", "waiting_external", "declined"}:
            raise ValueError("invalid job wait terminal state")
        with self._connect() as connection:
            source_states = (
                ("awaiting_ui_action", "waiting", "waiting_external")
                if state == "terminal_returned"
                else ("waiting", "waiting_external")
                if state == "waiting_external"
                else ("awaiting_ui_action", "waiting", "waiting_external")
            )
            placeholders = ",".join("?" for _item in source_states)
            changed = connection.execute(
                f"""UPDATE agent_job_waits SET state=?,resumed_at=? WHERE session_id=?
                AND tool_call_id=? AND state IN({placeholders})""",
                (state, _now(), session_id, tool_call_id, *source_states),
            )
            return changed.rowcount == 1

    def job_wait_for_tool(self, project_id: str, tool_call_id: str) -> dict[str, object] | None:
        self.migrate()
        with self._connect() as connection:
            row = connection.execute(
                """SELECT session_id,project_id,run_id,tool_call_id,tool_name,job_id,state
                FROM agent_job_waits WHERE project_id=? AND tool_call_id=?""",
                (project_id, tool_call_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def job_wait_for_approval(
        self, project_id: str, approval_id: str
    ) -> dict[str, object] | None:
        """Find the suspended Agent Tool Call bound to one durable approval.

        Atomic B02 Tool Calls have an internal ID while provider Tool Calls use
        the model-facing facade ID. The approval action ID is the durable,
        parameter-bound identifier shared by both layers, so it is the only
        safe join key at the desktop approval boundary.
        """

        self.migrate()
        with self._connect() as connection:
            waits = list(
                connection.execute(
                    "SELECT session_id,project_id,run_id,tool_call_id,tool_name,job_id,state "
                    "FROM agent_job_waits WHERE project_id=? "
                    "AND state IN('awaiting_ui_action','terminal_returned') ORDER BY created_at",
                    (project_id,),
                )
            )
            for wait in waits:
                events = connection.execute(
                    "SELECT payload_json FROM agent_api_events WHERE session_id=? "
                    "AND event_type='tool.completed' ORDER BY sequence_no DESC",
                    (wait["session_id"],),
                )
                for event in events:
                    try:
                        payload = json.loads(str(event["payload_json"]))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if not isinstance(payload, dict) or payload.get("tool_call_id") != wait["tool_call_id"]:
                        continue
                    result = payload.get("result")
                    details = result.get("details") if isinstance(result, dict) else None
                    action = details.get("ui_action") if isinstance(details, dict) else None
                    if isinstance(action, dict) and action.get("action_id") == approval_id:
                        return dict(wait)
        return None

    def job_wait_for_ui_action(
        self, project_id: str, action_id: str, action_type: str
    ) -> dict[str, object] | None:
        """Find one suspended desktop action by its durable action id and type."""

        self.migrate()
        with self._connect() as connection:
            waits = list(
                connection.execute(
                    "SELECT session_id,project_id,run_id,tool_call_id,tool_name,job_id,state "
                    "FROM agent_job_waits WHERE project_id=? AND state='awaiting_ui_action' "
                    "ORDER BY created_at",
                    (project_id,),
                )
            )
            for wait in waits:
                events = connection.execute(
                    "SELECT payload_json FROM agent_api_events WHERE session_id=? "
                    "AND event_type='tool.completed' ORDER BY sequence_no DESC",
                    (wait["session_id"],),
                )
                for event in events:
                    try:
                        payload = json.loads(str(event["payload_json"]))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if not isinstance(payload, dict) or payload.get("tool_call_id") != wait["tool_call_id"]:
                        continue
                    result = payload.get("result")
                    details = result.get("details") if isinstance(result, dict) else None
                    action = details.get("ui_action") if isinstance(details, dict) else None
                    if (
                        isinstance(action, dict)
                        and action.get("action_id") == action_id
                        and action.get("type") == action_type
                    ):
                        return dict(wait)
        return None

    def active_job_waits(self, project_id: str) -> list[dict[str, object]]:
        self.migrate()
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT session_id,project_id,run_id,tool_call_id,tool_name,job_id,state
                FROM agent_job_waits WHERE project_id=?
                AND state IN('awaiting_ui_action','waiting','waiting_external')""",
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def resumable_job_waits(self, session_id: str) -> list[dict[str, object]]:
        """Return approved waits that need monitoring or Agent continuation."""

        self.migrate()
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT session_id,project_id,run_id,tool_call_id,tool_name,job_id,state
                FROM agent_job_waits WHERE session_id=?
                AND state IN('waiting','waiting_external','terminal_returned')
                ORDER BY resumed_at,created_at""",
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def terminal_job_waits(self, session_id: str) -> list[dict[str, object]]:
        """Return completed approval waits that may still need an Agent reply."""

        self.migrate()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT session_id,project_id,run_id,tool_call_id,tool_name,job_id,state "
                "FROM agent_job_waits WHERE session_id=? AND state='terminal_returned' "
                "ORDER BY resumed_at,created_at",
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def pending_ui_actions(self, session_id: str) -> list[dict[str, object]]:
        """Recover desktop-only approval actions without closing their Tool Calls.

        ``awaiting_ui_action`` is deliberately not appended to the Agent
        transcript: doing so would make the model treat the suspended Tool Call
        as complete. The desktop still needs a durable way to restore its
        approval controls after a refresh or conversation switch, so replay the
        already-redacted ``tool.completed`` event for active waits only.
        """

        self.migrate()
        with self._connect() as connection:
            waits = list(
                connection.execute(
                    "SELECT tool_call_id,tool_name FROM agent_job_waits "
                    "WHERE session_id=? AND state='awaiting_ui_action' "
                    "ORDER BY created_at",
                    (session_id,),
                )
            )
            events = list(
                connection.execute(
                    "SELECT payload_json FROM agent_api_events WHERE session_id=? "
                    "AND event_type='tool.completed' ORDER BY sequence_no DESC",
                    (session_id,),
                )
            )
        by_call: dict[str, dict[str, object]] = {}
        for event in events:
            try:
                payload = json.loads(str(event["payload_json"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            call_id = payload.get("tool_call_id")
            result = payload.get("result")
            details = result.get("details") if isinstance(result, dict) else None
            if (
                not isinstance(call_id, str)
                or not isinstance(result, dict)
                or not isinstance(details, dict)
                or details.get("status") != "awaiting_ui_action"
                or not isinstance(details.get("ui_action"), dict)
            ):
                continue
            by_call.setdefault(call_id, result)
        return [
            {
                "tool_call_id": str(wait["tool_call_id"]),
                "tool_name": str(wait["tool_name"]),
                "result": by_call[str(wait["tool_call_id"])],
            }
            for wait in waits
            if str(wait["tool_call_id"]) in by_call
        ]

    async def listener(self, session_id: str, operation_id: str, event: AgentEvent) -> None:
        if event.type is AgentEventType.MESSAGE_END and isinstance(
            event.payload.get("message"), dict
        ):
            self.append_message(session_id, message_from_json(json.dumps(event.payload["message"])))
        elif event.type is AgentEventType.TOOL_EXECUTION_START:
            with self._connect() as connection:
                connection.execute(
                    "INSERT OR REPLACE INTO agent_tool_operations VALUES(?,?,?,?,?,?)",
                    (
                        session_id,
                        str(event.payload["tool_call_id"]),
                        operation_id,
                        str(event.payload["tool_name"]),
                        "running",
                        None,
                    ),
                )
        elif event.type is AgentEventType.TOOL_EXECUTION_END:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE agent_tool_operations SET state='completed', result_json=? WHERE session_id=? AND tool_call_id=?",
                    (
                        json.dumps(event.payload.get("result")),
                        session_id,
                        str(event.payload["tool_call_id"]),
                    ),
                )
        elif event.type is AgentEventType.AGENT_END:
            self.finish_operation(operation_id)

    def append_api_event(self, session_id: str, event_type: str, payload: dict[str, object]) -> int:
        with self._connect() as connection:
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence_no),0)+1 FROM agent_api_events WHERE session_id=?",
                (session_id,),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO agent_api_events VALUES(?,?,?,?,?)",
                (session_id, sequence, event_type, json.dumps(payload), _now()),
            )
        return int(sequence)

    def api_events(self, session_id: str, after: int, limit: int) -> list[sqlite3.Row]:
        safe_limit = min(max(limit, 1), 100)
        with self._connect() as connection:
            return list(
                connection.execute(
                    "SELECT sequence_no,event_type,payload_json,created_at FROM agent_api_events "
                    "WHERE session_id=? AND sequence_no>? ORDER BY sequence_no LIMIT ?",
                    (session_id, after, safe_limit),
                )
            )

    def api_event_cursor(self, session_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence_no),0) FROM agent_api_events WHERE session_id=?",
                (session_id,),
            ).fetchone()
        return int(row[0])

    def latest_api_event_payload(
        self, session_id: str, event_type: str
    ) -> dict[str, object] | None:
        """Return one durable event payload without making event history a new schema."""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM agent_api_events WHERE session_id=? AND event_type=? "
                "ORDER BY sequence_no DESC LIMIT 1",
                (session_id, event_type),
            ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(str(row["payload_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def terminal_error_code(self, session_id: str) -> str | None:
        """Return the durable outcome of the most recent conversation run.

        ``agent.idle`` is emitted by the core loop's ``finally`` block, so it
        can precede the runtime-level ``conversation.failed`` projection.  The
        semantic conversation events, rather than the low-level idle event,
        are therefore the recovery source of truth.
        """

        row = self._terminal_conversation_event(session_id)
        if row is None or row["event_type"] != "conversation.failed":
            return None
        try:
            payload = json.loads(str(row["payload_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            return "agent_error"
        code = payload.get("code") if isinstance(payload, dict) else None
        return str(code) if isinstance(code, str) and code else "agent_error"

    def terminal_outcome(self, session_id: str) -> str | None:
        """Return the latest semantic conversation terminal event, if any."""

        row = self._terminal_conversation_event(session_id)
        return str(row["event_type"]) if row is not None else None

    def _terminal_conversation_event(self, session_id: str) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute(
                "SELECT event_type,payload_json FROM agent_api_events "
                "WHERE session_id=? AND event_type IN ('conversation.completed','conversation.failed') "
                "ORDER BY sequence_no DESC LIMIT 1",
                (session_id,),
            ).fetchone()

    def _interrupt_unfinished(self, connection: sqlite3.Connection, session_id: str) -> None:
        ids = [
            row[0]
            for row in connection.execute(
                "SELECT id FROM agent_operations WHERE session_id=? AND state='running'",
                (session_id,),
            )
        ]
        if not ids:
            return
        connection.execute(
            "UPDATE agent_operations SET state='interrupted', ended_at=? WHERE session_id=? AND state='running'",
            (_now(), session_id),
        )
        connection.execute(
            "UPDATE agent_tool_operations SET state='interrupted' WHERE session_id=? AND state='running'",
            (session_id,),
        )

    def _interrupt_unfinished_compactions(
        self, connection: sqlite3.Connection, session_id: str
    ) -> None:
        connection.execute(
            "UPDATE agent_compactions SET state='interrupted' WHERE session_id=? AND state='started'",
            (session_id,),
        )

    @staticmethod
    def _apply_migration(connection: sqlite3.Connection, version: int, script: bytes) -> None:
        checksum = hashlib.sha256(script).hexdigest()
        row = connection.execute(
            "SELECT checksum FROM agent_schema_migrations WHERE version=?", (version,)
        ).fetchone()
        if row is not None and row[0] != checksum:
            raise RuntimeError("Agent session migration checksum mismatch")
        connection.executescript(script.decode("utf-8"))
        if row is None:
            connection.execute(
                "INSERT INTO agent_schema_migrations VALUES(?,?,?)", (version, checksum, _now())
            )

    @staticmethod
    def _record_to_dict(record: CompactionRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "reason": record.reason,
            "summary": record.summary,
            "first_kept_sequence": record.first_kept_sequence,
            "tokens_before": record.tokens_before,
            "tokens_after": record.tokens_after,
        }

    def _latest_compaction(
        self, connection: sqlite3.Connection, session_id: str
    ) -> CompactionRecord | None:
        row = connection.execute(
            "SELECT * FROM agent_compactions WHERE session_id=? AND state='committed' ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        tail = tuple(
            message_from_dict(item) for item in json.loads(row["retained_tail_json"] or "[]")
        )
        return CompactionRecord(
            row["id"],
            row["state"],
            row["reason"],
            row["summary"],
            row["first_kept_sequence"],
            tail,
            row["tokens_before"],
            row["tokens_after"],
            json.loads(row["usage_json"]) if row["usage_json"] else None,
            row["provider_id"],
            row["model"],
            row["previous_compaction_id"],
            row["created_at"],
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, isolation_level=None, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection
