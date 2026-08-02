from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from aipic_to_model.agent.core.events import CancellationToken
from aipic_to_model.agent.providers.auth import (
    CredentialStore,
    GitHubCopilotOAuthFlow,
    KeyringCredentialStore,
    OAuthEndpoints,
    OAuthFlow,
    OAuthToken,
    OAuthTokenManager,
    OpenRouterOAuthFlow,
    ProviderAuthResolver,
    RadiusOAuthFlow,
    oauth_flow_for_provider,
    oauth_provider_spec,
    resolve_api_key,
)
from aipic_to_model.agent.providers.catalog import frozen_descriptors


class Store(CredentialStore):
    def __init__(self) -> None:
        self.values = {"ref": "stored"}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


class Keyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service_name: str, username: str) -> str | None:
        return self.values.get((service_name, username))

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.values[(service_name, username)] = password

    def delete_password(self, service_name: str, username: str) -> None:
        self.values.pop((service_name, username), None)


def test_api_key_prefers_environment_without_disclosing_values() -> None:
    store = Store()
    assert resolve_api_key({"KEY": "environment"}, store, "ref", ("KEY",)) == "environment"
    assert resolve_api_key({}, store, "ref", ("KEY",)) == "stored"


def test_keyring_store_round_trips_and_logout_is_idempotent() -> None:
    backend = Keyring()
    store = KeyringCredentialStore("test-service", backend)
    store.set("provider/ref", "opaque-token")
    assert store.get("provider/ref") == "opaque-token"
    store.delete("provider/ref")
    store.delete("provider/ref")
    assert store.get("provider/ref") is None


@pytest.mark.asyncio
async def test_oauth_refresh_is_serialized_and_logout_removes_token() -> None:
    store = Store()
    token = OAuthToken("expired", "refresh", datetime.now(UTC) - timedelta(seconds=1))
    saved: list[OAuthToken] = []
    calls = 0

    async def refresh(_token: OAuthToken) -> OAuthToken:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return OAuthToken("fresh", "refresh", datetime.now(UTC) + timedelta(hours=1))

    manager = OAuthTokenManager(store, "ref", lambda: saved[-1] if saved else token, saved.append)
    assert await asyncio.gather(manager.access_token(refresh), manager.access_token(refresh)) == [
        "fresh",
        "fresh",
    ]
    assert calls == 1
    manager.logout()
    assert store.get("ref") is None


def test_cloud_credential_resolution_requires_all_cloudflare_and_vertex_fields() -> None:
    descriptors = {descriptor.provider_id: descriptor for descriptor in frozen_descriptors()}
    store = Store()
    cloudflare = ProviderAuthResolver(
        store,
        {
            "CLOUDFLARE_API_KEY": "key",
            "CLOUDFLARE_ACCOUNT_ID": "account",
            "CLOUDFLARE_GATEWAY_ID": "gateway",
        },
    ).resolve(descriptors["cloudflare-ai-gateway"])
    assert cloudflare is not None
    assert cloudflare.headers is not None and "cf-aig-authorization" in cloudflare.headers
    vertex = ProviderAuthResolver(
        store,
        {"GOOGLE_APPLICATION_CREDENTIALS": "credential.json", "GOOGLE_CLOUD_PROJECT": "project"},
    ).resolve(descriptors["google-vertex"])
    assert vertex is None
    vertex_with_location = ProviderAuthResolver(
        store,
        {
            "GOOGLE_APPLICATION_CREDENTIALS": "credential.json",
            "GOOGLE_CLOUD_PROJECT": "project",
            "GOOGLE_CLOUD_LOCATION": "us-central1",
        },
    ).resolve(descriptors["google-vertex"])
    assert (
        vertex_with_location is not None and vertex_with_location.source == "Google service account"
    )


