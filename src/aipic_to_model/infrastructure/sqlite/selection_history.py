"""Append-only B02 selection revision history and restart-safe undo/redo."""

from __future__ import annotations

import json
from pathlib import Path

from ...domain.ids import canonical_json, new_id, utc_now
from .connection import connect, transaction


class SelectionHistoryRepository:
    def apply(
        self,
        database: Path,
        *,
        project_id: str,
        selection_id: str,
        expected_revision: int,
        command_type: str,
        geometry: dict[str, object] | None = None,
    ) -> int:
        if command_type not in {
            "move",
            "resize_n",
            "resize_ne",
            "resize_e",
            "resize_se",
            "resize_s",
            "resize_sw",
            "resize_w",
            "resize_nw",
            "numeric",
            "clear",
            "confirm",
        }:
            raise ValueError("unsupported selection command")
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                row = connection.execute(
                    "SELECT revision,geometry_json,status FROM selections WHERE id=? AND project_id=?",
                    (selection_id, project_id),
                ).fetchone()
                if row is None or row["revision"] != expected_revision:
                    raise ValueError("selection revision conflict")
                before = json.loads(row["geometry_json"])
                if command_type == "clear":
                    after = {"rects": []}
                else:
                    after = geometry if geometry is not None else before
                if command_type == "confirm" and (
                    not before.get("rects") or row["status"] == "confirmed"
                ):
                    raise ValueError("selection cannot be confirmed")
                next_revision = expected_revision + 1
                status = "confirmed" if command_type == "confirm" else row["status"]
                visual_state = "user_confirmed" if command_type == "confirm" else "user_edited"
                changed = connection.execute(
                    """UPDATE selections SET geometry_json=?,status=?,confirmed_by_user=?,visual_state=?,
                    revision=?,updated_at=? WHERE id=? AND revision=?""",
                    (
                        canonical_json(after),
                        status,
                        int(command_type == "confirm" or row["status"] == "confirmed"),
                        visual_state,
                        next_revision,
                        utc_now(),
                        selection_id,
                        expected_revision,
                    ),
                )
                if changed.rowcount != 1:
                    raise ValueError("selection revision conflict")
                connection.execute(
                    "INSERT INTO selection_revisions VALUES(?,?,?,?,?,?,?,?)",
                    (
                        selection_id,
                        next_revision,
                        command_type,
                        None,
                        canonical_json(before),
                        canonical_json(after),
                        new_id(),
                        utc_now(),
                    ),
                )
                return next_revision
        finally:
            connection.close()

    def undo(
        self, database: Path, *, project_id: str, selection_id: str, expected_revision: int
    ) -> int:
        return self._replay(database, project_id, selection_id, expected_revision, "undo")

    def redo(
        self, database: Path, *, project_id: str, selection_id: str, expected_revision: int
    ) -> int:
        return self._replay(database, project_id, selection_id, expected_revision, "redo")

    def _replay(
        self,
        database: Path,
        project_id: str,
        selection_id: str,
        expected_revision: int,
        command: str,
    ) -> int:
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                row = connection.execute(
                    "SELECT revision,geometry_json,status FROM selections WHERE id=? AND project_id=?",
                    (selection_id, project_id),
                ).fetchone()
                if row is None or row["revision"] != expected_revision:
                    raise ValueError("selection revision conflict")
                history = connection.execute(
                    "SELECT revision,command_type,before_json,after_json,target_revision FROM selection_revisions WHERE selection_id=? ORDER BY revision DESC",
                    (selection_id,),
                ).fetchall()
                if command == "undo":
                    target = next(
                        (
                            entry
                            for entry in history
                            if entry["command_type"] not in {"undo", "redo", "confirm"}
                        ),
                        None,
                    )
                    if target is None or target["before_json"] is None:
                        raise ValueError("nothing to undo")
                    after = json.loads(target["before_json"])
                    target_revision = target["revision"]
                else:
                    last = history[0] if history else None
                    if (
                        last is None
                        or last["command_type"] != "undo"
                        or last["target_revision"] is None
                    ):
                        raise ValueError("nothing to redo")
                    target = connection.execute(
                        "SELECT after_json FROM selection_revisions WHERE selection_id=? AND revision=?",
                        (selection_id, last["target_revision"]),
                    ).fetchone()
                    if target is None or target["after_json"] is None:
                        raise ValueError("nothing to redo")
                    after = json.loads(target["after_json"])
                    target_revision = last["revision"]
                next_revision = expected_revision + 1
                connection.execute(
                    """UPDATE selections SET geometry_json=?,visual_state='user_edited',revision=?,updated_at=?
                    WHERE id=? AND revision=?""",
                    (
                        canonical_json(after),
                        next_revision,
                        utc_now(),
                        selection_id,
                        expected_revision,
                    ),
                )
                connection.execute(
                    "INSERT INTO selection_revisions VALUES(?,?,?,?,?,?,?,?)",
                    (
                        selection_id,
                        next_revision,
                        command,
                        target_revision,
                        row["geometry_json"],
                        canonical_json(after),
                        new_id(),
                        utc_now(),
                    ),
                )
                return next_revision
        finally:
            connection.close()
