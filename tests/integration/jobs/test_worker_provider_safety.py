from __future__ import annotations

from pathlib import Path

from aipic_to_model.application.jobs.worker import ProductionJobWorker
from aipic_to_model.domain.job_models import JobStage, JobStatus, ResumeClass
from aipic_to_model.infrastructure.sqlite.connection import connect, migrate
from aipic_to_model.infrastructure.sqlite.job_repository import SqliteJobRepository


def test_interrupted_paid_submission_is_not_marked_safe_to_retry(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    database = root / "project.sqlite3"
    migrate(database, root / "recovery")
    connection = connect(database)
    try:
        connection.execute(
            """INSERT INTO projects(id,name,created_at,updated_at)
               VALUES('project-1','test','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')"""
        )
        connection.execute("INSERT INTO event_counters VALUES('project-1',1)")
        connection.execute(
            """INSERT INTO tool_calls(
                 id,round_index,tool_name,tool_version,arguments_json,
                 arguments_hash,idempotency_key,risk_level,status
               ) VALUES(
                 'call-1',0,'image.generate','1.0.0','{}',
                 'hash','idem','external_paid','queued'
               )"""
        )
    finally:
        connection.close()

    jobs = SqliteJobRepository()
    jobs.create(
        database,
        job_id="job-1",
        tool_call_id="call-1",
        job_type="image.generate",
        provider="meshy/default",
    )

    def crash(*args, **kwargs):
        jobs.mark_submission_started(
            database,
            job_id=args[2].id,
            owner=kwargs["owner"],
        )
        raise RuntimeError("simulated process boundary")

    worker = ProductionJobWorker(jobs, {"image.generate": crash})
    assert worker.run_once(root, "project-1", owner="worker") == "job-1"

    stored = jobs.get(database, job_id="job-1")
    assert stored.resume_class is ResumeClass.UNKNOWN_SUBMISSION
    assert stored.stage is JobStage.UNKNOWN_SUBMISSION
    assert stored.error is not None
    assert stored.error["code"] == "JOB_UNKNOWN_SUBMISSION"
    assert stored.error["safe_to_retry"] is False
    assert stored.error["recommended_action"] == "confirm_new_submission"


def test_paid_result_local_failure_is_not_misreported_as_unknown_submission(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    database = root / "project.sqlite3"
    migrate(database, root / "recovery")
    connection = connect(database)
    try:
        connection.execute(
            """INSERT INTO projects(id,name,created_at,updated_at)
               VALUES('project-1','test','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')"""
        )
        connection.execute("INSERT INTO event_counters VALUES('project-1',1)")
        connection.execute(
            """INSERT INTO tool_calls(
                 id,round_index,tool_name,tool_version,arguments_json,
                 arguments_hash,idempotency_key,risk_level,status
               ) VALUES(
                 'call-1',0,'image.generate','1.0.0','{}',
                 'hash','idem','external_paid','queued'
               )"""
        )
    finally:
        connection.close()

    jobs = SqliteJobRepository()
    jobs.create(
        database,
        job_id="job-1",
        tool_call_id="call-1",
        job_type="image.generate",
        provider="image-generation/auto",
    )

    def fail_after_provider_result(*args, **kwargs):
        job_id = args[2].id
        jobs.mark_submission_started(database, job_id=job_id, owner=kwargs["owner"])
        jobs.update(
            database,
            job_id=job_id,
            target=JobStatus.RUNNING,
            stage=JobStage.POSTPROCESSING,
            resume_class=ResumeClass.MANUAL_REVIEW,
        )
        raise RuntimeError("simulated local materialization failure")

    worker = ProductionJobWorker(jobs, {"image.generate": fail_after_provider_result})
    assert worker.run_once(root, "project-1", owner="worker") == "job-1"

    stored = jobs.get(database, job_id="job-1")
    assert stored.status is JobStatus.FAILED
    assert stored.resume_class is ResumeClass.MANUAL_REVIEW
    assert stored.stage is JobStage.POSTPROCESSING
    assert stored.error is not None
    assert stored.error["code"] == "JOB_POSTPROCESSING_FAILED"
    assert stored.error["fee_incurred"] is True
    assert stored.error["safe_to_retry"] is False
