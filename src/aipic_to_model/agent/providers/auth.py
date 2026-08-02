"""Shared, secret-safe credential resolution and OAuth token refresh control."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar, Protocol

import httpx

from ..core.events import CancellationToken
from .catalog import ProviderDescriptor


@dataclass(frozen=True)
class OAuthToken:
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= datetime.now(UTC)

    def serialized(self) -> str:
        return json.dumps(
            {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_serialized(cls, value: str | None) -> OAuthToken | None:
        if not value:
            return None
        try:
            raw = json.loads(value)
            expires = raw.get("expires_at")
            return cls(
                str(raw["access_token"]),
                str(raw["refresh_token"]) if raw.get("refresh_token") else None,
                datetime.fromisoformat(expires) if isinstance(expires, str) else None,
            )
        except KeyError, TypeError, ValueError, json.JSONDecodeError:
            return None


class CredentialStore:
    """Minimal keyring-shaped protocol; values are never formatted into diagnostics."""

    def get(self, key: str) -> str | None:  # pragma: no cover - protocol shape
        raise NotImplementedError

    def set(self, key: str, value: str) -> None:  # pragma: no cover - protocol shape
        raise NotImplementedError

    def delete(self, key: str) -> None:  # pragma: no cover - protocol shape
        raise NotImplementedError


class KeyringBackend(Protocol):
    def get_password(self, service_name: str, username: str) -> str | None: ...

    def set_password(self, service_name: str, username: str, password: str) -> None: ...

    def delete_password(self, service_name: str, username: str) -> None: ...


class KeyringCredentialStore(CredentialStore):
    """OS-keyring storage for API keys and serialized OAuth tokens.

    The backend is injectable solely for deterministic tests; callers never
    receive an enumerating API, so credentials cannot be accidentally logged.
    """

    def __init__(
        self, service_name: str = "aipic-to-model.agent", backend: KeyringBackend | None = None
    ) -> None:
        if backend is None:
            import keyring

            backend = keyring
        self._service_name = service_name
        self._backend = backend

    def get(self, key: str) -> str | None:
        value = self._backend.get_password(self._service_name, key)
        return value if isinstance(value, str) else None

    def set(self, key: str, value: str) -> None:
        self._backend.set_password(self._service_name, key, value)

    def delete(self, key: str) -> None:
        try:
            self._backend.delete_password(self._service_name, key)
        except Exception as error:
            # Backends disagree on the exception type for a missing key.  A
            # logout is idempotent; only suppress their documented absence.
            if error.__class__.__name__ != "PasswordDeleteError":
                raise


def resolve_api_key(
    environment: dict[str, str], store: CredentialStore, ref: str, names: tuple[str, ...]
) -> str | None:
    for name in names:
        if environment.get(name):
            return environment[name]
    return store.get(ref)


class OAuthTokenManager:
    def __init__(
        self,
        store: CredentialStore,
        ref: str,
        loader: Callable[[], OAuthToken | None],
        saver: Callable[[OAuthToken], None],
    ) -> None:
        self._store, self._ref, self._loader, self._saver = store, ref, loader, saver
        self._lock = asyncio.Lock()

    async def access_token(
        self, refresh: Callable[[OAuthToken], Awaitable[OAuthToken]]
    ) -> str | None:
        async with self._lock:
            token = self._loader()
            if token is None:
                return None
            if token.expired:
                token = await refresh(token)
                self._saver(token)
            return token.access_token

    def logout(self) -> None:
        self._store.delete(self._ref)


@dataclass(frozen=True)
class ResolvedAuth:
    """Request-only credential material; never serialize or log this object."""

    api_key: str | None = None
    headers: dict[str, str] | None = None
    base_url: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class ProviderConfigurationStatus:
    provider_id: str
    state: str
    credential_source: str | None = None


class ProviderAuthResolver:
    """Resolve provider credentials without provider transport coupling.

    Stored credentials own a provider.  Environment is considered only when no
    stored value exists, preventing a failed stored OAuth refresh from silently
    switching accounts.
    """

    def __init__(self, store: CredentialStore, environment: dict[str, str] | None = None) -> None:
        self._store = store
        self._environment = environment if environment is not None else dict(os.environ)

    def resolve(self, descriptor: ProviderDescriptor) -> ResolvedAuth | None:
        stored = self._store.get(descriptor.credential_ref)
        if stored:
            token = OAuthToken.from_serialized(stored)
            if token:
                return ResolvedAuth(api_key=token.access_token, source="stored OAuth")
            return ResolvedAuth(api_key=stored, source="stored credential")
        cloud = _cloud_auth(descriptor, self._environment)
        if cloud is not None:
            return cloud
        if descriptor.provider_id in {
            "amazon-bedrock",
            "cloudflare-ai-gateway",
            "cloudflare-workers-ai",
            "google-vertex",
            "radius",
        }:
            return None
        key = next(
            (
                self._environment[name]
                for name in descriptor.environment
                if self._environment.get(name)
            ),
            None,
        )
        if key:
            return ResolvedAuth(api_key=key, source="environment")
        return None

    def configuration_status(self, descriptor: ProviderDescriptor) -> ProviderConfigurationStatus:
        resolved = self.resolve(descriptor)
        return ProviderConfigurationStatus(
            descriptor.provider_id,
            "configured" if resolved is not None else "not_configured",
            resolved.source if resolved is not None else None,
        )


def _cloud_auth(descriptor: ProviderDescriptor, environment: dict[str, str]) -> ResolvedAuth | None:
    if descriptor.provider_id == "amazon-bedrock":
        if environment.get("AWS_BEARER_TOKEN_BEDROCK"):
            return ResolvedAuth(
                headers={"authorization": "Bearer " + environment["AWS_BEARER_TOKEN_BEDROCK"]},
                source="AWS bearer token",
            )
        if environment.get("AWS_PROFILE") or (
            environment.get("AWS_ACCESS_KEY_ID") and environment.get("AWS_SECRET_ACCESS_KEY")
        ):
            return ResolvedAuth(source="AWS credential chain")
    if descriptor.provider_id == "google-vertex":
        if environment.get("GOOGLE_CLOUD_API_KEY"):
            return ResolvedAuth(
                api_key=environment["GOOGLE_CLOUD_API_KEY"], source="Google Cloud API key"
            )
        has_project_and_location = bool(
            environment.get("GOOGLE_CLOUD_PROJECT") and environment.get("GOOGLE_CLOUD_LOCATION")
        )
        if environment.get("GOOGLE_APPLICATION_CREDENTIALS") and has_project_and_location:
            return ResolvedAuth(source="Google service account")
        if has_project_and_location:
            return ResolvedAuth(source="Google ADC")
    if descriptor.provider_id in {"cloudflare-ai-gateway", "cloudflare-workers-ai"}:
        key, account = (
            environment.get("CLOUDFLARE_API_KEY"),
            environment.get("CLOUDFLARE_ACCOUNT_ID"),
        )
        gateway = environment.get("CLOUDFLARE_GATEWAY_ID")
        if key and account and (descriptor.provider_id != "cloudflare-ai-gateway" or gateway):
            if descriptor.provider_id == "cloudflare-ai-gateway":
                return ResolvedAuth(
                    headers={
                        "cf-aig-authorization": f"Bearer {key}",
                        "cf-account-id": account,
                        "cf-gateway-id": gateway or "",
                    },
                    source="Cloudflare configuration",
                )
            return ResolvedAuth(api_key=key, source="Cloudflare configuration")
    if descriptor.provider_id == "radius" and environment.get("RADIUS_GATEWAY_URL"):
        return ResolvedAuth(
            api_key=environment.get("RADIUS_API_KEY"),
            base_url=environment["RADIUS_GATEWAY_URL"],
            source="Radius gateway configuration",
        )
    return None


@dataclass(frozen=True)
class OAuthEndpoints:
    authorization_url: str
    token_url: str
    client_id: str
    scopes: tuple[str, ...]
    device_url: str | None = None


@dataclass(frozen=True)
class OAuthProviderSpec:
    """Frozen provider-specific OAuth contract; tokens never live in this metadata."""

    provider_id: str
    flow: str
    endpoints: OAuthEndpoints | None


_OAUTH_PROVIDER_SPECS = {
    "anthropic": OAuthProviderSpec(
        "anthropic",
        "browser_pkce",
        OAuthEndpoints(
            "https://claude.ai/oauth/authorize",
            "https://platform.claude.com/v1/oauth/token",
            "9d1c250a-e61b-44d9-88ed-5944d1962f5e",
            (
                "org:create_api_key",
                "user:profile",
                "user:inference",
                "user:sessions:claude_code",
                "user:mcp_servers",
                "user:file_upload",
            ),
        ),
    ),
    "github-copilot": OAuthProviderSpec(
        "github-copilot",
        "device_code",
        OAuthEndpoints(
            "https://github.com/login/oauth/authorize",
            "https://github.com/login/oauth/access_token",
            "Iv1.b507a08c87ecfe98",
            (),
            "https://github.com/login/device/code",
        ),
    ),
    "kimi-coding": OAuthProviderSpec(
        "kimi-coding",
        "device_code",
        OAuthEndpoints(
            "https://auth.kimi.com",
            "https://auth.kimi.com/api/oauth/token",
            "17e5f671-d194-4dfb-9706-5516cb48c098",
            (),
            "https://auth.kimi.com/api/oauth/device_authorization",
        ),
    ),
    "openai-codex": OAuthProviderSpec(
        "openai-codex",
        "browser_pkce",
        OAuthEndpoints(
            "https://auth.openai.com/oauth/authorize",
            "https://auth.openai.com/oauth/token",
            "app_EMoamEEZ73f0CkXaXp7hrann",
            ("openid", "profile", "email", "offline_access"),
        ),
    ),
    "openrouter": OAuthProviderSpec("openrouter", "browser_callback_key", None),
    "radius": OAuthProviderSpec("radius", "gateway_discovery", None),
    "xai": OAuthProviderSpec(
        "xai",
        "device_code",
        OAuthEndpoints(
            "https://auth.x.ai/oauth2/authorize",
            "https://auth.x.ai/oauth2/token",
            "b1a00492-073a-47ea-816f-4c329264a828",
            ("openid", "profile", "email", "offline_access", "grok-cli:access", "api:access"),
            "https://auth.x.ai/oauth2/device/code",
        ),
    ),
}


def oauth_provider_spec(provider_id: str) -> OAuthProviderSpec:
    try:
        return _OAUTH_PROVIDER_SPECS[provider_id]
    except KeyError as error:
        raise ValueError(f"Provider does not have a frozen OAuth flow: {provider_id}") from error


def oauth_flow_for_provider(provider_id: str, client: httpx.AsyncClient | None = None) -> OAuthFlow:
    spec = oauth_provider_spec(provider_id)
    if spec.endpoints is None:
        raise ValueError(f"Provider {provider_id} requires its {spec.flow} OAuth flow.")
    return OAuthFlow(spec.endpoints, client)


class OpenRouterOAuthFlow:
    """OpenRouter's PKCE flow exchanges the callback code for a durable API key."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    def browser_url(self, callback_url: str) -> tuple[str, str]:
        verifier = secrets.token_urlsafe(48)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        return (
            str(
                httpx.URL(
                    "https://openrouter.ai/auth",
                    params={
                        "callback_url": callback_url,
                        "code_challenge": challenge,
                        "code_challenge_method": "S256",
                    },
                )
            ),
            verifier,
        )

    async def exchange_code(self, code: str, verifier: str) -> OAuthToken:
        client, owns = self._client or httpx.AsyncClient(timeout=30), self._client is None
        try:
            response = await client.post(
                "https://openrouter.ai/api/v1/auth/keys",
                headers={"accept": "application/json"},
                json={"code": code, "code_verifier": verifier, "code_challenge_method": "S256"},
            )
            response.raise_for_status()
            value = response.json()
            if (
                not isinstance(value, dict)
                or not isinstance(value.get("key"), str)
                or not value["key"]
            ):
                raise TypeError("OpenRouter OAuth key exchange returned an invalid response.")
            return OAuthToken(value["key"], None, None)
        finally:
            if owns:
                await client.aclose()


