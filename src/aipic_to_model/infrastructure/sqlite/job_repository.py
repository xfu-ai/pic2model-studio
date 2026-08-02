"""SQLite implementation of B02's durable Job Manager primitives."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...application.jobs.manager import validate_resume_payload
from ...domain.ids import canonical_json, new_id, utc_now
from ...domain.job_models import JobStage, JobStatus, ResumeClass, assert_job_transition
from .connection import connect, transaction

MAX_LOCAL_AUTOMATIC_ATTEMPTS = 3


@dataclass(frozen=True)
class StoredJob:
    id: str
    tool_call_id: str
    job_type: str
    status: JobStatus
    stage: JobStage
    resume_class: ResumeClass
    provider: str | None
    external_task_id: str | None
    resume: dict[str, Any]
    progress: int | None
    result_asset_ids: list[str]
    error: dict[str, Any] | None
    lease_owner: str | None
    lease_until: str | None
    heartbeat_at: str | None
    created_at: str
    updated_at: str


class SqliteJobRepository:
    """All state changes append an outbox record in the same transaction."""

    def create(
        self,
        database: Path,
        *,
        job_id: str,
        tool_call_id: str,
        job_type: str,
        provider: str | None,
        resume_class: ResumeClass = ResumeClass.FRESH,
        stage: JobStage = JobStage.QUEUED,
        resume: dict[str, Any] | None = None,
    ) -> StoredJob:
        now = utc_now()
        validate_resume_payload(resume or {})
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                connection.execute(
                    """INSERT INTO jobs(
                        id,tool_call_id,job_type,status,stage,provider,resume_class,resume_json,
                        created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        job_id,
                        tool_call_id,
                        job_type,
                        JobStatus.QUEUED.value,
                        stage.value,
                        provider,
                        resume_class.value,
                        canonical_json(resume or {}),
                        now,
                        now,
                    ),
                )
                self._emit(
                    connection, job_id, "job.created", self._payload(JobStatus.QUEUED, stage)
                )
                return self._load(connection, job_id)
        finally:
            connection.close()

    def claim(self, database: Path, *, owner: str, lease_until: str) -> StoredJob | None:
        """Atomically lease only safe work; unknown submissions are never claimed."""
        now = utc_now()
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                row = connection.execute(
                    """SELECT jobs.id,jobs.status FROM jobs
                    WHERE ((status='queued' AND resume_class IN('fresh','local_restartable','download_retry'))
                       OR (status='interrupted' AND (
                           resume_class='download_retry'
                           OR (resume_class='local_restartable' AND (
                               SELECT COUNT(*) FROM outbox_events AS attempts
                               WHERE attempts.aggregate_id=jobs.id
                                 AND attempts.event_type='job.started'
                           )<?)
                       )))
                      AND (
                          status!='interrupted'
                          OR COALESCE(
                              CAST(json_extract(error_json,'$.retry_after_seconds') AS INTEGER),
                              0
                          )<=0
                          OR julianday(updated_at) + (
                              CAST(json_extract(error_json,'$.retry_after_seconds') AS INTEGER)
                              / 86400.0
                          )<=julianday(?)
                      )
                      AND (lease_until IS NULL OR lease_until<?)
                    ORDER BY CASE status WHEN 'queued' THEN 0 ELSE 1 END, created_at,id LIMIT 1""",
                    (MAX_LOCAL_AUTOMATIC_ATTEMPTS, now, now),
                ).fetchone()
                if row is None:
                    return None
                current = JobStatus(row["status"])
                assert_job_transition(current, JobStatus.RUNNING)
                updated = connection.execute(
                    """UPDATE jobs SET status=?,lease_owner=?,lease_until=?,heartbeat_at=?,
                    error_json=NULL,updated_at=?
                    WHERE id=? AND status=? AND (lease_until IS NULL OR lease_until<?)""",
                    (
                        JobStatus.RUNNING.value,
                        owner,
                        lease_until,
                        now,
                        now,
                        row["id"],
                        current.value,
                        now,
                    ),
                )
                if updated.rowcount != 1:
                    return None
                record = self._load(connection, str(row["id"]))
                self._emit(
                    connection, record.id, "job.started", self._payload(record.status, record.stage)
                )
                return record
        finally:
            connection.close()

    def claim_remote(self, database: Path, *, owner: str, lease_until: str) -> StoredJob | None:
        """Lease a known remote task for GET-only polling.

        Unknown submissions are intentionally excluded.  A recovered remote
        job can therefore cross only a read boundary until its artifact is
        ready for the separately resumable download phase.
        """
        now = utc_now()
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                row = connection.execute(
                    """SELECT id,status FROM jobs
                    WHERE status IN('waiting','interrupted')
                      AND resume_class='remote_poll'
                      AND external_task_id IS NOT NULL
                      AND (lease_until IS NULL OR lease_until<?)
                    ORDER BY created_at,id LIMIT 1""",
                    (now,),
                ).fetchone()
                if row is None:
                    return None
                current = JobStatus(row["status"])
                assert_job_transition(current, JobStatus.RUNNING)
                changed = connection.execute(
                    """UPDATE jobs SET status='running',lease_owner=?,lease_until=?,
                    heartbeat_at=?,error_json=NULL,updated_at=?
                    WHERE id=? AND status=? AND external_task_id IS NOT NULL
                      AND resume_class='remote_poll'
                      AND (lease_until IS NULL OR lease_until<?)""",
                    (owner, lease_until, now, now, row["id"], current.value, now),
                )
                if changed.rowcount != 1:
                    return None
                record = self._load(connection, str(row["id"]))
                self._emit(
                    connection, record.id, "job.started", self._payload(record.status, record.stage)
                )
                return record
        finally:
            connection.close()

    def heartbeat(self, database: Path, *, job_id: str, owner: str, lease_until: str) -> bool:
        now = utc_now()
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                changed = connection.execute(
                    """UPDATE jobs SET heartbeat_at=?,lease_until=?,updated_at=?
                    WHERE id=? AND status='running' AND lease_owner=?""",
                    (now, lease_until, now, job_id, owner),
                )
                return changed.rowcount == 1
        finally:
            connection.close()

    def mark_submission_started(
        self,
        database: Path,
        *,
        job_id: str,
        owner: str,
    ) -> StoredJob:
        """Persist the paid-create boundary before entering a Provider adapter."""

        now = utc_now()
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                changed = connection.execute(
                    """UPDATE jobs SET stage=?,resume_class=?,error_json=NULL,updated_at=?
                    WHERE id=? AND status='running' AND lease_owner=?""",
                    (
                        JobStage.CREATING.value,
                        ResumeClass.UNKNOWN_SUBMISSION.value,
                        now,
                        job_id,
                        owner,
                    ),
                )
                if changed.rowcount != 1:
                    raise ValueError("paid submission boundary requires the active Job lease")
            return self.get(database, job_id=job_id)
        finally:
            connection.close()

    def get(self, database: Path, *, job_id: str) -> StoredJob:
        connection = connect(database, read_only=True)
        try:
            return self._load(connection, job_id)
        finally:
            connection.close()

    def get_by_tool_call(self, database: Path, *, tool_call_id: str) -> StoredJob | None:
        connection = connect(database, read_only=True)
        try:
            row = connection.execute(
                "SELECT id FROM jobs WHERE tool_call_id=? ORDER BY created_at DESC LIMIT 1",
                (tool_call_id,),
            ).fetchone()
            return self._load(connection, str(row["id"])) if row is not None else None
        finally:
            connection.close()

    def list_nonterminal(self, database: Path) -> list[StoredJob]:
        """Return recovery candidates without changing their state or lease."""
        connection = connect(database, read_only=True)
        try:
            rows = connection.execute(
                """SELECT id FROM jobs WHERE status NOT IN('succeeded','failed','cancelled')
                ORDER BY created_at,id"""
            ).fetchall()
            return [self._load(connection, str(row["id"])) for row in rows]
        finally:
            connection.close()

    def list_recent(self, database: Path, *, limit: int = 32) -> list[StoredJob]:
        """Return the latest durable jobs for the task center, including results."""
        connection = connect(database, read_only=True)
        try:
            rows = connection.execute(
                "SELECT id FROM jobs ORDER BY updated_at DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [self._load(connection, str(row["id"])) for row in rows]
        finally:
            connection.close()

    def retry_context(self, database: Path, *, job_id: str) -> dict[str, Any]:
        """Return the frozen, already-redacted Tool input needed for a retry."""
        connection = connect(database, read_only=True)
        try:
            job_row = connection.execute(
                """SELECT id,status,resume_class,error_json,tool_call_id,resume_json
                FROM jobs WHERE id=?""",
                (job_id,),
            ).fetchone()
            if job_row is None:
                raise KeyError(job_id)
            resume = json.loads(str(job_row["resume_json"]))
            stored_source = resume.get("source_tool_call_id")
            source_tool_call_id = (
                stored_source
                if isinstance(stored_source, str) and stored_source
                else str(job_row["tool_call_id"])
            )
            tool_row = connection.execute(
                """SELECT tool_name,tool_version,arguments_json,provider_profile,risk_level
                FROM tool_calls WHERE id=?""",
                (source_tool_call_id,),
            ).fetchone()
            if tool_row is None:
                raise KeyError(source_tool_call_id)
            return {
                "job_id": str(job_row["id"]),
                "status": str(job_row["status"]),
                "resume_class": str(job_row["resume_class"]),
                "error": (
                    json.loads(str(job_row["error_json"])) if job_row["error_json"] else None
                ),
                "source_tool_call_id": source_tool_call_id,
                "tool_name": str(tool_row["tool_name"]),
                "tool_version": str(tool_row["tool_version"]),
                "arguments": json.loads(str(tool_row["arguments_json"])),
                "provider_profile": tool_row["provider_profile"],
                "risk_level": str(tool_row["risk_level"]),
            }
        finally:
            connection.close()

    def bind_external_task(
        self,
        database: Path,
        *,
        job_id: str,
        provider: str,
        external_task_id: str,
        submission_summary: dict[str, object],
    ) -> StoredJob:
        """Cross the paid boundary once and durably record its opaque task ID."""
        if not external_task_id.strip():
            raise ValueError("external_task_id must not be empty")
        validate_resume_payload(submission_summary)
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                current = self._load(connection, job_id)
                if current.external_task_id and current.external_task_id != external_task_id:
                    raise ValueError("job already has a different external task")
                if current.external_task_id == external_task_id:
                    return current
                assert_job_transition(current.status, JobStatus.WAITING)
                now = utc_now()
                connection.execute(
                    """UPDATE jobs SET status=?,stage=?,provider=?,external_task_id=?,resume_class=?,
                    resume_json=?,heartbeat_at=?,lease_owner=NULL,lease_until=NULL,updated_at=?
                    WHERE id=?""",
                    (
                        JobStatus.WAITING.value,
                        JobStage.REMOTE_QUEUED.value,
                        provider,
                        external_task_id,
                        ResumeClass.REMOTE_POLL.value,
                        canonical_json(submission_summary),
                        now,
                        now,
                        job_id,
                    ),
                )
                record = self._load(connection, job_id)
                self._emit(
                    connection,
                    job_id,
                    "job.waiting",
                    self._payload(record.status, record.stage, record),
                )
                return record
        finally:
            connection.close()

    def update(
        self,
        database: Path,
        *,
        job_id: str,
        target: JobStatus,
        stage: JobStage,
        resume_class: ResumeClass | None = None,
        progress: int | None = None,
        error: dict[str, Any] | None = None,
        result_asset_ids: list[str] | None = None,
        resume: dict[str, Any] | None = None,
    ) -> StoredJob:
        if resume is not None:
            validate_resume_payload(resume)
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                current = self._load(connection, job_id)
                if target is not current.status:
                    assert_job_transition(current.status, target)
                next_resume = resume_class or current.resume_class
                now = utc_now()
                connection.execute(
                    """UPDATE jobs SET status=?,stage=?,resume_class=?,progress=?,error_json=?,
                    resume_json=?,
                    result_asset_ids_json=?,lease_owner=CASE WHEN ? IN ('waiting','succeeded','failed','cancelled','interrupted') THEN NULL ELSE lease_owner END,
                    lease_until=CASE WHEN ? IN ('waiting','succeeded','failed','cancelled','interrupted') THEN NULL ELSE lease_until END,
                    updated_at=? WHERE id=?""",
                    (
                        target.value,
                        stage.value,
                        next_resume.value,
                        progress,
                        canonical_json(error) if error is not None else None,
                        canonical_json(resume if resume is not None else current.resume),
                        canonical_json(
                            result_asset_ids
                            if result_asset_ids is not None
                            else current.result_asset_ids
                        ),
                        target.value,
                        target.value,
                        now,
                        job_id,
                    ),
                )
                record = self._load(connection, job_id)
                event = {
                    JobStatus.RUNNING: "job.progressed",
                    JobStatus.WAITING: "job.waiting",
                    JobStatus.SUCCEEDED: "job.completed",
                    JobStatus.FAILED: "job.failed",
                    JobStatus.CANCELLED: "job.cancelled",
                    JobStatus.INTERRUPTED: "job.interrupted",
                }.get(target)
                if event is not None:
                    self._emit(
                        connection,
                        job_id,
                        event,
                        self._payload(record.status, record.stage, record),
                    )
                if target is JobStatus.SUCCEEDED and record.result_asset_ids:
                    self._emit(
                        connection,
                        job_id,
                        "job.result_ready",
                        self._payload(record.status, record.stage, record),
                    )
                return record
        finally:
            connection.close()

    def request_cancel(self, database: Path, *, job_id: str, mode: str) -> StoredJob:
        """Persist cancellation intent; remote completion can still win the race."""
        if mode not in {"local", "remote", "stop_waiting"}:
            raise ValueError("invalid cancellation mode")
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                current = self._load(connection, job_id)
                if current.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
                    return current
                now = utc_now()
                target = JobStatus.CANCELLED if mode == "local" else JobStatus.WAITING
                if target is not current.status:
                    assert_job_transition(current.status, target)
                stage = (
                    JobStage.STOP_WAITING if mode == "stop_waiting" else JobStage.CANCEL_REQUESTED
                )
                resume_class = (
                    ResumeClass.STOP_WAITING if mode == "stop_waiting" else current.resume_class
                )
                connection.execute(
                    """UPDATE jobs SET status=?,stage=?,resume_class=?,cancel_requested_at=?,cancel_mode=?,
                    lease_owner=NULL,lease_until=NULL,updated_at=? WHERE id=?""",
                    (target.value, stage.value, resume_class.value, now, mode, now, job_id),
                )
                record = self._load(connection, job_id)
                self._emit(
                    connection,
                    job_id,
                    "job.cancelled" if target is JobStatus.CANCELLED else "job.waiting",
                    self._payload(record.status, record.stage, record),
                )
                return record
        finally:
            connection.close()

    def interrupt_expired(self, database: Path, *, before: str) -> list[str]:
        """Mark stale local workers interrupted without claiming remote work."""
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                rows = connection.execute(
                    """SELECT id FROM jobs WHERE status='running' AND heartbeat_at IS NOT NULL
                    AND heartbeat_at<? AND resume_class IN('fresh','local_restartable','download_retry')""",
                    (before,),
                ).fetchall()
                ids = [str(row["id"]) for row in rows]
                for job_id in ids:
                    current = self._load(connection, job_id)
                    assert_job_transition(current.status, JobStatus.INTERRUPTED)
                    now = utc_now()
                    connection.execute(
                        """UPDATE jobs SET status=?,stage=?,lease_owner=NULL,lease_until=NULL,
                        updated_at=? WHERE id=?""",
                        (JobStatus.INTERRUPTED.value, JobStage.QUEUED.value, now, job_id),
                    )
                    interrupted = self._load(connection, job_id)
                    self._emit(
                        connection,
                        job_id,
                        "job.interrupted",
                        self._payload(interrupted.status, interrupted.stage, interrupted),
                    )
                return ids
        finally:
            connection.close()

    def replay_outbox(
        self, database: Path, *, after: int, limit: int = 100
    ) -> list[dict[str, Any]]:
        connection = connect(database, read_only=True)
        try:
            rows = connection.execute(
                """SELECT sequence_no,id,aggregate_id,event_type,payload_json,created_at
                FROM outbox_events WHERE sequence_no>? ORDER BY sequence_no LIMIT ?""",
                (after, min(max(limit, 1), 1000)),
            ).fetchall()
            return [{**dict(row), "payload": json.loads(str(row["payload_json"]))} for row in rows]
        finally:
            connection.close()

    def consume(
        self, database: Path, *, consumer_name: str, event_id: str, sequence_no: int
    ) -> bool:
        connection = connect(database)
        try:
            with transaction(connection, immediate=True):
                known = connection.execute(
                    "SELECT 1 FROM event_consumptions WHERE consumer_name=? AND event_id=?",
                    (consumer_name, event_id),
                ).fetchone()
                if known is not None:
                    return False
                now = utc_now()
                connection.execute(
                    "INSERT INTO event_consumptions VALUES(?,?,?)", (consumer_name, event_id, now)
                )
                connection.execute(
                    """INSERT INTO event_consumer_cursors(consumer_name,last_sequence_no,updated_at)
                    VALUES(?,?,?) ON CONFLICT(consumer_name) DO UPDATE SET
                    last_sequence_no=MAX(last_sequence_no,excluded.last_sequence_no),updated_at=excluded.updated_at""",
                    (consumer_name, sequence_no, now),
                )
                return True
        finally:
            connection.close()

    @staticmethod
    def _load(connection: sqlite3.Connection, job_id: str) -> StoredJob:
        row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return StoredJob(
            id=str(row["id"]),
            tool_call_id=str(row["tool_call_id"]),
            job_type=str(row["job_type"]),
            status=JobStatus(row["status"]),
            stage=JobStage(row["stage"]),
            resume_class=ResumeClass(row["resume_class"]),
            provider=row["provider"],
            external_task_id=row["external_task_id"],
            resume=json.loads(str(row["resume_json"])),
            progress=row["progress"],
            result_asset_ids=json.loads(str(row["result_asset_ids_json"])),
            error=json.loads(str(row["error_json"])) if row["error_json"] else None,
            lease_owner=row["lease_owner"],
            lease_until=row["lease_until"],
            heartbeat_at=row["heartbeat_at"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _payload(
        status: JobStatus, stage: JobStage, record: StoredJob | None = None
    ) -> dict[str, Any]:
        return {
            "status": status.value,
            "stage": stage.value,
            "progress": record.progress if record else None,
            "provider": record.provider if record else None,
            "can_cancel": status in {JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.WAITING},
            "can_stop_waiting": status is JobStatus.WAITING,
            "output_asset_ids": record.result_asset_ids if record else [],
            "focus_policy": "respect_dirty_canvas",
        }

    @staticmethod
    def _emit(
        connection: sqlite3.Connection, aggregate_id: str, event_type: str, payload: dict[str, Any]
    ) -> None:
        connection.execute(
            "INSERT INTO outbox_events(id,aggregate_id,event_type,payload_json,created_at) VALUES(?,?,?,?,?)",
            (new_id(), aggregate_id, event_type, canonical_json(payload), utc_now()),
        )
