from __future__ import annotations

import pytest

from aipic_to_model.agent.providers.catalog import (
    ADAPTER_IDS,
    CHAT_PROVIDER_IDS,
    ProviderDescriptor,
    frozen_descriptors,
    validate_descriptors,
)
from aipic_to_model.agent.providers.registry import DescriptorRegistry


def test_frozen_provider_inventory_has_unique_39_descriptors_and_11_adapters() -> None:
    descriptors = frozen_descriptors()
    assert len(CHAT_PROVIDER_IDS) == 38
    assert len(descriptors) == 39
    assert len({item.provider_id for item in descriptors}) == 39
    assert len(ADAPTER_IDS) == 11
    assert all(item.adapter_id in ADAPTER_IDS for item in descriptors)


def test_descriptor_validation_rejects_duplicate_and_unknown_adapter() -> None:
    with pytest.raises(ValueError, match="unique"):
        validate_descriptors(
            (
                ProviderDescriptor("same", "A", "", "openai-completions"),
                ProviderDescriptor("same", "B", "", "openai-completions"),
            )
        )
    with pytest.raises(ValueError, match="unknown adapter"):
        validate_descriptors((ProviderDescriptor("one", "One", "", "unknown"),))


def test_descriptor_registry_exposes_frozen_provider_metadata() -> None:
    registry = DescriptorRegistry(frozen_descriptors())
    assert registry.get("deepseek").adapter_id == "openai-completions"
    assert len(registry.all()) == 39
