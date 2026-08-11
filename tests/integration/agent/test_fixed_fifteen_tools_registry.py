from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from aipic_to_model.agent.core.events import CancellationToken
from aipic_to_model.agent.core.tool import ToolContext
from aipic_to_model.agent.integrations.progressive_tools import (
    AGGREGATE_TOOL_NAMES,
    MODEL_TOOL_NAMES,
    PERMANENT_TOOL_NAMES,
)
from aipic_to_model.agent.integrations.runtime import AgentRuntime
from aipic_to_model.application.host_capabilities import HostCapabilityStore
from aipic_to_model.composition import compose_local_app
from aipic_to_model.domain.prompt_parser import BilingualPrompt
from tests.fixtures.glb import minimal_test_glb


async def _execute(tool, call_id: str, arguments: dict[str, object]):
    return await tool.execute(
        call_id,
        arguments,
        ToolContext(()),
        CancellationToken(),
    )


@pytest.mark.agent
@pytest.mark.asyncio
async def test_fixed_tools_execute_against_real_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise representative single-operation Tools without a live Provider."""

    monkeypatch.setenv("AIPIC_CONTROLLED_E2E", "1")
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Fixed tool registry")
    dependencies.roots[project.id] = root

    source_file = tmp_path / "source.png"
    Image.new("RGB", (32, 32), "navy").save(source_file)
    source = dependencies.assets.import_file(
        root, project.id, source_file, "source_image", "source"
    )
    prompt = dependencies.prompt_versions.create_bilingual(
        root,
        project.id,
        kind="content",
        bilingual=BilingualPrompt(
            "蓝色产品主体",
            "blue product subject",
            "蓝色产品主体",
            "blue product subject",
        ),
        request_id="prompt",
    )["asset"]
    selection = dependencies.selections.save(
        root,
        project.id,
        str(source["id"]),
        [{"x": 2, "y": 2, "width": 16, "height": 16}],
        "subject",
        "user",
        request_id="selection",
    )
    confirmed = dependencies.selections.confirm(
        root,
        project.id,
        str(selection["id"]),
        int(selection["revision"]),
        "confirm",
    )
    model_file = tmp_path / "fixture-model.glb"
    model_file.write_bytes(minimal_test_glb())
    model = dependencies.assets.import_file(
        root, project.id, model_file, "glb", "model"
    )

    runtime = AgentRuntime(dependencies.registry, dependencies.root_for)
    created = runtime.create(project.id)
    conversation = runtime._conversations[(project.id, str(created["id"]))]
    active_tools = {tool.name: tool for tool in conversation.harness.agent.state.tools}
    assert tuple(active_tools) == PERMANENT_TOOL_NAMES
    tools = {tool.name: tool for tool in conversation.harness._tool_catalog.all()}
    assert tuple(tools) == MODEL_TOOL_NAMES
    assert not set(AGGREGATE_TOOL_NAMES) & set(tools)

    write = await _execute(
        tools["write"],
        "write-call",
        {"path": "agent-tool-smoke.txt", "content": "before"},
    )
    edit = await _execute(
        tools["edit"],
        "edit-call",
        {
            "path": "agent-tool-smoke.txt",
            "old_text": "before",
            "new_text": "after",
        },
    )
    read = await _execute(
        tools["read"], "read-call", {"path": "agent-tool-smoke.txt"}
    )
    bash = await _execute(
        tools["bash"],
        "bash-call",
        {"command": "Write-Output fixed-15-bash", "timeout": 10},
    )
    assert not write.is_error and not edit.is_error
    assert "after" in read.content[0].text
    assert "fixed-15-bash" in bash.content[0].text

    results = {}
    results["project.get_state"] = await _execute(
        tools["project.get_state"], "inspect-call", {}
    )
    results["asset.set_current"] = await _execute(
        tools["asset.set_current"],
        "select-call",
        {
            "asset_ref": str(source["id"]),
            "reason": "The controlled workflow has exactly one selected source.",
        },
    )
    results["image.analyze_content"] = await _execute(
        tools["image.analyze_content"],
        "analyze-call",
        {
            "source_asset_ref": str(source["id"]),
        },
    )
    results["image.understand_for_agent"] = await _execute(
        tools["image.understand_for_agent"],
        "understand-call",
        {
            "source_asset_ref": str(source["id"]),
            "question": "What is visible in this image?",
        },
    )
    results["image.generate_from_prompt_asset"] = await _execute(
        tools["image.generate_from_prompt_asset"],
        "generate-images-call",
        {
            "prompt_asset_ref": str(prompt["id"]),
            "candidate_count": 2,
        },
    )
    results["image.remove_background_provider"] = await _execute(
        tools["image.remove_background_provider"],
        "edit-image-call",
        {
            "source_asset_ref": str(source["id"]),
        },
    )
    results["element.split_selection"] = await _execute(
        tools["element.split_selection"],
        "split-call",
        {
            "source_asset_ref": str(source["id"]),
            "selection_ref": str(confirmed["id"]),
            "prompt_asset_ref": str(prompt["id"]),
        },
    )
    results["multiview.generate"] = await _execute(
        tools["multiview.generate"],
        "multiview-call",
        {
            "source_asset_ref": str(source["id"]),
            "prompt_asset_ref": str(prompt["id"]),
        },
    )
    results["model3d.generate_from_image"] = await _execute(
        tools["model3d.generate_from_image"],
        "generate-model-call",
        {
            "image_asset_ref": str(source["id"]),
            "parameters": {},
        },
    )
    results["model3d.inspect"] = await _execute(
        tools["model3d.inspect"],
        "process-model-call",
        {"asset_refs": [str(model["id"])]},
    )
    analyze_job = results["image.analyze_content"].details["job"]["job_id"]
    results["job.get_status"] = await _execute(
        tools["job.get_status"],
        "control-job-call",
        {"job_ref": str(analyze_job)},
    )

    assert all(not result.is_error for result in results.values())
    assert results["project.get_state"].details["status"] == "succeeded"
    assert results["asset.set_current"].details["status"] == "succeeded"
    assert results["job.get_status"].details["status"] == "succeeded"
    assert results["image.analyze_content"].details["status"] == "queued"
    assert results["image.understand_for_agent"].details["status"] == "succeeded"
    assert "Controlled image understanding" in results["image.understand_for_agent"].content[0].text
    assert results["image.remove_background_provider"].details["status"] == "queued"
    assert results["model3d.inspect"].details["status"] == "succeeded"
    assert results["model3d.inspect"].details["data"]["inspection"]["format"] == "glb"
    assert '"inspection"' in results["model3d.inspect"].content[0].text
    assert results["image.generate_from_prompt_asset"].details["status"] == "queued"
    assert results["element.split_selection"].details["status"] == "awaiting_ui_action"
    assert results["multiview.generate"].details["status"] == "awaiting_ui_action"
    assert results["model3d.generate_from_image"].details["status"] == "queued"
