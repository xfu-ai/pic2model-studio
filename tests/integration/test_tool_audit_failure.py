from pathlib import Path

import pytest

from aipic_to_model.application.projects import ProjectService
from aipic_to_model.application.tools import ToolRegistry
from aipic_to_model.domain.common import DomainErrorV1, ErrorCode, RiskLevel
from aipic_to_model.domain.tools import ToolManifestV1, ToolResultV1
from aipic_to_model.infrastructure.sqlite.connection import connect
from aipic_to_model.infrastructure.sqlite.repositories import EventRepository


def test_b01_10_audit_completion_failure_never_reports_success(tmp_path: Path, monkeypatch):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Audit")
    registry = ToolRegistry()
    schema = {"type": "object", "additionalProperties": False, "properties": {}}
    registry.register(
        ToolManifestV1(
            "fake.audit_failure",
            "1",
            "Audit failure",
            "Injected audit completion failure",
            schema,
            {"type": "object", "additionalProperties": True},
            RiskLevel.LOCAL_REVERSIBLE,
            "sync",
            True,
            False,
            [],
            "test:audit_failure",
        ),
        lambda _root, _project, _arguments, call_id: ToolResultV1(
            True, "succeeded", call_id, [], "executor completed", []
        ),
    )

    def fail_event(*_args, **_kwargs):
        raise OSError("audit database write failed")

    monkeypatch.setattr(EventRepository, "append_named", fail_event)
    with pytest.raises(OSError, match="audit database write failed"):
        registry.execute(root, project.id, "fake.audit_failure", "1", {}, "request")
    connection = connect(root / "project.sqlite3")
    call = connection.execute("SELECT status,error_json,result_json FROM tool_calls").fetchone()
    state = connection.execute("SELECT state FROM tool_idempotency").fetchone()[0]
    connection.close()
    assert call["status"] == "failed" and call["result_json"] is None
    assert "TOOL_EXECUTION_FAILED" in call["error_json"]
    assert state == "failed_terminal"


def test_b01_10_terminal_failure_replays_for_a_new_request_id(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Terminal failure")
    registry = ToolRegistry()
    schema = {"type": "object", "additionalProperties": False, "properties": {}}
    calls = 0

    def fail_terminal(*_args):
        nonlocal calls
        calls += 1
        raise DomainErrorV1(ErrorCode.PROJECT_NOT_FOUND, "Project does not exist.")

    registry.register(
        ToolManifestV1(
            "fake.terminal_failure",
            "1",
            "Terminal failure",
            "Terminal failure fixture",
            schema,
            {"type": "object", "additionalProperties": True},
            RiskLevel.READ_ONLY,
            "sync",
            True,
            False,
            [],
            "test:terminal_failure",
        ),
        fail_terminal,
    )

    for request_id in ("first-request", "second-request"):
        with pytest.raises(DomainErrorV1) as failure:
            registry.execute(
                root,
                project.id,
                "fake.terminal_failure",
                "1",
                {},
                request_id,
            )
        assert failure.value.code == ErrorCode.PROJECT_NOT_FOUND

    connection = connect(root / "project.sqlite3")
    try:
        assert connection.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM tool_requests").fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM tool_requests WHERE error_json IS NOT NULL"
        ).fetchone()[0] == 2
    finally:
        connection.close()
    assert calls == 1
