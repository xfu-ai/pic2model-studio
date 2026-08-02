"""Metadata persistence for validated managed GLB assets."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...domain.ids import canonical_json
from .connection import connect, transaction


class SqliteModelAssetRepository:
    def relative_path(self, database: Path, project_id: str, asset_id: str) -> str | None:
        connection = connect(database, read_only=True)
        try:
            row = connection.execute(
                """SELECT relative_path FROM assets WHERE id=? AND project_id=? AND asset_type='glb'
                AND trashed_at IS NULL""",
                (asset_id, project_id),
            ).fetchone()
            return str(row["relative_path"]) if row else None
        finally:
            connection.close()

    def store_inspection(
        self, database: Path, project_id: str, asset_id: str, inspection: Mapping[str, Any]
    ) -> bool:
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                row = connection.execute(
                    """SELECT metadata_json FROM assets WHERE id=? AND project_id=? AND asset_type='glb'
                    AND trashed_at IS NULL""",
                    (asset_id, project_id),
                ).fetchone()
                if row is None:
                    return False
                metadata = json.loads(str(row["metadata_json"]))
                metadata["model_inspection"] = inspection
                connection.execute(
                    "UPDATE assets SET metadata_json=? WHERE id=?",
                    (canonical_json(metadata), asset_id),
                )
                return True
        finally:
            connection.close()
