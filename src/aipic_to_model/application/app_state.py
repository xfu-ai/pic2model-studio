"""App-scoped command journal and health service, independent of SQLite."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..domain.common import canonical_json
from .ports import AppStateRepository


class AppStateService:
    def __init__(self, repository: AppStateRepository) -> None:
        self._repository = repository

    @staticmethod
    def _payload_hash(payload: dict[str, str]) -> str:
        return hashlib.sha256(canonical_json(payload).encode()).hexdigest()

    def replay_command(
        self, app_db: Path, action: str, payload: dict[str, str], request_id: str
    ) -> dict[str, object] | None:
        return self._repository.replay_operation(
            app_db, action, self._payload_hash(payload), request_id
        )

    def complete_command(
        self,
        app_db: Path,
        action: str,
        payload: dict[str, str],
        request_id: str,
        result: dict[str, object],
    ) -> None:
        self._repository.complete_operation(
            app_db, action, self._payload_hash(payload), request_id, result
        )

    def health_snapshot(self, roots: tuple[Path, ...], app_db: Path) -> dict[str, object]:
        return self._repository.health_snapshot(roots, app_db)

    def record_recent_project(self, app_db: Path, project_id: str, root: Path) -> None:
        self._repository.record_recent_project(app_db, project_id, root)

    def recent_projects(self, app_db: Path) -> list[dict[str, object]]:
        return self._repository.list_recent_projects(app_db)

    def recent_project_root(self, app_db: Path, project_id: str) -> Path | None:
        return self._repository.recent_project_root(app_db, project_id)
