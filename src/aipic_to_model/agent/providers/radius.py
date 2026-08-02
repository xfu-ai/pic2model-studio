"""Persisted, validated runtime model discovery for Radius gateways.

The Radius catalog is deliberately separate from the frozen Pi model catalog:
it is gateway-owned, may change at runtime, and never contains credentials.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from ..core.events import CancellationToken
from .base import ModelCapabilities, ModelProfile


@dataclass(frozen=True)
class RadiusGatewayModel:
    model_id: str
    name: str
    reasoning: bool
    input_modalities: tuple[str, ...]
    context_window: int
    max_output_tokens: int
    cost: dict[str, float]


@dataclass(frozen=True)
class RadiusGatewayConfig:
    base_url: str
    models: tuple[RadiusGatewayModel, ...]


def normalize_radius_gateway_url(value: str) -> str:
    candidate = value if value.startswith(("https://", "http://")) else f"https://{value}"
    parsed = urlsplit(candidate)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Radius gateway URL must be an HTTP(S) origin without user credentials.")
    if parsed.query or parsed.fragment:
        raise ValueError("Radius gateway URL must not include a query or fragment.")
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"


def parse_radius_gateway_config(value: object) -> RadiusGatewayConfig:
    if not isinstance(value, Mapping):
        raise TypeError("Radius gateway configuration must be an object.")
    base_url = value.get("baseUrl")
    models = value.get("models")
    if not isinstance(base_url, str) or not isinstance(models, list):
        raise TypeError("Radius gateway configuration has invalid required fields.")
    parsed_models = tuple(_parse_model(item) for item in models)
    seen = [item.model_id for item in parsed_models]
    if len(seen) != len(set(seen)):
        raise ValueError("Radius gateway configuration has duplicate model IDs.")
    return RadiusGatewayConfig(normalize_radius_gateway_url(base_url), parsed_models)


class RadiusCatalogStore:
    """A credential-free JSON cache with atomic replacement."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> RadiusGatewayConfig | None:
        if not self._path.is_file():
            return None
        try:
            return parse_radius_gateway_config(json.loads(self._path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise ValueError("Stored Radius gateway configuration is invalid.") from error

    def save(self, config: RadiusGatewayConfig) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                _config_dict(config), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ),
            encoding="utf-8",
        )
        temporary.replace(self._path)


class RadiusModelDiscovery:
    def __init__(
        self, store: RadiusCatalogStore, *, client: httpx.AsyncClient | None = None
    ) -> None:
        self._store = store
        self._client = client

    async def refresh(
        self, gateway_url: str, api_key: str | None, cancellation: CancellationToken
    ) -> RadiusGatewayConfig:
        gateway = normalize_radius_gateway_url(gateway_url)
        headers = {"accept": "application/json"}
        if api_key:
            headers["authorization"] = f"Bearer {api_key}"
        client = self._client or httpx.AsyncClient(timeout=20)
        owns_client = self._client is None
        try:
            response = await cancellation.wait_for(
                client.get(f"{gateway}/v1/config", headers=headers)
            )
            if not response.is_success:
                raise ValueError(
                    f"Radius gateway configuration request failed ({response.status_code})."
                )
            config = parse_radius_gateway_config(response.json())
            self._store.save(config)
            return config
        finally:
            if owns_client:
                await client.aclose()

    def cached_models(
        self, credential_ref: str = "agent/radius/default"
    ) -> tuple[ModelProfile, ...]:
        config = self._store.load()
        if config is None:
            return ()
        return tuple(
            ModelProfile("radius", model.model_id, config.base_url, credential_ref=credential_ref)
            for model in config.models
        )


def radius_capabilities(model: RadiusGatewayModel) -> ModelCapabilities:
    return ModelCapabilities(
        context_window=model.context_window,
        max_output_tokens=model.max_output_tokens,
        input_modalities=model.input_modalities,
        tool_calling=True,
        reasoning=model.reasoning,
        transport=("sse",),
    )


def _parse_model(value: object) -> RadiusGatewayModel:
    if not isinstance(value, Mapping):
        raise TypeError("Radius gateway model must be an object.")
    model_id, name = value.get("id"), value.get("name")
    inputs = value.get("input")
    context, maximum = value.get("contextWindow"), value.get("maxTokens")
    cost = value.get("cost")
    if (
        not isinstance(model_id, str)
        or not model_id
        or not isinstance(name, str)
        or not isinstance(inputs, list)
        or not all(item in {"text", "image"} for item in inputs)
        or not isinstance(context, int)
        or context <= 0
        or not isinstance(maximum, int)
        or maximum <= 0
        or not isinstance(cost, Mapping)
    ):
        raise ValueError("Radius gateway model has invalid fields.")
    parsed_cost = {
        str(key): float(item) for key, item in cost.items() if isinstance(item, int | float)
    }
    return RadiusGatewayModel(
        model_id,
        name,
        bool(value.get("reasoning", False)),
        tuple(inputs),
        context,
        maximum,
        parsed_cost,
    )


def _config_dict(config: RadiusGatewayConfig) -> dict[str, object]:
    return {
        "baseUrl": config.base_url,
        "models": [
            {
                "id": model.model_id,
                "name": model.name,
                "reasoning": model.reasoning,
                "input": list(model.input_modalities),
                "contextWindow": model.context_window,
                "maxTokens": model.max_output_tokens,
                "cost": model.cost,
            }
            for model in config.models
        ],
    }
