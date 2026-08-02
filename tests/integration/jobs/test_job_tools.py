from __future__ import annotations

from pathlib import Path

from aipic_to_model.application.b02_runtime import PersistentB02ToolRuntime
from aipic_to_model.application.projects import ProjectService
from aipic_to_model.domain.common import RiskLevel
from aipic_to_model.domain.job_models import JobStage, JobStatus, ResumeClass
from aipic_to_model.infrastructure.sqlite.approval_repository import SqliteApprovalRepository
from aipic_to_model.infrastructure.sqlite.connection import connect
from aipic_to_model.infrastructure.sqlite.job_repository import SqliteJobRepository


def _tool_call(
    database: Path,
    call_id: str,
    *,
    name: str,
    arguments: str,
    risk: str,
    provider: str | None = None,
) -> None:
    connection = connect(database)
    try:
        connection.execute(
            """INSERT INTO tool_calls(id,round_index,tool_name,tool_version,arguments_json,
            arguments_hash,idempotency_key,provider_profile,risk_level,status)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                call_id,
                0,
                name,
                "1.0.0",
                arguments,
                call_id,
                call_id,
                provider,
                risk,
                "queued",
            ),
        )
    finally:
        connection.close()


def _runtime(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Job tools")
    jobs, approvals = SqliteJobRepository(), SqliteApprovalRepository()
    return root, project.id, jobs, approvals, PersistentB02ToolRuntime(jobs, approvals)


def test_approved_action_is_parameter_bound_and_consumed_once(tmp_path: Path) -> None:
    root, project_id, jobs, approvals, runtime = _runtime(tmp_path)
    database = root / "project.sqlite3"
    _tool_call(
        database,
        "paid-call",
        name="model3d.generate",
        arguments="{}",
        risk="external_paid",
        provider="fake",
    )
    requested = runtime.invoke(
        "model3d.generate",
        RiskLevel.EXTERNAL_PAID,
        "job",
        root,
        project_id,
        {
            "mode": "image",
            "image_asset_id": "asset-1",
            "provider_profile": "fake",
            "model": "m",
            "parameters": {},
        },
        "paid-call",
    )
    assert requested.ui_action is not None
    approval_id = requested.ui_action["action_id"]
    approved = runtime.decide_approval(root, project_id, approval_id, approved=True)
    replayed = runtime.decide_approval(root, project_id, approval_id, approved=True)
    assert approved.status == "queued" and replayed.reused
    assert approved.job is not None and replayed.job is not None
    assert approved.job["job_id"] == replayed.job["job_id"]
    assert len(jobs.list_nonterminal(database)) == 1
    assert approvals.get(database, approval_id=approval_id).decision == "consumed"


def test_job_retry_uses_frozen_arguments_and_paid_retry_requires_new_approval(
    tmp_path: Path,
) -> None:
    root, project_id, jobs, _approvals, runtime = _runtime(tmp_path)
    database = root / "project.sqlite3"
    _tool_call(
        database,
        "original",
        name="model3d.generate",
        arguments='{"provider_profile":"fake","mode":"image"}',
        risk="external_paid",
        provider="fake",
    )
    _tool_call(
        database,
        "retry-call",
        name="job.retry",
        arguments='{"job_id":"failed-job"}',
        risk="local_reversible",
    )
    jobs.create(
        database,
        job_id="failed-job",
        tool_call_id="original",
        job_type="model3d.generate",
        provider="fake",
    )
    jobs.update(
        database,
        job_id="failed-job",
        target=JobStatus.FAILED,
        stage=JobStage.CREATING,
        resume_class=ResumeClass.MANUAL_REVIEW,
        error={"code": "PROVIDER_UNAVAILABLE", "safe_to_retry": True},
    )
    result = runtime.invoke(
        "job.retry",
        RiskLevel.LOCAL_REVERSIBLE,
        "sync",
        root,
        project_id,
        {"job_id": "failed-job"},
        "retry-call",
    )
    assert result.status == "awaiting_ui_action"
    assert jobs.list_nonterminal(database) == []
    assert result.ui_action is not None
    approved = runtime.decide_approval(
        root,
        project_id,
        result.ui_action["action_id"],
        approved=True,
    )
    assert approved.job is not None
    retry_context = jobs.retry_context(database, job_id=approved.job["job_id"])
    assert retry_context["source_tool_call_id"] == "original"
    assert retry_context["tool_name"] == "model3d.generate"
    assert retry_context["arguments"] == {
        "provider_profile": "fake",
        "mode": "image",
    }


def test_nonpaid_job_retry_preserves_original_worker_arguments(tmp_path: Path) -> None:
    root, project_id, jobs, _approvals, runtime = _runtime(tmp_path)
    database = root / "project.sqlite3"
    _tool_call(
        database,
        "original-detect",
        name="multiview.detect_regions",
        arguments=(
            '{"multiview_set_id":"set-1","provider_profile":"gemini/google/default",'
            '"model":"gemini-flash-lite-latest"}'
        ),
        risk="external",
        provider="gemini/google/default",
    )
    _tool_call(
        database,
        "retry-detect",
        name="job.retry",
        arguments='{"job_id":"failed-detect"}',
        risk="local_reversible",
    )
    jobs.create(
        database,
        job_id="failed-detect",
        tool_call_id="original-detect",
        job_type="multiview.detect_regions",
        provider="gemini/google/default",
    )
    jobs.update(
        database,
        job_id="failed-detect",
        target=JobStatus.INTERRUPTED,
        stage=JobStage.POSTPROCESSING,
        resume_class=ResumeClass.LOCAL_RESTARTABLE,
        error={"code": "PROVIDER_RATE_LIMITED", "safe_to_retry": True},
    )

    result = runtime.invoke(
        "job.retry",
        RiskLevel.LOCAL_REVERSIBLE,
        "sync",
        root,
        project_id,
        {"job_id": "failed-detect"},
        "retry-detect",
    )

    assert result.job is not None
    retried = jobs.get(database, job_id=result.job["job_id"])
    assert retried.tool_call_id == "retry-detect"
    retry_context = jobs.retry_context(database, job_id=retried.id)
    assert retry_context["source_tool_call_id"] == "original-detect"
    assert retry_context["tool_name"] == "multiview.detect_regions"
    assert retry_context["arguments"]["multiview_set_id"] == "set-1"


def test_unknown_submission_cannot_be_retried(tmp_path: Path) -> None:
    root, project_id, jobs, _approvals, runtime = _runtime(tmp_path)
    database = root / "project.sqlite3"
    _tool_call(
        database,
        "original",
        name="model3d.generate",
        arguments='{"provider_profile":"fake"}',
        risk="external_paid",
        provider="fake",
    )
    _tool_call(
        database,
        "retry-call",
        name="job.retry",
        arguments='{"job_id":"unknown-job"}',
        risk="local_reversible",
    )
    jobs.create(
        database,
        job_id="unknown-job",
        tool_call_id="original",
        job_type="model3d.generate",
        provider="fake",
    )
    jobs.update(
        database,
        job_id="unknown-job",
        target=JobStatus.INTERRUPTED,
        stage=JobStage.UNKNOWN_SUBMISSION,
        resume_class=ResumeClass.UNKNOWN_SUBMISSION,
        error={"code": "JOB_UNKNOWN_SUBMISSION", "safe_to_retry": False},
    )
    result = runtime.invoke(
        "job.retry",
        RiskLevel.LOCAL_REVERSIBLE,
        "sync",
        root,
        project_id,
        {"job_id": "unknown-job"},
        "retry-call",
    )
    assert result.status == "failed"
    assert result.error is not None and result.error["code"] == "JOB_NOT_RETRYABLE"


def test_running_paid_submission_checkpoint_is_not_user_confirmation_state(
    tmp_path: Path,
) -> None:
    root, project_id, jobs, _approvals, runtime = _runtime(tmp_path)
    database = root / "project.sqlite3"
    _tool_call(
        database,
        "original",
        name="image.generate",
        arguments=(
            '{"prompt_asset_id":"prompt-1","provider_profile":"meshy/default",'
            '"channel":"meshy","model":"nano-banana","candidate_count":2}'
        ),
        risk="external_paid",
        provider="meshy/default",
    )
    _tool_call(
        database,
        "confirm-call",
        name="job.confirm_new_submission",
        arguments='{"job_id":"retrying-job"}',
        risk="local_reversible",
    )
    jobs.create(
        database,
        job_id="retrying-job",
        tool_call_id="original",
        job_type="image.generate",
        provider="meshy/default",
    )
    jobs.update(
        database,
        job_id="retrying-job",
        target=JobStatus.INTERRUPTED,
        stage=JobStage.POSTPROCESSING,
        resume_class=ResumeClass.LOCAL_RESTARTABLE,
        error={
            "code": "PROVIDER_UNAVAILABLE",
            "safe_to_retry": True,
            "recommended_action": "retry",
        },
    )

    claimed = jobs.claim(
        database,
        owner="worker",
        lease_until="2099-01-01T00:00:00Z",
    )
    assert claimed is not None
    assert claimed.error is None
    active = jobs.mark_submission_started(
        database,
        job_id="retrying-job",
        owner="worker",
    )
    assert active.status is JobStatus.RUNNING
    assert active.stage is JobStage.CREATING
    assert active.resume_class is ResumeClass.UNKNOWN_SUBMISSION
    assert active.error is None

    view = runtime.job_view(root, "retrying-job")
    assert view["recovery_actions"] == []
    result = runtime.invoke(
        "job.confirm_new_submission",
        RiskLevel.LOCAL_REVERSIBLE,
        "sync",
        root,
        project_id,
        {"job_id": "retrying-job"},
        "confirm-call",
    )
    assert result.status == "failed"
    assert result.error is not None
    assert result.error["code"] == "JOB_CONFIRMATION_NOT_REQUIRED"


def test_unknown_submission_requires_dedicated_confirmation_and_creates_linked_job(
    tmp_path: Path,
) -> None:
    root, project_id, jobs, _approvals, runtime = _runtime(tmp_path)
    database = root / "project.sqlite3"
    _tool_call(
        database,
        "original",
        name="image.generate",
        arguments=(
            '{"prompt_asset_id":"prompt-1","provider_profile":"meshy/default",'
            '"channel":"meshy","model":"nano-banana","candidate_count":2}'
        ),
        risk="external_paid",
        provider="meshy/default",
    )
    _tool_call(
        database,
        "confirm-call",
        name="job.confirm_new_submission",
        arguments='{"job_id":"unknown-job"}',
        risk="local_reversible",
    )
    jobs.create(
        database,
        job_id="unknown-job",
        tool_call_id="original",
        job_type="image.generate",
        provider="meshy/default",
    )
    jobs.update(
        database,
        job_id="unknown-job",
        target=JobStatus.INTERRUPTED,
        stage=JobStage.UNKNOWN_SUBMISSION,
        resume_class=ResumeClass.UNKNOWN_SUBMISSION,
        error={"code": "JOB_UNKNOWN_SUBMISSION", "safe_to_retry": False},
    )

    view = runtime.job_view(root, "unknown-job")
    assert view["recovery_actions"] == ["confirm_new_submission"]
    result = runtime.invoke(
        "job.confirm_new_submission",
        RiskLevel.LOCAL_REVERSIBLE,
        "sync",
        root,
        project_id,
        {"job_id": "unknown-job"},
        "confirm-call",
    )

    assert result.status == "awaiting_ui_action"
    assert result.ui_action is not None
    assert jobs.get(database, job_id="unknown-job").resume_class is ResumeClass.UNKNOWN_SUBMISSION
    approved = runtime.decide_approval(
        root,
        project_id,
        result.ui_action["action_id"],
        approved=True,
    )
    assert approved.job is not None
    assert approved.job["job_id"] != "unknown-job"
    replacement = jobs.retry_context(database, job_id=approved.job["job_id"])
    assert replacement["source_tool_call_id"] == "original"


def test_status_tells_agent_to_wait_for_the_desktop_completion_event(tmp_path: Path) -> None:
    root, project_id, jobs, _approvals, runtime = _runtime(tmp_path)
    database = root / "project.sqlite3"
    _tool_call(
        database,
        "status-call",
        name="job.get_status",
        arguments='{"job_id":"running-job"}',
        risk="read_only",
    )
    jobs.create(
        database,
        job_id="running-job",
        tool_call_id="status-call",
        job_type="image.generate",
        provider="fake",
    )
    jobs.update(
        database,
        job_id="running-job",
        target=JobStatus.RUNNING,
        stage=JobStage.REMOTE_RUNNING,
        progress=35,
    )

    result = runtime.invoke(
        "job.get_status",
        RiskLevel.READ_ONLY,
        "sync",
        root,
        project_id,
        {"job_id": "running-job"},
        "status-call",
    )

    assert result.status == "succeeded"
    assert result.output_asset_ids == []
    assert "running" in result.summary
    assert "remote_running" in result.summary
    assert "Do not poll this job again" in result.summary
    assert "desktop will send a completion event" in result.summary

    jobs.update(
        database,
        job_id="running-job",
        target=JobStatus.INTERRUPTED,
        stage=JobStage.POSTPROCESSING,
        error={"code": "PROVIDER_RATE_LIMITED", "safe_to_retry": True},
    )
    interrupted = runtime.invoke(
        "job.get_status",
        RiskLevel.READ_ONLY,
        "sync",
        root,
        project_id,
        {"job_id": "running-job"},
        "interrupted-status-call",
    )

    assert "interrupted" in interrupted.summary
    assert "postprocessing" in interrupted.summary
    assert "Error code: PROVIDER_RATE_LIMITED." in interrupted.summary
