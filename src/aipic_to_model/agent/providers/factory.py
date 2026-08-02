"""Provider factories driven entirely by descriptors and frozen model metadata."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from ..core.events import CancellationToken
from ..core.models import ProviderEvent
from .api.adapter_provider import AdapterProvider
from .api.bedrock_converse_stream import BedrockConverseStreamProvider
from .api.google_generative_ai import GoogleGenerativeAIProvider
from .api.google_vertex import GoogleVertexProvider
from .api.openai_completions import OpenAICompletionsProvider
from .api.openrouter_images import OpenRouterImagesProvider
from .auth import ProviderAuthResolver
from .base import AgentModelProvider, ModelProfile, ModelRequest
from .catalog import ProviderDescriptor, frozen_descriptors
from .model_catalog import FrozenModelCatalog, load_frozen_catalog

OPENAI_COMPATIBLE_IDS = frozenset(
    descriptor.provider_id
    for descriptor in frozen_descriptors()
    if descriptor.adapter_id == "openai-completions"
)


class DescriptorProvider:
    """Routes a model request to the adapter recorded in the frozen catalog."""

    def __init__(
        self,
        descriptor: ProviderDescriptor,
        resolve: Callable[[str], str | None],
        catalog: FrozenModelCatalog | None = None,
    ) -> None:
        self._descriptor = descriptor
        self._catalog = catalog or load_frozen_catalog()
        self._providers: dict[str, AgentModelProvider] = {
            adapter_id: _provider(adapter_id, resolve) for adapter_id in descriptor.adapter_ids
        }

    async def stream(
        self, request: ModelRequest, cancellation: CancellationToken
    ) -> AsyncIterator[ProviderEvent]:
        adapter_id = self.adapter_id_for_model(request.profile.model)
        provider = self._providers.get(adapter_id)
        if provider is None:
            raise ValueError(
                f"Model {request.profile.provider_id}/{request.profile.model} requires an unregistered adapter."
            )
        async for event in provider.stream(request, cancellation):
            yield event

    def adapter_id_for_model(self, model_id: str) -> str:
        for model in self._catalog.for_provider(self._descriptor.provider_id):
            if model.model_id == model_id:
                return model.api
        return self._descriptor.adapter_id


def create_provider(
    descriptor: ProviderDescriptor,
    resolve: Callable[[str], str | None],
    catalog: FrozenModelCatalog | None = None,
) -> AgentModelProvider:
    return DescriptorProvider(descriptor, resolve, catalog)


def create_authenticated_provider(
    descriptor: ProviderDescriptor,
    auth: ProviderAuthResolver,
    catalog: FrozenModelCatalog | None = None,
) -> AgentModelProvider:
    """Create a provider that resolves keyring/environment credentials per request."""

    return create_provider(
        descriptor,
        lambda _ref: resolved.api_key if (resolved := auth.resolve(descriptor)) else None,
        catalog,
    )


def model_profile_for_descriptor(
    descriptor: ProviderDescriptor,
    model_id: str,
    auth: ProviderAuthResolver,
    catalog: FrozenModelCatalog | None = None,
) -> ModelProfile:
    """Materialize descriptor/model/auth metadata without exposing credentials."""

    frozen = catalog or load_frozen_catalog()
    model = next(
        (item for item in frozen.for_provider(descriptor.provider_id) if item.model_id == model_id),
        None,
    )
    if model is None and not descriptor.dynamic_models:
        raise ValueError(f"Unknown frozen model: {descriptor.provider_id}/{model_id}")
    resolved = auth.resolve(descriptor)
    headers = dict(descriptor.default_headers)
    if resolved and resolved.headers:
        headers.update(resolved.headers)
    base_url = (resolved.base_url if resolved and resolved.base_url else None) or (
        model.base_url if model else descriptor.base_url
    )
    return ModelProfile(
        descriptor.provider_id,
        model_id,
        base_url,
        credential_ref=descriptor.credential_ref,
        max_output_tokens=model.max_output_tokens if model else None,
        headers=headers,
    )


def _provider(adapter_id: str, resolve: Callable[[str], str | None]) -> AgentModelProvider:
    if adapter_id == "openai-completions":
        return OpenAICompletionsProvider(resolve)
    if adapter_id == "bedrock-converse-stream":
        return BedrockConverseStreamProvider(resolve)
    if adapter_id == "openrouter-images":
        return OpenRouterImagesProvider(resolve)
    if adapter_id == "google-vertex":
        return GoogleVertexProvider(resolve)
    if adapter_id == "google-generative-ai":
        return GoogleGenerativeAIProvider(resolve)
    return AdapterProvider(adapter_id, resolve)
