from __future__ import annotations

import json
from pathlib import Path

import pytest

from aipic_to_model.infrastructure.keyring_store import OSKeyringStore
from aipic_to_model.infrastructure.providers.config import NANOBANANA_PROFILE


@pytest.mark.real_provider
def test_nanobanana_browser_profile_is_ready() -> None:
    root = Path(__file__).parents[3]
    config = json.loads((root / ".local" / "nanobanana.local.json").read_text("utf-8-sig"))
    assert config["protocol"] == "browser_ui"
    assert config["credential_ref"] == NANOBANANA_PROFILE
    assert config["image_count"] == 1
    assert OSKeyringStore().get(NANOBANANA_PROFILE)
