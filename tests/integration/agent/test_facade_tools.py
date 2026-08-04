from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from aipic_to_model.agent.core.events import CancellationToken
from aipic_to_model.agent.core.tool import ToolContext
from aipic_to_model.agent.integrations.aipic_tools import AIPicToolInvocation
from aipic_to_model.agent.integrations.facade_tools import (
    FACADE_TOOL_NAMES,
    FACADE_TOOL_SPECS,
    facade_tools,
)
from aipic_to_model.domain.tools import ToolResultV1


class RecordingRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.manifests: dict[tuple[str, str], object] = {
            (f"atomic-{index}", "1.0.0"): object() for index in range(59)
        }

    def execute(self, *args: Any) -> ToolResultV1:
        self.calls.append(args)
        return ToolResultV1(
            True,
            "succeeded",
            "internal-call",
            ["asset-output"],
            "Facade dispatch succeeded.",
            [],
        )


def _tools(tmp_path: Path) -> tuple[RecordingRegistry, dict[str, Any]]:
    registry = RecordingRegistry()
    tools = facade_tools(
        registry,  # type: ignore[arg-type]
        lambda: AIPicToolInvocation(
            tmp_path,
            "project-bound-by-host",
            "conversation-request",
            run_id="conversation",
        ),
    )
    return registry, {tool.name: tool for tool in tools}


@pytest.mark.agent
def test_facade_catalog_is_fixed_precise_and_valid() -> None:
    assert tuple(spec.name for spec in FACADE_TOOL_SPECS) == FACADE_TOOL_NAMES
    assert len(FACADE_TOOL_SPECS) == 11
    for spec in FACADE_TOOL_SPECS:
        Draft202012Validator.check_schema(dict(spec.parameters))
        assert "Do not " in spec.description
        assert spec.parameters["additionalProperties"] is False
        assert all(
            isinstance(value, dict) and value.get("description")
            for value in spec.parameters["properties"].values()
        )
    inspect = next(spec for spec in FACADE_TOOL_SPECS if spec.name == "inspect_workspace")
    assert inspect.parameters["properties"]["group"]["enum"] == [
        "input_images",
        "generated_images",
        "split_elements",
        "multiview_and_crops",
        "models",
        "exports",
    ]
    generate = next(spec for spec in FACADE_TOOL_SPECS if spec.name == "generate_model3d")
    model_parameters = generate.parameters["properties"]["parameters"]["properties"]
    for name in ("texture", "pbr"):
        assert model_parameters[name]["const"] is True
        assert model_parameters[name]["default"] is True


@pytest.mark.agent
@pytest.mark.asyncio
async def test_asset_inspection_returns_newest_assets_first(tmp_path: Path) -> None:
    class AssetRegistry(RecordingRegistry):
        def execute(self, *args: Any) -> ToolResultV1:
            self.calls.append(args)
            assets = [
                {"id": "old-current", "created_at": "2026-07-28T04:46:03Z", "is_current": True},
                {"id": "latest", "created_at": "2026-07-29T06:03:51Z", "is_current": False},
            ]
            return ToolResultV1(
                True,
                "succeeded",
                "internal-call",
                [item["id"] for item in assets],
                json.dumps(assets),
                [],
            )

    registry = AssetRegistry()
    tool = next(
        tool
        for tool in facade_tools(
            registry,  # type: ignore[arg-type]
            lambda: AIPicToolInvocation(tmp_path, "project", "request"),
        )
        if tool.name == "inspect_workspace"
    )
    result = await tool.execute(
        "inspect-assets",
        {"view": "assets", "group": "generated_images"},
        ToolContext(()),
        CancellationToken(),
    )

    assert not result.is_error
    assert isinstance(result.details, dict)
    assert [item["id"] for item in result.details["data"]] == ["latest", "old-current"]
    assert result.details["output_asset_ids"] == ["latest", "old-current"]


