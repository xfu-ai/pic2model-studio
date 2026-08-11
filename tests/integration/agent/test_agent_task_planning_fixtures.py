from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.mark.agent
def test_controlled_agent_task_planning_fixture_is_offline_and_complete() -> None:
    """Keep every regression sample deterministic before production behavior changes."""

    fixture_path = (
        Path(__file__).parents[2]
        / "fixtures"
        / "controlled"
        / "agent_task_planning_job_wait.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert fixture["provider"] == "controlled-offline"
    assert fixture["project"]["reference_asset_ref"]
    assert len(fixture["historical_assets"]) == 2
    assert {asset["name"] for asset in fixture["historical_assets"]} == {"component-sheet.png"}
    assert fixture["controlled_job"]["terminal_after_seconds"] < 180
    assert fixture["scenarios"]["known_output_asset"]["legacy_wrong_ref"] not in fixture[
        "scenarios"
    ]["known_output_asset"]["tool_result_output_asset_refs"]
    assert fixture["scenarios"]["reference_image_revision"]["initial_mode"] == "from_image"
    assert fixture["scenarios"]["qwen_text_tool_json"]["text"].startswith("{")
