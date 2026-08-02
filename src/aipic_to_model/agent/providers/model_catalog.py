"""Read and validate the checked-in frozen Pi model catalog."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

from .catalog import CATALOG_SCHEMA_VERSION, CHAT_PROVIDER_IDS, FROZEN_PI_COMMIT

CATALOG_RESOURCE = "data/frozen_pi_models.json"
CATALOG_SHA256 = "067e2c2cc610e2ba946743716aec545baebb0ac6bdffb087fae68b43af656f53"


@dataclass(frozen=True)
class CatalogModel:
    provider_id: str
    model_id: str
    api: str
    base_url: str
    context_window: int
    max_output_tokens: int
    input_modalities: tuple[str, ...]
    reasoning: bool
    cost: dict[str, float]
    cache: bool
    compatibility: dict[str, object]


@dataclass(frozen=True)
class FrozenModelCatalog:
    schema_version: int
    source_pi_commit: str
    content_hash: str
    models: tuple[CatalogModel, ...]

    def for_provider(self, provider_id: str) -> tuple[CatalogModel, ...]:
        return tuple(model for model in self.models if model.provider_id == provider_id)


def load_frozen_catalog() -> FrozenModelCatalog:
    raw = files(__package__).joinpath(CATALOG_RESOURCE).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != CATALOG_SHA256:
        raise ValueError("Frozen Pi model catalog content hash does not match its manifest.")
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise TypeError("Frozen Pi model catalog must be an object.")
    models: list[CatalogModel] = []
    for provider_id, provider_models in decoded.items():
        if not isinstance(provider_id, str) or not isinstance(provider_models, dict):
            raise TypeError("Frozen Pi model catalog has an invalid provider section.")
        for model_id, value in provider_models.items():
            models.append(_model(provider_id, model_id, value))
    catalog = FrozenModelCatalog(CATALOG_SCHEMA_VERSION, FROZEN_PI_COMMIT, digest, tuple(models))
    validate_frozen_catalog(catalog)
    return catalog


def validate_frozen_catalog(catalog: FrozenModelCatalog) -> None:
    if catalog.schema_version != CATALOG_SCHEMA_VERSION:
        raise ValueError("Unsupported frozen Pi model catalog schema.")
    if catalog.source_pi_commit != FROZEN_PI_COMMIT:
        raise ValueError(
            "Frozen Pi model catalog source commit differs from the descriptor inventory."
        )
    seen: set[tuple[str, str]] = set()
    for model in catalog.models:
        key = (model.provider_id, model.model_id)
        if key in seen:
            raise ValueError("Frozen Pi model catalog has duplicate provider/model entries.")
        seen.add(key)
        if model.provider_id not in CHAT_PROVIDER_IDS:
            raise ValueError("Frozen Pi model catalog contains an unknown provider.")
        if not model.api or model.context_window <= 0 or model.max_output_tokens <= 0:
            raise ValueError("Frozen Pi model catalog has invalid model limits or adapter.")
        if not model.input_modalities:
            raise ValueError("Frozen Pi model catalog model is missing input modalities.")


def _model(provider_id: str, model_id: object, value: object) -> CatalogModel:
    if not isinstance(model_id, str) or not isinstance(value, dict):
        raise TypeError("Frozen Pi model catalog has an invalid model entry.")
    api = _string(value, "api")
    base_url = _string(value, "baseUrl")
    context_window = _positive_int(value, "contextWindow")
    max_output_tokens = _positive_int(value, "maxTokens")
    input_value = value.get("input", [])
    if not isinstance(input_value, list) or not all(isinstance(item, str) for item in input_value):
        raise ValueError("Frozen Pi model catalog has invalid input modalities.")
    cost_value = value.get("cost", {})
    if not isinstance(cost_value, dict):
        raise TypeError("Frozen Pi model catalog has invalid cost metadata.")
    cost = {key: float(item) for key, item in cost_value.items() if isinstance(item, int | float)}
    compatibility = value.get("compat", {})
    if not isinstance(compatibility, dict):
        raise TypeError("Frozen Pi model catalog has invalid compatibility metadata.")
    return CatalogModel(
        provider_id,
        model_id,
        api,
        base_url,
        context_window,
        max_output_tokens,
        tuple(input_value),
        bool(value.get("reasoning", False)),
        cost,
        bool(cost.get("cacheRead", 0) or cost.get("cacheWrite", 0)),
        dict(compatibility),
    )


def _string(value: dict[str, Any], field: str) -> str:
    result = value.get(field)
    if not isinstance(result, str):
        raise TypeError(f"Frozen Pi model catalog field {field} must be a string.")
    return result


def _positive_int(value: dict[str, Any], field: str) -> int:
    result = value.get(field)
    if not isinstance(result, int) or result <= 0:
        raise ValueError(f"Frozen Pi model catalog field {field} must be a positive integer.")
    return result
