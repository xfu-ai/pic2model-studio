import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from aipic_to_model.application.projects import ProjectService
from aipic_to_model.application.tool_catalog import MANIFEST_SHA256, register_b01_tools
from aipic_to_model.application.tools import InMemoryJobSubmitter, ToolRegistry
from aipic_to_model.domain.common import RiskLevel
from aipic_to_model.domain.tools import ToolManifestV1, ToolResultV1


def test_tool_result_dto_enforces_manifest_field_types():
    with pytest.raises(ValueError):
        ToolResultV1(
            True,
            "queued",
            "call",
            [],
            "queued",
            [],
            job={
                "job_id": "id",
                "status": "queued",
                "job_type": "kind",
                "stage": "stage",
                "elapsed_seconds": 0,
                "provider": "local",
                "can_cancel": "yes",
                "can_stop_waiting": False,
            },
        )
    with pytest.raises(ValueError):
        ToolResultV1(True, "succeeded", "call", [7], 99, [False])
    with pytest.raises(ValueError):
        ToolResultV1(
            False,
            "failed",
            "call",
            [],
            "failed",
            [],
            error={
                "code": "E",
                "category": "local",
                "user_message": "failed",
                "recoverable": False,
                "retry_after_seconds": True,
            },
        )


def test_b01_10_tool_result_branches_are_mutually_exclusive():
    assert ToolResultV1(True, "succeeded", "call", [], "ok", []).status == "succeeded"
    assert (
        ToolResultV1(
            True,
            "queued",
            "call",
            [],
            "queued",
            [],
            job={
                "job_id": "job",
                "status": "queued",
                "job_type": "local",
                "stage": "queued",
                "elapsed_seconds": 0,
                "provider": "local",
                "can_cancel": False,
                "can_stop_waiting": True,
            },
        ).status
        == "queued"
    )
    assert (
        ToolResultV1(
            True,
            "awaiting_ui_action",
            "call",
            [],
            "wait",
            [],
            expected_action={"type": "select_rectangle"},
            ui_action={"action_id": "action", "type": "select_rectangle", "workspace_mode": "x"},
        ).status
        == "awaiting_ui_action"
    )
    assert (
        ToolResultV1(
            False,
            "failed",
            "call",
            [],
            "failed",
            [],
            error={
                "code": "FAILED",
                "category": "local",
                "user_message": "failed",
                "recoverable": False,
            },
        ).status
        == "failed"
    )

    with pytest.raises(ValueError):
        ToolResultV1(True, "succeeded", "call", [], "bad", [], job={})
    with pytest.raises(ValueError):
        ToolResultV1(False, "failed", "call", [], "bad", [], error={"code": "missing-fields"})
    with pytest.raises(ValueError):
        ToolResultV1(True, "queued", "call", [], "bad", [])
    with pytest.raises(ValueError):
        ToolResultV1(
            True,
            "failed",
            "call",
            [],
            "bad",
            [],
            error={"code": "x", "category": "local", "user_message": "x", "recoverable": False},
        )
    with pytest.raises(ValueError):
        ToolResultV1(
            False,
            "failed",
            "call",
            [],
            "bad",
            [],
            error={
                "code": "x",
                "category": "local",
                "user_message": "x",
                "recoverable": False,
                "unknown": True,
            },
        )
    with pytest.raises(ValueError):
        ToolResultV1(
            False,
            "failed",
            "call",
            [],
            "bad",
            [],
            error={
                "code": "x",
                "category": "local",
                "user_message": "x",
                "recoverable": False,
                "safe_to_retry": "yes",
            },
        )


