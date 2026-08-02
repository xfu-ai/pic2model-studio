from __future__ import annotations

import base64
import json

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from aipic_to_model.agent.providers.api.google_credentials import GoogleCredentials


@pytest.mark.asyncio
async def test_service_account_credentials_sign_and_exchange_a_short_lived_access_token(
    tmp_path,
) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    credential_file = tmp_path / "service-account.json"
    credential_file.write_text(
        json.dumps(
            {
                "type": "service_account",
                "client_email": "agent@example.test",
                "private_key": pem,
                "token_uri": "https://oauth.test/token",
            }
        ),
        encoding="utf-8",
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        assertion = body.split("assertion=", 1)[1]
        claim = assertion.split(".")[1] + "=" * (-len(assertion.split(".")[1]) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(claim))
        assert decoded["scope"] == "https://www.googleapis.com/auth/cloud-platform"
        return httpx.Response(200, json={"access_token": "short-lived", "expires_in": 3600})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    credentials = GoogleCredentials(
        {"GOOGLE_APPLICATION_CREDENTIALS": str(credential_file)}, client
    )
    token = await credentials.access_token()
    await client.aclose()
    assert token is not None and token.token == "short-lived" and not token.expired


@pytest.mark.asyncio
async def test_adc_authorized_user_refreshes_without_persisting_access_token(tmp_path) -> None:
    credential_file = tmp_path / "adc.json"
    credential_file.write_text(
        json.dumps(
            {
                "type": "authorized_user",
                "client_id": "client",
                "client_secret": "secret",
                "refresh_token": "refresh",
                "token_uri": "https://oauth.test/token",
            }
        ),
        encoding="utf-8",
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert b"grant_type=refresh_token" in request.content
        return httpx.Response(200, json={"access_token": "adc-token", "expires_in": 900})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    credentials = GoogleCredentials(
        {"GOOGLE_APPLICATION_CREDENTIALS": str(credential_file)}, client
    )
    token = await credentials.access_token()
    await client.aclose()
    assert token is not None and token.token == "adc-token"
