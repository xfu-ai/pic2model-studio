from __future__ import annotations

from pathlib import Path

from aipic_to_model.domain.job_models import JobStage, JobStatus
from aipic_to_model.infrastructure.sqlite.connection import connect, migrate
from aipic_to_model.infrastructure.sqlite.job_repository import SqliteJobRepository


def _database(tmp_path):
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
            'call-1',0,'image.analyze_content','1.0.0','{}','hash','idem','external','queued')"""
        )
    finally:
        connection.close()
    return database


def test_local_cancel_wins_before_result_commit(tmp_path: Path) -> None:
    database = _database(tmp_path)
    jobs = SqliteJobRepository()
    jobs.create(database, job_id="job-1", tool_call_id="call-1", job_type="local", provider=None)
    assert jobs.claim(database, owner="worker", lease_until="2099-01-01T00:00:00Z") is not None
    assert jobs.request_cancel(database, job_id="job-1", mode="local").status is JobStatus.CANCELLED


def test_stop_waiting_is_not_reported_as_remote_cancel(tmp_path: Path) -> None:
    database = _database(tmp_path)
    jobs = SqliteJobRepository()
    jobs.create(database, job_id="job-1", tool_call_id="call-1", job_type="remote", provider="fake")
    assert jobs.claim(database, owner="worker", lease_until="2099-01-01T00:00:00Z") is not None
    record = jobs.request_cancel(database, job_id="job-1", mode="stop_waiting")
    assert record.status is JobStatus.WAITING
    assert record.resume_class.value == "stop_waiting"


def test_late_cancel_cannot_overwrite_a_committed_result(tmp_path: Path) -> None:
    database = _database(tmp_path)
    jobs = SqliteJobRepository()
    jobs.create(database, job_id="job-1", tool_call_id="call-1", job_type="remote", provider="fake")
    assert jobs.claim(database, owner="worker", lease_until="2099-01-01T00:00:00Z") is not None
    completed = jobs.update(
        database,
        job_id="job-1",
        target=JobStatus.SUCCEEDED,
        stage=JobStage.VERIFYING,
        result_asset_ids=["asset-result"],
    )
    assert completed.result_asset_ids == ["asset-result"]
    late_cancel = jobs.request_cancel(database, job_id="job-1", mode="remote")
    assert late_cancel.status is JobStatus.SUCCEEDED
    assert late_cancel.result_asset_ids == ["asset-result"]