class GitHubCopilotOAuthFlow:
    """Exchange a GitHub device credential for the short-lived Copilot token."""

    _headers: ClassVar[dict[str, str]] = {
        "accept": "application/json",
        "user-agent": "GitHubCopilotChat/0.35.0",
        "editor-version": "vscode/1.107.0",
        "editor-plugin-version": "copilot-chat/0.35.0",
        "copilot-integration-id": "vscode-chat",
    }

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def exchange_github_token(
        self, github_token: str, enterprise_domain: str | None = None
    ) -> OAuthToken:
        domain = enterprise_domain or "github.com"
        url = f"https://api.{domain}/copilot_internal/v2/token"
        client, owns = self._client or httpx.AsyncClient(timeout=30), self._client is None
        try:
            response = await client.get(
                url, headers={**self._headers, "authorization": f"Bearer {github_token}"}
            )
            response.raise_for_status()
            value = response.json()
            token, expires_at = (
                (value.get("token"), value.get("expires_at"))
                if isinstance(value, dict)
                else (None, None)
            )
            if not isinstance(token, str) or not isinstance(expires_at, int):
                raise TypeError("GitHub Copilot token exchange returned an invalid response.")
            return OAuthToken(
                token,
                github_token,
                datetime.fromtimestamp(max(expires_at - 300, 0), UTC),
            )
        finally:
            if owns:
                await client.aclose()


