"""Independent background loop for durable production Jobs."""

from __future__ import annotations

import threading
import sqlite3
from pathlib import Path
from typing import Any


class BackgroundJobRunner:
    """Run one bounded lease step per open project without blocking the API."""

    def __init__(
        self,
        worker: Any,
        roots: dict[str, Path],
        *,
        interval_seconds: float = 2.0,
    ) -> None:
        self._worker = worker
        self._roots = roots
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._owner = f"sidecar-{id(self):x}"

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="aipic-production-jobs",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self._thread = None

    def wake(self) -> None:
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            for project_id, root in tuple(self._roots.items()):
                if self._stop.is_set():
                    break
                try:
                    self._worker.run_once(root, project_id, owner=self._owner)
                except (OSError, ValueError, sqlite3.Error):
                    # Opening/recovery races are retried on the next bounded tick.
                    continue
            # A remote Tripo task is durable and is advanced one bounded step
            # at a time.  Polling again after 50 ms hammers the provider (up
            # to 20 requests/second), leading to rate limits that look like a
            # permanently running task.  New submissions still call wake(),
            # so the first step starts immediately; subsequent checks use the
            # configured, provider-safe cadence.
            self._wake.wait(self._interval)
            self._wake.clear()
