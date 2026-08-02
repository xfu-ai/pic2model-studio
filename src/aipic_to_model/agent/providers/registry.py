from __future__ import annotations

from collections.abc import Callable

from ..core.errors import ProviderError
from .base import AgentModelProvider, ModelCapabilities
from .catalog import ProviderDescriptor, frozen_descriptors, validate_descriptors
from .factory import create_provider


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, AgentModelProvider] = {}

    def register(self, provider_id: str, provider: AgentModelProvider) -> None:
        if provider_id in self._providers:
            raise ValueError(f"Duplicate provider id: {provider_id}")
        self._providers[provider_id] = provider

    def get(self, provider_id: str) -> AgentModelProvider:
        try:
            return self._providers[provider_id]
        except KeyError as error:
            raise ProviderError(f"Unknown provider: {provider_id}") from error

    def ids(self) -> tuple[str, ...]:
        return tuple(self._providers)


class ModelCatalog:
    def __init__(self) -> None:
        self._models: dict[tuple[str, str], ModelCapabilities] = {}

    def register(self, provider_id: str, model: str, capabilities: ModelCapabilities) -> None:
        key = (provider_id, model)
        if key in self._models:
            raise ValueError(f"Duplicate model: {provider_id}/{model}")
        self._models[key] = capabilities

    def get(self, provider_id: str, model: str) -> ModelCapabilities:
        try:
            return self._models[(provider_id, model)]
        except KeyError as error:
            raise ProviderError(f"Unknown model: {provider_id}/{model}") from error


class DescriptorRegistry:
    """Frozen provider metadata registry, independent of live transports."""

    def __init__(self, descriptors: tuple[ProviderDescriptor, ...] = ()) -> None:
        validate_descriptors(descriptors)
        self._descriptors = {item.provider_id: item for item in descriptors}

    def get(self, provider_id: str) -> ProviderDescriptor:
        try:
            return self._descriptors[provider_id]
        except KeyError as error:
            raise ProviderError(f"Unknown provider descriptor: {provider_id}") from error

    def all(self) -> tuple[ProviderDescriptor, ...]:
        return tuple(self._descriptors.values())


def create_frozen_provider_registry(
    resolve: Callable[[str], str | None],
    descriptors: tuple[ProviderDescriptor, ...] | None = None,
) -> ProviderRegistry:
    """Instantiate every frozen descriptor for import/packaging smoke tests."""

    registry = ProviderRegistry()
    for descriptor in descriptors or frozen_descriptors():
        registry.register(descriptor.provider_id, create_provider(descriptor, resolve))
    return registry


CredentialResolver = Callable[[str], str | None]
