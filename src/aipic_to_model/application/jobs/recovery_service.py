"""Application-level startup recovery that never resubmits paid work."""

from __future__ import annotations

from pathlib import Path

from ...infrastructure.sqlite.job_repository import SqliteJobRepository
from .recovery import classify_recovery


class JobRecoveryService:
    def __init__(self, jobs: SqliteJobRepository) -> None:
        self._jobs = jobs

    def recover(self, root: Path) -> list[dict[str, str]]:
        database = root / "project.sqlite3"
        recovered: list[dict[str, str]] = []
        for job in self._jobs.list_nonterminal(database):
            decision = classify_recovery(job.id, job.resume_class, job.external_task_id)
            recovered.append({"job_id": job.id, "action": decision.action})
        return recovered
