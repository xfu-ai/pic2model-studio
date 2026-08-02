"""SQLite persistence for B02 image-generation candidate groups."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ...application.events import EventService, NewEvent
from ...domain.ids import canonical_json, new_id, utc_now
from ...domain.production_models import CandidateGroupDTO, CandidateItemDTO
from .connection import connect, transaction
from .repositories import EventRepository


@dataclass(frozen=True)
class CandidateDraft:
    asset_id: str
    provider: str
    model: str
    parameters: dict[str, object]
    evaluation_status: str = "not_evaluated"
    short_evaluation: str | None = None
    anomalies: tuple[str, ...] = ()


class CandidateRepository:
    """SQLite group writes; all externally generated images remain separate assets."""

    def create(
        self,
        database: Path,
        *,
        project_id: str,
        prompt_asset_id: str,
        source_asset_id: str | None,
        provider: str,
        request: dict[str, object],
        items: list[CandidateDraft],
    ) -> str:
        if not 1 <= len(items) <= 8:
            raise ValueError("candidate groups require 1 to 8 items")
        group_id = new_id()
        now = utc_now()
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                connection.execute(
                    """INSERT INTO candidate_groups(id,project_id,source_asset_id,prompt_asset_id,provider,
                    requested_count,request_json,status,created_at) VALUES(?,?,?,?,?,?,?,'created',?)""",
                    (
                        group_id,
                        project_id,
                        source_asset_id,
                        prompt_asset_id,
                        provider,
                        len(items),
                        canonical_json(request),
                        now,
                    ),
                )
                for ordinal, item in enumerate(items, start=1):
                    connection.execute(
                        "INSERT INTO candidate_items(group_id,asset_id,ordinal) VALUES(?,?,?)",
                        (group_id, item.asset_id, ordinal),
                    )
                    connection.execute(
                        """INSERT INTO candidate_assessments(group_id,asset_id,evaluation_status,
                        short_evaluation,anomalies_json,created_at) VALUES(?,?,?,?,?,?)""",
                        (
                            group_id,
                            item.asset_id,
                            item.evaluation_status,
                            item.short_evaluation,
                            canonical_json(list(item.anomalies)),
                            now,
                        ),
                    )
                connection.execute(
                    "UPDATE candidate_groups SET status='ready' WHERE id=?", (group_id,)
                )
                EventService(EventRepository()).append_in_tx(
                    NewEvent(
                        transaction=connection,
                        project_id=project_id,
                        event_type="candidate.created",
                        payload={
                            "candidate_group_id": group_id,
                            "asset_ids": [item.asset_id for item in items],
                        },
                        entity_id=group_id,
                    )
                )
            return group_id
        finally:
            connection.close()

    def select(
        self,
        database: Path,
        *,
        project_id: str,
        group_id: str,
        asset_ids: list[str],
        selection_mode: str,
    ) -> None:
        if selection_mode not in {"single_continue", "multi_compare"}:
            raise ValueError("invalid candidate selection mode")
        if selection_mode == "single_continue" and len(asset_ids) != 1:
            raise ValueError("single continuation requires exactly one candidate")
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                known = {
                    row[0]
                    for row in connection.execute(
                        "SELECT asset_id FROM candidate_items WHERE group_id=?", (group_id,)
                    )
                }
                if not set(asset_ids) <= known:
                    raise ValueError("candidate selection contains an unknown asset")
                connection.execute(
                    "UPDATE candidate_items SET selected=0 WHERE group_id=?", (group_id,)
                )
                connection.executemany(
                    "UPDATE candidate_items SET selected=1 WHERE group_id=? AND asset_id=?",
                    [(group_id, asset_id) for asset_id in asset_ids],
                )
                connection.execute(
                    "UPDATE candidate_groups SET status='selected' WHERE id=?", (group_id,)
                )
                EventService(EventRepository()).append_in_tx(
                    NewEvent(
                        transaction=connection,
                        project_id=project_id,
                        event_type="candidate.selected",
                        payload={
                            "candidate_group_id": group_id,
                            "selected_asset_ids": asset_ids,
                            "selection_mode": selection_mode,
                        },
                        entity_id=group_id,
                    )
                )
        finally:
            connection.close()

    def get(self, database: Path, group_id: str) -> CandidateGroupDTO:
        connection = connect(database, read_only=True)
        try:
            group = connection.execute(
                "SELECT * FROM candidate_groups WHERE id=?", (group_id,)
            ).fetchone()
            if group is None:
                raise KeyError(group_id)
            rows = connection.execute(
                """SELECT candidate_items.asset_id,candidate_items.selected,ordinal,assets.version_no,candidate_groups.provider,
                candidate_groups.request_json,assets.created_at,candidate_assessments.* FROM candidate_items
                JOIN candidate_groups ON candidate_groups.id=candidate_items.group_id
                JOIN assets ON assets.id=candidate_items.asset_id
                JOIN candidate_assessments ON candidate_assessments.group_id=candidate_items.group_id
                AND candidate_assessments.asset_id=candidate_items.asset_id WHERE candidate_items.group_id=? ORDER BY ordinal""",
                (group_id,),
            ).fetchall()
            items = [
                CandidateItemDTO(
                    asset_id=row["asset_id"],
                    ordinal=row["ordinal"],
                    version_no=row["version_no"],
                    created_at=row["created_at"],
                    provider=row["provider"],
                    model=json.loads(row["request_json"]).get("model", "unknown"),
                    parameters=json.loads(row["request_json"]),
                    evaluation_status=row["evaluation_status"],
                    short_evaluation=row["short_evaluation"],
                    anomalies=json.loads(row["anomalies_json"]),
                )
                for row in rows
            ]
            return CandidateGroupDTO(
                id=group_id,
                source_asset_id=group["source_asset_id"],
                prompt_asset_id=group["prompt_asset_id"],
                requested_count=group["requested_count"],
                status=group["status"],
                items=items,
                selected_asset_ids=[row["asset_id"] for row in rows if row["selected"]],
                warnings=json.loads(group["warnings_json"]),
            )
        finally:
            connection.close()
