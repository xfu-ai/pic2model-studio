from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from aipic_to_model.agent.core.models import (
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResult,
    ToolResultMessage,
)
from aipic_to_model.agent.execution.approved_job_wait import ApprovedToolJobWait
from aipic_to_model.agent.session.sqlite import LinearSessionRepository
from aipic_to_model.domain.job_models import JobStatus


class _Broker:
    def __init__(self, terminal: object | None) -> None:
        self.terminal = terminal
        self.calls: list[tuple[Path, str, float]] = []

    async def wait_for_terminal(
        self, database: Path, job_id: str, *, timeout_seconds: float
    ) -> object | None:
        self.calls.append((database, job_id, timeout_seconds))
        return self.terminal


def _wait(repository: LinearSessionRepository) -> dict[str, object]:
    session = repository.create(session_id="conversation")
    repository.append_message(
        session.id,
        AssistantMessage((ToolCall("call-1", "generate_images", {}),), stop_reason="tool_use"),
    )
    repository.register_job_wait(
        session.id,
        project_id="project-1",
        run_id="run-1",
        tool_call_id="call-1",
        tool_name="generate_images",
    )
    assert repository.bind_job_wait(session.id, "call-1", "job-1")
    wait = repository.job_wait_for_tool("project-1", "call-1")
    assert wait is not None
    return wait


@pytest.mark.agent
def test_pending_ui_action_recovers_the_approval_card_without_closing_the_tool_call(
    tmp_path: Path,
) -> None:
    repository = LinearSessionRepository(tmp_path / "agent.sqlite3")
    session = repository.create(session_id="conversation")
    repository.append_message(
        session.id,
        AssistantMessage(
            (ToolCall("call-approval", "generate_model3d", {}),), stop_reason="tool_use"
        ),
    )
    repository.register_job_wait(
        session.id,
        project_id="project-1",
        run_id="run-1",
        tool_call_id="call-approval",
        tool_name="generate_model3d",
    )
    repository.append_api_event(
        session.id,
        "tool.completed",
        {
            "conversation_id": session.id,
            "tool_call_id": "call-approval",
            "tool_name": "generate_model3d",
            "is_error": False,
            "result": {
                "content": [{"type": "text", "text": "Approval is required."}],
                "details": {
                    "status": "awaiting_ui_action",
                    "ui_action": {"action_id": "approval-1", "type": "approval_required"},
                },
                "is_error": False,
            },
        },
    )

    # The transcript remains open for the eventual terminal Tool Result.
    assert repository.open(session.id).messages[-1].role == "assistant"
    assert repository.pending_ui_actions(session.id) == [
        {
            "tool_call_id": "call-approval",
            "tool_name": "generate_model3d",
            "result": {
                "content": [{"type": "text", "text": "Approval is required."}],
                "details": {
                    "status": "awaiting_ui_action",
                    "ui_action": {"action_id": "approval-1", "type": "approval_required"},
                },
                "is_error": False,
            },
        }
    ]
    recovered = repository.job_wait_for_approval("project-1", "approval-1")
    assert recovered is not None
    assert recovered["tool_call_id"] == "call-approval"


@pytest.mark.agent
def test_multiview_desktop_action_is_resolved_only_by_matching_type(tmp_path: Path) -> None:
    repository = LinearSessionRepository(tmp_path / "agent.sqlite3")
    session = repository.create(session_id="conversation")
    repository.append_message(
        session.id,
        AssistantMessage(
            (
                ToolCall(
                    "call-regions",
                    "multiview.request_region_confirmation",
                    {"source_asset_ref": "sheet-1"},
                ),
            ),
            stop_reason="tool_use",
        ),
    )
    repository.register_job_wait(
        session.id,
        project_id="project-1",
        run_id="run-1",
        tool_call_id="call-regions",
        tool_name="multiview.request_region_confirmation",
    )
    repository.append_api_event(
        session.id,
        "tool.completed",
        {
            "tool_call_id": "call-regions",
            "result": {
                "details": {
                    "status": "awaiting_ui_action",
                    "ui_action": {
                        "action_id": "confirm-regions-1",
                        "type": "confirm_multiview_regions",
                    },
                }
            },
        },
    )

    assert repository.job_wait_for_ui_action(
        "project-1", "confirm-regions-1", "approval_required"
    ) is None
    recovered = repository.job_wait_for_ui_action(
        "project-1", "confirm-regions-1", "confirm_multiview_regions"
    )
    assert recovered is not None
    assert recovered["tool_call_id"] == "call-regions"


@pytest.mark.agent
def test_new_instruction_can_close_an_unapproved_tool_call_as_superseded(
    tmp_path: Path,
) -> None:
    repository = LinearSessionRepository(tmp_path / "agent.sqlite3")
    session = repository.create(session_id="conversation")
    repository.append_message(
        session.id,
        AssistantMessage(
            (ToolCall("call-old", "model3d.generate_from_multiview", {}),),
            stop_reason="tool_use",
        ),
    )
    repository.register_job_wait(
        session.id,
        project_id="project-1",
        run_id="run-1",
        tool_call_id="call-old",
        tool_name="model3d.generate_from_multiview",
    )
    wait = repository.job_wait_for_tool("project-1", "call-old")
    assert wait is not None

    ApprovedToolJobWait(repository, _Broker(None)).complete_superseded(wait)

    result = repository.open(session.id).messages[-1]
    assert isinstance(result, ToolResultMessage)
    assert result.result.details["status"] == "superseded"
    completed = repository.job_wait_for_tool("project-1", "call-old")
    assert completed is not None and completed["state"] == "declined"


