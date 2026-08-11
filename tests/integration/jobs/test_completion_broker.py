from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aipic_to_model.application.jobs.completion_broker import JobCompletionBroker
from aipic_to_model.domain.job_models import JobStage, JobStatus, ResumeClass
from aipic_to_model.infrastructure.sqlite.connection import connect, migrate
from aipic_to_model.infrastructure.sqlite.job_repository import SqliteJobRepository


def _database(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    database = root / "project.sqlite3"
    migrate(database, root / "recovery")
    connection = connect(database)
    try:
        connection.execute(
            "INSERT INTO projects(id,name,created_at,updated_at) VALUES('project-1','test','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')"
        )
        connection.execute("INSERT INTO event_counters VALUES('project-1',1)")
        connection.execute(
            """INSERT INTO tool_calls(id,round_index,tool_name,tool_version,arguments_json,
            arguments_hash,idempotency_key,risk_level,status) VALUES(
            'call-1',0,'image.generate','1.0.0','{}','hash','idem','external','queued')"""
        )
    finally:
        connection.close()
    return database


def _queued_job(database: Path) -> SqliteJobRepository:
    jobs = SqliteJobRepository()
    jobs.create(
        database, job_id="job-1", tool_call_id="call-1", job_type="image.generate", provider="fake"
    )
    return jobs


@pytest.mark.agent
@pytest.mark.asyncio
async def test_completion_broker_returns_a_committed_terminal_job_after_notification(tmp_path: Path) -> None:
    database = _database(tmp_path)
    jobs = _queued_job(database)
    broker = JobCompletionBroker(jobs, wait_slice_seconds=0.001)

    waiter = asyncio.create_task(broker.wait_for_terminal(database, "job-1", timeout_seconds=1))
    await asyncio.sleep(0)
    assert jobs.claim(database, owner="worker", lease_until="2099-01-01T00:00:00Z") is not None
    jobs.update(
        database,
        job_id="job-1",
        target=JobStatus.SUCCEEDED,
        stage=JobStage.POSTPROCESSING,
        result_asset_ids=["asset-output"],
    )
    broker.notify_terminal("job-1")

    result = await waiter

    assert result is not None
    assert result.status is JobStatus.SUCCEEDED
    assert result.result_asset_ids == ["asset-output"]


@pytest.mark.agent
@pytest.mark.asyncio
async def test_completion_broker_timeout_leaves_the_job_queued_and_does_not_cancel_it(tmp_path: Path) -> None:
    database = _database(tmp_path)
    jobs = _queued_job(database)
    broker = JobCompletionBroker(jobs, wait_slice_seconds=0.001)

    result = await broker.wait_for_terminal(database, "job-1", timeout_seconds=0.01)

    assert result is None
    assert jobs.get(database, job_id="job-1").status is JobStatus.QUEUED


@pytest.mark.agent
@pytest.mark.asyncio
async def test_completion_broker_returns_interrupted_job_with_durable_error(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    jobs = _queued_job(database)
    jobs.update(
        database,
        job_id="job-1",
        target=JobStatus.INTERRUPTED,
        stage=JobStage.UNKNOWN_SUBMISSION,
        resume_class=ResumeClass.UNKNOWN_SUBMISSION,
        error={"code": "JOB_UNKNOWN_SUBMISSION", "safe_to_retry": False},
    )
    broker = JobCompletionBroker(jobs, wait_slice_seconds=0.001)

    result = await broker.wait_for_terminal(database, "job-1", timeout_seconds=0.01)

    assert result is not None
    assert result.status is JobStatus.INTERRUPTED
    assert result.error == {"code": "JOB_UNKNOWN_SUBMISSION", "safe_to_retry": False}


@pytest.mark.agent
@pytest.mark.asyncio
async def test_completion_broker_keeps_resumable_interrupted_job_open(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    jobs = _queued_job(database)
    jobs.update(
        database,
        job_id="job-1",
        target=JobStatus.INTERRUPTED,
        stage=JobStage.DOWNLOADING,
        resume_class=ResumeClass.DOWNLOAD_RETRY,
    )
    broker = JobCompletionBroker(jobs, wait_slice_seconds=0.001)

    result = await broker.wait_for_terminal(database, "job-1", timeout_seconds=0.01)

    assert result is None
