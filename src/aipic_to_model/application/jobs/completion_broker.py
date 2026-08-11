"""In-process notification and bounded waiting for durable Job terminal states."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


def _is_terminal(job: Any) -> bool:
    """Treat an interrupted Job with a durable error as terminal for Agent waits.

    A plain interrupted state is a resumable worker handoff boundary.  An
    interrupted record carrying an error (notably ``JOB_UNKNOWN_SUBMISSION``)
    requires user-visible recovery instead, so leaving it out of the terminal
    set makes the Agent wait forever after the task center has already stopped.
    """

    status = getattr(job.status, "value", job.status)
    return status in _TERMINAL_STATUSES or (
        status == "interrupted" and bool(getattr(job, "error", None))
    )


class JobCompletionBroker:
    """Wait for a durable Job without changing, cancelling, or resubmitting it.

    The repository remains authoritative. Notifications only reduce wake-up
    latency; checking the repository before and after each wait closes races
    with a worker that completed before a waiter subscribed.
    """

    def __init__(
        self,
        jobs: Any,
        *,
        clock: Callable[[], float] = time.monotonic,
        wait_slice_seconds: float = 0.25,
    ) -> None:
        self._jobs = jobs
        self._clock = clock
        self._wait_slice_seconds = wait_slice_seconds
        self._condition = threading.Condition()

    def notify_terminal(self, job_id: str) -> None:
        """Wake waiters after a worker has committed a terminal repository state."""

        with self._condition:
            self._condition.notify_all()

    async def wait_for_terminal(
        self, database: Path, job_id: str, *, timeout_seconds: float = 180.0
    ) -> Any | None:
        """Return the terminal repository record, or ``None`` on wait expiry."""

        return await asyncio.to_thread(
            self._wait_for_terminal, database, job_id, timeout_seconds
        )

    def _wait_for_terminal(self, database: Path, job_id: str, timeout_seconds: float) -> Any | None:
        deadline = self._clock() + max(0.0, timeout_seconds)
        while True:
            job = self._jobs.get(database, job_id=job_id)
            if _is_terminal(job):
                return job
            remaining = deadline - self._clock()
            if remaining <= 0:
                # The final repository read is intentional: a terminal update
                # that races the clock wins over returning waiting_external.
                job = self._jobs.get(database, job_id=job_id)
                return job if _is_terminal(job) else None
            with self._condition:
                self._condition.wait(min(remaining, self._wait_slice_seconds))
