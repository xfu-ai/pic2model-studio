from __future__ import annotations

from pathlib import Path

from aipic_to_model.application.b02_runtime import PersistentB02ToolRuntime
from aipic_to_model.application.jobs.recovery_service import JobRecoveryService
from aipic_to_model.application.projects import ProjectService
from aipic_to_model.domain.common import RiskLevel
from aipic_to_model.domain.job_models import JobStage, JobStatus, ResumeClass
from aipic_to_model.infrastructure.sqlite.approval_repository import SqliteApprovalRepository
from aipic_to_model.infrastructure.sqlite.connection import connect
from aipic_to_model.infrastructure.sqlite.job_repository import SqliteJobRepository


def _project(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "project"
    project = ProjectService().create(root, "Jobs")
    database = root / "project.sqlite3"
    connection = connect(database)
    try:
        for call_id in ("call-local", "call-paid"):
            connection.execute(
                """INSERT INTO tool_calls(id,round_index,tool_name,tool_version,arguments_json,
                arguments_hash,idempotency_key,risk_level,status) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    call_id,
                    0,
                    "fixture",
                    "1.0.0",
                    "{}",
                    call_id,
                    call_id,
                    "local_reversible",
                    "queued",
                ),
            )
    finally:
        connection.close()
    return root, project.id


def test_job_runtime_creates_durable_job_and_outbox(tmp_path: Path) -> None:
    root, project_id = _project(tmp_path)
    jobs = SqliteJobRepository()
    runtime = PersistentB02ToolRuntime(jobs, SqliteApprovalRepository())
    result = runtime.invoke(
        "model3d.import_local",
        RiskLevel.LOCAL_REVERSIBLE,
        "job",
        root,
        project_id,
        {"staged_file_id": "stage-1"},
        "call-local",
    )
    assert result.status == "queued" and result.job is not None
    record = jobs.get(root / "project.sqlite3", job_id=result.job["job_id"])
    assert record.tool_call_id == "call-local"
    assert [
        event["event_type"] for event in jobs.replay_outbox(root / "project.sqlite3", after=0)
    ] == ["job.created"]


def test_preview_requests_the_desktop_capture_surface_and_optimization_is_truthful(tmp_path: Path) -> None:
    root, project_id = _project(tmp_path)
    jobs = SqliteJobRepository()
    runtime = PersistentB02ToolRuntime(jobs, SqliteApprovalRepository())
    preview = runtime.invoke(
        "model3d.render_preview", RiskLevel.LOCAL_REVERSIBLE, "sync", root, project_id,
        {"asset_id": "model-1"}, "call-local",
    )
    assert preview.status == "awaiting_ui_action"
    assert preview.ui_action == {
        "action_id": "call-local", "type": "capture_model_preview", "workspace_mode": "model3d"
    }
    assert jobs.list_nonterminal(root / "project.sqlite3") == []
    optimize = runtime.invoke(
        "model3d.optimize", RiskLevel.LOCAL_REVERSIBLE, "job", root, project_id,
        {"asset_id": "model-1"}, "call-local",
    )
    assert optimize.status == "failed"
    assert optimize.error is not None and optimize.error["code"] == "TOOL_NOT_AVAILABLE"


def test_paid_runtime_persists_parameter_bound_approval_without_a_network_call(
    tmp_path: Path,
) -> None:
    root, project_id = _project(tmp_path)
    approvals = SqliteApprovalRepository()
    runtime = PersistentB02ToolRuntime(SqliteJobRepository(), approvals)
    result = runtime.invoke(
        "image.generate",
        RiskLevel.EXTERNAL_PAID,
        "job",
        root,
        project_id,
        {
            "prompt_asset_id": "prompt-1",
            "provider_profile": "test",
            "channel": "banana",
            "model": "m",
            "candidate_count": 2,
        },
        "call-paid",
    )
    assert result.status == "awaiting_ui_action" and result.ui_action is not None
    approval = approvals.get(root / "project.sqlite3", approval_id=result.ui_action["action_id"])
    assert approval.tool_call_id == "call-paid" and approval.decision == "requires_user"
    assert SqliteJobRepository().list_nonterminal(root / "project.sqlite3") == []


def test_startup_recovery_only_classifies_and_never_claims_ambiguous_work(tmp_path: Path) -> None:
    root, _project_id = _project(tmp_path)
    jobs = SqliteJobRepository()
    database = root / "project.sqlite3"
    jobs.create(
        database, job_id="unknown", tool_call_id="call-local", job_type="tripo", provider="fake"
    )
    jobs.update(
        database,
        job_id="unknown",
        target=JobStatus.INTERRUPTED,
        stage=JobStage.UNKNOWN_SUBMISSION,
        resume_class=ResumeClass.UNKNOWN_SUBMISSION,
    )
    assert JobRecoveryService(jobs).recover(root) == [
        {"job_id": "unknown", "action": "manual_review"}
    ]
    assert jobs.claim(database, owner="worker", lease_until="2099-01-01T00:00:00Z") is None
