"""VAL-B02-05 contract coverage for both frozen image channels."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aipic_to_model.domain.provider_models import GenerationRequest
from aipic_to_model.infrastructure.providers.image_payloads import banana_payload, gpt_image_payload


def _request(channel: str, mode: str, count: int) -> GenerationRequest:
    return GenerationRequest(
        prompt_asset_id="prompt-1",
        source_asset_id="source-1" if mode == "i2i" else None,
        provider_profile="test",
        channel=channel,
        mode=mode,
        model="fake-model",
        candidate_count=count,
        size="1024x1024",
        structure_strength=0.8,
    )


@pytest.mark.parametrize("channel", ["banana", "gpt_image"])
@pytest.mark.parametrize("mode", ["t2i", "i2i"])
@pytest.mark.parametrize("count", [1, 2, 4, 8])
def test_generation_payloads_accept_only_supported_candidate_counts(
    channel: str, mode: str, count: int
) -> None:
    request = _request(channel, mode, count)
    builder = banana_payload if channel == "banana" else gpt_image_payload
    payload = builder(
        request, prompt="robot", **({"remote_input_id": "input-1"} if mode == "i2i" else {})
    )
    assert payload["n"] == count


@pytest.mark.parametrize("count", [0, 9])
def test_generation_request_rejects_unsupported_candidate_counts_before_provider_use(
    count: int,
) -> None:
    with pytest.raises(ValidationError):
        _request("banana", "t2i", count)
