from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from aipic_to_model.agent.core.events import CancellationToken
from aipic_to_model.agent.providers.radius import (
    RadiusCatalogStore,
    RadiusModelDiscovery,
    normalize_radius_gateway_url,
    parse_radius_gateway_config,
)


def _config() -> dict[str, object]:
    return {
        "baseUrl": "https://radius.test/",
        "models": [
            {
                "id": "radius-model",
                "name": "Radius model",
                "reasoning": True,
                "input": ["text", "image"],
                "contextWindow": 1000,
                "maxTokens": 100,
                "cost": {"input": 1},
            }
        ],
    }


def test_radius_config_normalizes_and_rejects_duplicate_or_credentialed_origins() -> None:
    assert normalize_radius_gateway_url("radius.test/") == "https://radius.test"
    with pytest.raises(ValueError, match="credentials"):
        normalize_radius_gateway_url("https://key@radius.test")
    duplicate = _config()
    model = _config()["models"]
    assert isinstance(model, list)
    duplicate["models"] = [model[0], model[0]]
    with pytest.raises(ValueError, match="duplicate"):
        parse_radius_gateway_config(duplicate)


@pytest.mark.asyncio
async def test_radius_discovery_persists_validated_catalog_without_persisting_auth(
    tmp_path: Path,
) -> None:
    seen_headers: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(request.headers)
        assert request.url.path == "/v1/config"
        return httpx.Response(200, json=_config())

    store = RadiusCatalogStore(tmp_path / "radius.json")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await RadiusModelDiscovery(store, client=client).refresh(
        "https://radius.test", "secret-value", CancellationToken()
    )
    await client.aclose()

    assert result.models[0].model_id == "radius-model"
    assert RadiusModelDiscovery(store).cached_models()[0].model == "radius-model"
    assert seen_headers["authorization"] == "Bearer secret-value"
    assert "secret-value" not in (tmp_path / "radius.json").read_text(encoding="utf-8")
