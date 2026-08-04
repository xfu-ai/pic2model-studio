from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from aipic_to_model.agent.core.events import CancellationToken
from aipic_to_model.agent.core.tool import ToolContext
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
    """Exercise every model-visible Tool without contacting a live Provider."""

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
    tools = {tool.name: tool for tool in conversation.harness.agent.state.tools}
    assert len(tools) == 15

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
    results["inspect_workspace"] = await _execute(
        tools["inspect_workspace"], "inspect-call", {"view": "summary"}
    )
    results["select_asset"] = await _execute(
        tools["select_asset"],
        "select-call",
        {
            "asset_ref": str(source["id"]),
            "reason": "The controlled workflow has exactly one selected source.",
        },
    )
    results["analyze_image"] = await _execute(
        tools["analyze_image"],
        "analyze-call",
        {
            "source_asset_ref": str(source["id"]),
            "analysis_type": "content",
        },
    )
    results["understand_image"] = await _execute(
        tools["understand_image"],
        "understand-call",
        {
            "source_asset_ref": str(source["id"]),
            "question": "What is visible in this image?",
        },
    )
    results["generate_images"] = await _execute(
        tools["generate_images"],
        "generate-images-call",
        {
            "mode": "from_prompt",
            "prompt_asset_ref": str(prompt["id"]),
            "candidate_count": 2,
        },
    )
    results["edit_image"] = await _execute(
        tools["edit_image"],
        "edit-image-call",
        {
            "operation": "remove_background",
            "source_asset_ref": str(source["id"]),
        },
    )
    results["split_image"] = await _execute(
        tools["split_image"],
        "split-call",
        {
            "source_asset_ref": str(source["id"]),
            "selection_ref": str(confirmed["id"]),
            "prompt_asset_ref": str(prompt["id"]),
            "split_mode": "boxsplit",
        },
    )
    results["prepare_multiview"] = await _execute(
        tools["prepare_multiview"],
        "multiview-call",
        {
            "operation": "create",
            "source_asset_ref": str(source["id"]),
            "prompt_asset_ref": str(prompt["id"]),
        },
    )
    results["generate_model3d"] = await _execute(
        tools["generate_model3d"],
        "generate-model-call",
        {
            "mode": "image",
            "image_asset_ref": str(source["id"]),
            "parameters": {},
        },
    )
    results["process_model3d"] = await _execute(
        tools["process_model3d"],
        "process-model-call",
        {"operation": "inspect", "asset_refs": [str(model["id"])]},
    )
    analyze_job = results["analyze_image"].details["job"]["job_id"]
    results["control_job"] = await _execute(
        tools["control_job"],
        "control-job-call",
        {"action": "status", "job_ref": str(analyze_job)},
    )

    assert set(results) == set(tools) - {"read", "write", "edit", "bash"}
    assert all(not result.is_error for result in results.values())
    assert results["inspect_workspace"].details["status"] == "succeeded"
    assert results["select_asset"].details["status"] == "succeeded"
    assert results["control_job"].details["status"] == "succeeded"
    assert results["analyze_image"].details["status"] == "queued"
    assert results["understand_image"].details["status"] == "succeeded"
    assert "Controlled image understanding" in results["understand_image"].content[0].text
    assert results["edit_image"].details["status"] == "queued"
    assert results["process_model3d"].details["status"] == "succeeded"
    assert results["generate_images"].details["status"] == "awaiting_ui_action"
    assert results["split_image"].details["status"] == "awaiting_ui_action"
    assert results["prepare_multiview"].details["status"] == "awaiting_ui_action"
    assert results["generate_model3d"].details["status"] == "awaiting_ui_action"
