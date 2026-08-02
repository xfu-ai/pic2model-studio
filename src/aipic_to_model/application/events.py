from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..domain.common import EventCursorCodec, EventEnvelopeV1
from .ports import EventRepositoryPort


@dataclass(frozen=True)
class NewEvent:
    """Application event command bound to an already-open business transaction."""

    transaction: object
    project_id: str
    event_type: str
    payload: dict[str, object]
    entity_id: str | None = None
    run_id: str | None = None


class EventService:
    def __init__(self, repository: EventRepositoryPort) -> None:
        self._repository = repository

    def append_in_tx(self, event: NewEvent) -> EventEnvelopeV1:
        """Append without opening or committing a transaction owned by the caller."""
        return self._repository.append_named_in_tx(
            event.transaction,
            event.project_id,
            event.event_type,
            event.payload,
            event.entity_id,
            event.run_id,
        )

    def replay(
        self, root: Path, project_id: str, after: str | None, limit: int = 100
    ) -> dict[str, Any]:
        return self.replay_project(root, project_id, after, limit)

    def replay_project(
        self, root: Path, project_id: str, after: str | None, limit: int = 100
    ) -> dict[str, Any]:
        """Read a project's durable event log without exposing SQLite to API adapters."""
        return self._page(
            self._repository.replay_project(
                root / "project.sqlite3", project_id, self._sequence(after, project_id), limit
            ),
            project_id,
            after,
        )

    def ack(
        self,
        root: Path,
        project_id: str,
        consumer_id: str,
        sequence_no: int,
    ) -> None:
        self._repository.ack_committed(
            root / "project.sqlite3", project_id, consumer_id, sequence_no
        )

    @staticmethod
    def _sequence(after: str | None, project_id: str) -> int:
        return 0 if after is None else EventCursorCodec.decode(after, project_id)

    @staticmethod
    def _page(rows: list[Any], project_id: str, after: str | None) -> dict[str, Any]:
        items = [
            EventEnvelopeV1(
                row["event_id"],
                row["event_type"],
                row["event_version"],
                row["project_id"],
                row["sequence_no"],
                json.loads(row["payload_json"]),
                row["created_at"],
                row["conversation_id"],
                row["run_id"],
                row["entity_id"],
            ).__dict__
            for row in rows
        ]
        next_cursor = (
            EventCursorCodec.encode(project_id, rows[-1]["sequence_no"]) if rows else after
        )
        return {"items": items, "next_cursor": next_cursor}
