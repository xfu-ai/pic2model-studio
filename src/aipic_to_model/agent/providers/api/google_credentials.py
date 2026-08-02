"""Google ADC and service-account access-token acquisition for Vertex AI."""

from __future__ import annotations

import base64
import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from ...core.errors import ProviderError

GOOGLE_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
DEFAULT_ADC_PATH = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"


@dataclass(frozen=True)
class GoogleAccessToken:
    token: str
    expires_at: float

    @property
    def expired(self) -> bool:
        return self.expires_at <= time.time() + 60


class GoogleCredentials:
    """Refreshes ADC JSON credentials without persisting access tokens."""

    def __init__(
        self, environment: Mapping[str, str] | None = None, client: httpx.AsyncClient | None = None
    ) -> None:
        self._environment = dict(environment) if environment is not None else dict(os.environ)
        self._client = client
        self._cached: GoogleAccessToken | None = None

    def configured_path(self) -> Path | None:
        configured = self._environment.get("GOOGLE_APPLICATION_CREDENTIALS")
        path = Path(configured).expanduser() if configured else DEFAULT_ADC_PATH
        return path if path.is_file() else None

    async def access_token(self) -> GoogleAccessToken | None:
        if self._cached and not self._cached.expired:
            return self._cached
        path = self.configured_path()
        if path is None:
            return None
        try:
            credential = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ProviderError("Google credential file could not be read.") from error
        if not isinstance(credential, dict):
            raise ProviderError("Google credential file is invalid.")
        credential_type = credential.get("type")
        if credential_type == "service_account":
            token = await self._service_account_token(credential)
        elif credential_type == "authorized_user":
            token = await self._authorized_user_token(credential)
        else:
            raise ProviderError("Google credential type is not supported for Vertex AI.")
        self._cached = token
        return token

    async def _service_account_token(self, credential: dict[str, object]) -> GoogleAccessToken:
        email = credential.get("client_email")
        private_key = credential.get("private_key")
        token_uri = credential.get("token_uri", "https://oauth2.googleapis.com/token")
        if (
            not isinstance(email, str)
            or not isinstance(private_key, str)
            or not isinstance(token_uri, str)
        ):
            raise ProviderError("Google service-account credential is incomplete.")
        now = int(time.time())
        assertion = _sign_jwt(
            {"alg": "RS256", "typ": "JWT"},
            {
                "iss": email,
                "scope": GOOGLE_CLOUD_PLATFORM_SCOPE,
                "aud": token_uri,
                "iat": now,
                "exp": now + 3600,
            },
            private_key,
        )
        return await self._token_request(
            token_uri,
            {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
        )

    async def _authorized_user_token(self, credential: dict[str, object]) -> GoogleAccessToken:
        client_id = credential.get("client_id")
        client_secret = credential.get("client_secret")
        refresh_token = credential.get("refresh_token")
        token_uri = credential.get("token_uri", "https://oauth2.googleapis.com/token")
        if (
            not isinstance(client_id, str)
            or not isinstance(client_secret, str)
            or not isinstance(refresh_token, str)
            or not isinstance(token_uri, str)
        ):
            raise ProviderError("Google ADC credential is incomplete.")
        return await self._token_request(
            token_uri,
            {
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            },
        )

    async def _token_request(self, token_uri: str, data: dict[str, str]) -> GoogleAccessToken:
        client = self._client or httpx.AsyncClient(timeout=20)
        owns_client = self._client is None
        try:
            response = await client.post(token_uri, data=data)
            if response.status_code >= 400:
                raise ProviderError(
                    "Google credential refresh failed.", status_code=response.status_code
                )
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("access_token"), str):
                raise ProviderError("Google credential refresh returned an invalid response.")
            expires = payload.get("expires_in")
            return GoogleAccessToken(
                payload["access_token"],
                time.time() + (expires if isinstance(expires, int) else 3600),
            )
        except httpx.HTTPError as error:
            raise ProviderError(
                "Google credential refresh transport failed.", retryable=True
            ) from error
        finally:
            if owns_client:
                await client.aclose()


def _sign_jwt(header: dict[str, str], claim: dict[str, object], private_key: str) -> str:
    encoded_header = _url64(json.dumps(header, separators=(",", ":")).encode())
    encoded_claim = _url64(json.dumps(claim, separators=(",", ":")).encode())
    body = f"{encoded_header}.{encoded_claim}".encode()
    key = serialization.load_pem_private_key(private_key.encode(), password=None)
    if not isinstance(key, RSAPrivateKey):
        raise TypeError("Google service-account private key must be RSA.")
    signature = key.sign(body, padding.PKCS1v15(), hashes.SHA256())
    return f"{encoded_header}.{encoded_claim}.{_url64(signature)}"


def _url64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")
