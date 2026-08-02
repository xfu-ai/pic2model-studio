from __future__ import annotations

from aipic_to_model.agent.providers.adapters import ADAPTERS
from aipic_to_model.agent.providers.catalog import ADAPTER_IDS, frozen_descriptors


def test_every_frozen_adapter_and_descriptor_is_contractually_registered() -> None:
    assert set(ADAPTERS) == set(ADAPTER_IDS)
    assert all(descriptor.adapter_id in ADAPTERS for descriptor in frozen_descriptors())