@pytest.mark.agent
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("facade_name", "arguments", "internal_name", "internal_arguments"),
    [
        (
            "inspect_workspace",
            {"view": "summary"},
            "project.get_state",
            {"project_id": "project-bound-by-host"},
        ),
        (
            "select_asset",
            {"asset_ref": "asset-1", "reason": "The user selected it."},
            "asset.set_current",
            {
                "asset_id": "asset-1",
                "decision_source": "agent",
                "reason": "The user selected it.",
            },
        ),
        (
            "analyze_image",
            {"source_asset_ref": "asset-1", "analysis_type": "content"},
            "image.analyze_content",
            {
                "asset_id": "asset-1",
                "provider_profile": "gemini/google/default",
                "model": "gemini-flash-lite-latest",
            },
        ),
        (
            "understand_image",
            {"source_asset_ref": "asset-1", "question": "What object is visible?"},
            "image.understand_for_agent",
            {
                "asset_id": "asset-1",
                "question": "What object is visible?",
                "provider_profile": "gemini/google/default",
                "model": "gemini-flash-lite-latest",
            },
        ),
        (
            "generate_images",
            {
                "mode": "from_prompt",
                "prompt_asset_ref": "prompt-1",
                "candidate_count": 4,
            },
            "image.generate",
            {
                "prompt_asset_id": "prompt-1",
                "provider_profile": "image-generation/auto",
                "channel": "auto",
                "model": "auto",
                "candidate_count": 4,
            },
        ),
        (
            "edit_image",
            {"operation": "remove_background", "source_asset_ref": "asset-1"},
            "image.remove_background",
            {"source_asset_id": "asset-1", "provider_profile": "image-generation/auto"},
        ),
        (
            "edit_image",
            {
                "operation": "trim_transparent",
                "source_asset_ref": "asset-1",
                "padding": 4,
                "alpha_threshold": 2,
            },
            "image.trim_transparent",
            {"source_asset_id": "asset-1", "padding": 4, "alpha_threshold": 2},
        ),
        (
            "edit_image",
            {
                "operation": "normalize",
                "source_asset_ref": "asset-1",
                "max_long_edge": 1024,
                "output_format": "webp",
                "quality": 82,
            },
            "image.normalize",
            {
                "source_asset_id": "asset-1",
                "max_long_edge": 1024,
                "output_format": "webp",
                "quality": 82,
            },
        ),
        (
            "edit_image",
            {
                "operation": "remove_background_local",
                "source_asset_ref": "asset-1",
                "background_method": "color_key",
                "target_color": [0, 255, 0],
                "tolerance": 16,
            },
            "image.remove_background_local",
            {
                "source_asset_id": "asset-1",
                "method": "color_key",
                "target_color": [0, 255, 0],
                "tolerance": 16,
            },
        ),
        (
            "edit_image",
            {
                "operation": "upscale_local",
                "source_asset_ref": "asset-1",
                "scale": 4,
            },
            "image.upscale_local",
            {"source_asset_id": "asset-1", "scale": 4},
        ),
        (
            "split_image",
            {
                "source_asset_ref": "asset-1",
                "split_mode": "alpha_components",
                "min_area": 16,
                "max_outputs": 8,
            },
            "image.split_local",
            {
                "source_asset_id": "asset-1",
                "mode": "alpha_components",
                "min_area": 16,
                "max_outputs": 8,
            },
        ),
        (
            "split_image",
            {
                "source_asset_ref": "asset-1",
                "split_mode": "grid",
                "columns": 3,
                "rows": 2,
            },
            "image.split_local",
            {
                "source_asset_id": "asset-1",
                "mode": "grid",
                "columns": 3,
                "rows": 2,
            },
        ),
        (
            "split_image",
            {
                "source_asset_ref": "asset-1",
                "selection_ref": "selection-1",
                "prompt_asset_ref": "prompt-1",
                "split_mode": "boxsplit",
            },
            "element.split",
            {
                "source_asset_id": "asset-1",
                "selection_id": "selection-1",
                "prompt_asset_id": "prompt-1",
                "provider_profile": "image-generation/auto",
                "channel": "auto",
                "model": "auto",
                "split_mode": "boxsplit",
            },
        ),
        (
            "split_image",
            {
                "source_asset_ref": "asset-1",
                "prompt_asset_ref": "prompt-1",
                "split_mode": "boxsplit",
            },
            "selection.request_user",
            {"asset_id": "asset-1"},
        ),
        (
            "split_image",
            {
                "source_asset_ref": "asset-1",
                "prompt_asset_ref": "prompt-1",
                "split_mode": "element",
            },
            "element.split",
            {
                "source_asset_id": "asset-1",
                "prompt_asset_id": "prompt-1",
                "provider_profile": "image-generation/auto",
                "channel": "auto",
                "model": "auto",
                "split_mode": "element",
            },
        ),
        (
            "prepare_multiview",
            {"operation": "create", "source_asset_ref": "asset-1"},
            "multiview.generate",
            {
                "source_asset_id": "asset-1",
                "provider_profile": "image-generation/auto",
                "channel": "auto",
                "model": "auto",
            },
        ),
        (
            "generate_model3d",
            {"mode": "image", "image_asset_ref": "asset-1", "parameters": {}},
            "model3d.generate",
            {
                "mode": "image",
                "image_asset_id": "asset-1",
                "provider_profile": "tripo3d/default",
                "model": "tripo-v2.5-20250123",
                "parameters": {"texture": True, "pbr": True},
            },
        ),
        (
            "process_model3d",
            {"operation": "inspect", "asset_refs": ["model-1"]},
            "model3d.inspect",
            {"asset_id": "model-1"},
        ),
        (
            "control_job",
            {"action": "status", "job_ref": "job-1"},
            "job.get_status",
            {"job_id": "job-1"},
        ),
    ],
)
async def test_each_facade_dispatches_to_one_canonical_internal_tool(
    tmp_path: Path,
    facade_name: str,
    arguments: dict[str, object],
    internal_name: str,
    internal_arguments: dict[str, object],
) -> None:
    registry, tools = _tools(tmp_path)

    result = await tools[facade_name].execute(
        f"call-{facade_name}",
        arguments,
        ToolContext(()),
        CancellationToken(),
    )

    assert not result.is_error
    assert len(registry.calls) == 1
    call = registry.calls[0]
    assert call[2] == internal_name
    assert call[4] == internal_arguments
    assert call[1] == "project-bound-by-host"
    assert '"output_asset_refs":["asset-output"]' in result.content[0].text


