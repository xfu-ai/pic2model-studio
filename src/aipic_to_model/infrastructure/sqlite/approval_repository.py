"""Durable, parameter-bound approval records for B02 paid/external work."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...domain.ids import canonical_json, new_id, utc_now
from .connection import connect, transaction


@dataclass(frozen=True)
class StoredApproval:
    id: str
    project_id: str
    tool_call_id: str
    tool_name: str
    provider_profile: str
    arguments_hash: str
    scope_hash: str
    decision: str


class SqliteApprovalRepository:
    """Approvals are immutable in scope and can be consumed exactly once."""

    def request(
        self,
        database: Path,
        *,
        project_id: str,
        tool_call_id: str,
        tool_name: str,
        provider_profile: str,
        arguments_hash: str,
        scope_hash: str,
        input_asset_summary: list[str],
        cost_summary: dict[str, object],
        arguments_summary: dict[str, object],
    ) -> StoredApproval:
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                existing = connection.execute(
                    "SELECT * FROM production_approvals WHERE tool_call_id=?", (tool_call_id,)
                ).fetchone()
                if existing is not None:
                    return self._record(existing)
                approval_id = new_id()
                now = utc_now()
                connection.execute(
                    """INSERT INTO production_approvals(
                    id,project_id,tool_call_id,tool_name,provider_profile,arguments_hash,scope_hash,
                    input_asset_summary_json,cost_summary_json,arguments_summary_json,decision,requested_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        approval_id,
                        project_id,
                        tool_call_id,
                        tool_name,
                        provider_profile,
                        arguments_hash,
                        scope_hash,
                        canonical_json(input_asset_summary),
                        canonical_json(cost_summary),
                        canonical_json(arguments_summary),
                        "requires_user",
                        now,
                    ),
                )
                return self._load(connection, approval_id)
        finally:
            connection.close()

    def decide(self, database: Path, *, approval_id: str, approved: bool) -> StoredApproval:
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                current = self._load(connection, approval_id)
                if current.decision == "consumed":
                    raise ValueError("approval was already consumed")
                target = "approved" if approved else "denied"
                if current.decision == target:
                    return current
                if current.decision != "requires_user":
                    raise ValueError("approval decision is immutable")
                connection.execute(
                    "UPDATE production_approvals SET decision=?,decided_at=? WHERE id=?",
                    (target, utc_now(), approval_id),
                )
                return self._load(connection, approval_id)
        finally:
            connection.close()

    def consume(
        self,
        database: Path,
        *,
        approval_id: str,
        tool_call_id: str,
        provider_profile: str,
        arguments_hash: str,
        scope_hash: str,
    ) -> StoredApproval:
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                current = self._load(connection, approval_id)
                if current.tool_call_id != tool_call_id:
                    raise ValueError("approval is bound to a different tool call")
                if (current.provider_profile, current.arguments_hash, current.scope_hash) != (
                    provider_profile,
                    arguments_hash,
                    scope_hash,
                ):
                    raise ValueError("approval scope no longer matches this request")
                if current.decision != "approved":
                    raise ValueError("approval is not approved")
                connection.execute(
                    "UPDATE production_approvals SET decision='consumed',consumed_at=? WHERE id=?",
                    (utc_now(), approval_id),
                )
                return self._load(connection, approval_id)
        finally:
            connection.close()

    def get(self, database: Path, *, approval_id: str) -> StoredApproval:
        connection = connect(database, read_only=True)
        try:
            return self._load(connection, approval_id)
        finally:
            connection.close()

    def get_for_tool_call(self, database: Path, *, tool_call_id: str) -> StoredApproval:
        connection = connect(database, read_only=True)
        try:
            row = connection.execute(
                "SELECT * FROM production_approvals WHERE tool_call_id=?", (tool_call_id,)
            ).fetchone()
            if row is None:
                raise KeyError(tool_call_id)
            return self._record(row)
        finally:
            connection.close()

    @staticmethod
    def summaries(database: Path, *, approval_id: str) -> dict[str, Any]:
        connection = connect(database, read_only=True)
        try:
            row = connection.execute(
                "SELECT input_asset_summary_json,cost_summary_json,arguments_summary_json "
                "FROM production_approvals WHERE id=?",
                (approval_id,),
            ).fetchone()
            if row is None:
                raise KeyError(approval_id)
            return {
                "input_asset_ids": json.loads(str(row["input_asset_summary_json"])),
                "cost_summary": json.loads(str(row["cost_summary_json"])),
                "arguments_summary": json.loads(str(row["arguments_summary_json"])),
            }
        finally:
            connection.close()

    @staticmethod
    def _load(connection: Any, approval_id: str) -> StoredApproval:
        row = connection.execute(
            "SELECT * FROM production_approvals WHERE id=?", (approval_id,)
        ).fetchone()
        if row is None:
            raise KeyError(approval_id)
        return SqliteApprovalRepository._record(row)

    @staticmethod
    def _record(row: Any) -> StoredApproval:
        return StoredApproval(
            id=str(row["id"]),
            project_id=str(row["project_id"]),
            tool_call_id=str(row["tool_call_id"]),
            tool_name=str(row["tool_name"]),
            provider_profile=str(row["provider_profile"]),
            arguments_hash=str(row["arguments_hash"]),
            scope_hash=str(row["scope_hash"]),
            decision=str(row["decision"]),
        )