class RadiusOAuthFlow:
    """Gateway-discovered Radius OAuth with shared PKCE/device token handling."""

    def __init__(self, gateway: str, client: httpx.AsyncClient | None = None) -> None:
        self._gateway = gateway.rstrip("/")
        self._client = client

    async def _flow(self) -> OAuthFlow:
        client, owns = self._client or httpx.AsyncClient(timeout=20), self._client is None
        try:
            response = await client.get(
                f"{self._gateway}/v1/oauth", headers={"accept": "application/json"}
            )
            response.raise_for_status()
            value = response.json()
            endpoint = value.get("authorizationEndpoint") if isinstance(value, dict) else None
            if not isinstance(endpoint, str) or not endpoint.startswith(("https://", "http://")):
                raise TypeError(
                    "Radius OAuth discovery returned an invalid authorization endpoint."
                )
            return OAuthFlow(
                OAuthEndpoints(
                    endpoint,
                    f"{self._gateway}/v1/oauth/token",
                    "pi-gateway",
                    ("gateway", "offline_access"),
                    f"{self._gateway}/v1/oauth/device",
                ),
                self._client,
            )
        finally:
            if owns:
                await client.aclose()

    async def browser_url(self, redirect_uri: str) -> tuple[str, str]:
        return (await self._flow()).browser_url(redirect_uri)

    async def begin_device(self) -> dict[str, object]:
        return await (await self._flow()).begin_device()

    async def poll_device(
        self, device: dict[str, object], cancellation: CancellationToken
    ) -> OAuthToken:
        return await (await self._flow()).poll_device(device, cancellation)

    async def refresh(self, token: OAuthToken) -> OAuthToken:
        return await (await self._flow()).refresh(token)


