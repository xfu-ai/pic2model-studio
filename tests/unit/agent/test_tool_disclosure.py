from __future__ import annotations

from collections.abc import Mapping

import pytest

from aipic_to_model.agent.core.events import CancellationToken
from aipic_to_model.agent.core.models import (
    AssistantMessage,
    ProviderEvent,
    ProviderEventType,
    TextContent,
    ToolCall,
    ToolResult,
)
from aipic_to_model.agent.core.tool import (
    ActiveToolSet,
    AgentToolCatalog,
    ToolContext,
    ToolExecutionMode,
)
from aipic_to_model.agent.harness import AgentHarness
from aipic_to_model.agent.integrations.progressive_tools import (
    AGGREGATE_TOOL_NAMES,
    MODEL_TOOL_NAMES,
    OPERATION_TOOL_SPECS,
    PERMANENT_TOOL_NAMES,
    ToolboxLoadTool,
    ToolboxStatusTool,
    planner_tool_names,
)
from aipic_to_model.agent.integrations.tool_guidance import _AGENT_TOOL_GUIDANCE
from aipic_to_model.agent.planning import ExecutionPlan, PlanStep
from aipic_to_model.agent.providers.base import ModelProfile
from aipic_to_model.agent.providers.fake import FakeProvider, ScriptedResponse
from aipic_to_model.agent.session.sqlite import LinearSessionRepository
from aipic_to_model.application.b02_tool_catalog import B02_TOOLS
from aipic_to_model.application.tool_catalog import B01_TOOLS


class RecordingTool:
    label = "Recording tool"
    description = "A test-only recording tool."
    execution_mode: ToolExecutionMode = "sequential"

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0
        self.parameters: Mapping[str, object] = {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }

    async def execute(
        self,
        tool_call_id: str,
        arguments: dict[str, object],
        context: ToolContext,
        cancellation: CancellationToken,
        on_update=None,
    ) -> ToolResult:
        del tool_call_id, arguments, context, cancellation, on_update
        self.calls += 1
        return ToolResult((TextContent("executed"),))


def _response(message: AssistantMessage) -> ScriptedResponse:
    return ScriptedResponse(
        (
            ProviderEvent(ProviderEventType.MESSAGE_START),
            ProviderEvent(ProviderEventType.MESSAGE_END, message=message),
        )
    )


def test_progressive_catalog_has_exactly_ten_permanent_tools_and_no_aggregate_tools() -> None:
    assert PERMANENT_TOOL_NAMES == (
        "read",
        "write",
        "edit",
        "bash",
        "toolbox.status",
        "toolbox.load",
        "project.get_state",
        "image.understand_for_agent",
        "image.remove_background_local",
        "model3d.generate_from_image",
    )
    assert len(PERMANENT_TOOL_NAMES) == 10
    assert not set(AGGREGATE_TOOL_NAMES) & set(MODEL_TOOL_NAMES)
    assert set(PERMANENT_TOOL_NAMES) <= set(MODEL_TOOL_NAMES)


def test_narrow_operation_schemas_match_their_exact_dispatch_contracts() -> None:
    schemas = {spec.name: spec.parameters for spec in OPERATION_TOOL_SPECS}

    assert schemas["asset.get_metadata"]["properties"]["asset_refs"] | {
        "minItems": 1,
        "maxItems": 1,
    } == schemas["asset.get_metadata"]["properties"]["asset_refs"]
    assert schemas["asset.compare"]["properties"]["asset_refs"] | {
        "minItems": 2,
        "maxItems": 2,
    } == schemas["asset.compare"]["properties"]["asset_refs"]
    for name in (
        "model3d.inspect",
        "model3d.render_preview",
        "model3d.convert",
        "model3d.optimize",
    ):
        refs = schemas[name]["properties"]["asset_refs"]
        assert refs["minItems"] == refs["maxItems"] == 1
    for name in ("image.transform_from_reference", "image.generate_variants"):
        properties = schemas[name]["properties"]
        assert "seed" not in properties
        assert "steps" not in properties


def test_every_narrow_operation_has_distinct_selection_guidance() -> None:
    by_name = {spec.name: spec for spec in OPERATION_TOOL_SPECS}

    assert len({spec.description for spec in OPERATION_TOOL_SPECS}) == len(
        OPERATION_TOOL_SPECS
    )
    assert all("Use " in spec.description and "Do not " in spec.description for spec in OPERATION_TOOL_SPECS)
    assert all(spec.search_terms for spec in OPERATION_TOOL_SPECS)
    assert "image.understand_for_agent" in by_name["image.analyze_content"].description
    assert "image.remove_background_provider" in by_name["image.remove_background_local"].description
    assert "image.generate_variants" in by_name["image.transform_from_reference"].description
    assert "confirmed" in by_name["model3d.generate_from_multiview"].description


def test_every_atomic_tool_has_guidance_if_the_legacy_adapter_exposes_it() -> None:
    atomic_names = {item[0] for item in (*B01_TOOLS, *B02_TOOLS)}

    assert atomic_names <= set(_AGENT_TOOL_GUIDANCE)
    assert "job.get_status" in _AGENT_TOOL_GUIDANCE["model3d.get_status"][0]
    assert "job.cancel" in _AGENT_TOOL_GUIDANCE["model3d.cancel"][0]