@pytest.mark.agent
def test_terminal_job_wait_is_available_for_recovered_agent_continuation(tmp_path: Path) -> None:
    repository = LinearSessionRepository(tmp_path / "agent.sqlite3")
    wait = _wait(repository)
    assert repository.complete_job_wait("conversation", "call-1", "terminal_returned")

    assert repository.terminal_job_waits("conversation") == [
        {**wait, "state": "terminal_returned"}
    ]


@pytest.mark.agent
@pytest.mark.asyncio
async def test_approved_job_wait_writes_one_terminal_result_for_the_original_tool_call(tmp_path: Path) -> None:
    repository = LinearSessionRepository(tmp_path / "agent.sqlite3")
    wait = _wait(repository)
    broker = _Broker(SimpleNamespace(status=JobStatus.SUCCEEDED, result_asset_ids=["asset-exact"]))
    service = ApprovedToolJobWait(repository, broker)

    await service.wait_and_complete(wait, project_root=tmp_path)
    await service.wait_and_complete(wait, project_root=tmp_path)

    messages = repository.open("conversation").messages
    results = [message for message in messages if isinstance(message, ToolResultMessage)]
    assert len(results) == 1
    assert results[0].tool_call_id == "call-1"
    assert results[0].result.details["output_asset_refs"] == ["asset-exact"]
    completed = repository.job_wait_for_tool("project-1", "call-1")
    assert completed is not None and completed["state"] == "terminal_returned"


@pytest.mark.agent
@pytest.mark.asyncio
async def test_approved_job_wait_timeout_stays_open_and_later_terminal_result_completes(
    tmp_path: Path,
) -> None:
    repository = LinearSessionRepository(tmp_path / "agent.sqlite3")
    wait = _wait(repository)
    service = ApprovedToolJobWait(repository, _Broker(None))

    state = await service.wait_and_complete(
        wait,
        project_root=tmp_path,
        timeout_seconds=0.01,
    )

    results = [
        message for message in repository.open("conversation").messages if isinstance(message, ToolResultMessage)
    ]
    assert state == "waiting_external"
    assert results == []
    completed = repository.job_wait_for_tool("project-1", "call-1")
    assert completed is not None and completed["state"] == "waiting_external"
    assert repository.resumable_job_waits("conversation") == [completed]

    terminal_service = ApprovedToolJobWait(
        repository,
        _Broker(
            SimpleNamespace(
                status=JobStatus.SUCCEEDED,
                result_asset_ids=["asset-after-timeout"],
            )
        ),
    )
    terminal_state = await terminal_service.wait_and_complete(
        completed,
        project_root=tmp_path,
        timeout_seconds=0.01,
    )

    results = [
        message
        for message in repository.open("conversation").messages
        if isinstance(message, ToolResultMessage)
    ]
    assert terminal_state == "terminal_returned"
    assert len(results) == 1
    assert results[0].result.details["status"] == "succeeded"
    assert results[0].result.details["output_asset_refs"] == ["asset-after-timeout"]


@pytest.mark.agent
@pytest.mark.asyncio
async def test_terminal_recovery_replaces_legacy_waiting_external_result_in_place(
    tmp_path: Path,
) -> None:
    repository = LinearSessionRepository(tmp_path / "agent.sqlite3")
    _wait(repository)
    assert repository.complete_job_wait("conversation", "call-1", "waiting_external")
    repository.append_message(
        "conversation",
        ToolResultMessage(
            "call-1",
            "generate_images",
            ToolResult(
                (TextContent("Still processing."),),
                details={"status": "waiting_external"},
            ),
        ),
    )
    recovered = repository.job_wait_for_tool("project-1", "call-1")
    assert recovered is not None
    service = ApprovedToolJobWait(
        repository,
        _Broker(
            SimpleNamespace(
                status=JobStatus.SUCCEEDED,
                result_asset_ids=["asset-recovered"],
            )
        ),
    )

    await service.wait_and_complete(recovered, project_root=tmp_path)

    results = [
        message
        for message in repository.open("conversation").messages
        if isinstance(message, ToolResultMessage)
    ]
    assert len(results) == 1
    assert results[0].result.details["status"] == "succeeded"
    assert results[0].result.details["output_asset_refs"] == ["asset-recovered"]


@pytest.mark.agent
@pytest.mark.asyncio
async def test_unknown_submission_interrupt_requires_explicit_user_recovery(
    tmp_path: Path,
) -> None:
    repository = LinearSessionRepository(tmp_path / "agent.sqlite3")
    wait = _wait(repository)
    service = ApprovedToolJobWait(
        repository,
        _Broker(
            SimpleNamespace(
                id="job-1",
                status=JobStatus.INTERRUPTED,
                result_asset_ids=[],
                error={"code": "JOB_UNKNOWN_SUBMISSION", "safe_to_retry": False},
            )
        ),
    )

    await service.wait_and_complete(wait, project_root=tmp_path)

    result = repository.open("conversation").messages[-1]
    assert isinstance(result, ToolResultMessage)
    assert result.is_error is True
    assert result.result.details["status"] == "interrupted"
    assert result.result.details["job_ref"] == "job-1"
    assert result.result.details["requires_user_confirmation"] is True
    assert result.result.details["recommended_action"] == "job.confirm_new_submission"
    assert "Do not claim completion or retry automatically" in result.result.content[0].text
