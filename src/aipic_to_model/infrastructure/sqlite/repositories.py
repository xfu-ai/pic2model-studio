"""Small B01 repositories: the only SQL boundary used by project services."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import cast

from ...application.events import EventService, NewEvent
from ...domain.common import (
    DomainErrorV1,
    ErrorCode,
    EventEnvelopeV1,
    canonical_json,
    new_id,
    utc_now,
)
from ...domain.event_payloads import validate_event_payload
from ...domain.provenance import ProvenanceV1
from ..fs.atomic_io import atomic_write_text
from .connection import connect, migrate_app, transaction


class SqliteAppStateRepository:
    """SQLite adapter for app-wide commands and sidecar health inspection."""

    def replay_operation(
        self, app_db: Path, action: str, payload_hash: str, request_id: str
    ) -> dict[str, object] | None:
        migrate_app(app_db)
        connection = connect(app_db)
        try:
            previous = connection.execute(
                "SELECT action,payload_hash,result_json FROM app_operations WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if previous is None:
                return None
            if previous["action"] != action or previous["payload_hash"] != payload_hash:
                raise DomainErrorV1(ErrorCode.IDEMPOTENCY_CONFLICT, "request_id conflict")
            return json.loads(previous["result_json"])
        finally:
            connection.close()

    def complete_operation(
        self,
        app_db: Path,
        action: str,
        payload_hash: str,
        request_id: str,
        result: dict[str, object],
    ) -> None:
        migrate_app(app_db)
        connection = connect(app_db)
        try:
            with transaction(connection, immediate=True):
                connection.execute(
                    "INSERT INTO app_operations VALUES(?,?,?,?,?)",
                    (request_id, action, payload_hash, canonical_json(result), utc_now()),
                )
        finally:
            connection.close()

    def health_snapshot(self, roots: tuple[Path, ...], app_db: Path) -> dict[str, object]:
        connections: list[sqlite3.Connection] = []
        try:
            unfinished = 0
            migration = "not_opened"
            for root in roots:
                connection = connect(root / "project.sqlite3")
                connections.append(connection)
                unfinished += int(
                    connection.execute(
                        "SELECT COUNT(*) FROM operations WHERE state!='completed'"
                    ).fetchone()[0]
                )
                migration = str(
                    connection.execute(
                        "SELECT COALESCE(MAX(version),0) FROM schema_migrations"
                    ).fetchone()[0]
                )
            app_available = app_db.exists() or app_db.parent.exists()
            disk_target = app_db.parent if app_db.parent.exists() else Path(tempfile.gettempdir())
            return {
                "sidecar": "available",
                "project_db": "available" if roots else "not_opened",
                "app_db": "available" if app_available else "unavailable",
                "project_root": "available" if roots else "not_opened",
                "disk_free_bytes": shutil.disk_usage(disk_target).free,
                "keyring": "unknown",
                "migration": migration,
                "unfinished_operations": unfinished,
                "event_lag": 0,
            }
        finally:
            for connection in connections:
                connection.close()

    def record_recent_project(self, app_db: Path, project_id: str, root: Path) -> None:
        migrate_app(app_db)
        connection = connect(app_db)
        try:
            with transaction(connection, immediate=True):
                connection.execute(
                    "INSERT INTO recent_projects(project_id,root_path,last_opened_at,availability) VALUES(?,?,?,?) "
                    "ON CONFLICT(project_id) DO UPDATE SET root_path=excluded.root_path,last_opened_at=excluded.last_opened_at,availability=excluded.availability",
                    (project_id, str(root.resolve(strict=False)), utc_now(), "available"),
                )
        finally:
            connection.close()

    def list_recent_projects(self, app_db: Path) -> list[dict[str, object]]:
        migrate_app(app_db)
        connection = connect(app_db)
        try:
            rows = connection.execute(
                "SELECT project_id,root_path,last_opened_at,availability FROM recent_projects ORDER BY last_opened_at DESC"
            ).fetchall()
        finally:
            connection.close()
        results: list[dict[str, object]] = []
        for row in rows:
            root = Path(row["root_path"])
            try:
                metadata = json.loads((root / "project.json").read_text(encoding="utf-8"))
                name = str(metadata["name"])
                availability = "available" if root.is_dir() else "unavailable"
            except (FileNotFoundError, KeyError, json.JSONDecodeError, OSError):
                name, availability = "Unavailable project", "unavailable"
            results.append({"id": row["project_id"], "name": name, "availability": availability, "last_opened_at": row["last_opened_at"]})
        return results

    def recent_project_root(self, app_db: Path, project_id: str) -> Path | None:
        migrate_app(app_db)
        connection = connect(app_db)
        try:
            row = connection.execute("SELECT root_path FROM recent_projects WHERE project_id=?", (project_id,)).fetchone()
            return Path(row["root_path"]) if row is not None else None
        finally:
            connection.close()


class SettingsRepository:
    """Owns all B01 settings and app-command SQL transactions."""

    @staticmethod
    def _read_settings(connection: sqlite3.Connection, table: str) -> dict[str, object]:
        rows = connection.execute(f"SELECT key,value_json FROM {table} ORDER BY key").fetchall()
        return {row["key"]: json.loads(row["value_json"]) for row in rows}

    def get_app(self, app_db: Path) -> dict[str, object]:
        migrate_app(app_db)
        connection = connect(app_db)
        try:
            return self._read_settings(connection, "app_settings")
        finally:
            connection.close()

    def update_app(
        self, app_db: Path, patch: dict[str, object], payload_hash: str, request_id: str | None
    ) -> dict[str, object]:
        migrate_app(app_db)
        connection = connect(app_db)
        try:
            with transaction(connection, immediate=True):
                if request_id:
                    previous = connection.execute(
                        "SELECT action,payload_hash,result_json FROM app_operations WHERE request_id=?",
                        (request_id,),
                    ).fetchone()
                    if previous:
                        if (
                            previous["action"] != "settings.update_app"
                            or previous["payload_hash"] != payload_hash
                        ):
                            raise DomainErrorV1(
                                ErrorCode.IDEMPOTENCY_CONFLICT,
                                "request_id is already bound to a different command",
                            )
                        return json.loads(previous["result_json"])
                for key, value in patch.items():
                    connection.execute(
                        "INSERT INTO app_settings VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
                        (key, canonical_json(value), utc_now()),
                    )
                result = self._read_settings(connection, "app_settings")
                if request_id:
                    connection.execute(
                        "INSERT INTO app_operations VALUES(?,?,?,?,?)",
                        (
                            request_id,
                            "settings.update_app",
                            payload_hash,
                            canonical_json(result),
                            utc_now(),
                        ),
                    )
                return result
        finally:
            connection.close()

    def update_project(
        self, database: Path, patch: dict[str, object], payload_hash: str, request_id: str | None
    ) -> dict[str, object]:
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                operation_id: str | None = None
                if request_id:
                    previous = connection.execute(
                        "SELECT payload_json,recovery_json,state FROM operations WHERE idempotency_key=?",
                        (request_id,),
                    ).fetchone()
                    if previous:
                        payload, recovery = (
                            json.loads(previous["payload_json"]),
                            json.loads(previous["recovery_json"]),
                        )
                        if (
                            payload.get("action") != "settings.update_project"
                            or payload.get("payload_hash") != payload_hash
                        ):
                            raise DomainErrorV1(
                                ErrorCode.IDEMPOTENCY_CONFLICT, "request_id conflict"
                            )
                        if previous["state"] == "completed" and "result" in recovery:
                            return recovery["result"]
                        raise DomainErrorV1(
                            ErrorCode.IDEMPOTENCY_CONFLICT, "settings command is incomplete"
                        )
                    operation_id = new_id()
                    now = utc_now()
                    connection.execute(
                        "INSERT INTO operations VALUES(?,?,?,?,?,?,?,?)",
                        (
                            operation_id,
                            "settings_project",
                            "prepared",
                            request_id,
                            canonical_json(
                                {"action": "settings.update_project", "payload_hash": payload_hash}
                            ),
                            "{}",
                            now,
                            now,
                        ),
                    )
                for key, value in patch.items():
                    connection.execute(
                        "INSERT INTO project_settings VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
                        (key, canonical_json(value), utc_now()),
                    )
                result = self._read_settings(connection, "project_settings")
                if operation_id:
                    connection.execute(
                        "UPDATE operations SET state='completed',recovery_json=?,updated_at=? WHERE id=?",
                        (canonical_json({"result": result}), utc_now(), operation_id),
                    )
                return result
        finally:
            connection.close()

    def replay_app_operation(
        self, app_db: Path, action: str, payload_hash: str, request_id: str
    ) -> dict[str, object] | None:
        return SqliteAppStateRepository().replay_operation(app_db, action, payload_hash, request_id)

    def record_app_operation(
        self,
        app_db: Path,
        action: str,
        payload_hash: str,
        request_id: str,
        result: dict[str, object],
    ) -> None:
        SqliteAppStateRepository().complete_operation(
            app_db, action, payload_hash, request_id, result
        )

class EventRepository:
    """SQLite persistence for the append-only B01 event log."""

    def append(
        self,
        connection: sqlite3.Connection,
        event: EventEnvelopeV1,
    ) -> None:
        connection.execute(
            "INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                event.event_id,
                event.event_type,
                event.event_version,
                event.project_id,
                event.conversation_id,
                event.run_id,
                event.entity_id,
                event.sequence_no,
                canonical_json(event.payload),
                event.created_at,
            ),
        )
        connection.execute(
            "UPDATE event_counters SET next_sequence_no=? WHERE project_id=?",
            (event.sequence_no + 1, event.project_id),
        )

    def append_named(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        event_type: str,
        payload: Mapping[str, object],
        entity_id: str | None = None,
        run_id: str | None = None,
    ) -> EventEnvelopeV1:
        validate_event_payload(event_type, dict(payload))
        event = EventEnvelopeV1(
            new_id(),
            event_type,
            1,
            project_id,
            self.next_sequence(connection, project_id),
            dict(payload),
            utc_now(),
            run_id=run_id,
            entity_id=entity_id,
        )
        self.append(connection, event)
        return event

    def append_named_in_tx(
        self,
        transaction: object,
        project_id: str,
        event_type: str,
        payload: Mapping[str, object],
        entity_id: str | None = None,
        run_id: str | None = None,
    ) -> EventEnvelopeV1:
        if not isinstance(transaction, sqlite3.Connection):
            raise TypeError("SQLite event transaction handle is invalid")
        if not transaction.in_transaction:
            raise RuntimeError("append_in_tx requires an active caller-owned transaction")
        return self.append_named(
            transaction,
            project_id,
            event_type,
            payload,
            entity_id,
            run_id,
        )

    def next_sequence(self, connection: sqlite3.Connection, project_id: str) -> int:
        row = connection.execute(
            "SELECT next_sequence_no FROM event_counters WHERE project_id=?", (project_id,)
        ).fetchone()
        return int(row[0])

    def replay(
        self, connection: sqlite3.Connection, project_id: str, sequence: int, limit: int
    ) -> list[sqlite3.Row]:
        return connection.execute(
            "SELECT * FROM events WHERE project_id=? AND sequence_no>? ORDER BY sequence_no ASC LIMIT ?",
            (project_id, sequence, min(max(limit, 1), 1000)),
        ).fetchall()

    def replay_project(
        self, database: Path, project_id: str, sequence: int, limit: int
    ) -> list[sqlite3.Row]:
        connection = connect(database)
        try:
            return self.replay(connection, project_id, sequence, limit)
        finally:
            connection.close()

    def append_named_committed(
        self,
        database: Path,
        project_id: str,
        event_type: str,
        payload: Mapping[str, object],
        entity_id: str | None = None,
        run_id: str | None = None,
    ) -> EventEnvelopeV1:
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                return self.append_named(
                    connection, project_id, event_type, payload, entity_id, run_id
                )
        finally:
            connection.close()

    def ack_committed(
        self, database: Path, project_id: str, consumer_id: str, sequence_no: int
    ) -> None:
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                self.ack(connection, project_id, consumer_id, sequence_no)
        finally:
            connection.close()

    def append_named_many_committed(
        self, database: Path, project_id: str, entries: Sequence[Mapping[str, object]]
    ) -> None:
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                for entry in entries:
                    self.append_named(
                        connection,
                        project_id,
                        str(entry["event_type"]),
                        cast(Mapping[str, object], entry["payload"]),
                    )
        finally:
            connection.close()

    def ack(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        consumer_id: str,
        sequence_no: int,
    ) -> None:
        old = connection.execute(
            "SELECT last_sequence_no FROM event_consumers WHERE project_id=? AND consumer_id=?",
            (project_id, consumer_id),
        ).fetchone()
        if old is None:
            connection.execute(
                "INSERT INTO event_consumers VALUES(?,?,?,?)",
                (project_id, consumer_id, max(0, sequence_no), utc_now()),
            )
        elif sequence_no > old[0]:
            connection.execute(
                "UPDATE event_consumers SET last_sequence_no=?,updated_at=? WHERE project_id=? AND consumer_id=?",
                (sequence_no, utc_now(), project_id, consumer_id),
            )


def _append_business_event(
    connection: sqlite3.Connection,
    project_id: str,
    event_type: str,
    payload: Mapping[str, object],
    entity_id: str | None = None,
    run_id: str | None = None,
) -> EventEnvelopeV1:
    """Route every business event through the public same-transaction service."""
    return EventService(EventRepository()).append_in_tx(
        NewEvent(
            transaction=connection,
            project_id=project_id,
            event_type=event_type,
            payload=dict(payload),
            entity_id=entity_id,
            run_id=run_id,
        )
    )


class SelectionRepository:
    """Read-side and idempotency queries for persisted B01 selections."""

    def get(
        self,
        database: Path,
        project_id: str,
        selection_id: str,
        *,
        read_only: bool = False,
    ) -> sqlite3.Row | None:
        connection = connect(database, read_only=read_only)
        try:
            return connection.execute(
                "SELECT * FROM selections WHERE id=? AND project_id=?", (selection_id, project_id)
            ).fetchone()
        finally:
            connection.close()

    def ids_for_asset(self, database: Path, project_id: str, asset_id: str) -> list[str]:
        connection = connect(database)
        try:
            rows = connection.execute(
                "SELECT id FROM selections WHERE project_id=? AND asset_id=? ORDER BY updated_at,id",
                (project_id, asset_id),
            ).fetchall()
            return [str(row["id"]) for row in rows]
        finally:
            connection.close()

    def current_id(
        self,
        database: Path,
        project_id: str,
        asset_id: str,
        *,
        read_only: bool = False,
    ) -> str | None:
        connection = connect(database, read_only=read_only)
        try:
            row = connection.execute(
                "SELECT id FROM selections WHERE project_id=? AND asset_id=? ORDER BY updated_at DESC LIMIT 1",
                (project_id, asset_id),
            ).fetchone()
            return None if row is None else str(row["id"])
        finally:
            connection.close()

    def editable_metadata(
        self, database: Path, project_id: str, asset_id: str
    ) -> sqlite3.Row | None:
        connection = connect(database)
        try:
            return self.editable_asset(connection, project_id, asset_id)
        finally:
            connection.close()

    @staticmethod
    def confirm(
        connection: sqlite3.Connection, project_id: str, selection_id: str, expected_revision: int
    ) -> sqlite3.Row | None:
        changed = connection.execute(
            "UPDATE selections SET status='confirmed',confirmed_by_user=1,"
            "revision=revision+1,updated_at=? "
            "WHERE id=? AND project_id=? AND revision=? AND status!='confirmed'",
            (utc_now(), selection_id, project_id, expected_revision),
        ).rowcount
        if changed != 1:
            return None
        return connection.execute(
            "SELECT * FROM selections WHERE id=? AND project_id=?", (selection_id, project_id)
        ).fetchone()

    @staticmethod
    def editable_asset(
        connection: sqlite3.Connection, project_id: str, asset_id: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT relative_path,metadata_json FROM assets WHERE id=? AND project_id=? AND asset_type IN ('source_image','generated_image','annotation','crop','multiview') AND trashed_at IS NULL",
            (asset_id, project_id),
        ).fetchone()

    @staticmethod
    def upsert(
        connection: sqlite3.Connection,
        project_id: str,
        asset_id: str,
        selection_id: str,
        expected_revision: int | None,
        rects: Sequence[Mapping[str, object]],
        label: str,
        confidence: float | None,
        source: str,
        status: str,
    ) -> sqlite3.Row | None:
        old = connection.execute(
            "SELECT revision,status FROM selections WHERE id=? AND project_id=?",
            (selection_id, project_id),
        ).fetchone()
        now, geometry = utc_now(), canonical_json({"rects": rects})
        if old:
            if (
                old["status"] == "confirmed"
                or expected_revision != old["revision"]
                or status not in {"draft", "edited"}
                or (old["status"] == "edited" and status != "edited")
            ):
                return None
            connection.execute(
                """UPDATE selections SET geometry_json=?,label=?,confidence=?,source=?,status=?,visual_state=?,
                revision=?,updated_at=? WHERE id=?""",
                (
                    geometry,
                    label,
                    confidence,
                    source,
                    status,
                    "agent_suggested" if source == "agent" else "user_edited",
                    old["revision"] + 1,
                    now,
                    selection_id,
                ),
            )
        else:
            if status != "draft":
                return None
            connection.execute(
                """INSERT INTO selections(
                id,project_id,asset_id,selection_type,geometry_json,label,confidence,source,status,
                confirmed_by_user,revision,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    selection_id,
                    project_id,
                    asset_id,
                    "rect" if len(rects) == 1 else "multi_rect",
                    geometry,
                    label,
                    confidence,
                    source,
                    status,
                    0,
                    1,
                    now,
                    now,
                ),
            )
        return connection.execute(
            "SELECT * FROM selections WHERE id=? AND project_id=?", (selection_id, project_id)
        ).fetchone()

    def source_asset(self, database: Path, project_id: str, asset_id: str) -> sqlite3.Row | None:
        connection = connect(database)
        try:
            return connection.execute(
                "SELECT relative_path,sha256 FROM assets WHERE id=? AND project_id=? AND trashed_at IS NULL",
                (asset_id, project_id),
            ).fetchone()
        finally:
            connection.close()

    def cancellation_exists(
        self, connection: sqlite3.Connection, project_id: str, action_id: str
    ) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM events WHERE project_id=? AND event_type='selection.cancelled' AND payload_json LIKE ?",
                (project_id, f"%{action_id}%"),
            ).fetchone()
            is not None
        )

    @staticmethod
    def _append_event(
        connection: sqlite3.Connection,
        project_id: str,
        event_type: str,
        payload: Mapping[str, object],
        entity_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        _append_business_event(connection, project_id, event_type, payload, entity_id, run_id)

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, object]:
        result = dict(row)
        result["rects"] = json.loads(str(result.pop("geometry_json")))["rects"]
        result["confirmed_by_user"] = bool(result["confirmed_by_user"])
        return result

    def save_committed(
        self,
        database: Path,
        *,
        project_id: str,
        asset_id: str,
        selection_id: str,
        expected_revision: int | None,
        rects: Sequence[Mapping[str, object]],
        label: str,
        confidence: float | None,
        source: str,
        status: str,
        payload_hash: str,
        request_id: str | None,
    ) -> dict[str, object]:
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                operation_id: str | None = None
                if request_id:
                    previous = OperationRepository(connection).command(request_id)
                    if previous:
                        payload, recovery = (
                            json.loads(previous["payload_json"]),
                            json.loads(previous["recovery_json"]),
                        )
                        if (
                            payload.get("action") != "selection.save"
                            or payload.get("payload") != payload_hash
                        ):
                            raise DomainErrorV1(
                                ErrorCode.IDEMPOTENCY_CONFLICT, "request_id conflict"
                            )
                        if previous["state"] == "completed" and "result" in recovery:
                            return recovery["result"]
                        raise DomainErrorV1(
                            ErrorCode.IDEMPOTENCY_CONFLICT, "selection command incomplete"
                        )
                    operation_id = OperationRepository(connection).prepare(
                        "selection_save",
                        request_id,
                        {"action": "selection.save", "payload": payload_hash},
                    )
                if self.editable_asset(connection, project_id, asset_id) is None:
                    raise DomainErrorV1(
                        ErrorCode.INVALID_SELECTION, "selection asset is unavailable"
                    )
                saved = self.upsert(
                    connection,
                    project_id,
                    asset_id,
                    selection_id,
                    expected_revision,
                    rects,
                    label,
                    confidence,
                    source,
                    status,
                )
                if saved is None:
                    raise DomainErrorV1(ErrorCode.INVALID_SELECTION, "Selection version conflict.")
                result = self._public(saved)
                self._append_event(
                    connection,
                    project_id,
                    "selection.changed",
                    {
                        "selection_id": selection_id,
                        "asset_id": asset_id,
                        "revision": saved["revision"],
                        "status": status,
                    },
                    selection_id,
                )
                if operation_id:
                    OperationRepository(connection).complete_with_result(operation_id, result)
                return result
        finally:
            connection.close()

    def confirm_committed(
        self,
        database: Path,
        *,
        project_id: str,
        selection_id: str,
        expected_revision: int,
        payload_hash: str,
        request_id: str | None,
    ) -> dict[str, object]:
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                operation_id: str | None = None
                if request_id:
                    previous = OperationRepository(connection).command(request_id)
                    if previous:
                        payload, recovery = (
                            json.loads(previous["payload_json"]),
                            json.loads(previous["recovery_json"]),
                        )
                        if (
                            payload.get("action") != "selection.confirm"
                            or payload.get("payload") != payload_hash
                        ):
                            raise DomainErrorV1(
                                ErrorCode.IDEMPOTENCY_CONFLICT, "request_id conflict"
                            )
                        if previous["state"] == "completed" and "result" in recovery:
                            return recovery["result"]
                        raise DomainErrorV1(
                            ErrorCode.IDEMPOTENCY_CONFLICT, "selection command incomplete"
                        )
                    operation_id = OperationRepository(connection).prepare(
                        "selection_confirm",
                        request_id,
                        {"action": "selection.confirm", "payload": payload_hash},
                    )
                confirmed = self.confirm(connection, project_id, selection_id, expected_revision)
                if confirmed is None:
                    raise DomainErrorV1(
                        ErrorCode.INVALID_SELECTION, "Selection cannot be confirmed."
                    )
                selection = self._public(confirmed)
                event = _append_business_event(
                    connection,
                    project_id,
                    "selection.changed",
                    {
                        "selection_id": selection_id,
                        "asset_id": confirmed["asset_id"],
                        "revision": confirmed["revision"],
                        "status": "confirmed",
                    },
                    selection_id,
                )
                result = {"selection": selection, "event": event.__dict__}
                if operation_id:
                    OperationRepository(connection).complete_with_result(operation_id, result)
                return result
        finally:
            connection.close()

    def cancel_committed(
        self,
        database: Path,
        *,
        project_id: str,
        selection_id: str | None,
        action_id: str,
        run_id: str | None,
    ) -> None:
        connection = connect(database)
        try:
            with transaction(connection):
                if not self.cancellation_exists(connection, project_id, action_id):
                    self._append_event(
                        connection,
                        project_id,
                        "selection.cancelled",
                        {
                            "action_id": action_id,
                            "selection_id": selection_id,
                            "run_id": run_id,
                        },
                        selection_id,
                        run_id,
                    )
        finally:
            connection.close()


