from __future__ import annotations

from aipic_to_model.application.b02_runtime import _collect_input_asset_ids


def test_collect_input_asset_ids_recurses_and_deduplicates() -> None:
    assert _collect_input_asset_ids(
        {
            "source_asset_id": "source",
            "prompt": {
                "prompt_asset_id": "prompt",
                "reference_asset_ids": ["reference-a", "reference-b", "reference-a"],
            },
            "provider_profile": "meshy/default",
            "unrelated_id": "not-an-asset",
        }
    ) == ["source", "prompt", "reference-a", "reference-b"]
