from __future__ import annotations

from pathlib import Path

import pytest

from aipic_to_model.application.jobs.manager import validate_resume_payload
from aipic_to_model.application.jobs.recovery import classify_recovery
from aipic_to_model.domain.event_payloads import validate_event_payload
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
            'call-1',0,'image.analyze_content','1.0.0','{}','hash','idem','external','queued')"""
        )
    finally:
        connection.close()
    return database


def test_only_one_worker_can_claim_a_safe_job(tmp_path: Path) -> None:
    database = _database(tmp_path)
    jobs = SqliteJobRepository()
    jobs.create(
        database, job_id="job-1", tool_call_id="call-1", job_type="analysis", provider="fake"
    )
    first = jobs.claim(database, owner="worker-a", lease_until="2099-01-01T00:00:00Z")
    assert first is not None
    assert first.status is JobStatus.RUNNING
    assert jobs.claim(database, owner="worker-b", lease_until="2099-01-01T00:00:00Z") is None
    assert not jobs.heartbeat(
        database, job_id="job-1", owner="worker-b", lease_until="2099-01-01T00:00:00Z"
    )
    assert jobs.heartbeat(
        database, job_id="job-1", owner="worker-a", lease_until="2099-01-01T00:00:00Z"
    )


def test_unknown_submission_is_never_automatically_claimed(tmp_path: Path) -> None:
    database = _database(tmp_path)
    jobs = SqliteJobRepository()
    jobs.create(
        database,
        job_id="job-unknown",
        tool_call_id="call-1",
        job_type="tripo",
        provider="fake",
        resume_class=ResumeClass.UNKNOWN_SUBMISSION,
        stage=JobStage.UNKNOWN_SUBMISSION,
    )
    assert jobs.claim(database, owner="worker", lease_until="2099-01-01T00:00:00Z") is None
    assert (
        classify_recovery("job-unknown", ResumeClass.UNKNOWN_SUBMISSION, None).action
        == "manual_review"
    )


def test_resume_payload_cannot_contain_url_secret_or_path() -> None:
    validate_resume_payload(
        {"artifact_refs": [{"artifact_id": "safe", "host_fingerprint": "hash"}]}
    )
    for forbidden in (
        {"url": "https://bad.invalid"},
        {"Authorization": "secret"},
        {"path": "C:/bad"},
        {"artifact_url": "https://bad.invalid"},
        {"temporary": "C:/bad"},
    ):
        with pytest.raises(ValueError):
            validate_resume_payload(forbidden)


def test_outbox_replay_and_event_consumption_are_idempotent(tmp_path: Path) -> None:
    database = _database(tmp_path)
    jobs = SqliteJobRepository()
    jobs.create(
        database, job_id="job-1", tool_call_id="call-1", job_type="analysis", provider="fake"
    )
    claimed = jobs.claim(database, owner="worker", lease_until="2099-01-01T00:00:00Z")
    assert claimed is not None
    events = jobs.replay_outbox(database, after=0)
    assert [event["event_type"] for event in events] == ["job.created", "job.started"]
    assert jobs.consume(
        database,
        consumer_name="test",
        event_id=events[0]["id"],
        sequence_no=events[0]["sequence_no"],
    )
    assert not jobs.consume(
        database,
        consumer_name="test",
        event_id=events[0]["id"],
        sequence_no=events[0]["sequence_no"],
    )


def test_stale_local_job_is_interrupted_and_remote_poll_is_not(tmp_path: Path) -> None:
    database = _database(tmp_path)
    jobs = SqliteJobRepository()
    jobs.create(
        database, job_id="job-local", tool_call_id="call-1", job_type="analysis", provider="fake"
    )
    assert jobs.claim(database, owner="worker", lease_until="2000-01-01T00:00:00Z") is not None
    assert jobs.interrupt_expired(database, before="2099-01-01T00:00:00Z") == ["job-local"]
    jobs.create(
        database,
        job_id="job-remote",
        tool_call_id="call-1",
        job_type="tripo",
        provider="fake",
        resume_class=ResumeClass.REMOTE_POLL,
        stage=JobStage.REMOTE_RUNNING,
    )
    assert jobs.claim(database, owner="worker", lease_until="2099-01-01T00:00:00Z") is None


def test_local_restartable_job_stops_after_three_automatic_attempts(tmp_path: Path) -> None:
    database = _database(tmp_path)
    jobs = SqliteJobRepository()
    jobs.create(
        database,
        job_id="job-retry-limit",
        tool_call_id="call-1",
        job_type="analysis",
        provider="fake",
        resume_class=ResumeClass.LOCAL_RESTARTABLE,
    )

    for attempt in range(3):
        claimed = jobs.claim(
            database,
            owner=f"worker-{attempt}",
            lease_until="2000-01-01T00:00:00Z",
        )
        assert claimed is not None
        assert jobs.interrupt_expired(
            database,
            before="2099-01-01T00:00:00Z",
        ) == ["job-retry-limit"]

    assert (
        jobs.claim(
            database,
            owner="worker-over-limit",
            lease_until="2099-01-01T00:00:00Z",
        )
        is None
    )
    starts = [
        event
        for event in jobs.replay_outbox(database, after=0)
        if event["event_type"] == "job.started"
    ]
    assert len(starts) == 3


def test_rate_limited_job_is_not_reclaimed_before_retry_delay(tmp_path: Path) -> None:
    database = _database(tmp_path)
    jobs = SqliteJobRepository()
    jobs.create(
        database,
        job_id="job-rate-limited",
        tool_call_id="call-1",
        job_type="analysis",
        provider="gemini/google/default",
    )
    assert (
        jobs.claim(
            database,
            owner="worker-first",
            lease_until="2099-01-01T00:00:00Z",
        )
        is not None
    )
    jobs.update(
        database,
        job_id="job-rate-limited",
        target=JobStatus.INTERRUPTED,
        stage=JobStage.POSTPROCESSING,
        resume_class=ResumeClass.LOCAL_RESTARTABLE,
        error={
            "code": "PROVIDER_RATE_LIMITED",
            "safe_to_retry": True,
            "retry_after_seconds": 60,
        },
    )

    assert (
        jobs.claim(
            database,
            owner="worker-too-early",
            lease_until="2099-01-01T00:00:00Z",
        )
        is None
    )

    connection = connect(database)
    try:
        connection.execute(
            "UPDATE jobs SET updated_at='2020-01-01T00:00:00Z' WHERE id='job-rate-limited'"
        )
    finally:
        connection.close()
    assert (
        jobs.claim(
            database,
            owner="worker-after-delay",
            lease_until="2099-01-01T00:00:00Z",
        )
        is not None
    )


def test_completed_result_emits_respect_dirty_canvas_focus_policy(tmp_path: Path) -> None:
    database = _database(tmp_path)
    jobs = SqliteJobRepository()
    jobs.create(
        database, job_id="job-result", tool_call_id="call-1", job_type="analysis", provider="fake"
    )
    assert jobs.claim(database, owner="worker", lease_until="2099-01-01T00:00:00Z") is not None
    jobs.update(
        database,
        job_id="job-result",
        target=JobStatus.SUCCEEDED,
        stage=JobStage.VERIFYING,
        result_asset_ids=["analysis-asset"],
    )
    result_ready = jobs.replay_outbox(database, after=0)[-1]
    assert result_ready["event_type"] == "job.result_ready"
    assert result_ready["payload"]["focus_policy"] == "respect_dirty_canvas"
    validate_event_payload(result_ready["event_type"], result_ready["payload"])
