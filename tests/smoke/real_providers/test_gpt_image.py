from __future__ import annotations

import base64
from io import BytesIO

import pytest
from PIL import Image

from aipic_to_model.infrastructure.keyring_store import OSKeyringStore
from aipic_to_model.infrastructure.providers.config import (
    OPENAI_PROFILE,
    CredentialResolver,
    load_openai_public_settings,
)
from aipic_to_model.infrastructure.providers.openai_compatible import (
    OpenAICompatibleImageProvider,
)


@pytest.mark.real_provider
def test_gpt_image_minimal_t2i_returns_one_valid_image() -> None:
    store = OSKeyringStore()
    credentials = CredentialResolver(store)
    assert credentials.get(OPENAI_PROFILE), "openai/default is not configured"
    settings = load_openai_public_settings()
    provider = OpenAICompatibleImageProvider(
        settings,
        credentials.callback(OPENAI_PROFILE),
    )
    result = provider.generate(
        {
            "prompt_asset_id": "smoke-prompt",
            "provider_profile": OPENAI_PROFILE,
            "channel": "gpt_image",
            "mode": "t2i",
            "model": settings.image_model,
            "candidate_count": 1,
            "size": "1024x1024",
            "quality": "low",
            "output_format": "png",
            "prompt": (
                "A single matte gray toy cube centered on a plain white background, "
                "orthographic product reference, no text."
            ),
        }
    )
    assert result.ok, result.error.code if result.error else "invalid Provider response"
    assert result.provider_request_id
    images = result.payload.get("images")
    assert isinstance(images, list) and len(images) == 1
    for item in images:
        assert isinstance(item, dict)
        content = base64.b64decode(str(item["base64"]), validate=True)
        with Image.open(BytesIO(content)) as image:
            image.verify()
