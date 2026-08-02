from __future__ import annotations

import base64
import os
from io import BytesIO

import pytest
from PIL import Image

from aipic_to_model.infrastructure.keyring_store import OSKeyringStore
from aipic_to_model.infrastructure.providers.config import (
    MESHY_PROFILE,
    CredentialResolver,
    MeshyImageSettings,
)
from aipic_to_model.infrastructure.providers.meshy_image import MeshyTextToImageProvider


@pytest.mark.real_provider
def test_meshy_minimal_t2i_returns_one_valid_image() -> None:
    credentials = CredentialResolver(OSKeyringStore())
    assert credentials.get(MESHY_PROFILE), "meshy/default is not configured"
    settings = MeshyImageSettings(
        base_url=os.environ.get("MESHY_BASE_URL", "https://api.meshy.ai"),
        allowed_image_hosts=frozenset(
            item.strip().lower()
            for item in os.environ.get("MESHY_IMAGE_HOSTS", "assets.meshy.ai").split(",")
            if item.strip()
        ),
    )
    provider = MeshyTextToImageProvider(
        settings,
        credentials.callback(MESHY_PROFILE),
    )

    result = provider.generate(
        {
            "provider_profile": MESHY_PROFILE,
            "channel": "meshy",
            "mode": "t2i",
            "model": os.environ.get("MESHY_IMAGE_MODEL", "nano-banana"),
            "candidate_count": 1,
            "aspect_ratio": "1:1",
            "prompt": (
                "A single matte gray toy cube centered on a plain white background, "
                "minimal product reference, no text."
            ),
        }
    )

    assert result.ok, result.error.code if result.error else "invalid Provider response"
    assert result.provider_request_id
    images = result.payload.get("images")
    assert isinstance(images, list) and len(images) == 1
    content = base64.b64decode(str(images[0]["base64"]), validate=True)
    with Image.open(BytesIO(content)) as image:
        image.verify()