class OAuthFlow:
    """PKCE browser/device flow helper with explicit cancellation and no logging."""

    def __init__(self, endpoints: OAuthEndpoints, client: httpx.AsyncClient | None = None) -> None:
        self._endpoints, self._client = endpoints, client

    def browser_url(self, redirect_uri: str, state: str | None = None) -> tuple[str, str]:
        verifier = secrets.token_urlsafe(48)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        query = {
            "response_type": "code",
            "client_id": self._endpoints.client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(self._endpoints.scopes),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state or secrets.token_urlsafe(18),
        }
        return str(httpx.URL(self._endpoints.authorization_url, params=query)), verifier

    async def exchange_code(self, code: str, verifier: str, redirect_uri: str) -> OAuthToken:
        return await self._exchange(
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
            }
        )

    async def begin_device(self) -> dict[str, object]:
        if not self._endpoints.device_url:
            raise ValueError("This provider does not expose a device authorization endpoint.")
        client, owns = self._client or httpx.AsyncClient(timeout=20), self._client is None
        try:
            response = await client.post(
                self._endpoints.device_url,
                data={
                    "client_id": self._endpoints.client_id,
                    "scope": " ".join(self._endpoints.scopes),
                },
            )
            response.raise_for_status()
            value = response.json()
            if not isinstance(value, dict):
                raise TypeError("Device authorization returned an invalid response.")
            return value
        finally:
            if owns:
                await client.aclose()

    async def poll_device(
        self, device: dict[str, object], cancellation: CancellationToken
    ) -> OAuthToken:
        """Complete an RFC 8628 device flow with bounded, cancellable polling."""

        raw_code = device.get("device_code")
        if not isinstance(raw_code, str) or not raw_code:
            raise ValueError("Device authorization returned no device code.")
        interval = device.get("interval", 5)
        poll_seconds = max(float(interval) if isinstance(interval, int | float) else 5.0, 1.0)
        expires = device.get("expires_in", 900)
        deadline = asyncio.get_running_loop().time() + (
            float(expires) if isinstance(expires, int | float) else 900.0
        )
        while asyncio.get_running_loop().time() < deadline:
            cancellation.raise_if_cancelled()
            client, owns = self._client or httpx.AsyncClient(timeout=20), self._client is None
            try:
                response = await cancellation.wait_for(
                    client.post(
                        self._endpoints.token_url,
                        data={
                            "client_id": self._endpoints.client_id,
                            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                            "device_code": raw_code,
                        },
                    )
                )
                if response.is_success:
                    return _oauth_token(response.json())
                value = _oauth_error(response)
                if value == "slow_down":
                    poll_seconds += 5
                elif value not in {"authorization_pending", "slow_down"}:
                    raise ValueError("Device authorization was denied or expired.")
            finally:
                if owns:
                    await client.aclose()
            await cancellation.wait_for(asyncio.sleep(poll_seconds))
        raise TimeoutError("Device authorization timed out.")

    async def refresh(self, token: OAuthToken) -> OAuthToken:
        if not token.refresh_token:
            raise ValueError("OAuth token cannot be refreshed without a refresh token.")
        return await self._exchange(
            {"grant_type": "refresh_token", "refresh_token": token.refresh_token},
            previous_refresh_token=token.refresh_token,
        )

    async def _exchange(
        self, data: dict[str, str], *, previous_refresh_token: str | None = None
    ) -> OAuthToken:
        client, owns = self._client or httpx.AsyncClient(timeout=20), self._client is None
        try:
            response = await client.post(
                self._endpoints.token_url, data={"client_id": self._endpoints.client_id, **data}
            )
            response.raise_for_status()
            return _oauth_token(response.json(), previous_refresh_token)
        finally:
            if owns:
                await client.aclose()


def _oauth_error(response: httpx.Response) -> str | None:
    try:
        value = response.json()
    except json.JSONDecodeError:
        return None
    return (
        value.get("error")
        if isinstance(value, dict) and isinstance(value.get("error"), str)
        else None
    )


def _oauth_token(value: object, previous_refresh_token: str | None = None) -> OAuthToken:
    if not isinstance(value, dict) or not isinstance(value.get("access_token"), str):
        raise TypeError("OAuth token endpoint returned an invalid response.")
    refresh = value.get("refresh_token")
    refresh_token = refresh if isinstance(refresh, str) else previous_refresh_token
    expires_in = value.get("expires_in")
    expires_at = (
        datetime.fromtimestamp(datetime.now(UTC).timestamp() + expires_in, UTC)
        if isinstance(expires_in, int) and expires_in > 0
        else None
    )
    return OAuthToken(value["access_token"], refresh_token, expires_at)