@pytest.mark.agent
@pytest.mark.asyncio
async def test_generate_model3d_cannot_dispatch_without_textures_and_pbr(
    tmp_path: Path,
) -> None:
    registry, tools = _tools(tmp_path)

    result = await tools["generate_model3d"].execute(
        "call-generate-model3d-textured",
        {
            "mode": "image",
            "image_asset_ref": "asset-1",
            "parameters": {"texture": False, "pbr": False, "face_limit": 50_000},
        },
        ToolContext(()),
        CancellationToken(),
    )

    assert not result.is_error
    assert registry.calls[0][4]["parameters"] == {
        "texture": True,
        "pbr": True,
        "face_limit": 50_000,
    }


@pytest.mark.agent
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("facade_name", "arguments", "internal_name"),
    [
        (
            "generate_images",
            {
                "mode": "from_image",
                "prompt_asset_ref": "prompt-1",
                "source_asset_ref": "asset-1",
                "candidate_count": 2,
            },
            "image.transform",
        ),
        (
            "generate_images",
            {
                "mode": "variants",
                "prompt_asset_ref": "prompt-1",
                "source_asset_ref": "asset-1",
                "candidate_count": 2,
            },
            "image.generate_variants",
        ),
        (
            "edit_image",
            {"operation": "upscale", "source_asset_ref": "asset-1", "scale": 2},
            "image.upscale",
        ),
        (
            "edit_image",
            {
                "operation": "inpaint",
                "source_asset_ref": "asset-1",
                "selection_ref": "selection-1",
                "prompt_asset_ref": "prompt-1",
            },
            "image.inpaint_selection",
        ),
        (
            "edit_image",
            {"operation": "export_transparent", "source_asset_ref": "asset-1"},
            "element.export_transparent",
        ),
        (
            "prepare_multiview",
            {"operation": "detect_regions", "multiview_ref": "multiview-1"},
            "multiview.detect_regions",
        ),
        (
            "prepare_multiview",
            {
                "operation": "regenerate_view",
                "multiview_ref": "multiview-1",
                "target_view": "side",
            },
            "multiview.regenerate_view",
        ),
        (
            "generate_model3d",
            {
                "mode": "multiview",
                "multiview_ref": "multiview-1",
                "view_asset_refs": {
                    "front": "front-1",
                    "side": "side-1",
                    "back": "back-1",
                },
                "parameters": {},
            },
            "model3d.generate",
        ),
        (
            "process_model3d",
            {
                "operation": "convert",
                "asset_refs": ["model-1"],
                "target_format": "fbx",
            },
            "model3d.convert",
        ),
        (
            "process_model3d",
            {
                "operation": "optimize",
                "asset_refs": ["model-1"],
                "target_triangles": 1000,
            },
            "model3d.optimize",
        ),
        (
            "process_model3d",
            {"operation": "package", "asset_refs": ["model-1", "texture-1"]},
            "model3d.package",
        ),
        ("control_job", {"action": "cancel", "job_ref": "job-1"}, "job.cancel"),
        ("control_job", {"action": "retry", "job_ref": "job-1"}, "job.retry"),
    ],
)
async def test_facade_operation_variants_have_unambiguous_routes(
    tmp_path: Path,
    facade_name: str,
    arguments: dict[str, object],
    internal_name: str,
) -> None:
    registry, tools = _tools(tmp_path)

    result = await tools[facade_name].execute(
        f"call-{facade_name}",
        arguments,
        ToolContext(()),
        CancellationToken(),
    )

    assert not result.is_error
    assert registry.calls[0][2] == internal_name