@pytest.mark.agent
@pytest.mark.asyncio
async def test_toolbox_searches_multilingual_aliases_and_returns_selection_metadata() -> None:
    target = RecordingTool("image.split_grid")
    target.label = "Split a verified image grid"
    target.description = "Split a regular grid locally. Do not use for semantic extraction."
    target.search_terms = ("grid split", "网格拆分", "宫格切图")
    catalog_box: list[AgentToolCatalog] = []
    status = ToolboxStatusTool(lambda: catalog_box[0])
    catalog_box.append(AgentToolCatalog((status, target)))

    result = await status.execute(
        "search-call",
        {"query": "网格拆分"},
        ToolContext(()),
        CancellationToken(),
    )

    assert result.details["matches"] == [
        {
            "name": "image.split_grid",
            "label": "Split a verified image grid",
            "description": target.description,
            "required_parameters": [],
            "execution_mode": "sequential",
            "active": False,
            "permanent": False,
        }
    ]


def test_active_tool_set_is_append_only_stable_and_deduplicated() -> None:
    tools = tuple(RecordingTool(name) for name in ("one", "two", "three"))
    catalog = AgentToolCatalog(tools)
    active = ActiveToolSet(catalog, ("one",), ("two", "one", "missing"))

    assert active.names == ("one", "two")
    assert active.activate(("two", "three", "three")) == ("three",)
    assert active.names == ("one", "two", "three")
    assert tuple(tool.name for tool in active.tools) == active.names


def test_planner_preload_maps_operations_to_single_tools_in_plan_order() -> None:
    plan = ExecutionPlan(
        version=1,
        goal="prepare transparent components",
        deliverables=("components",),
        constraints=(),
        acceptance_criteria=(),
        assumptions=(),
        blocking_questions=(),
        steps=(
            PlanStep(
                "remove",
                "Remove background",
                "edit_image",
                "source",
                "transparent atlas",
                (),
                operation="remove_background_local",
            ),
            PlanStep(
                "split",
                "Split components",
                None,
                "prior output",
                "components",
                (),
                operation="split_alpha_components_local",
            ),
            PlanStep(
                "verify",
                "Inspect output",
                "asset.get_metadata",
                "prior output",
                "verified assets",
                (),
                operation="verify_output",
            ),
        ),
        current_step_id="remove",
        state="executing",
        next_action="execute",
    )

    assert planner_tool_names(plan) == (
        "image.remove_background_local",
        "image.split_alpha_components",
        "asset.get_metadata",
    )


def test_planner_preloads_resize_and_upscale_tools() -> None:
    plan = ExecutionPlan(
        version=1,
        goal="resize and upscale images",
        deliverables=("resized images",),
        constraints=("offline",),
        acceptance_criteria=(),
        assumptions=(),
        blocking_questions=(),
        steps=(
            PlanStep("resize", "Resize", None, "source", "resized", (), operation="resize_image_local"),
            PlanStep("upscale", "Upscale", None, "prior output", "upscaled", (), operation="upscale_image_local"),
        ),
        current_step_id="resize",
        state="executing",
        next_action="execute",
    )

    assert planner_tool_names(plan) == ("image.normalize", "image.upscale_local")
    by_name = {spec.name: spec for spec in OPERATION_TOOL_SPECS}
    assert "改变图片尺寸" in by_name["image.normalize"].search_terms
    assert "超分放大" in by_name["image.upscale_local"].search_terms


@pytest.mark.agent
@pytest.mark.asyncio
async def test_toolbox_discovers_then_loads_schema_on_the_next_turn_and_executes(
    tmp_path,
) -> None:
    target = RecordingTool("image.split_grid")
    catalog_box: list[AgentToolCatalog] = []
    harness_box: list[AgentHarness] = []
    active_names = lambda: harness_box[0].active_tool_names if harness_box else (
        "toolbox.status",
        "toolbox.load",
    )
    status = ToolboxStatusTool(lambda: catalog_box[0], active_names)
    loader = ToolboxLoadTool(lambda: catalog_box[0], active_names)
    catalog = AgentToolCatalog((status, loader, target))
    catalog_box.append(catalog)
    provider = FakeProvider(
        (
            _response(
                AssistantMessage(
                    (ToolCall("status-call", "toolbox.status", {"query": "grid"}),),
                    stop_reason="tool_use",
                )
            ),
            _response(
                AssistantMessage(
                    (ToolCall("load-call", "toolbox.load", {"tool_names": [target.name]}),),
                    stop_reason="tool_use",
                )
            ),
            _response(
                AssistantMessage(
                    (ToolCall("target-call", target.name, {}),),
                    stop_reason="tool_use",
                )
            ),
            _response(AssistantMessage((TextContent("done"),))),
        )
    )
    repository = LinearSessionRepository(tmp_path / "agent.sqlite3")
    session = repository.create(
        system_prompt="cache-stable-prefix",
        active_tools=("toolbox.status", "toolbox.load"),
    )
    harness = AgentHarness(
        provider,
        ModelProfile("fake", "fake", "http://fake"),
        repository,
        session.id,
        tool_catalog=catalog,
        active_tool_names=session.active_tools,
    )
    harness_box.append(harness)

    await harness.prompt("split it")

    request_names = [
        tuple(item["function"]["name"] for item in request.tools)
        for request in provider.requests
    ]
    assert request_names == [
        ("toolbox.status", "toolbox.load"),
        ("toolbox.status", "toolbox.load"),
        ("toolbox.status", "toolbox.load", "image.split_grid"),
        ("toolbox.status", "toolbox.load", "image.split_grid"),
    ]
    assert all(request.messages[0].content == "cache-stable-prefix" for request in provider.requests)
    assert not any(
        message.role == "system" and "image.split_grid" in str(message.content)
        for request in provider.requests
        for message in request.messages
    )
    assert target.calls == 1
    assert repository.open(session.id).active_tools == (
        "toolbox.status",
        "toolbox.load",
        "image.split_grid",
    )
    status_result = repository.open(session.id).messages[2]
    assert status_result.role == "tool_result"
    assert status_result.result.details["matches"][0]["name"] == "image.split_grid"