class AssetRepository:
    """Read-side asset, lineage and usage persistence for B01 services."""

    def get(
        self,
        database: Path,
        project_id: str,
        asset_id: str,
        *,
        read_only: bool = False,
    ) -> sqlite3.Row | None:
        connection = connect(database, read_only=read_only)
        try:
            return connection.execute(
                "SELECT * FROM assets WHERE id=? AND project_id=?", (asset_id, project_id)
            ).fetchone()
        finally:
            connection.close()

    def content(self, database: Path, project_id: str, asset_id: str) -> sqlite3.Row | None:
        connection = connect(database)
        try:
            return connection.execute(
                "SELECT relative_path,mime_type,sha256,size_bytes FROM assets WHERE id=? AND project_id=? AND trashed_at IS NULL",
                (asset_id, project_id),
            ).fetchone()
        finally:
            connection.close()

    def usage(
        self, database: Path, project_id: str, asset_id: str
    ) -> tuple[bool, int, int, int] | None:
        connection = connect(database)
        try:
            asset = connection.execute(
                "SELECT is_current FROM assets WHERE id=? AND project_id=?", (asset_id, project_id)
            ).fetchone()
            if asset is None:
                return None
            children = connection.execute(
                "SELECT COUNT(*) FROM assets WHERE project_id=? AND parent_asset_id=? AND trashed_at IS NULL",
                (project_id, asset_id),
            ).fetchone()[0]
            inbound = connection.execute(
                "SELECT COUNT(*) FROM asset_links WHERE to_asset_id=?", (asset_id,)
            ).fetchone()[0]
            outbound = connection.execute(
                "SELECT COUNT(*) FROM asset_links WHERE from_asset_id=?", (asset_id,)
            ).fetchone()[0]
            return bool(asset[0]), int(children), int(inbound), int(outbound)
        finally:
            connection.close()

    @staticmethod
    def active_hashes(
        connection: sqlite3.Connection,
        project_id: str,
        asset_ids: list[str],
        *,
        include_trashed: bool = False,
    ) -> list[str] | None:
        hashes: list[str] = []
        for asset_id in asset_ids:
            row = connection.execute(
                "SELECT sha256 FROM assets WHERE id=? AND project_id=? "
                "AND (? OR trashed_at IS NULL)",
                (asset_id, project_id, include_trashed),
            ).fetchone()
            if row is None:
                return None
            hashes.append(str(row["sha256"]))
        return hashes

    @staticmethod
    def operation(connection: sqlite3.Connection, request_id: str) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT id,payload_json,recovery_json,state FROM operations WHERE idempotency_key=?",
            (request_id,),
        ).fetchone()

    @staticmethod
    def delete_operation(connection: sqlite3.Connection, operation_id: str) -> None:
        connection.execute("DELETE FROM operations WHERE id=?", (operation_id,))

    @staticmethod
    def valid_parent(connection: sqlite3.Connection, project_id: str, asset_id: str) -> bool:
        row = connection.execute(
            "SELECT project_id,trashed_at FROM assets WHERE id=?", (asset_id,)
        ).fetchone()
        return bool(row and row["project_id"] == project_id and row["trashed_at"] is None)

    @staticmethod
    def register_import(
        connection: sqlite3.Connection,
        *,
        asset_id: str,
        project_id: str,
        parent_asset_id: str | None,
        asset_type: str,
        asset_group: str | None,
        name: str,
        relative_path: str,
        mime: str,
        size: int,
        digest: str,
        metadata: Mapping[str, object],
        provenance: Mapping[str, object],
        created_at: str,
        thumbnail: tuple[str, str, str, int, str] | None,
    ) -> None:
        family_id, version_no = asset_id, 1
        if parent_asset_id is not None:
            parent = connection.execute(
                "SELECT asset_family_id, asset_type FROM assets WHERE id=? AND project_id=? AND trashed_at IS NULL",
                (parent_asset_id, project_id),
            ).fetchone()
            if parent is None:
                raise DomainErrorV1(ErrorCode.ASSET_NOT_FOUND, "parent asset is not available")
            if parent["asset_type"] != asset_type:
                raise DomainErrorV1(ErrorCode.INVALID_ASSET_CONTENT, "new version must keep the same asset type")
            family_id = str(parent["asset_family_id"])
            version_no = int(connection.execute(
                "SELECT COALESCE(MAX(version_no), 0) + 1 FROM assets WHERE project_id=? AND asset_family_id=?",
                (project_id, family_id),
            ).fetchone()[0])
        connection.execute(
            "INSERT INTO assets VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                asset_id,
                project_id,
                family_id,
                parent_asset_id,
                asset_type,
                asset_group,
                name,
                version_no,
                relative_path,
                None,
                mime,
                size,
                digest,
                canonical_json(metadata),
                canonical_json(ProvenanceV1.model_validate(provenance).model_dump(mode="json")),
                0,
                0,
                None,
                None,
                created_at,
            ),
        )
        if thumbnail is None:
            return
        thumbnail_id, thumbnail_rel, thumbnail_digest, thumbnail_size, thumbnail_name = thumbnail
        connection.execute(
            "INSERT INTO assets VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                thumbnail_id,
                project_id,
                thumbnail_id,
                asset_id,
                "preview",
                None,
                thumbnail_name,
                1,
                thumbnail_rel,
                None,
                "image/jpeg",
                thumbnail_size,
                thumbnail_digest,
                canonical_json({"width": None, "height": None, "format": "JPEG"}),
                canonical_json(
                    {
                        "schema_version": 1,
                        "source_kind": "conversion",
                        "input_asset_ids": [asset_id],
                        "parameters": {"operation": "thumbnail"},
                        "created_at": created_at,
                    }
                ),
                0,
                0,
                None,
                None,
                created_at,
            ),
        )
        connection.execute(
            "INSERT INTO asset_links VALUES(?,?,?)", (thumbnail_id, asset_id, "preview_of")
        )
        connection.execute(
            "UPDATE assets SET thumbnail_asset_id=? WHERE id=?", (thumbnail_id, asset_id)
        )

    @staticmethod
    def impact_rows(
        connection: sqlite3.Connection, project_id: str, asset_id: str
    ) -> tuple[list[sqlite3.Row], list[sqlite3.Row], list[sqlite3.Row], str | None]:
        children = connection.execute(
            """
            WITH RECURSIVE descendants(id) AS (
              SELECT id FROM assets WHERE project_id=? AND parent_asset_id=?
              UNION ALL
              SELECT assets.id FROM assets JOIN descendants ON assets.parent_asset_id=descendants.id
              WHERE assets.project_id=?
            ) SELECT id FROM descendants ORDER BY id
            """,
            (project_id, asset_id, project_id),
        ).fetchall()
        links = connection.execute(
            "SELECT from_asset_id,relation_type FROM asset_links WHERE to_asset_id=? ORDER BY from_asset_id,relation_type",
            (asset_id,),
        ).fetchall()
        calls = connection.execute(
            """
            SELECT tool_calls.id,tool_calls.status,tool_calls.run_id FROM tool_call_assets
            JOIN tool_calls ON tool_calls.id=tool_call_assets.tool_call_id
            JOIN assets ON assets.id=tool_call_assets.asset_id
            WHERE tool_call_assets.asset_id=? AND assets.project_id=?
              AND tool_calls.status IN ('proposed','approved','running','queued','awaiting_ui_action','unknown_submission')
            ORDER BY tool_calls.id
            """,
            (asset_id, project_id),
        ).fetchall()
        current = connection.execute(
            "SELECT current_asset_id FROM projects WHERE id=?", (project_id,)
        ).fetchone()
        return children, links, calls, None if current is None else current[0]

    @staticmethod
    def get_in_tx(
        connection: sqlite3.Connection, project_id: str, asset_id: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM assets WHERE id=? AND project_id=?", (asset_id, project_id)
        ).fetchone()

    @staticmethod
    def active_in_tx(
        connection: sqlite3.Connection, project_id: str, asset_id: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM assets WHERE id=? AND project_id=? AND trashed_at IS NULL",
            (asset_id, project_id),
        ).fetchone()

    @staticmethod
    def set_hidden(
        connection: sqlite3.Connection, project_id: str, asset_id: str, hidden: bool
    ) -> bool:
        return (
            connection.execute(
                "UPDATE assets SET is_hidden=? WHERE id=? AND project_id=? AND trashed_at IS NULL",
                (int(hidden), asset_id, project_id),
            ).rowcount
            == 1
        )

    @staticmethod
    def set_current(
        connection: sqlite3.Connection,
        project_id: str,
        asset_id: str,
        decision_id: str,
        source: str,
        reason: str | None,
    ) -> tuple[bool, str | None]:
        asset = connection.execute(
            "SELECT id FROM assets WHERE id=? AND project_id=? AND trashed_at IS NULL",
            (asset_id, project_id),
        ).fetchone()
        if asset is None:
            return False, None
        project = connection.execute(
            "SELECT current_asset_id FROM projects WHERE id=?", (project_id,)
        ).fetchone()
        old = None if project is None else project["current_asset_id"]
        connection.execute("UPDATE assets SET is_current=0 WHERE project_id=?", (project_id,))
        connection.execute("UPDATE assets SET is_current=1 WHERE id=?", (asset_id,))
        connection.execute(
            "UPDATE projects SET current_asset_id=? WHERE id=?", (asset_id, project_id)
        )
        connection.execute(
            "INSERT INTO asset_decisions VALUES(?,?,?,?,?,?,?,?)",
            (decision_id, project_id, asset_id, old, source, None, reason, utc_now()),
        )
        return True, None if old is None else str(old)

    @staticmethod
    def current_asset_id(connection: sqlite3.Connection, project_id: str) -> str | None:
        row = connection.execute(
            "SELECT current_asset_id FROM projects WHERE id=?", (project_id,)
        ).fetchone()
        return None if row is None or row[0] is None else str(row[0])

    @staticmethod
    def apply_current(
        connection: sqlite3.Connection,
        project_id: str,
        asset_id: str,
        decision_id: str,
        previous_asset_id: str | None,
        source: str,
        reason: str | None,
        created_at: str,
    ) -> None:
        connection.execute("UPDATE assets SET is_current=0 WHERE project_id=?", (project_id,))
        connection.execute("UPDATE assets SET is_current=1 WHERE id=?", (asset_id,))
        connection.execute(
            "UPDATE projects SET current_asset_id=? WHERE id=?", (asset_id, project_id)
        )
        connection.execute(
            "INSERT INTO asset_decisions VALUES(?,?,?,?,?,?,?,?)",
            (
                decision_id,
                project_id,
                asset_id,
                previous_asset_id,
                source,
                None,
                reason,
                created_at,
            ),
        )

    @staticmethod
    def lineage_relations(
        connection: sqlite3.Connection, project_id: str, asset_id: str, family_id: str
    ) -> tuple[list[sqlite3.Row], list[sqlite3.Row], list[sqlite3.Row]]:
        siblings = connection.execute(
            "SELECT id FROM assets WHERE project_id=? AND asset_family_id=? AND id!=? AND trashed_at IS NULL ORDER BY version_no",
            (project_id, family_id, asset_id),
        ).fetchall()
        descendants = connection.execute(
            """
            WITH RECURSIVE tree(id,depth) AS (
              SELECT id,1 FROM assets WHERE project_id=? AND parent_asset_id=? AND trashed_at IS NULL
              UNION ALL
              SELECT assets.id,tree.depth+1 FROM assets JOIN tree ON assets.parent_asset_id=tree.id
              WHERE assets.project_id=? AND assets.trashed_at IS NULL AND tree.depth<128
            ) SELECT id,depth FROM tree ORDER BY depth,id
            """,
            (project_id, asset_id, project_id),
        ).fetchall()
        inputs = connection.execute(
            "SELECT to_asset_id,relation_type FROM asset_links WHERE from_asset_id=? ORDER BY relation_type,to_asset_id",
            (asset_id,),
        ).fetchall()
        return siblings, descendants, inputs

    @staticmethod
    def replay_command(connection: sqlite3.Connection, request_id: str) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT payload_json,recovery_json,state FROM operations WHERE idempotency_key=?",
            (request_id,),
        ).fetchone()

    @staticmethod
    def complete_command(connection: sqlite3.Connection, request_id: str) -> None:
        connection.execute(
            "UPDATE operations SET state='completed',updated_at=? WHERE idempotency_key=?",
            (utc_now(), request_id),
        )

    def list_with_usage(
        self,
        database: Path,
        project_id: str,
        include_trashed: bool,
        group: str | None,
        include_hidden: bool,
        *,
        read_only: bool = False,
    ) -> list[sqlite3.Row]:
        connection = connect(database, read_only=read_only)
        try:
            clauses, values = ["project_id=?"], [project_id]
            if not include_trashed:
                clauses.append("trashed_at IS NULL")
            if not include_hidden:
                clauses.append("is_hidden=0")
            if group is not None:
                clauses.append("asset_group=?")
                values.append(group)
            rows = connection.execute(
                f"SELECT * FROM assets WHERE {' AND '.join(clauses)} ORDER BY asset_group,name,version_no,id",
                values,
            ).fetchall()
            ids = [row["id"] for row in rows]
            if not ids:
                return rows
            marks = ",".join("?" for _ in ids)
            children = dict(
                connection.execute(
                    f"SELECT parent_asset_id,COUNT(*) FROM assets WHERE project_id=? AND trashed_at IS NULL AND parent_asset_id IN ({marks}) GROUP BY parent_asset_id",
                    [project_id, *ids],
                ).fetchall()
            )
            incoming = dict(
                connection.execute(
                    f"SELECT to_asset_id,COUNT(*) FROM asset_links WHERE to_asset_id IN ({marks}) GROUP BY to_asset_id",
                    ids,
                ).fetchall()
            )
            outgoing = dict(
                connection.execute(
                    f"SELECT from_asset_id,COUNT(*) FROM asset_links WHERE from_asset_id IN ({marks}) GROUP BY from_asset_id",
                    ids,
                ).fetchall()
            )
            # Preserve the existing row interface while carrying aggregate counts.
            return [
                dict(row)
                | {
                    "_children": children.get(row["id"], 0),
                    "_incoming": incoming.get(row["id"], 0),
                    "_outgoing": outgoing.get(row["id"], 0),
                }
                for row in rows
            ]  # type: ignore[return-value]
        finally:
            connection.close()

    def usage_counts(
        self,
        database: Path,
        project_id: str,
        asset_id: str,
        *,
        read_only: bool = False,
    ) -> dict[str, int] | None:
        connection = connect(database, read_only=read_only)
        try:
            row = connection.execute(
                "SELECT is_current FROM assets WHERE id=? AND project_id=?", (asset_id, project_id)
            ).fetchone()
            if row is None:
                return None
            return {
                "child_count": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM assets WHERE project_id=? AND parent_asset_id=? AND trashed_at IS NULL",
                        (project_id, asset_id),
                    ).fetchone()[0]
                ),
                "input_link_count": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM asset_links WHERE to_asset_id=?", (asset_id,)
                    ).fetchone()[0]
                ),
                "output_link_count": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM asset_links WHERE from_asset_id=?", (asset_id,)
                    ).fetchone()[0]
                ),
                "is_project_current": int(row[0]),
            }
        finally:
            connection.close()

    # The following committed operations are the application-facing asset port.
    # They intentionally own connection lifetime, writer locks, journal state,
    # event append and SQL.  File bytes are still moved by the use case after a
    # durable `prepare_*` result, so crash recovery remains journal driven.
    def prepare_import(
        self, database: Path, *, project_id: str, request_id: str, payload: Mapping[str, object]
    ) -> dict[str, object]:
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                previous = self.operation(connection, request_id)
                if previous:
                    old = json.loads(previous["payload_json"])
                    if any(
                        old.get(k) != payload.get(k)
                        for k in ("source_sha256", "asset_type", "parent_asset_id")
                    ):
                        raise DomainErrorV1(
                            ErrorCode.IDEMPOTENCY_CONFLICT,
                            "same request_id cannot import different input",
                        )
                    if previous["state"] == "completed":
                        return {"replayed": True, "asset_id": old["asset_id"]}
                    recovery = json.loads(previous["recovery_json"])
                    if (
                        previous["state"] == "failed"
                        and recovery.get("recovered") == "orphan_files_removed"
                        and recovery.get("safe_to_retry") is True
                    ):
                        self.delete_operation(connection, previous["id"])
                    else:
                        raise DomainErrorV1(
                            ErrorCode.IDEMPOTENCY_CONFLICT, "import command incomplete"
                        )
                parent = payload.get("parent_asset_id")
                if isinstance(parent, str) and not self.valid_parent(
                    connection, project_id, parent
                ):
                    raise DomainErrorV1(ErrorCode.ASSET_NOT_FOUND, "parent asset is unavailable")
                operation_id = OperationRepository(connection).prepare(
                    "asset_write", request_id, dict(payload)
                )
                return {"replayed": False, "operation_id": operation_id}
        finally:
            connection.close()

    def commit_import(
        self,
        database: Path,
        *,
        operation_id: str,
        project_id: str,
        asset_id: str,
        parent_asset_id: str | None,
        asset_type: str,
        asset_group: str | None,
        name: str,
        relative_path: str,
        mime: str,
        size: int,
        digest: str,
        metadata: Mapping[str, object],
        provenance: Mapping[str, object],
        created_at: str,
        thumbnail: tuple[str, str, str, int, str] | None,
    ) -> None:
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                OperationRepository(connection).mark(operation_id, "file_written")
                self.register_import(
                    connection,
                    asset_id=asset_id,
                    project_id=project_id,
                    parent_asset_id=parent_asset_id,
                    asset_type=asset_type,
                    asset_group=asset_group,
                    name=name,
                    relative_path=relative_path,
                    mime=mime,
                    size=size,
                    digest=digest,
                    metadata=metadata,
                    provenance=provenance,
                    created_at=created_at,
                    thumbnail=thumbnail,
                )
                _append_business_event(
                    connection,
                    project_id,
                    "asset.created",
                    {
                        "asset_id": asset_id,
                        "asset_type": asset_type,
                        "asset_group": asset_group,
                        "parent_asset_id": parent_asset_id,
                    },
                    asset_id,
                )
                operations = OperationRepository(connection)
                operations.mark(operation_id, "db_committed")
                operations.mark(operation_id, "completed")
        finally:
            connection.close()

    def mark_operation_failed(self, database: Path, operation_id: str) -> None:
        connection = connect(database)
        try:
            with transaction(connection):
                OperationRepository(connection).mark_failed_recoverable(operation_id)
        finally:
            connection.close()

    def mark_operation_file_written(self, database: Path, operation_id: str) -> None:
        """Persist the file phase before the following business-data transaction."""
        connection = connect(database)
        try:
            with transaction(connection):
                OperationRepository(connection).mark(operation_id, "file_written")
        finally:
            connection.close()

    def set_current_committed(
        self,
        database: Path,
        *,
        project_id: str,
        asset_id: str,
        source: str,
        reason: str | None,
        request_id: str,
    ) -> dict[str, object]:
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                payload = {
                    "project_id": project_id,
                    "asset_id": asset_id,
                    "source": source,
                    "reason": reason,
                }
                previous = self.replay_command(connection, request_id)
                if previous:
                    if (
                        json.loads(previous["payload_json"])
                        != {"action": "asset.set_current", "payload": payload}
                        or previous["state"] != "completed"
                    ):
                        raise DomainErrorV1(ErrorCode.IDEMPOTENCY_CONFLICT, "request_id conflict")
                    replayed = json.loads(previous["recovery_json"]).get("result")
                    if not isinstance(replayed, dict):
                        raise DomainErrorV1(
                            ErrorCode.IDEMPOTENCY_CONFLICT,
                            "set-current replay result is unavailable",
                        )
                    return cast(dict[str, object], replayed)
                asset = self.active_in_tx(connection, project_id, asset_id)
                if not asset:
                    raise DomainErrorV1(ErrorCode.ASSET_NOT_FOUND, "资产不存在或已回收。")
                operation_id = OperationRepository(connection).prepare(
                    "asset_set_current",
                    request_id,
                    {"action": "asset.set_current", "payload": payload},
                )
                old = self.current_asset_id(connection, project_id)
                decision_id = new_id()
                created_at = utc_now()
                self.apply_current(
                    connection,
                    project_id,
                    asset_id,
                    decision_id,
                    old,
                    source,
                    reason,
                    created_at,
                )
                event = _append_business_event(
                    connection,
                    project_id,
                    "asset.current_changed",
                    {
                        "previous_asset_id": old,
                        "asset_id": asset_id,
                        "decision_id": decision_id,
                        "decision_source": source,
                    },
                    asset_id,
                )
                result: dict[str, object] = {
                    "decision": {
                        "id": decision_id,
                        "project_id": project_id,
                        "asset_id": asset_id,
                        "previous_asset_id": old,
                        "decision_source": source,
                        "run_id": None,
                        "reason": reason,
                        "created_at": created_at,
                    },
                    "event": event.__dict__,
                }
                OperationRepository(connection).complete_with_result(operation_id, result)
                return result
        finally:
            connection.close()

    def prepare_derived(
        self, database: Path, *, project_id: str, request_id: str, payload: Mapping[str, object]
    ) -> dict[str, object]:
        return self.prepare_import(
            database, project_id=project_id, request_id=request_id, payload=payload
        )

    def commit_derived(
        self,
        database: Path,
        *,
        operation_id: str,
        project_id: str,
        asset_id: str,
        parent_asset_id: str | None,
        input_asset_ids: Sequence[str],
        lineage_mode: str,
        asset_type: str,
        asset_group: str | None,
        name: str,
        relative_path: str,
        mime_type: str,
        size: int,
        digest: str,
        metadata: Mapping[str, object],
        provenance: Mapping[str, object],
        created_at: str,
    ) -> None:
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                OperationRepository(connection).mark(operation_id, "file_written")
                self.register_derived(
                    connection,
                    project_id=project_id,
                    asset_id=asset_id,
                    parent_asset_id=parent_asset_id,
                    input_asset_ids=input_asset_ids,
                    lineage_mode=lineage_mode,
                    asset_type=asset_type,
                    asset_group=asset_group,
                    name=name,
                    relative_path=relative_path,
                    mime_type=mime_type,
                    size=size,
                    digest=digest,
                    metadata=metadata,
                    provenance=provenance,
                    created_at=created_at,
                )
                _append_business_event(
                    connection,
                    project_id,
                    "asset.created",
                    {
                        "asset_id": asset_id,
                        "asset_type": asset_type,
                        "asset_group": asset_group,
                        "parent_asset_id": parent_asset_id,
                    },
                    asset_id,
                )
                operations = OperationRepository(connection)
                operations.mark(operation_id, "db_committed")
                operations.mark(operation_id, "completed")
        finally:
            connection.close()

    def lineage(
        self,
        database: Path,
        project_id: str,
        asset_id: str,
        *,
        read_only: bool = False,
    ) -> dict[str, object] | None:
        connection = connect(database, read_only=read_only)
        try:
            current = self.get_in_tx(connection, project_id, asset_id)
            if not current:
                return None
            siblings, descendants, inputs = self.lineage_relations(
                connection, project_id, asset_id, current["asset_family_id"]
            )
            return {
                "parent_asset_id": current["parent_asset_id"],
                "siblings": [row[0] for row in siblings],
                "children": [row[0] for row in descendants if row[1] == 1],
                "descendants": [{"asset_id": row[0], "depth": row[1]} for row in descendants],
                "inputs": [{"asset_id": row[0], "relation_type": row[1]} for row in inputs],
            }
        finally:
            connection.close()

    def hide_committed(
        self,
        database: Path,
        *,
        project_id: str,
        asset_id: str,
        hidden: bool,
        request_id: str | None,
    ) -> bool:
        connection = connect(database)
        try:
            with transaction(connection):
                payload = {"project_id": project_id, "asset_id": asset_id, "hidden": hidden}
                if request_id:
                    previous = self.replay_command(connection, request_id)
                    if previous:
                        if (
                            json.loads(previous["payload_json"])
                            != {"action": "asset.visibility", "payload": payload}
                            or previous["state"] != "completed"
                        ):
                            raise DomainErrorV1(
                                ErrorCode.IDEMPOTENCY_CONFLICT, "request_id conflict"
                            )
                        return True
                    operation_id = OperationRepository(connection).prepare(
                        "asset_visibility",
                        request_id,
                        {"action": "asset.visibility", "payload": payload},
                    )
                else:
                    operation_id = None
                if not self.set_hidden(connection, project_id, asset_id, hidden):
                    return False
                _append_business_event(
                    connection,
                    project_id,
                    "asset.visibility.changed",
                    {"asset_id": asset_id, "is_hidden": hidden, "trashed_at": None},
                    asset_id,
                )
                if operation_id:
                    OperationRepository(connection).mark(operation_id, "completed")
                return True
        finally:
            connection.close()

    @staticmethod
    def _impact(
        connection: sqlite3.Connection,
        project_id: str,
        asset_id: str,
        issued_at: int,
        exclude_tool_call_id: str | None = None,
    ) -> dict[str, object]:
        children, links, tool_calls, current = AssetRepository.impact_rows(
            connection, project_id, asset_id
        )
        tool_calls = [row for row in tool_calls if row[0] != exclude_tool_call_id]
        fingerprint = hashlib.sha256(
            (
                asset_id
                + str([row[0] for row in children])
                + str([(row[0], row[1]) for row in links])
                + str([(row[0], row[1]) for row in tool_calls])
                + str(current)
                + str(issued_at)
            ).encode()
        ).hexdigest()
        return {
            "asset_id": asset_id,
            "children": [row[0] for row in children],
            "incoming_links": [{"asset_id": row[0], "relation_type": row[1]} for row in links],
            "active_tool_calls": [{"tool_call_id": row[0], "status": row[1]} for row in tool_calls],
            "active_runs": sorted({row[2] for row in tool_calls if row[2]}),
            "active_jobs": [
                {"tool_call_id": row[0], "status": row[1]}
                for row in tool_calls
                if row[1] in {"running", "queued", "awaiting_ui_action", "unknown_submission"}
            ],
            "is_current": current == asset_id,
            "impact_token": f"{issued_at}.{fingerprint}",
            "expires_at": issued_at + 60,
        }

    def impact(
        self, database: Path, project_id: str, asset_id: str, issued_at: int
    ) -> dict[str, object]:
        connection = connect(database)
        try:
            return self._impact(connection, project_id, asset_id, issued_at)
        finally:
            connection.close()

    def prepare_trash(
        self,
        database: Path,
        *,
        project_id: str,
        asset_id: str,
        token: str | None,
        request_id: str | None,
        now_seconds: int,
    ) -> dict[str, object]:
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                if request_id:
                    previous = OperationRepository(connection).by_request_id(request_id)
                    if previous:
                        payload = json.loads(previous["payload_json"])
                        if previous["kind"] != "trash" or payload.get("asset_id") != asset_id:
                            raise DomainErrorV1(
                                ErrorCode.IDEMPOTENCY_CONFLICT, "request_id conflict"
                            )
                        if previous["state"] == "completed":
                            return {"replayed": True}
                        raise DomainErrorV1(
                            ErrorCode.IDEMPOTENCY_CONFLICT, "trash command incomplete"
                        )
                preview = self._impact(
                    connection,
                    project_id,
                    asset_id,
                    now_seconds,
                    request_id,
                )
                requires_confirmation = bool(
                    preview["children"]
                    or preview["incoming_links"]
                    or preview["active_tool_calls"]
                    or preview["is_current"]
                )
                try:
                    issued_at = int((token or "").split(".", 1)[0])
                except TypeError, ValueError:
                    issued_at = -1
                confirmed = (
                    issued_at >= 0
                    and now_seconds <= issued_at + 60
                    and token
                    == self._impact(
                        connection,
                        project_id,
                        asset_id,
                        issued_at,
                        request_id,
                    )["impact_token"]
                )
                if requires_confirmation and not confirmed:
                    raise DomainErrorV1(
                        ErrorCode.ASSET_REFERENCED,
                        "资产仍被引用，请重新确认影响。",
                        details=preview,
                    )
                row = self.trash_source(connection, project_id, asset_id)
                if not row:
                    raise DomainErrorV1(ErrorCode.ASSET_NOT_FOUND, "资产不存在。")
                old, new = str(row[0]), f"assets/trash/{asset_id}/{Path(str(row[0])).name}"
                operation_id = OperationRepository(connection).prepare(
                    "trash",
                    request_id or asset_id,
                    {"asset_id": asset_id, "source_relative_path": old, "trash_relative_path": new},
                )
                return {
                    "replayed": False,
                    "operation_id": operation_id,
                    "source_relative_path": old,
                    "trash_relative_path": new,
                    "was_current": bool(preview["is_current"]),
                }
        finally:
            connection.close()

    def commit_trash_committed(
        self,
        database: Path,
        *,
        operation_id: str,
        project_id: str,
        asset_id: str,
        old: str,
        new: str,
        was_current: bool,
    ) -> None:
        connection = connect(database)
        try:
            with transaction(connection):
                operations = OperationRepository(connection)
                operations.mark(operation_id, "file_written")
                decision_id = new_id() if was_current else None
                now = self.commit_trash(
                    connection, project_id, asset_id, old, new, was_current, decision_id
                )
                if was_current:
                    _append_business_event(
                        connection,
                        project_id,
                        "asset.current_changed",
                        {
                            "previous_asset_id": asset_id,
                            "asset_id": None,
                            "decision_id": decision_id,
                            "decision_source": "system",
                        },
                        asset_id,
                    )
                _append_business_event(
                    connection,
                    project_id,
                    "asset.visibility.changed",
                    {"asset_id": asset_id, "is_hidden": False, "trashed_at": now},
                    asset_id,
                )
                operations.mark(operation_id, "db_committed")
                operations.mark(operation_id, "completed")
        finally:
            connection.close()

    def prepare_restore(
        self, database: Path, *, project_id: str, asset_id: str, request_id: str
    ) -> dict[str, object]:
        connection = connect(database)
        try:
            with transaction(connection):
                previous = OperationRepository(connection).by_request_id(request_id)
                if previous:
                    payload = json.loads(previous["payload_json"])
                    if previous["kind"] != "restore" or payload.get("asset_id") != asset_id:
                        raise DomainErrorV1(ErrorCode.IDEMPOTENCY_CONFLICT, "request_id conflict")
                    if previous["state"] == "completed":
                        return {"replayed": True}
                    raise DomainErrorV1(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "restore command incomplete"
                    )
                row = self.restore_source(connection, project_id, asset_id)
                if not row:
                    raise DomainErrorV1(ErrorCode.ASSET_NOT_FOUND, "回收资产不存在。")
                if not row["original_relative_path"]:
                    raise DomainErrorV1(ErrorCode.ASSET_CONTENT_MISMATCH, "回收资产缺少原始位置。")
                old, original = str(row["relative_path"]), str(row["original_relative_path"])
                operation_id = OperationRepository(connection).prepare(
                    "restore",
                    request_id,
                    {
                        "asset_id": asset_id,
                        "trash_relative_path": old,
                        "restored_relative_path": original,
                    },
                )
                return {
                    "replayed": False,
                    "operation_id": operation_id,
                    "trash_relative_path": old,
                    "restored_relative_path": original,
                }
        finally:
            connection.close()

    def commit_restore_committed(
        self,
        database: Path,
        *,
        operation_id: str,
        project_id: str,
        asset_id: str,
        relative_path: str,
    ) -> None:
        connection = connect(database)
        try:
            with transaction(connection):
                operations = OperationRepository(connection)
                operations.mark(operation_id, "file_written")
                self.commit_restore(connection, asset_id, relative_path)
                _append_business_event(
                    connection,
                    project_id,
                    "asset.visibility.changed",
                    {"asset_id": asset_id, "is_hidden": False, "trashed_at": None},
                    asset_id,
                )
                operations.mark(operation_id, "db_committed")
                operations.mark(operation_id, "completed")
        finally:
            connection.close()

    @staticmethod
    def derived_existing(connection: sqlite3.Connection, request_id: str) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT id,payload_json,recovery_json,state FROM operations WHERE idempotency_key=?",
            (request_id,),
        ).fetchone()

    @staticmethod
    def register_derived(
        connection: sqlite3.Connection,
        *,
        project_id: str,
        asset_id: str,
        parent_asset_id: str | None,
        input_asset_ids: Sequence[str],
        lineage_mode: str,
        asset_type: str,
        asset_group: str | None,
        name: str,
        relative_path: str,
        mime_type: str,
        size: int,
        digest: str,
        metadata: Mapping[str, object],
        provenance: Mapping[str, object],
        created_at: str,
    ) -> None:
        family_id, version = asset_id, 1
        if lineage_mode == "new_version":
            if not parent_asset_id:
                raise DomainErrorV1(
                    ErrorCode.INVALID_ASSET_CONTENT, "new version requires a parent asset"
                )
            parent = connection.execute(
                "SELECT asset_family_id,asset_type FROM assets WHERE id=? AND project_id=? AND trashed_at IS NULL",
                (parent_asset_id, project_id),
            ).fetchone()
            if not parent or parent["asset_type"] != asset_type:
                raise DomainErrorV1(
                    ErrorCode.INVALID_ASSET_CONTENT, "parent asset type does not match"
                )
            family_id = parent["asset_family_id"]
            version = int(
                connection.execute(
                    "SELECT COALESCE(MAX(version_no),0)+1 FROM assets WHERE project_id=? AND asset_family_id=?",
                    (project_id, family_id),
                ).fetchone()[0]
            )
        for input_id in input_asset_ids:
            if not connection.execute(
                "SELECT 1 FROM assets WHERE id=? AND project_id=? AND trashed_at IS NULL",
                (input_id, project_id),
            ).fetchone():
                raise DomainErrorV1(ErrorCode.ASSET_NOT_FOUND, "input asset is unavailable")
        connection.execute(
            "INSERT INTO assets VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                asset_id,
                project_id,
                family_id,
                parent_asset_id,
                asset_type,
                asset_group,
                name,
                version,
                relative_path,
                None,
                mime_type,
                size,
                digest,
                canonical_json(metadata),
                canonical_json(ProvenanceV1.model_validate(provenance).model_dump(mode="json")),
                0,
                0,
                None,
                None,
                created_at,
            ),
        )
        for input_id in input_asset_ids:
            if input_id != parent_asset_id:
                connection.execute(
                    "INSERT INTO asset_links VALUES(?,?,?)", (asset_id, input_id, "input")
                )

    @staticmethod
    def new_version_parent(
        connection: sqlite3.Connection, project_id: str, asset_id: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT asset_family_id,asset_type FROM assets WHERE id=? AND project_id=? AND trashed_at IS NULL",
            (asset_id, project_id),
        ).fetchone()

    @staticmethod
    def next_family_version(connection: sqlite3.Connection, project_id: str, family_id: str) -> int:
        return int(
            connection.execute(
                "SELECT COALESCE(MAX(version_no),0)+1 FROM assets WHERE project_id=? AND asset_family_id=?",
                (project_id, family_id),
            ).fetchone()[0]
        )

    @staticmethod
    def active_asset_exists(connection: sqlite3.Connection, project_id: str, asset_id: str) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM assets WHERE id=? AND project_id=? AND trashed_at IS NULL",
                (asset_id, project_id),
            ).fetchone()
            is not None
        )

    @staticmethod
    def insert_derived(connection: sqlite3.Connection, values: tuple[object, ...]) -> None:
        connection.execute(
            "INSERT INTO assets VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values
        )

    @staticmethod
    def link_input(connection: sqlite3.Connection, derived_id: str, input_id: str) -> None:
        connection.execute("INSERT INTO asset_links VALUES(?,?,?)", (derived_id, input_id, "input"))

    @staticmethod
    def list_rows(
        connection: sqlite3.Connection, clauses: Sequence[str], values: Sequence[object]
    ) -> list[sqlite3.Row]:
        return connection.execute(
            f"SELECT * FROM assets WHERE {' AND '.join(clauses)} ORDER BY asset_group,name,version_no,id",
            values,
        ).fetchall()

    @staticmethod
    def relation_counts(
        connection: sqlite3.Connection, project_id: str, asset_ids: Sequence[str]
    ) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
        if not asset_ids:
            return {}, {}, {}
        marks = ",".join("?" for _ in asset_ids)
        children = dict(
            connection.execute(
                f"SELECT parent_asset_id,COUNT(*) FROM assets WHERE project_id=? AND trashed_at IS NULL AND parent_asset_id IN ({marks}) GROUP BY parent_asset_id",
                [project_id, *asset_ids],
            ).fetchall()
        )
        incoming = dict(
            connection.execute(
                f"SELECT to_asset_id,COUNT(*) FROM asset_links WHERE to_asset_id IN ({marks}) GROUP BY to_asset_id",
                asset_ids,
            ).fetchall()
        )
        outgoing = dict(
            connection.execute(
                f"SELECT from_asset_id,COUNT(*) FROM asset_links WHERE from_asset_id IN ({marks}) GROUP BY from_asset_id",
                asset_ids,
            ).fetchall()
        )
        return children, incoming, outgoing

    @staticmethod
    def usage_row(
        connection: sqlite3.Connection, project_id: str, asset_id: str
    ) -> tuple[bool, int, int, int] | None:
        asset = connection.execute(
            "SELECT is_current FROM assets WHERE id=? AND project_id=?", (asset_id, project_id)
        ).fetchone()
        if asset is None:
            return None
        child = connection.execute(
            "SELECT COUNT(*) FROM assets WHERE project_id=? AND parent_asset_id=? AND trashed_at IS NULL",
            (project_id, asset_id),
        ).fetchone()[0]
        incoming = connection.execute(
            "SELECT COUNT(*) FROM asset_links WHERE to_asset_id=?", (asset_id,)
        ).fetchone()[0]
        outgoing = connection.execute(
            "SELECT COUNT(*) FROM asset_links WHERE from_asset_id=?", (asset_id,)
        ).fetchone()[0]
        return bool(asset[0]), int(child), int(incoming), int(outgoing)

    @staticmethod
    def trash_source(
        connection: sqlite3.Connection, project_id: str, asset_id: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT relative_path FROM assets WHERE id=? AND project_id=? AND trashed_at IS NULL",
            (asset_id, project_id),
        ).fetchone()

    @staticmethod
    def commit_trash(
        connection: sqlite3.Connection,
        project_id: str,
        asset_id: str,
        old: str,
        new: str,
        was_current: bool,
        decision_id: str | None,
    ) -> str:
        now = utc_now()
        connection.execute(
            "UPDATE assets SET relative_path=?,original_relative_path=?,trashed_at=?,is_current=0 WHERE id=?",
            (new, old, now, asset_id),
        )
        connection.execute(
            "UPDATE projects SET current_asset_id=NULL WHERE id=? AND current_asset_id=?",
            (project_id, asset_id),
        )
        if was_current and decision_id:
            connection.execute(
                "INSERT INTO asset_decisions VALUES(?,?,?,?,?,?,?,?)",
                (decision_id, project_id, None, asset_id, "system", None, "asset_trashed", now),
            )
        return now

    @staticmethod
    def restore_source(
        connection: sqlite3.Connection, project_id: str, asset_id: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT relative_path,original_relative_path FROM assets WHERE id=? AND project_id=? AND trashed_at IS NOT NULL",
            (asset_id, project_id),
        ).fetchone()

    @staticmethod
    def commit_restore(connection: sqlite3.Connection, asset_id: str, relative_path: str) -> None:
        connection.execute(
            "UPDATE assets SET relative_path=?,original_relative_path=NULL,trashed_at=NULL WHERE id=?",
            (relative_path, asset_id),
        )