@pytest.mark.agent
@pytest.mark.asyncio
async def test_generate_images_materializes_a_direct_prompt_before_dispatch(tmp_path: Path) -> None:
    registry = RecordingRegistry()
    created: list[tuple[AIPicToolInvocation, str, str]] = []
    tool = next(
        item
        for item in facade_tools(
            registry,  # type: ignore[arg-type]
            lambda: AIPicToolInvocation(tmp_path, "project-bound-by-host", "conversation-request"),
            prompt_creator=lambda invocation, prompt, request_id: (
                created.append((invocation, prompt, request_id)) or "prompt-direct"
            ),
        )
        if item.name == "generate_images"
    )

    result = await tool.execute(
        "call-direct-prompt",
        {"mode": "from_prompt", "prompt": "A small clay fox", "candidate_count": 2},
        ToolContext(()),
        CancellationToken(),
    )

    assert created and created[0][1] == "A small clay fox"
    assert registry.calls[0][2] == "image.generate"
    assert registry.calls[0][4]["prompt_asset_id"] == "prompt-direct"
    assert result.details["data"]["prompt_asset_id"] == "prompt-direct"


@pytest.mark.agent
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("facade_name", "arguments", "message"),
    [
        (
            "generate_images",
            {
                "mode": "from_image",
                "prompt_asset_ref": "prompt-1",
                "candidate_count": 2,
            },
            "source_asset_ref",
        ),
        (
            "edit_image",
            {"operation": "upscale", "source_asset_ref": "asset-1"},
            "scale",
        ),
        (
            "generate_model3d",
            {"mode": "multiview", "parameters": {}},
            "multiview_ref",
        ),
        (
            "process_model3d",
            {
                "operation": "convert",
                "asset_refs": ["model-1"],
            },
            "target_format",
        ),
    ],
)
async def test_facade_conditional_argument_failures_are_structured(
    tmp_path: Path,
    facade_name: str,
    arguments: dict[str, object],
    message: str,
) -> None:
    registry, tools = _tools(tmp_path)

    result = await tools[facade_name].execute(
        f"call-{facade_name}",
        arguments,
        ToolContext(()),
        CancellationToken(),
    )

    assert result.is_error
    assert registry.calls == []
    assert message in result.content[0].text
    assert isinstance(result.details, dict)
    assert result.details["error"]["code"] == "TOOL_ARGUMENT_INVALID"
    assert result.details["ok"] is False
    assert result.details["tool_call_id"] == f"call-{facade_name}"
    assert result.details["retry"]["automatic"] is False


@pytest.mark.agent
@pytest.mark.asyncio
async def test_capability_view_returns_injected_current_runtime_state(tmp_path: Path) -> None:
    registry = RecordingRegistry()
    snapshot = {
        "schema_version": 1,
        "snapshot_version": 42,
        "configuration_state": "current",
        "capabilities": {"image_generation": {"available": False}},
    }
    tools = facade_tools(
        registry,  # type: ignore[arg-type]
        lambda: AIPicToolInvocation(tmp_path, "project", "request"),
        lambda: snapshot,
    )

    result = await next(
        tool for tool in tools if tool.name == "inspect_workspace"
    ).execute(
        "capabilities-call",
        {"view": "capabilities"},
        ToolContext(()),
        CancellationToken(),
    )

    assert registry.calls == []
    assert not result.is_error
    assert isinstance(result.details, dict)
    assert result.details["data"] == snapshot
    assert result.details["tool_call_id"] == "capabilities-call"
