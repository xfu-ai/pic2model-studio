"""Append-only B02 multiview member persistence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...domain.ids import new_id, utc_now
from .connection import connect, transaction


class MultiviewRepository:
    def create_set(
        self, database: Path, *, project_id: str, source_asset_id: str, members: dict[str, str]
    ) -> str:
        if set(members) != {"front", "side", "back"}:
            raise ValueError("a multiview set requires front, side, and back")
        set_id, now = new_id(), utc_now()
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                connection.execute(
                    "INSERT INTO multiview_sets(id,project_id,source_asset_id,status,created_at,updated_at) VALUES(?,?,?,'draft',?,?)",
                    (set_id, project_id, source_asset_id, now, now),
                )
                connection.executemany(
                    "INSERT INTO multiview_members(set_id,view_name,revision,asset_id,is_current,ordinal,created_at) VALUES(?,?,1,?,1,?,?)",
                    [
                        (set_id, view, asset, ordinal, now)
                        for ordinal, (view, asset) in enumerate(members.items(), start=1)
                    ],
                )
            return set_id
        finally:
            connection.close()

    def regenerate_view(self, database: Path, *, set_id: str, view_name: str, asset_id: str) -> int:
        if view_name not in {"front", "side", "back"}:
            raise ValueError("unknown multiview member")
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                current = connection.execute(
                    "SELECT revision,ordinal FROM multiview_members WHERE set_id=? AND view_name=? AND is_current=1",
                    (set_id, view_name),
                ).fetchone()
                if current is None:
                    raise KeyError(view_name)
                next_revision, now = int(current["revision"]) + 1, utc_now()
                connection.execute(
                    "UPDATE multiview_members SET is_current=0 WHERE set_id=? AND view_name=? AND is_current=1",
                    (set_id, view_name),
                )
                connection.execute(
                    "INSERT INTO multiview_members(set_id,view_name,revision,asset_id,is_current,ordinal,created_at) VALUES(?,?,?,?,1,?,?)",
                    (set_id, view_name, next_revision, asset_id, current["ordinal"], now),
                )
                self._invalidate_validation(connection, set_id=set_id, now=now)
                return next_revision
        finally:
            connection.close()

    def current_assets(self, database: Path, set_id: str) -> dict[str, str]:
        connection = connect(database, read_only=True)
        try:
            return {
                str(row["view_name"]): str(row["asset_id"])
                for row in connection.execute(
                    "SELECT view_name,asset_id FROM multiview_members WHERE set_id=? AND is_current=1",
                    (set_id,),
                )
            }
        finally:
            connection.close()

    def attach_regions(self, database: Path, *, set_id: str, selection_ids: dict[str, str]) -> None:
        if set(selection_ids) != {"front", "side", "back"}:
            raise ValueError("regions require front, side, and back selections")
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                connection.executemany(
                    """UPDATE multiview_members SET selection_id=?
                    WHERE set_id=? AND view_name=? AND is_current=1""",
                    [(selection_id, set_id, view) for view, selection_id in selection_ids.items()],
                )
                self._invalidate_validation(connection, set_id=set_id, now=utc_now())
        finally:
            connection.close()

    def region_selection_ids(self, database: Path, *, set_id: str) -> dict[str, str]:
        connection = connect(database, read_only=True)
        try:
            rows = connection.execute(
                """SELECT view_name,selection_id FROM multiview_members
                WHERE set_id=? AND is_current=1""",
                (set_id,),
            ).fetchall()
            if len(rows) != 3 or any(row["selection_id"] is None for row in rows):
                raise ValueError("multiview regions have not been created")
            return {str(row["view_name"]): str(row["selection_id"]) for row in rows}
        finally:
            connection.close()

    def record_validation(self, database: Path, *, set_id: str, validation: dict[str, Any]) -> None:
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                changed = connection.execute(
                    "UPDATE multiview_sets SET status=?,validation_json=?,updated_at=? WHERE id=?",
                    (
                        "validated" if validation["can_continue"] else "blocked",
                        json.dumps(validation, separators=(",", ":"), ensure_ascii=False),
                        utc_now(),
                        set_id,
                    ),
                )
                if changed.rowcount != 1:
                    raise KeyError(set_id)
        finally:
            connection.close()

    def is_ready_for_submission(
        self, database: Path, *, set_id: str, members: dict[str, str]
    ) -> bool:
        """Return true only when the current exact views have a passing user validation."""
        if set(members) != {"front", "side", "back"}:
            return False
        connection = connect(database, read_only=True)
        try:
            row = connection.execute(
                "SELECT status,validation_json FROM multiview_sets WHERE id=?", (set_id,)
            ).fetchone()
            if row is None or row["status"] != "validated" or not row["validation_json"]:
                return False
            if self.current_assets(database, set_id) != members:
                return False
            validation = json.loads(str(row["validation_json"]))
            return validation.get("can_continue") is True
        except TypeError, ValueError, json.JSONDecodeError:
            return False
        finally:
            connection.close()

    @staticmethod
    def _invalidate_validation(connection: Any, *, set_id: str, now: str) -> None:
        connection.execute(
            "UPDATE multiview_sets SET status='draft',validation_json='{}',updated_at=? WHERE id=?",
            (now, set_id),
        )
