import pytest

from aipic_to_model.agent.core.errors import ProviderError
from aipic_to_model.agent.providers.base import ModelCapabilities
from aipic_to_model.agent.providers.fake import FakeProvider
from aipic_to_model.agent.providers.registry import ModelCatalog, ProviderRegistry


@pytest.mark.agent
def test_registry_and_catalog_reject_duplicates_and_unknown_entries() -> None:
    registry = ProviderRegistry()
    provider = FakeProvider(())
    registry.register("fake", provider)
    assert registry.get("fake") is provider
    with pytest.raises(ValueError, match="Duplicate"):
        registry.register("fake", provider)
    with pytest.raises(ProviderError, match="Unknown provider"):
        registry.get("missing")

    catalog = ModelCatalog()
    capabilities = ModelCapabilities(context_window=4096, max_output_tokens=512, tool_calling=True)
    catalog.register("fake", "demo", capabilities)
    assert catalog.get("fake", "demo") is capabilities
    with pytest.raises(ValueError, match="Duplicate"):
        catalog.register("fake", "demo", capabilities)
    with pytest.raises(ProviderError, match="Unknown model"):
        catalog.get("fake", "missing")