def test_b01_10_manifest_output_schema_is_frozen_one_of_and_job_ref_fixture_parses():
    registry = ToolRegistry()
    register_b01_tools(registry, InMemoryJobSubmitter())
    for manifest in registry.manifests.values():
        assert "oneOf" in manifest.output_schema
        Draft202012Validator.check_schema(manifest.output_schema)
    manifest_dir = (
        Path(__file__).parents[2] / "src" / "aipic_to_model" / "application" / "tool_manifests"
    )
    assert {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in manifest_dir.glob("*.json")
    } == MANIFEST_SHA256
    schema = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "schemas" / "job-ref-v1.json").read_text()
    )
    Draft202012Validator(schema).validate(
        {
            "job_id": "job_1",
            "status": "queued",
            "job_type": "legacy_import",
            "stage": "queued",
            "elapsed_seconds": 0,
            "provider": "local",
            "can_cancel": False,
            "can_stop_waiting": True,
        }
    )


def test_b01_10_manifest_nested_result_contracts_reject_generic_objects():
    registry = ToolRegistry()
    register_b01_tools(registry, InMemoryJobSubmitter())
    schema = registry.manifests[("asset.list", "1.0.0")].output_schema
    validator = Draft202012Validator(schema)
    base = {
        "ok": False,
        "status": "failed",
        "tool_call_id": "call",
        "output_asset_ids": [],
        "summary": "failed",
        "warnings": [],
        "expected_action": None,
        "ui_action": None,
        "job": None,
        "reused": False,
    }
    assert list(validator.iter_errors({**base, "error": {"code": "only-code"}}))
    assert list(
        validator.iter_errors(
            {
                **base,
                "error": {
                    "code": "FAILED",
                    "category": "local",
                    "user_message": "failed",
                    "recoverable": False,
                    "unapproved": True,
                },
            }
        )
    )


def test_b01_10_ui_action_is_durable_before_the_result_is_returned(tmp_path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "UI")
    registry = ToolRegistry()
    schema = {"type": "object", "additionalProperties": False, "properties": {}}
    registry.register(
        ToolManifestV1(
            "fake.ui",
            "1.0.0",
            "UI",
            "UI",
            schema,
            {"type": "object"},
            RiskLevel.LOCAL_REVERSIBLE,
            "sync",
            True,
            False,
            [],
            "fake.ui",
        ),
        lambda _root, _project, _arguments, call_id: ToolResultV1(
            True,
            "awaiting_ui_action",
            call_id,
            [],
            "wait",
            [],
            expected_action={"type": "select_rectangle"},
            ui_action={
                "action_id": "action-1",
                "type": "select_rectangle",
                "workspace_mode": "rectangle_selection",
            },
        ),
    )
    result = registry.execute(root, project.id, "fake.ui", "1.0.0", {}, "ui")
    from aipic_to_model.infrastructure.sqlite.connection import connect

    connection = connect(root / "project.sqlite3")
    row = connection.execute("SELECT status FROM tool_calls").fetchone()
    event = connection.execute(
        "SELECT event_type FROM events WHERE event_type='workspace.action.requested'"
    ).fetchone()
    connection.close()
    assert result.status == "awaiting_ui_action" and row["status"] == "awaiting_ui_action" and event


def test_tool_result_dto_enforces_manifest_non_empty_contract_fields():
    with pytest.raises(ValueError):
        ToolResultV1(
            False,
            "failed",
            "call",
            [],
            "failed",
            [],
            error={"code": "E", "category": "", "user_message": "", "recoverable": False},
        )
    with pytest.raises(ValueError):
        ToolResultV1(
            True,
            "queued",
            "call",
            [],
            "queued",
            [],
            job={
                "job_id": "",
                "status": "queued",
                "job_type": "image",
                "stage": "queued",
                "elapsed_seconds": 0,
                "provider": "fake",
                "can_cancel": True,
                "can_stop_waiting": True,
            },
        )
    with pytest.raises(ValueError):
        ToolResultV1(
            True,
            "awaiting_ui_action",
            "call",
            [],
            "select",
            [],
            expected_action={"type": ""},
            ui_action={"action_id": "", "type": "select", "workspace_mode": "asset"},
        )
