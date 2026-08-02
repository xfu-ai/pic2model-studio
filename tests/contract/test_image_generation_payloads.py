from __future__ import annotations

from aipic_to_model.domain.provider_models import GenerationRequest
from aipic_to_model.infrastructure.providers.image_payloads import (
    banana_payload,
    gpt_image_payload,
    structure_strength_directive,
)


def _request(channel: str, mode: str, strength: float = 0.85) -> GenerationRequest:
    return GenerationRequest(
        prompt_asset_id="prompt-1",
        source_asset_id="asset-1" if mode == "i2i" else None,
        provider_profile="test",
        channel=channel,
        mode=mode,
        model="not-allowed",
        candidate_count=2,
        aspect_ratio="1:1",
        size="1024x1024",
        quality="high",
        output_format="png",
        structure_strength=strength,
    )


def test_banana_image_to_image_uses_prompt_directive_not_unsupported_strength_field() -> None:
    payload = banana_payload(
        _request("banana", "i2i", 0.95), prompt="robot", remote_input_id="file-1"
    )
    assert "structure_strength" not in str(payload)
    assert payload["messages"][0]["content"][0]["text"].startswith("Structure lock:")
    assert payload["messages"][0]["content"][1] == {"type": "provider_file", "file_id": "file-1"}


def test_gpt_payload_normalizes_model_and_requires_managed_input_id() -> None:
    payload = gpt_image_payload(_request("gpt_image", "t2i"), prompt="robot")
    assert payload["model"] == "gpt-image-2-r1"
    edit = gpt_image_payload(_request("gpt_image", "i2i"), prompt="robot", remote_input_id="file-1")
    assert edit["image_file_id"] == "file-1"
    assert structure_strength_directive(0.0).startswith("Exploratory variation:")
