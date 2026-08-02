from __future__ import annotations

from aipic_to_model.agent.providers.auth import CredentialStore, ProviderAuthResolver
from aipic_to_model.agent.providers.catalog import frozen_descriptors
from aipic_to_model.agent.providers.factory import (
    DescriptorProvider,
    create_authenticated_provider,
    create_provider,
    model_profile_for_descriptor,
)
from aipic_to_model.agent.providers.model_catalog import load_frozen_catalog


class Store(CredentialStore):
    def get(self, key: str) -> str | None:
        return None

    def set(self, key: str, value: str) -> None:
        del key, value

    def delete(self, key: str) -> None:
        del key


def test_all_descriptors_construct_without_credentials() -> None:
    descriptors = {item.provider_id: item for item in frozen_descriptors()}
    for provider_id, descriptor in descriptors.items():
        provider = create_provider(descriptor, lambda _ref: None)
        assert isinstance(provider, DescriptorProvider), provider_id


def test_catalog_models_route_only_to_descriptor_registered_adapters() -> None:
    catalog = load_frozen_catalog()
    descriptors = {item.provider_id: item for item in frozen_descriptors()}
    for model in catalog.models:
        provider = create_provider(descriptors[model.provider_id], lambda _ref: None, catalog)
        assert isinstance(provider, DescriptorProvider)
        assert (
            provider.adapter_id_for_model(model.model_id)
            in descriptors[model.provider_id].adapter_ids
        )


def test_descriptor_auth_builds_runtime_profile_without_copying_provider_branches() -> None:
    descriptors = {item.provider_id: item for item in frozen_descriptors()}
    auth = ProviderAuthResolver(
        Store(),
        {
            "CLOUDFLARE_API_KEY": "key",
            "CLOUDFLARE_ACCOUNT_ID": "account",
            "CLOUDFLARE_GATEWAY_ID": "gateway",
        },
    )
    profile = model_profile_for_descriptor(
        descriptors["cloudflare-ai-gateway"],
        load_frozen_catalog().for_provider("cloudflare-ai-gateway")[0].model_id,
        auth,
    )
    provider = create_authenticated_provider(descriptors["cloudflare-ai-gateway"], auth)
    assert profile.headers["cf-aig-authorization"] == "Bearer key"
    assert profile.headers["cf-account-id"] == "account"
    assert isinstance(provider, DescriptorProvider)
