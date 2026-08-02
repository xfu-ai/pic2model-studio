"""Port for B02 persistent Job and transactional outbox operations."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ...domain.job_models import JobStatus, ResumeClass


class JobRepository(Protocol):
    def create(self, database: Path, record: dict[str, object]) -> None: ...

    def claim(
        self, database: Path, *, owner: str, now: str, lease_until: str
    ) -> dict[str, object] | None: ...

    def update_status(
        self,
        database: Path,
        *,
        job_id: str,
        current: JobStatus,
        target: JobStatus,
        stage: str,
        resume_class: ResumeClass,
    ) -> None: ...