def test_unconfigured_providers_are_explicitly_reported_as_not_configured() -> None:
    resolver = ProviderAuthResolver(Store(), {})
    states = {
        item.provider_id: resolver.configuration_status(item) for item in frozen_descriptors()
    }
    assert {item.state for item in states.values()} == {"not_configured"}
    configured = ProviderAuthResolver(
        Store(), {"DEEPSEEK_API_KEY": "configured"}
    ).configuration_status(
        next(item for item in frozen_descriptors() if item.provider_id == "deepseek")
    )
    assert configured.state == "configured" and configured.credential_source == "environment"


@pytest.mark.asyncio
async def test_oauth_refresh_keeps_unrotated_refresh_token_and_device_poll_is_cancellable() -> None:
    requests: list[dict[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        form = dict(httpx.QueryParams(request.content.decode()))
        requests.append(form)
        if form["grant_type"] == "refresh_token":
            return httpx.Response(200, json={"access_token": "fresh", "expires_in": 60})
        return httpx.Response(200, json={"access_token": "device", "refresh_token": "next"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    flow = OAuthFlow(
        OAuthEndpoints("https://auth.test", "https://token.test", "client", ("scope",)), client
    )
    refreshed = await flow.refresh(OAuthToken("old", "keep", datetime.now(UTC)))
    token = await flow.poll_device(
        {"device_code": "code", "interval": 0, "expires_in": 1}, CancellationToken()
    )
    await client.aclose()

    assert refreshed.access_token == "fresh" and refreshed.refresh_token == "keep"
    assert token.access_token == "device" and token.expires_at is None
    assert [item["grant_type"] for item in requests] == [
        "refresh_token",
        "urn:ietf:params:oauth:grant-type:device_code",
    ]


def test_frozen_oauth_provider_specs_cover_every_supported_provider_flow() -> None:
    assert {
        oauth_provider_spec(provider_id).flow
        for provider_id in (
            "anthropic",
            "github-copilot",
            "kimi-coding",
            "openai-codex",
            "openrouter",
            "radius",
            "xai",
        )
    } == {"browser_pkce", "device_code", "browser_callback_key", "gateway_discovery"}
    assert (
        oauth_flow_for_provider("xai")._endpoints.device_url
        == "https://auth.x.ai/oauth2/device/code"
    )
    with pytest.raises(ValueError, match="browser_callback_key"):
        oauth_flow_for_provider("openrouter")


@pytest.mark.asyncio
async def test_openrouter_oauth_exchanges_callback_code_for_a_nonexpiring_key() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/auth/keys"
        assert json.loads(request.content) == {
            "code": "callback-code",
            "code_verifier": "verifier",
            "code_challenge_method": "S256",
        }
        return httpx.Response(200, json={"key": "opaque-key"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    flow = OpenRouterOAuthFlow(client)
    url, verifier = flow.browser_url("http://127.0.0.1:1234/callback")
    token = await flow.exchange_code("callback-code", "verifier")
    await client.aclose()
    assert "callback_url=" in url and verifier
    assert token == OAuthToken("opaque-key", None, None)


@pytest.mark.asyncio
async def test_copilot_exchanges_github_device_credential_for_service_token() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/copilot_internal/v2/token"
        assert request.headers["authorization"] == "Bearer github-token"
        return httpx.Response(200, json={"token": "copilot-token", "expires_at": 9_999_999_999})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    token = await GitHubCopilotOAuthFlow(client).exchange_github_token("github-token")
    await client.aclose()
    assert token.access_token == "copilot-token" and token.refresh_token == "github-token"


@pytest.mark.asyncio
async def test_radius_oauth_discovers_authorization_endpoint_per_gateway() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/oauth"
        return httpx.Response(
            200, json={"authorizationEndpoint": "https://login.radius.test/authorize"}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    url, verifier = await RadiusOAuthFlow("https://radius.test", client).browser_url(
        "http://127.0.0.1:1456/oauth/callback"
    )
    await client.aclose()
    assert url.startswith("https://login.radius.test/authorize?") and verifier
