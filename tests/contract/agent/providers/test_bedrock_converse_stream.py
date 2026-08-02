from __future__ import annotations

import json
import struct
from pathlib import Path
from zlib import crc32

import httpx
import pytest

from aipic_to_model.agent.core.events import CancellationToken
from aipic_to_model.agent.core.models import ProviderEventType, TextContent, UserMessage
from aipic_to_model.agent.providers.api.bedrock_converse_stream import (
    AwsCredentialChain,
    AwsCredentials,
    AwsEventStreamDecoder,
    BedrockConverseStreamProvider,
    sign_bedrock_request,
)
from aipic_to_model.agent.providers.base import ModelProfile, ModelRequest


def _header(name: str, value: str) -> bytes:
    encoded_name, encoded_value = name.encode(), value.encode()
    return (
        bytes((len(encoded_name),))
        + encoded_name
        + bytes((7,))
        + struct.pack(">H", len(encoded_value))
        + encoded_value
    )


def _frame(event: dict[str, object]) -> bytes:
    headers = _header(":message-type", "event") + _header(":event-type", "chunk")
    payload = json.dumps(event, separators=(",", ":")).encode()
    total = 16 + len(headers) + len(payload)
    prelude = struct.pack(">II", total, len(headers))
    prelude += struct.pack(">I", crc32(prelude) & 0xFFFFFFFF)
    body = prelude + headers + payload
    return body + struct.pack(">I", crc32(body) & 0xFFFFFFFF)


def test_eventstream_decoder_accepts_fragmented_valid_frames() -> None:
    frame = _frame({"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "雪"}}})
    decoder = AwsEventStreamDecoder()
    assert decoder.feed(frame[:9]) == ()
    assert decoder.feed(frame[9:]) == (
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "雪"}}},
    )


def test_sigv4_signing_is_deterministic_and_does_not_put_secret_in_headers() -> None:
    headers = sign_bedrock_request(
        "POST",
        "https://bedrock-runtime.us-east-1.amazonaws.com/model/demo/converse-stream",
        {"content-type": "application/json"},
        b"{}",
        AwsCredentials("AKID", "secret"),
        "us-east-1",
    )
    assert headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKID/")
    assert "secret" not in headers["authorization"]
    assert headers["host"] == "bedrock-runtime.us-east-1.amazonaws.com"


@pytest.mark.asyncio
async def test_aws_credential_chain_supports_web_identity_ecs_and_imds(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("jwt", encoding="utf-8")

    async def web_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "sts.test"
        return httpx.Response(
            200,
            text=(
                "<Credentials><AccessKeyId>web</AccessKeyId><SecretAccessKey>secret</SecretAccessKey>"
                "<SessionToken>token</SessionToken></Credentials>"
            ),
        )

    web_client = httpx.AsyncClient(transport=httpx.MockTransport(web_handler))
    web = await AwsCredentialChain(
        {
            "AWS_WEB_IDENTITY_TOKEN_FILE": str(token_file),
            "AWS_ROLE_ARN": "arn:aws:iam::1:role/test",
            "AWS_STS_REGIONAL_ENDPOINT": "https://sts.test",
        },
        client=web_client,
    ).resolve_async("us-east-1", CancellationToken())
    await web_client.aclose()
    assert web == AwsCredentials("web", "secret", "token")

    async def ecs_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer task"
        return httpx.Response(200, json={"AccessKeyId": "ecs", "SecretAccessKey": "secret"})

    ecs_client = httpx.AsyncClient(transport=httpx.MockTransport(ecs_handler))
    ecs = await AwsCredentialChain(
        {
            "AWS_CONTAINER_CREDENTIALS_FULL_URI": "https://ecs.test/credentials",
            "AWS_CONTAINER_AUTHORIZATION_TOKEN": "Bearer task",
        },
        client=ecs_client,
    ).resolve_async("us-east-1", CancellationToken())
    await ecs_client.aclose()
    assert ecs == AwsCredentials("ecs", "secret")

    async def imds_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(200, text="imds-token")
        if request.url.path.endswith("security-credentials/"):
            assert request.headers["x-aws-ec2-metadata-token"] == "imds-token"
            return httpx.Response(200, text="role")
        return httpx.Response(200, json={"AccessKeyId": "imds", "SecretAccessKey": "secret"})

    imds_client = httpx.AsyncClient(transport=httpx.MockTransport(imds_handler))
    imds = await AwsCredentialChain(
        {"AWS_EC2_METADATA_SERVICE_ENDPOINT": "http://metadata.test"}, client=imds_client
    ).resolve_async("us-east-1", CancellationToken())
    await imds_client.aclose()
    assert imds == AwsCredentials("imds", "secret")


@pytest.mark.asyncio
async def test_bedrock_provider_normalizes_text_tool_usage_and_stop_reason() -> None:
    frames = b"".join(
        (
            _frame({"messageStart": {"role": "assistant"}}),
            _frame({"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "ok"}}}),
            _frame(
                {
                    "contentBlockStart": {
                        "contentBlockIndex": 1,
                        "start": {"toolUse": {"toolUseId": "call-1", "name": "calculator.add"}},
                    }
                }
            ),
            _frame(
                {
                    "contentBlockDelta": {
                        "contentBlockIndex": 1,
                        "delta": {"toolUse": {"input": '{"a":1}'}},
                    }
                }
            ),
            _frame(
                {"metadata": {"usage": {"inputTokens": 2, "outputTokens": 3, "totalTokens": 5}}}
            ),
            _frame({"messageStop": {"stopReason": "tool_use"}}),
        )
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "application/vnd.amazon.eventstream"}, content=frames
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = BedrockConverseStreamProvider(lambda _ref: "bearer", client=client)
    request = ModelRequest(
        ModelProfile("amazon-bedrock", "demo", "https://bedrock-runtime.us-east-1.amazonaws.com"),
        (UserMessage((TextContent("hello"),)),),
    )
    events = [event async for event in provider.stream(request, CancellationToken())]
    await client.aclose()
    assert [event.type for event in events] == [
        ProviderEventType.MESSAGE_START,
        ProviderEventType.TEXT_DELTA,
        ProviderEventType.TOOL_CALL_START,
        ProviderEventType.TOOL_CALL_ARGUMENTS_DELTA,
        ProviderEventType.USAGE,
        ProviderEventType.MESSAGE_END,
    ]
    assert events[-1].message is not None
    assert events[-1].message.stop_reason == "tool_use"
    assert events[-1].message.usage.total_tokens == 5
