from __future__ import annotations

from pathlib import Path

from aipic_to_model.application.jobs.recovery import classify_recovery
from aipic_to_model.application.jobs.tripo_handler import (
    apply_remote_cancel_result,
    apply_remote_state,
    persist_submission_result,
)
from aipic_to_model.domain.job_models import JobStage, JobStatus, ResumeClass
from aipic_to_model.domain.provider_models import (
    ErrorCategory,
    ErrorDetail,
    ProviderResult,
    RecommendedAction,
    RemoteTaskState,
)
from aipic_to_model.infrastructure.providers.fake import FakeScenario, FakeTripo3DProvider
from aipic_to_model.infrastructure.sqlite.connection import connect, migrate
from aipic_to_model.infrastructure.sqlite.job_repository import SqliteJobRepository


def _database(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir(parents=True)
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
            'call-1',0,'model3d.generate','1.0.0','{}','input-hash','idem','external_paid','queued')"""
        )
    finally:
        connection.close()
    return database


def _claimed_job(database: Path) -> SqliteJobRepository:
    jobs = SqliteJobRepository()
    jobs.create(
        database,
        job_id="job-1",
        tool_call_id="call-1",
        job_type="model3d.generate",
        provider="fake-tripo",
        stage=JobStage.CREATING,
    )
    assert jobs.claim(database, owner="worker", lease_until="2099-01-01T00:00:00Z") is not None
    return jobs


def test_submission_persists_external_id_then_restart_only_queries_existing_task(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    jobs = _claimed_job(database)
    provider = FakeTripo3DProvider(
        [FakeScenario("tripo.create", "success", {"external_task_id": "remote-1"})]
    )
    decision = persist_submission_result(
        jobs,
        database,
        job_id="job-1",
        provider="fake-tripo",
        result=provider.create({"input": "opaque-upload"}, idempotency_key="idem"),
        submission_summary={"idempotency_hash": "input-hash", "input_ids": ["asset-1"]},
    )
    assert decision.external_task_id == "remote-1"
    saved = jobs.get(database, job_id="job-1")
    assert saved.status is JobStatus.WAITING
    assert saved.stage is JobStage.REMOTE_QUEUED
    assert saved.external_task_id == "remote-1"
    assert (
        classify_recovery(saved.id, saved.resume_class, saved.external_task_id).action
        == "query_remote"
    )

    # A fresh provider proves restart recovery performs GET only, never a second POST.
    restarted_provider = FakeTripo3DProvider(
        [FakeScenario("tripo.get", "success", {"status": "succeeded"})]
    )
    remote = restarted_provider.get(saved.external_task_id)
    assert isinstance(remote, RemoteTaskState)
    assert (
        apply_remote_state(jobs, database, job_id="job-1", state=remote)
        is ResumeClass.DOWNLOAD_RETRY
    )
    assert [call[0] for call in restarted_provider.calls] == ["tripo.get"]
    assert jobs.get(database, job_id="job-1").resume_class is ResumeClass.DOWNLOAD_RETRY
    assert (
        jobs.claim(database, owner="download-worker", lease_until="2099-01-01T00:00:00Z")
        is not None
    )


def test_lost_submission_response_becomes_manual_review_and_is_never_claimed(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    jobs = _claimed_job(database)
    decision = persist_submission_result(
        jobs,
        database,
        job_id="job-1",
        provider="fake-tripo",
        result=ProviderResult(ok=False, stage="creating", retryable=True),
        submission_summary={"idempotency_hash": "input-hash"},
    )
    assert decision.requires_manual_review
    saved = jobs.get(database, job_id="job-1")
    assert saved.status is JobStatus.INTERRUPTED
    assert saved.stage is JobStage.UNKNOWN_SUBMISSION
    assert saved.resume_class is ResumeClass.UNKNOWN_SUBMISSION
    assert jobs.claim(database, owner="unsafe-worker", lease_until="2099-01-01T00:00:00Z") is None


def test_deterministic_create_rejection_preserves_provider_error(tmp_path: Path) -> None:
    database = _database(tmp_path)
    jobs = _claimed_job(database)
    result = ProviderResult(
        ok=False,
        stage="creating",
        retryable=False,
        error=ErrorDetail(
            code="PROVIDER_REQUEST_INVALID",
            category=ErrorCategory.INPUT_INVALID,
            user_message="The Provider rejected an unsupported parameter combination.",
            recoverable=False,
            failed_object="provider",
            failed_step="creating",
            fee_incurred=False,
            safe_to_retry=False,
            recommended_action=RecommendedAction.FIX_INPUT,
        ),
    )

    decision = persist_submission_result(
        jobs,
        database,
        job_id="job-1",
        provider="fake-tripo",
        result=result,
        submission_summary={"idempotency_hash": "input-hash"},
    )

    assert decision.external_task_id is None
    assert not decision.requires_manual_review
    saved = jobs.get(database, job_id="job-1")
    assert saved.status is JobStatus.FAILED
    assert saved.stage is JobStage.CREATING
    assert saved.resume_class is ResumeClass.MANUAL_REVIEW
    assert saved.error["code"] == "PROVIDER_REQUEST_INVALID"
    assert saved.error["recommended_action"] == "fix_input"


def test_external_task_and_resume_summary_do_not_persist_signed_url(tmp_path: Path) -> None:
    database = _database(tmp_path)
    jobs = _claimed_job(database)
    persist_submission_result(
        jobs,
        database,
        job_id="job-1",
        provider="fake-tripo",
        result=ProviderResult(
            ok=True,
            stage="creating",
            retryable=False,
            payload={"external_task_id": "remote-1"},
        ),
        submission_summary={"artifact_ids": ["opaque-model"], "host_fingerprint": "sha256:host"},
    )
    connection = connect(database, read_only=True)
    try:
        row = connection.execute(
            "SELECT external_task_id,resume_json FROM jobs WHERE id='job-1'"
        ).fetchone()
        assert row["external_task_id"] == "remote-1"
        assert "?" not in row["resume_json"] and "http" not in row["resume_json"]
    finally:
        connection.close()


def test_remote_completion_can_win_cancel_race_but_unsupported_cancel_stops_waiting(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    jobs = _claimed_job(database)
    persist_submission_result(
        jobs,
        database,
        job_id="job-1",
        provider="fake-tripo",
        result=ProviderResult(
            ok=True,
            stage="creating",
            retryable=False,
            payload={"external_task_id": "remote-1"},
        ),
        submission_summary={"idempotency_hash": "input-hash"},
    )
    assert (
        apply_remote_cancel_result(
            jobs,
            database,
            job_id="job-1",
            result=ProviderResult(ok=True, stage="cancel", retryable=False),
        )
        is ResumeClass.REMOTE_POLL
    )
    assert jobs.get(database, job_id="job-1").stage is JobStage.CANCEL_REQUESTED
    assert (
        apply_remote_state(
            jobs,
            database,
            job_id="job-1",
            state=RemoteTaskState(external_task_id="remote-1", status="succeeded"),
        )
        is ResumeClass.DOWNLOAD_RETRY
    )
    assert jobs.get(database, job_id="job-1").status is JobStatus.INTERRUPTED

    second = _database(tmp_path / "second")
    unsupported_jobs = _claimed_job(second)
    unsupported = FakeTripo3DProvider([FakeScenario("tripo.cancel", "cancel_unsupported")]).cancel(
        "remote-2"
    )
    assert (
        apply_remote_cancel_result(unsupported_jobs, second, job_id="job-1", result=unsupported)
        is ResumeClass.STOP_WAITING
    )
    stopped = unsupported_jobs.get(second, job_id="job-1")
    assert stopped.status is JobStatus.WAITING
    assert stopped.resume_class is ResumeClass.STOP_WAITING
