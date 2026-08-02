"""Persistence for immutable B02 bilingual Prompt versions."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from ...domain.ids import new_id, utc_now
from .connection import connect, transaction


class PromptVersionRepository:
    def append(
        self,
        database: Path,
        *,
        project_id: str,
        asset_id: str,
        kind: str,
        language: str,
        body: str,
        parser_version: int,
    ) -> str:
        identifier = new_id()
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                connection.execute(
                    """INSERT INTO prompt_versions(
                    id,project_id,asset_id,kind,language,body,parser_version,created_at
                    ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        identifier,
                        project_id,
                        asset_id,
                        kind,
                        language,
                        body,
                        parser_version,
                        utc_now(),
                    ),
                )
            return identifier
        finally:
            connection.close()

    def list_for_asset(
        self, database: Path, *, project_id: str, asset_id: str
    ) -> list[Mapping[str, object]]:
        connection = connect(database, read_only=True)
        try:
            return [
                dict(row)
                for row in connection.execute(
                    """SELECT id,kind,language,body,parser_version,created_at
                    FROM prompt_versions WHERE project_id=? AND asset_id=?
                    ORDER BY created_at,id""",
                    (project_id, asset_id),
                )
            ]
        finally:
            connection.close()
