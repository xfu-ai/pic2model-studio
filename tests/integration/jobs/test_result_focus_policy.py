from __future__ import annotations

from pathlib import Path

from aipic_to_model.application.projects import ProjectService
from aipic_to_model.domain.job_models import JobStage, JobStatus
from aipic_to_model.infrastructure.sqlite.connection import connect
from aipic_to_model.infrastructure.sqlite.job_repository import SqliteJobRepository


def _complete(tmp_path: Path, *, dirty: bool) -> dict:
    root = tmp_path / ("dirty" if dirty else "clean")
    ProjectService().create(root, "Focus")
    database = root / "project.sqlite3"
    connection = connect(database)
    try:
        connection.execute(
            """INSERT INTO tool_calls(id,round_index,tool_name,tool_version,arguments_json,
            arguments_hash,idempotency_key,risk_level,status) VALUES(
            'call',0,'fixture','1.0.0','{}','h','h','local_reversible','queued')"""
        )
        if dirty:
            connection.execute(
                "UPDATE projects SET workspace_state_json=?",
                ('{"dirty_selection_draft":true,"current_asset_id":"unchanged"}',),
            )
    finally:
        connection.close()
    jobs = SqliteJobRepository()
    jobs.create(
        database,
        job_id="job",
        tool_call_id="call",
        job_type="fixture",
        provider=None,
    )
    assert jobs.claim(database, owner="worker", lease_until="2099-01-01T00:00:00Z")
    jobs.update(
        database,
        job_id="job",
        target=JobStatus.SUCCEEDED,
        stage=JobStage.VERIFYING,
        result_asset_ids=["result-asset"],
    )
    return jobs.replay_outbox(database, after=0)[-1]


def test_result_ready_never_changes_focus_with_or_without_dirty_canvas(tmp_path: Path) -> None:
    for dirty in (False, True):
        event = _complete(tmp_path, dirty=dirty)
        assert event["event_type"] == "job.result_ready"
        assert event["payload"]["focus_policy"] == "respect_dirty_canvas"
        assert "current_asset_id" not in event["payload"]
