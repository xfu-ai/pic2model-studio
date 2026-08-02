from __future__ import annotations

from aipic_to_model.agent.providers.catalog import CHAT_PROVIDER_IDS, frozen_descriptors
from aipic_to_model.agent.providers.model_catalog import CATALOG_SHA256, load_frozen_catalog
from aipic_to_model.agent.providers.registry import create_frozen_provider_registry


def test_frozen_catalog_is_hashed_parseable_and_matches_descriptors() -> None:
    catalog = load_frozen_catalog()
    assert catalog.content_hash == CATALOG_SHA256
    assert len(catalog.models) == 1110
    assert {model.provider_id for model in catalog.models} == set(CHAT_PROVIDER_IDS) - {"radius"}
    assert all(model.context_window > 0 and model.max_output_tokens > 0 for model in catalog.models)


def test_every_frozen_descriptor_is_importable_without_a_credential() -> None:
    registry = create_frozen_provider_registry(lambda _ref: None)
    assert {item.provider_id for item in frozen_descriptors()} == set(registry.ids())
