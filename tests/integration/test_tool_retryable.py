from pathlib import Path

from aipic_to_model.application.projects import ProjectService
from aipic_to_model.application.tools import ToolRegistry
from aipic_to_model.domain.common import RiskLevel
from aipic_to_model.domain.tools import ToolManifestV1, ToolResultV1
from aipic_to_model.infrastructure.sqlite.connection import connect


def test_b01_10_retryable_tool_failure_uses_single_compare_and_swap_claim(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Retry")
    registry = ToolRegistry()
    calls = []
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
        "required": [],
    }
    registry.register(
        ToolManifestV1(
            "fake.retry",
            "1",
            "Retry",
            "Retry contract fixture",
            schema,
            {
                "type": "object",
                "additionalProperties": True,
            },
            RiskLevel.LOCAL_REVERSIBLE,
            "sync",
            True,
            False,
            [],
            "test:retry",
        ),
        lambda _root, _project, _arguments, call_id: (
            calls.append(call_id)
            or (
                ToolResultV1(
                    False,
                    "failed",
                    call_id,
                    [],
                    "retry",
                    [],
                    error={
                        "code": "TEMP",
                        "category": "transient",
                        "user_message": "retry",
                        "recoverable": True,
                        "safe_to_retry": True,
                    },
                )
                if len(calls) == 1
                else ToolResultV1(True, "succeeded", call_id, [], "done", [])
            )
        ),
    )
    first = registry.execute(root, project.id, "fake.retry", "1", {}, "request-1")
    second = registry.execute(root, project.id, "fake.retry", "1", {}, "request-2")
    assert first.status == "failed"
    assert second.status == "succeeded"
    assert len(calls) == 2 and first.tool_call_id != second.tool_call_id
    connection = connect(root / "project.sqlite3")
    row = connection.execute("SELECT state,owner_tool_call_id FROM tool_idempotency").fetchone()
    count = connection.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0]
    connection.close()
    assert row["state"] == "succeeded" and row["owner_tool_call_id"] == second.tool_call_id
    assert count == 2
