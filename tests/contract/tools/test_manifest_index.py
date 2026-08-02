"""Frozen B02 Tool schema artifacts are consumable without importing services."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from aipic_to_model.application.b02_tool_catalog import B02_TOOLS, _input_schema, _output_schema

ROOT = Path(__file__).parents[3]


def test_b02_manifest_index_has_one_closed_schema_artifact_per_new_tool() -> None:
    index = json.loads((ROOT / "contracts" / "tools" / "manifest-index.json").read_text("utf-8"))
    indexed = {(item["name"], item["version"]): item for item in index["tools"]}
    for name, risk, execution, requires_approval, capability in B02_TOOLS:
        entry = indexed[(name, "1.0.0")]
        artifact = ROOT / "contracts" / "tools" / f"{name}@1.0.0.schema.json"
        payload = json.loads(artifact.read_text("utf-8"))
        assert payload["schema_version"] == 1
        assert payload["name"] == name
        assert payload["execution"] == entry["execution"] == execution
        assert payload["risk_level"] == entry["risk_level"] == risk.value
        assert payload["requires_approval"] is requires_approval
        assert payload["capability"] == capability
        assert payload["input_schema"] == _input_schema(name)
        assert payload["output_schema"] == _output_schema()
        Draft202012Validator.check_schema(payload["input_schema"])
        Draft202012Validator.check_schema(payload["output_schema"])


def test_external_paid_manifest_approval_requirements_are_frozen() -> None:
    paid = {
        name
        for name, risk, _, requires_approval, _ in B02_TOOLS
        if risk.value == "external_paid" and requires_approval
    }
    assert paid == {
        "image.generate",
        "image.transform",
        "image.generate_variants",
        "element.split",
        "multiview.generate",
        "multiview.regenerate_view",
        "model3d.generate",
    }
