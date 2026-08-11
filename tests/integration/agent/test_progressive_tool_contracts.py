from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from aipic_to_model.agent.core.events import CancellationToken
from aipic_to_model.agent.core.tool import ToolContext
from aipic_to_model.agent.integrations.aipic_tools import AIPicToolInvocation
from aipic_to_model.agent.integrations.facade_tools import _FacadeDispatcher
from aipic_to_model.agent.integrations.progressive_tools import (
    OPERATION_TOOL_SPECS,
    AIPicOperationTool,
)
from aipic_to_model.application.b02_tool_catalog import B02_TOOLS, _input_schema
from aipic_to_model.domain.tools import ToolResultV1


def _atomic_schemas() -> dict[str, dict[str, object]]:
    root = Path(__file__).parents[3]
    schemas = {
        item[0]: _input_schema(item[0])
        for item in B02_TOOLS
    }
    for path in (root / "src/aipic_to_model/application/tool_manifests").glob("*.json"):
        payload = json.loads(path.read_text("utf-8"))
        schemas[str(payload["name"])] = payload["input_schema"]
    return schemas


def _example(schema: dict[str, Any], path: str = "value") -> Any:
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    value_type = schema.get("type")
    if value_type == "string":
        return f"fixture-{path}"
    if value_type == "integer":
        return max(1, int(schema.get("minimum", 1)))
    if value_type == "number":
        return max(0.5, float(schema.get("minimum", 0)))
    if value_type == "boolean":
        return False
    if value_type == "array":
        count = max(1, int(schema.get("minItems", 1)))
        item_schema = schema.get("items", {"type": "string"})
        return [_example(item_schema, f"{path}-{index}") for index in range(count)]
    if value_type == "object" or "properties" in schema:
        properties = schema.get("properties", {})
        required = list(schema.get("required", []))
        one_of = schema.get("oneOf", [])
        if one_of:
            required.extend(one_of[0].get("required", []))
        return {
            name: _example(properties[name], f"{path}-{name}")
            for name in dict.fromkeys(required)
        }
    raise AssertionError(f"Cannot create example for schema at {path}: {schema}")


class SchemaCheckingRegistry:
    def __init__(self) -> None:
        self.schemas = _atomic_schemas()
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, *args: Any) -> ToolResultV1:
        name = str(args[2])
        arguments = dict(args[4])
        Draft202012Validator(self.schemas[name]).validate(arguments)
        self.calls.append((name, arguments))
        if name == "asset.get_metadata":
            asset_id = str(arguments["asset_id"])
            return ToolResultV1(
                True,
                "succeeded",
                "metadata-call",
                [asset_id],
                json.dumps(
                    {
                        "asset": {
                            "id": asset_id,
                            "asset_type": "source_image",
                            "mime_type": "image/png",
                        },
                        "lineage": [],
                    }
                ),
                [],
            )
        return ToolResultV1(
            True,
            "succeeded",
            "atomic-call",
            ["output-asset"],
            "Operation succeeded.",
            [],
        )


@pytest.mark.agent
@pytest.mark.asyncio
async def test_every_progressive_operation_translates_to_a_valid_atomic_call(
    tmp_path: Path,
) -> None:
    registry = SchemaCheckingRegistry()
    invocation = lambda: AIPicToolInvocation(
        tmp_path,
        "project-host-bound",
        "request",
        run_id="conversation",
    )
    dispatcher = _FacadeDispatcher(
        registry,  # type: ignore[arg-type]
        invocation,
        runtime_context=lambda: {"schema_version": 1, "capabilities": {}},
        prompt_creator=lambda *_args: "materialized-prompt",
    )

    for spec in OPERATION_TOOL_SPECS:
        arguments = _example(dict(spec.parameters), spec.name)
        Draft202012Validator(dict(spec.parameters)).validate(arguments)
        result = await AIPicOperationTool(dispatcher, spec).execute(
            f"call-{spec.name}",
            arguments,
            ToolContext(()),
            CancellationToken(),
        )
        assert not result.is_error, spec.name

    # runtime.get_capabilities is host-only; multiview and single-image 3D each
    # add one read-only asset-type validation before their atomic generation call.
    assert len(registry.calls) == len(OPERATION_TOOL_SPECS) - 1 + 2
    assert {name for name, _arguments in registry.calls} <= set(registry.schemas)
