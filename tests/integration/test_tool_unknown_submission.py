from pathlib import Path

import pytest

from aipic_to_model.application.projects import ProjectService
from aipic_to_model.application.tools import ToolRegistry
from aipic_to_model.domain.common import RiskLevel
from aipic_to_model.domain.tools import ToolManifestV1
from aipic_to_model.infrastructure.sqlite.connection import connect


def test_b01_10_unknown_external_submission_is_never_reissued(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Unknown")
    calls = 0

    def external(*_):
        nonlocal calls
        calls += 1
        raise RuntimeError("request left process")

    registry = ToolRegistry()
    registry.register(
        ToolManifestV1(
            "fake.external",
            "1.0.0",
            "External",
            "Failure after submission",
            {"type": "object", "additionalProperties": False, "properties": {}},
            {"type": "object"},
            RiskLevel.EXTERNAL,
            "sync",
            True,
            False,
            [],
            "fake.external",
        ),
        external,
    )
    with pytest.raises(RuntimeError):
        registry.execute(root, project.id, "fake.external", "1.0.0", {}, "first")
    restarted_registry = ToolRegistry()
    restarted_registry.register(
        ToolManifestV1(
            "fake.external",
            "1.0.0",
            "External",
            "Failure after submission",
            {"type": "object", "additionalProperties": False, "properties": {}},
            {"type": "object"},
            RiskLevel.EXTERNAL,
            "sync",
            True,
            False,
            [],
            "fake.external",
        ),
        external,
    )
    reused = restarted_registry.execute(root, project.id, "fake.external", "1.0.0", {}, "second")
    assert calls == 1 and reused.status == "queued" and reused.reused
    connection = connect(root / "project.sqlite3")
    assert (
        connection.execute("SELECT state FROM tool_idempotency").fetchone()[0]
        == "unknown_submission"
    )
    connection.close()
