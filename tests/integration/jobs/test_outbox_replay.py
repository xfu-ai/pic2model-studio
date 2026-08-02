from __future__ import annotations

from pathlib import Path

from aipic_to_model.application.projects import ProjectService
from aipic_to_model.domain.job_models import JobStage, JobStatus
from aipic_to_model.infrastructure.sqlite.connection import connect
from aipic_to_model.infrastructure.sqlite.job_repository import SqliteJobRepository


def test_job_outbox_replay_is_ordered_and_consumer_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "project"
    ProjectService().create(root, "Outbox replay")
    jobs = SqliteJobRepository()
    database = root / "project.sqlite3"
    connection = connect(database)
    try:
        connection.execute(
            """INSERT INTO tool_calls(
                id,round_index,tool_name,tool_version,arguments_json,arguments_hash,
                idempotency_key,risk_level,status
            ) VALUES('call-1',0,'model3d.inspect','1.0.0','{}','hash','key',
                     'local_reversible','queued')"""
        )
    finally:
        connection.close()
    created = jobs.create(
        database,
        job_id="job-1",
        tool_call_id="call-1",
        job_type="model3d.inspect",
        provider=None,
    )
    jobs.update(
        database,
        job_id=created.id,
        target=JobStatus.FAILED,
        stage=JobStage.POSTPROCESSING,
        error={"code": "TEST_FAILURE", "safe_to_retry": False},
    )

    first = jobs.replay_outbox(database, after=0, limit=1)
    second = jobs.replay_outbox(database, after=int(first[-1]["sequence_no"]), limit=100)
    events = first + second
    assert [int(item["sequence_no"]) for item in events] == sorted(
        int(item["sequence_no"]) for item in events
    )
    assert {item["event_type"] for item in events} >= {"job.created", "job.failed"}

    event = events[-1]
    assert jobs.consume(
        database,
        consumer_name="b03",
        event_id=str(event["id"]),
        sequence_no=int(event["sequence_no"]),
    )
    assert not jobs.consume(
        database,
        consumer_name="b03",
        event_id=str(event["id"]),
        sequence_no=int(event["sequence_no"]),
    )
