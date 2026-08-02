"""Provider protocols, test doubles, and transport adapters."""

from .base import AgentModelProvider, ModelCapabilities, ModelProfile, ModelRequest
from .catalog import (
    ADAPTER_IDS,
    CHAT_PROVIDER_IDS,
    FROZEN_PI_COMMIT,
    ProviderDescriptor,
    frozen_descriptors,
)
from .deepseek import create_deepseek_credential_resolver, create_deepseek_profile
from .factory import create_authenticated_provider, model_profile_for_descriptor
from .fake import FakeProvider, ScriptedResponse
from .model_catalog import CatalogModel, FrozenModelCatalog, load_frozen_catalog
from .radius import (
    RadiusCatalogStore,
    RadiusGatewayConfig,
    RadiusGatewayModel,
    RadiusModelDiscovery,
    normalize_radius_gateway_url,
    parse_radius_gateway_config,
    radius_capabilities,
)
from .registry import (
    CredentialResolver,
    DescriptorRegistry,
    ModelCatalog,
    ProviderRegistry,
    create_frozen_provider_registry,
)

__all__ = [
    "ADAPTER_IDS",
    "CHAT_PROVIDER_IDS",
    "FROZEN_PI_COMMIT",
    "AgentModelProvider",
    "CatalogModel",
    "CredentialResolver",
    "DescriptorRegistry",
    "FakeProvider",
    "FrozenModelCatalog",
    "ModelCapabilities",
    "ModelCatalog",
    "ModelProfile",
    "ModelRequest",
    "ProviderDescriptor",
    "ProviderRegistry",
    "RadiusCatalogStore",
    "RadiusGatewayConfig",
    "RadiusGatewayModel",
    "RadiusModelDiscovery",
    "ScriptedResponse",
    "create_authenticated_provider",
    "create_deepseek_credential_resolver",
    "create_deepseek_profile",
    "create_frozen_provider_registry",
    "frozen_descriptors",
    "load_frozen_catalog",
    "model_profile_for_descriptor",
    "normalize_radius_gateway_url",
    "parse_radius_gateway_config",
    "radius_capabilities",
]