class ToolRepository:
    """Durable B01 Tool audit and idempotency persistence."""

    @staticmethod
    def idempotency_record(connection: sqlite3.Connection, key: str) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT tool_idempotency.owner_tool_call_id,tool_idempotency.result_json,"
            "tool_idempotency.state,tool_calls.error_json "
            "FROM tool_idempotency JOIN tool_calls "
            "ON tool_calls.id=tool_idempotency.owner_tool_call_id "
            "WHERE tool_idempotency.idempotency_key=?",
            (key,),
        ).fetchone()

    @staticmethod
    def reserve(
        connection: sqlite3.Connection,
        *,
        call_id: str,
        run_id: str | None,
        round_index: int,
        name: str,
        version: str,
        arguments_json: str,
        key: str,
        provider_profile: str | None,
        risk_level: str,
        input_asset_ids: list[str],
        retrying: bool,
    ) -> bool:
        now = utc_now()
        connection.execute(
            "INSERT INTO tool_calls(id,run_id,round_index,tool_name,tool_version,arguments_json,arguments_hash,idempotency_key,provider_profile,risk_level,status,started_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                call_id,
                run_id,
                round_index,
                name,
                version,
                arguments_json,
                key,
                key,
                provider_profile,
                risk_level,
                "running",
                now,
            ),
        )
        for asset_id in input_asset_ids:
            connection.execute(
                "INSERT INTO tool_call_assets VALUES(?,?,?,?)",
                (call_id, asset_id, "input", "argument"),
            )
        if retrying:
            return (
                connection.execute(
                    "UPDATE tool_idempotency SET state='running',owner_tool_call_id=?,job_id=NULL,result_json=NULL,updated_at=? WHERE idempotency_key=? AND state='failed_retryable'",
                    (call_id, now, key),
                ).rowcount
                == 1
            )
        connection.execute(
            "INSERT INTO tool_idempotency VALUES(?,?,?,?,?,?,?,?)",
            (key, name, version, "running", call_id, None, None, now),
        )
        return True

    @staticmethod
    def finish(
        connection: sqlite3.Connection,
        *,
        project_id: str,
        call_id: str,
        key: str,
        status: str,
        state: str,
        payload: str,
        duration_ms: int,
        output_asset_ids: list[str],
    ) -> None:
        now = utc_now()
        connection.execute(
            "UPDATE tool_calls SET status=?,result_json=?,duration_ms=?,finished_at=? WHERE id=?",
            (status, payload, duration_ms, now, call_id),
        )
        connection.execute(
            "UPDATE tool_idempotency SET state=?,result_json=?,updated_at=? WHERE idempotency_key=?",
            (state, payload, now, key),
        )
        for asset_id in output_asset_ids:
            if connection.execute(
                "SELECT 1 FROM assets WHERE id=? AND project_id=?", (asset_id, project_id)
            ).fetchone():
                connection.execute(
                    "INSERT OR IGNORE INTO tool_call_assets VALUES(?,?,?,?)",
                    (call_id, asset_id, "output", "result"),
                )

    @staticmethod
    def fail(
        connection: sqlite3.Connection, key: str, unknown: bool, error_payload: Mapping[str, object]
    ) -> None:
        status = "unknown_submission" if unknown else "failed"
        state = "unknown_submission" if unknown else "failed_terminal"
        now = utc_now()
        connection.execute(
            "UPDATE tool_calls SET status=?,error_json=?,finished_at=? WHERE idempotency_key=?",
            (status, canonical_json(error_payload), now, key),
        )
        connection.execute(
            "UPDATE tool_idempotency SET state=?,updated_at=? WHERE idempotency_key=?",
            (state, now, key),
        )

    def reserve_committed(
        self,
        database: Path,
        *,
        project_id: str,
        call_id: str,
        request_id: str,
        request_payload_hash: str,
        run_id: str | None,
        round_index: int,
        name: str,
        version: str,
        arguments_json: str,
        arguments: Mapping[str, object],
        provider_profile: str | None,
        risk_level: str,
        input_asset_ids: list[str],
        key_factory: Callable[[str, str, Mapping[str, object], list[str], str | None], str],
    ) -> dict[str, object]:
        """Atomically validate inputs and reserve a cross-Run tool call."""
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                request = connection.execute(
                    "SELECT payload_hash,tool_call_id,result_json,error_json "
                    "FROM tool_requests WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                if request:
                    if request["payload_hash"] != request_payload_hash:
                        raise DomainErrorV1(
                            ErrorCode.IDEMPOTENCY_CONFLICT,
                            "request_id is already bound to a different Tool command.",
                        )
                    if request["result_json"]:
                        return {
                            "kind": "request_reused",
                            "result_json": request["result_json"],
                        }
                    if request["error_json"]:
                        return {
                            "kind": "request_failed",
                            "error_json": request["error_json"],
                        }
                    state = connection.execute(
                        "SELECT state FROM tool_idempotency WHERE owner_tool_call_id=?",
                        (request["tool_call_id"],),
                    ).fetchone()
                    return {
                        "kind": "request_pending",
                        "call_id": request["tool_call_id"],
                        "state": state["state"] if state else "running",
                    }
                hashes = AssetRepository.active_hashes(
                    connection,
                    project_id,
                    input_asset_ids,
                    include_trashed=name == "asset.restore_from_trash",
                )
                if hashes is None:
                    raise DomainErrorV1(
                        ErrorCode.ASSET_NOT_FOUND, "Tool input asset does not exist."
                    )
                key = str(key_factory(name, version, arguments, hashes, provider_profile))
                old = self.idempotency_record(connection, key)
                if old:
                    if old[2] in {"running", "queued", "unknown_submission"} and old[1]:
                        reused = json.loads(old[1])
                        reused["reused"] = True
                        replay_payload = canonical_json(reused)
                        now = utc_now()
                        connection.execute(
                            "INSERT INTO tool_requests VALUES(?,?,?,?,?,?,?,?)",
                            (
                                request_id,
                                project_id,
                                request_payload_hash,
                                old[0],
                                replay_payload,
                                None,
                                now,
                                now,
                            ),
                        )
                        return {
                            "kind": "reused",
                            "key": key,
                            "result_json": replay_payload,
                        }
                    if old[2] in {"running", "queued", "unknown_submission"}:
                        now = utc_now()
                        connection.execute(
                            "INSERT INTO tool_requests VALUES(?,?,?,?,?,?,?,?)",
                            (
                                request_id,
                                project_id,
                                request_payload_hash,
                                old[0],
                                None,
                                None,
                                now,
                                now,
                            ),
                        )
                        return {"kind": "pending", "key": key, "call_id": old[0], "state": old[2]}
                    if old[2] != "failed_retryable" and old[1]:
                        reused = json.loads(old[1])
                        reused["reused"] = True
                        replay_payload = canonical_json(reused)
                        now = utc_now()
                        connection.execute(
                            "INSERT INTO tool_requests VALUES(?,?,?,?,?,?,?,?)",
                            (
                                request_id,
                                project_id,
                                request_payload_hash,
                                old[0],
                                replay_payload,
                                None,
                                now,
                                now,
                            ),
                        )
                        return {
                            "kind": "reused",
                            "key": key,
                            "result_json": replay_payload,
                        }
                    if old[2] == "failed_terminal" and old["error_json"]:
                        error_payload = str(old["error_json"])
                        now = utc_now()
                        connection.execute(
                            "INSERT INTO tool_requests VALUES(?,?,?,?,?,?,?,?)",
                            (
                                request_id,
                                project_id,
                                request_payload_hash,
                                old[0],
                                None,
                                error_payload,
                                now,
                                now,
                            ),
                        )
                        return {
                            "kind": "request_failed",
                            "key": key,
                            "error_json": error_payload,
                        }
                retrying = bool(old and old[2] == "failed_retryable")
                if not self.reserve(
                    connection,
                    call_id=call_id,
                    run_id=run_id,
                    round_index=round_index,
                    name=name,
                    version=version,
                    arguments_json=arguments_json,
                    key=key,
                    provider_profile=provider_profile,
                    risk_level=risk_level,
                    input_asset_ids=input_asset_ids,
                    retrying=retrying,
                ):
                    raise DomainErrorV1(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "retry claim was already consumed"
                    )
                now = utc_now()
                connection.execute(
                    "INSERT INTO tool_requests VALUES(?,?,?,?,?,?,?,?)",
                    (
                        request_id,
                        project_id,
                        request_payload_hash,
                        call_id,
                        None,
                        None,
                        now,
                        now,
                    ),
                )
                return {"kind": "reserved", "key": key, "call_id": call_id}
        finally:
            connection.close()

    def complete_request_committed(self, database: Path, request_id: str, payload: str) -> None:
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                changed = connection.execute(
                    "UPDATE tool_requests SET result_json=?,updated_at=? "
                    "WHERE request_id=? AND result_json IS NULL AND error_json IS NULL",
                    (payload, utc_now(), request_id),
                ).rowcount
                if changed != 1:
                    raise DomainErrorV1(
                        ErrorCode.IDEMPOTENCY_CONFLICT,
                        "Tool request result could not be committed.",
                    )
        finally:
            connection.close()

    def finish_committed(
        self,
        database: Path,
        *,
        project_id: str,
        call_id: str,
        request_id: str,
        key: str,
        status: str,
        state: str,
        payload: str,
        duration_ms: int,
        output_asset_ids: list[str],
        ui_action: Mapping[str, object] | None,
        run_id: str | None,
    ) -> None:
        connection = connect(database)
        try:
            with transaction(connection):
                self.finish(
                    connection,
                    project_id=project_id,
                    call_id=call_id,
                    key=key,
                    status=status,
                    state=state,
                    payload=payload,
                    duration_ms=duration_ms,
                    output_asset_ids=output_asset_ids,
                )
                changed = connection.execute(
                    "UPDATE tool_requests SET result_json=?,updated_at=? "
                    "WHERE request_id=? AND tool_call_id=? "
                    "AND result_json IS NULL AND error_json IS NULL",
                    (payload, utc_now(), request_id, call_id),
                ).rowcount
                if changed != 1:
                    raise DomainErrorV1(
                        ErrorCode.IDEMPOTENCY_CONFLICT,
                        "Tool request result could not be committed.",
                    )
                if ui_action is not None:
                    _append_business_event(
                        connection,
                        project_id,
                        "workspace.action.requested",
                        ui_action,
                        call_id,
                        run_id,
                    )
                _append_business_event(
                    connection,
                    project_id,
                    "tool_call.status.changed",
                    {
                        "tool_call_id": call_id,
                        "status": status,
                        "output_asset_ids": output_asset_ids,
                        "job_id": None,
                    },
                    call_id,
                )
        finally:
            connection.close()

    def fail_committed(
        self,
        database: Path,
        key: str,
        request_id: str,
        unknown: bool,
        error_payload: Mapping[str, object],
    ) -> None:
        connection = connect(database)
        try:
            with transaction(connection):
                self.fail(connection, key, unknown, error_payload)
                connection.execute(
                    "UPDATE tool_requests SET error_json=?,updated_at=? "
                    "WHERE request_id=? AND result_json IS NULL AND error_json IS NULL",
                    (canonical_json(error_payload), utc_now(), request_id),
                )
        finally:
            connection.close()


class ProjectRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(self, project_id: str, name: str, created_at: str) -> None:
        self._connection.execute(
            "INSERT INTO projects(id,name,root_path,created_at,updated_at) VALUES(?,?,?,?,?)",
            (project_id, name, ".", created_at, created_at),
        )
        self._connection.execute("INSERT INTO event_counters VALUES(?,1)", (project_id,))

    def get(self, project_id: str | None = None) -> sqlite3.Row | None:
        if project_id is None:
            return self._connection.execute("SELECT * FROM projects").fetchone()
        return self._connection.execute(
            "SELECT * FROM projects WHERE id=?", (project_id,)
        ).fetchone()

    def rename(self, project_id: str, name: str, updated_at: str) -> None:
        self._connection.execute(
            "UPDATE projects SET name=?,updated_at=? WHERE id=?", (name, updated_at, project_id)
        )

    def current_asset_id(self, project_id: str) -> str | None:
        row = self._connection.execute(
            "SELECT current_asset_id FROM projects WHERE id=?", (project_id,)
        ).fetchone()
        return None if row is None else row[0]

    @classmethod
    def create_database(cls, database: Path, project_id: str, name: str, created_at: str) -> None:
        connection = connect(database)
        try:
            with transaction(connection):
                cls(connection).create(project_id, name, created_at)
        finally:
            connection.close()

    @classmethod
    def open_database(cls, database: Path, root: Path, read_only: bool) -> sqlite3.Row | None:
        connection = connect(database, read_only=read_only)
        try:
            if not read_only:
                with transaction(connection, immediate=True):
                    OperationRepository(connection).recover(root)
            return cls(connection).get()
        finally:
            connection.close()

    @classmethod
    def prepare_rename(
        cls,
        database: Path,
        request_id: str,
        old_name: str,
        new_name: str,
        old_updated_at: str,
        new_updated_at: str,
    ) -> str | None:
        connection = connect(database)
        try:
            with transaction(connection):
                previous = OperationRepository(connection).by_request_id(request_id)
                if previous:
                    payload = json.loads(previous["payload_json"])
                    if previous["kind"] != "rename" or payload.get("new_name") != new_name:
                        raise DomainErrorV1(ErrorCode.IDEMPOTENCY_CONFLICT, "request_id conflict")
                    if previous["state"] == "completed":
                        return None
                    raise DomainErrorV1(ErrorCode.IDEMPOTENCY_CONFLICT, "rename command incomplete")
                return OperationRepository(connection).prepare(
                    "rename",
                    request_id,
                    {
                        "old_name": old_name,
                        "new_name": new_name,
                        "old_updated_at": old_updated_at,
                        "new_updated_at": new_updated_at,
                    },
                )
        finally:
            connection.close()

    @classmethod
    def commit_rename(
        cls,
        database: Path,
        project_id: str,
        name: str,
        updated_at: str,
        request_id: str,
        operation_id: str,
    ) -> None:
        connection = connect(database)
        try:
            with transaction(connection):
                repository = cls(connection)
                repository.rename(project_id, name, updated_at)
                _append_business_event(
                    connection,
                    project_id,
                    "project.metadata.changed",
                    {"changed_fields": ["name"], "request_id": request_id},
                )
                operations = OperationRepository(connection)
                operations.mark(operation_id, "db_committed")
                operations.mark(operation_id, "completed")
        finally:
            connection.close()

    @staticmethod
    def mark_operation(database: Path, operation_id: str, state: str) -> None:
        connection = connect(database)
        try:
            with transaction(connection):
                OperationRepository(connection).mark(operation_id, state)
        finally:
            connection.close()

    @staticmethod
    def save_checkpoint(database: Path, project_id: str, request_id: str) -> dict[str, object]:
        connection = connect(database)
        try:
            with transaction(connection):
                event = _append_business_event(
                    connection,
                    project_id,
                    "project.metadata.changed",
                    {"changed_fields": [], "request_id": request_id},
                )
                return event.__dict__
        finally:
            connection.close()

    @staticmethod
    def update_workspace_state(
        database: Path, project_id: str, state: Mapping[str, object], request_id: str
    ) -> dict[str, object]:
        connection = connect(database)
        try:
            with transaction(connection):
                operations = OperationRepository(connection)
                previous = operations.by_request_id(request_id)
                payload = {"state": dict(state)}
                if previous:
                    if previous["kind"] != "workspace_state" or json.loads(previous["payload_json"]) != payload:
                        raise DomainErrorV1(ErrorCode.IDEMPOTENCY_CONFLICT, "request_id conflict")
                    if previous["state"] == "completed":
                        row = connection.execute("SELECT workspace_state_json FROM projects WHERE id=?", (project_id,)).fetchone()
                        return json.loads(row[0]) if row else {}
                    raise DomainErrorV1(ErrorCode.IDEMPOTENCY_CONFLICT, "workspace command incomplete")
                operation_id = operations.prepare("workspace_state", request_id, payload)
                row = connection.execute("SELECT workspace_state_json FROM projects WHERE id=?", (project_id,)).fetchone()
                if row is None:
                    raise DomainErrorV1(ErrorCode.PROJECT_NOT_FOUND, "Project does not exist.")
                merged = json.loads(row[0])
                merged.update(state)
                workspace_mode = str(merged.get("workspace_mode", "empty"))
                # Workspace layout is renderer-local state.  It intentionally does not
                # change the project metadata revision, which must stay in sync with
                # project.json for ProjectService.open() consistency checks.
                connection.execute(
                    "UPDATE projects SET workspace_mode=?,workspace_state_json=? WHERE id=?",
                    (workspace_mode, canonical_json(merged), project_id),
                )
                _append_business_event(connection, project_id, "project.metadata.changed", {"changed_fields": ["workspace_state"], "request_id": request_id})
                operations.mark(operation_id, "completed")
                return merged
        finally:
            connection.close()

    @staticmethod
    def workspace_state(database: Path, project_id: str) -> str | None:
        connection = connect(database, read_only=True)
        try:
            row = connection.execute(
                "SELECT workspace_state_json FROM projects WHERE id=?", (project_id,)
            ).fetchone()
            return str(row[0]) if row is not None else None
        finally:
            connection.close()


class PackageRepository:
    """Persistence boundary for package export/import projections."""

    @staticmethod
    def operation(connection: sqlite3.Connection, request_id: str) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT kind,state,payload_json,recovery_json FROM operations WHERE idempotency_key=?",
            (request_id,),
        ).fetchone()

    @staticmethod
    def export_projection(
        connection: sqlite3.Connection,
    ) -> tuple[
        dict[str, object],
        list[dict[str, object]],
        list[dict[str, object]],
        list[dict[str, object]],
        list[dict[str, object]],
    ]:
        project = dict(connection.execute("SELECT * FROM projects").fetchone())
        project["root_path"] = "."
        assets = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM assets WHERE trashed_at IS NULL ORDER BY created_at,id"
            )
        ]
        links = [dict(row) for row in connection.execute("SELECT * FROM asset_links")]
        selections = [dict(row) for row in connection.execute("SELECT * FROM selections")]
        decisions = [dict(row) for row in connection.execute("SELECT * FROM asset_decisions")]
        return project, assets, links, selections, decisions

    @staticmethod
    def complete_export(
        connection: sqlite3.Connection, operation_id: str, recovery: Mapping[str, object]
    ) -> None:
        connection.execute(
            "UPDATE operations SET state='completed',recovery_json=?,updated_at=? WHERE id=?",
            (canonical_json(recovery), utc_now(), operation_id),
        )

    @staticmethod
    def import_manifest(connection: sqlite3.Connection, manifest: Mapping[str, object]) -> None:
        project = manifest["project"]
        assert isinstance(project, Mapping)
        project_id = str(project["id"])
        connection.execute(
            "INSERT INTO projects VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                project_id,
                project["name"],
                ".",
                None,
                None,
                project.get("workspace_mode", "empty"),
                project.get("workspace_state_json", "{}"),
                1,
                project["created_at"],
                project["updated_at"],
            ),
        )
        connection.execute("INSERT INTO event_counters VALUES(?,1)", (project_id,))
        fields = (
            "id",
            "project_id",
            "asset_family_id",
            "parent_asset_id",
            "asset_type",
            "asset_group",
            "name",
            "version_no",
            "relative_path",
            "thumbnail_asset_id",
            "mime_type",
            "size_bytes",
            "sha256",
            "metadata_json",
            "provenance_json",
            "is_current",
            "is_hidden",
            "trashed_at",
            "original_relative_path",
            "created_at",
        )
        parent_links: list[tuple[object, object]] = []
        thumbnail_links: list[tuple[object, object]] = []
        for raw in cast(list[Mapping[str, object]], manifest.get("assets", [])):
            assert isinstance(raw, Mapping)
            values = [raw.get(field) for field in fields]
            parent_index, thumbnail_index = (
                fields.index("parent_asset_id"),
                fields.index("thumbnail_asset_id"),
            )
            if values[parent_index] is not None:
                parent_links.append((raw["id"], values[parent_index]))
                values[parent_index] = None
            if values[thumbnail_index] is not None:
                thumbnail_links.append((raw["id"], values[thumbnail_index]))
                values[thumbnail_index] = None
            connection.execute(
                f"INSERT INTO assets ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",
                tuple(values),
            )
        for asset_id, parent_id in parent_links:
            connection.execute(
                "UPDATE assets SET parent_asset_id=? WHERE id=?", (parent_id, asset_id)
            )
        for asset_id, thumbnail_id in thumbnail_links:
            connection.execute(
                "UPDATE assets SET thumbnail_asset_id=? WHERE id=?", (thumbnail_id, asset_id)
            )
        for link in cast(list[Mapping[str, object]], manifest.get("asset_links", [])):
            assert isinstance(link, Mapping)
            connection.execute(
                "INSERT INTO asset_links VALUES(?,?,?)",
                (link["from_asset_id"], link["to_asset_id"], link["relation_type"]),
            )
        for selection in cast(list[Mapping[str, object]], manifest.get("selections", [])):
            assert isinstance(selection, Mapping)
            keys = (
                "id",
                "project_id",
                "asset_id",
                "selection_type",
                "geometry_json",
                "label",
                "confidence",
                "source",
                "status",
                "confirmed_by_user",
                "revision",
                "created_at",
                "updated_at",
            )
            connection.execute(
                """INSERT INTO selections(
                id,project_id,asset_id,selection_type,geometry_json,label,confidence,source,status,
                confirmed_by_user,revision,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                tuple(selection[key] for key in keys),
            )
        for decision in cast(list[Mapping[str, object]], manifest.get("decisions", [])):
            assert isinstance(decision, Mapping)
            keys = (
                "id",
                "project_id",
                "asset_id",
                "previous_asset_id",
                "decision_source",
                "run_id",
                "reason",
                "created_at",
            )
            connection.execute(
                "INSERT INTO asset_decisions VALUES(?,?,?,?,?,?,?,?)",
                tuple(decision[key] for key in keys),
            )
        if project.get("current_asset_id"):
            connection.execute(
                "UPDATE projects SET current_asset_id=? WHERE id=?",
                (project["current_asset_id"], project_id),
            )

    def replay_export_request(
        self, database: Path, request_id: str, expected: Mapping[str, object]
    ) -> dict[str, object] | None:
        connection = connect(database)
        try:
            previous = self.operation(connection, request_id)
            if previous is None:
                return None
            payload = json.loads(previous["payload_json"])
            if previous["kind"] != "package_export" or any(
                payload.get(key) != value for key, value in expected.items()
            ):
                raise DomainErrorV1(ErrorCode.IDEMPOTENCY_CONFLICT, "request_id conflict")
            recovery = json.loads(previous["recovery_json"])
            if previous["state"] == "completed" and "result" in recovery:
                return recovery["result"]
            raise DomainErrorV1(ErrorCode.IDEMPOTENCY_CONFLICT, "export command incomplete")
        finally:
            connection.close()

    def prepare_export(self, database: Path, request_id: str, payload: Mapping[str, object]) -> str:
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                return OperationRepository(connection).prepare(
                    "package_export", request_id, dict(payload)
                )
        finally:
            connection.close()

    def export_projection_database(
        self, database: Path
    ) -> tuple[
        dict[str, object],
        list[dict[str, object]],
        list[dict[str, object]],
        list[dict[str, object]],
        list[dict[str, object]],
    ]:
        connection = connect(database, read_only=True)
        try:
            return self.export_projection(connection)
        finally:
            connection.close()

    def complete_export_committed(
        self, database: Path, operation_id: str, recovery: Mapping[str, object]
    ) -> None:
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                OperationRepository(connection).mark(operation_id, "file_written")
                OperationRepository(connection).mark(operation_id, "db_committed")
                self.complete_export(connection, operation_id, recovery)
        finally:
            connection.close()

    def mark_export_file_written(self, database: Path, operation_id: str) -> None:
        connection = connect(database)
        try:
            with transaction(connection):
                OperationRepository(connection).mark(operation_id, "file_written")
        finally:
            connection.close()

    def rollback_export_committed(self, database: Path, operation_id: str) -> None:
        connection = connect(database)
        try:
            with transaction(connection):
                self.complete_export(connection, operation_id, {"recovered": "export_rolled_back"})
        finally:
            connection.close()

    def import_manifest_committed(self, database: Path, manifest: Mapping[str, object]) -> None:
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                self.import_manifest(connection, manifest)
        finally:
            connection.close()


class OperationRepository:
    """Read-only journal lookup shared by recovery-aware application services."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def by_request_id(self, request_id: str) -> sqlite3.Row | None:
        return self._connection.execute(
            "SELECT kind,state,payload_json FROM operations WHERE idempotency_key=?", (request_id,)
        ).fetchone()

    def command(self, request_id: str) -> sqlite3.Row | None:
        return self._connection.execute(
            "SELECT payload_json,recovery_json,state FROM operations WHERE idempotency_key=?",
            (request_id,),
        ).fetchone()

    def prepare(self, kind: str, request_id: str, payload: dict[str, object]) -> str:
        operation_id, now = new_id(), utc_now()
        self._connection.execute(
            "INSERT INTO operations VALUES(?,?,?,?,?,?,?,?)",
            (
                operation_id,
                kind,
                "prepared",
                request_id,
                canonical_json(payload),
                "{}",
                now,
                now,
            ),
        )
        return operation_id

    def mark(self, operation_id: str, state: str) -> None:
        self._connection.execute(
            "UPDATE operations SET state=?,updated_at=? WHERE id=?",
            (state, utc_now(), operation_id),
        )

    def recover(self, root: Path) -> list[str]:
        """Recover journaled file/DB operations using only the owned SQLite connection."""
        recovered: list[str] = []
        rows = self._connection.execute(
            "SELECT id,kind,state,payload_json FROM operations WHERE state!='completed' ORDER BY created_at,id"
        ).fetchall()
        for row in rows:
            operation_id, kind, payload = row["id"], row["kind"], json.loads(row["payload_json"])
            if kind == "trash":
                source, target = (
                    root / payload["source_relative_path"],
                    root / payload["trash_relative_path"],
                )
                asset = self._connection.execute(
                    "SELECT trashed_at,relative_path FROM assets WHERE id=?", (payload["asset_id"],)
                ).fetchone()
                if (
                    asset
                    and asset["trashed_at"] is None
                    and target.exists()
                    and not source.exists()
                ):
                    source.parent.mkdir(parents=True, exist_ok=True)
                    target.replace(source)
                    state, report = "completed", {"recovered": "file_rolled_back"}
                elif (
                    asset
                    and asset["trashed_at"] is not None
                    and Path(asset["relative_path"]) == target.relative_to(root)
                ):
                    state, report = "completed", {"recovered": "committed"}
                else:
                    state, report = "failed", {"reason": "trash_state_inconsistent"}
                self._update_recovery(operation_id, state, report)
                if state == "completed":
                    recovered.append(operation_id)
                continue
            if kind == "asset_write":
                asset = self._connection.execute(
                    "SELECT relative_path FROM assets WHERE id=?", (payload["asset_id"],)
                ).fetchone()
                paths = [root / relative for relative in payload.get("relative_paths", [])]
                if asset and (root / asset["relative_path"]).is_file():
                    state, report = "completed", {"recovered": "committed"}
                elif asset:
                    state, report = "failed", {"reason": "db_committed_file_missing"}
                else:
                    for path in paths:
                        path.unlink(missing_ok=True)
                    state, report = (
                        "failed",
                        {"recovered": "orphan_files_removed", "safe_to_retry": True},
                    )
                self._update_recovery(operation_id, state, report)
                recovered.append(operation_id)
                continue
            if kind == "package_export":
                (root / payload["temporary_relative_path"]).unlink(missing_ok=True)
                self._update_recovery(
                    operation_id, "completed", {"recovered": "temporary_archive_removed"}
                )
                recovered.append(operation_id)
                continue
            if kind == "restore":
                source, target = (
                    root / payload["trash_relative_path"],
                    root / payload["restored_relative_path"],
                )
                asset = self._connection.execute(
                    "SELECT trashed_at,relative_path FROM assets WHERE id=?", (payload["asset_id"],)
                ).fetchone()
                if (
                    asset
                    and asset["trashed_at"] is None
                    and asset["relative_path"] == payload["restored_relative_path"]
                ):
                    state, report = "completed", {"recovered": "committed"}
                elif (
                    asset
                    and asset["trashed_at"] is not None
                    and target.exists()
                    and not source.exists()
                ):
                    source.parent.mkdir(parents=True, exist_ok=True)
                    target.replace(source)
                    state, report = "completed", {"recovered": "file_rolled_back"}
                else:
                    state, report = "failed", {"reason": "restore_state_inconsistent"}
                self._update_recovery(operation_id, state, report)
                recovered.append(operation_id)
                continue
            if kind != "rename":
                self._update_recovery(
                    operation_id, "failed", {"reason": "manual_recovery_required"}
                )
                continue
            current = self._connection.execute(
                "SELECT name,updated_at FROM projects LIMIT 1"
            ).fetchone()
            metadata_path = root / "project.json"
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except FileNotFoundError, json.JSONDecodeError:
                metadata = {}
            if current and current["name"] == payload["new_name"]:
                metadata["name"], metadata["updated_at"] = (
                    payload["new_name"],
                    payload.get("new_updated_at", current["updated_at"]),
                )
                state, report = "completed", {"recovered": "committed"}
            else:
                metadata["name"], metadata["updated_at"] = (
                    payload["old_name"],
                    payload.get("old_updated_at", current["updated_at"] if current else utc_now()),
                )
                state, report = "completed", {"recovered": "rolled_back"}
            atomic_write_text(metadata_path, canonical_json(metadata))
            self._update_recovery(operation_id, state, report)
            recovered.append(operation_id)
        return recovered

    def mark_failed_recoverable(self, operation_id: str) -> None:
        self._connection.execute(
            "UPDATE operations SET state='failed',recovery_json=?,updated_at=? WHERE id=?",
            (
                canonical_json({"recovered": "orphan_files_removed", "safe_to_retry": True}),
                utc_now(),
                operation_id,
            ),
        )

    def complete_with_result(self, operation_id: str, result: Mapping[str, object]) -> None:
        self._connection.execute(
            "UPDATE operations SET state='completed',recovery_json=?,updated_at=? WHERE id=?",
            (canonical_json({"result": result}), utc_now(), operation_id),
        )

    def _update_recovery(self, operation_id: str, state: str, report: Mapping[str, object]) -> None:
        self._connection.execute(
            "UPDATE operations SET state=?,recovery_json=?,updated_at=? WHERE id=?",
            (state, canonical_json(report), utc_now(), operation_id),
        )

    def unfinished_count(self) -> int:
        return int(
            self._connection.execute(
                "SELECT COUNT(*) FROM operations WHERE state!='completed'"
            ).fetchone()[0]
        )
