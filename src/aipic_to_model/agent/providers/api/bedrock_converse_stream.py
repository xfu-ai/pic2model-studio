"""Native AWS Bedrock ConverseStream transport.

This module does not depend on a Python AWS SDK.  It implements the bounded
parts of the AWS default credential chain needed by the Agent sidecar, SigV4
request signing, and AWS EventStream frame decoding.  Credentials are kept
only in request-local memory and never enter Agent messages or diagnostics.
"""

from __future__ import annotations

import configparser
import hashlib
import hmac
import json
import os
import struct
import xml.etree.ElementTree as element_tree
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlsplit
from zlib import crc32

import httpx

from ...core.errors import ContextOverflowError, ProviderError
from ...core.events import CancellationToken
from ...core.models import (
    AssistantMessage,
    ProviderEvent,
    ProviderEventType,
    TextContent,
    ToolCall,
    Usage,
)
from ..adapters import build_payload
from ..base import ModelRequest


@dataclass(frozen=True)
class AwsCredentials:
    access_key_id: str
    secret_access_key: str
    session_token: str | None = None


class AwsCredentialChain:
    """Bounded async AWS default credential chain for Bedrock signing."""

    def __init__(
        self,
        environment: Mapping[str, str] | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._environment = dict(environment) if environment is not None else dict(os.environ)
        self._client = client

    def resolve(self) -> AwsCredentials | None:
        credential = self._from_environment()
        if credential:
            return credential
        return self._from_profile()

    async def resolve_async(
        self, region: str, cancellation: CancellationToken
    ) -> AwsCredentials | None:
        credential = self._from_environment()
        if credential:
            return credential
        client = self._client or httpx.AsyncClient(timeout=2)
        owns_client = self._client is None
        try:
            return (
                await self._from_web_identity(region, client, cancellation)
                or await self._from_ecs(client, cancellation)
                or self._from_profile()
                or await self._from_imds(client, cancellation)
            )
        except httpx.HTTPError:
            return None
        finally:
            if owns_client:
                await client.aclose()

    def _from_environment(self) -> AwsCredentials | None:
        access_key = self._environment.get("AWS_ACCESS_KEY_ID")
        secret_key = self._environment.get("AWS_SECRET_ACCESS_KEY")
        if access_key and secret_key:
            return AwsCredentials(
                access_key, secret_key, self._environment.get("AWS_SESSION_TOKEN")
            )
        return None

    def _from_profile(self) -> AwsCredentials | None:
        profile = self._environment.get("AWS_PROFILE", "default")
        location = self._environment.get("AWS_SHARED_CREDENTIALS_FILE")
        path = Path(location).expanduser() if location else Path.home() / ".aws" / "credentials"
        if not path.is_file():
            return None
        parser = configparser.RawConfigParser()
        parser.read(path, encoding="utf-8")
        if not parser.has_section(profile):
            return None
        access_key = parser.get(profile, "aws_access_key_id", fallback=None)
        secret_key = parser.get(profile, "aws_secret_access_key", fallback=None)
        if not access_key or not secret_key:
            return None
        return AwsCredentials(
            access_key, secret_key, parser.get(profile, "aws_session_token", fallback=None)
        )

    async def _from_web_identity(
        self, region: str, client: httpx.AsyncClient, cancellation: CancellationToken
    ) -> AwsCredentials | None:
        token_file = self._environment.get("AWS_WEB_IDENTITY_TOKEN_FILE")
        role_arn = self._environment.get("AWS_ROLE_ARN")
        if not token_file or not role_arn:
            return None
        try:
            token = Path(token_file).read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not token:
            return None
        endpoint = self._environment.get("AWS_STS_REGIONAL_ENDPOINT")
        if not endpoint:
            endpoint = f"https://sts.{region}.amazonaws.com"
        response = await cancellation.wait_for(
            client.post(
                endpoint,
                data={
                    "Action": "AssumeRoleWithWebIdentity",
                    "Version": "2011-06-15",
                    "RoleArn": role_arn,
                    "RoleSessionName": self._environment.get(
                        "AWS_ROLE_SESSION_NAME", "aipic-agent"
                    ),
                    "WebIdentityToken": token,
                },
            )
        )
        if not response.is_success:
            return None
        return _credentials_from_sts_xml(response.text)

    async def _from_ecs(
        self, client: httpx.AsyncClient, cancellation: CancellationToken
    ) -> AwsCredentials | None:
        relative = self._environment.get("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI")
        full = self._environment.get("AWS_CONTAINER_CREDENTIALS_FULL_URI")
        if relative:
            url = f"http://169.254.170.2{relative}"
        elif full:
            parsed = urlsplit(full)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
                return None
            url = full
        else:
            return None
        headers: dict[str, str] = {}
        authorization_file = self._environment.get("AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE")
        if authorization_file:
            try:
                headers["authorization"] = (
                    Path(authorization_file).read_text(encoding="utf-8").strip()
                )
            except OSError:
                return None
        elif self._environment.get("AWS_CONTAINER_AUTHORIZATION_TOKEN"):
            headers["authorization"] = self._environment["AWS_CONTAINER_AUTHORIZATION_TOKEN"]
        response = await cancellation.wait_for(client.get(url, headers=headers))
        return _credentials_from_response(response) if response.is_success else None

    async def _from_imds(
        self, client: httpx.AsyncClient, cancellation: CancellationToken
    ) -> AwsCredentials | None:
        endpoint = self._environment.get(
            "AWS_EC2_METADATA_SERVICE_ENDPOINT", "http://169.254.169.254"
        )
        parsed = urlsplit(endpoint)
        if parsed.scheme != "http" or not parsed.netloc or parsed.username or parsed.query:
            return None
        base = endpoint.rstrip("/")
        token_response = await cancellation.wait_for(
            client.put(
                f"{base}/latest/api/token",
                headers={"x-aws-ec2-metadata-token-ttl-seconds": "21600"},
            )
        )
        headers = (
            {"x-aws-ec2-metadata-token": token_response.text}
            if token_response.is_success and token_response.text
            else {}
        )
        role_response = await cancellation.wait_for(
            client.get(f"{base}/latest/meta-data/iam/security-credentials/", headers=headers)
        )
        if not role_response.is_success or not role_response.text.strip():
            return None
        credentials_response = await cancellation.wait_for(
            client.get(
                f"{base}/latest/meta-data/iam/security-credentials/{role_response.text.strip()}",
                headers=headers,
            )
        )
        return (
            _credentials_from_response(credentials_response)
            if credentials_response.is_success
            else None
        )


def _credentials_from_aws_json(value: object) -> AwsCredentials | None:
    if not isinstance(value, dict):
        return None
    access = value.get("AccessKeyId")
    secret = value.get("SecretAccessKey")
    token = value.get("Token")
    if not isinstance(access, str) or not isinstance(secret, str) or not access or not secret:
        return None
    return AwsCredentials(access, secret, token if isinstance(token, str) else None)


def _credentials_from_response(response: httpx.Response) -> AwsCredentials | None:
    try:
        return _credentials_from_aws_json(response.json())
    except json.JSONDecodeError:
        return None


def _credentials_from_sts_xml(value: str) -> AwsCredentials | None:
    try:
        root = element_tree.fromstring(value)
    except element_tree.ParseError:
        return None
    access = root.findtext(".//{*}AccessKeyId")
    secret = root.findtext(".//{*}SecretAccessKey")
    token = root.findtext(".//{*}SessionToken")
    if not access or not secret:
        return None
    return AwsCredentials(access, secret, token)


def resolve_bedrock_region(base_url: str, environment: Mapping[str, str] | None = None) -> str:
    environment = environment or os.environ
    configured = environment.get("AWS_REGION") or environment.get("AWS_DEFAULT_REGION")
    if configured:
        return configured
    hostname = urlsplit(base_url).hostname or ""
    pieces = hostname.split(".")
    if len(pieces) >= 4 and pieces[0].startswith("bedrock-runtime"):
        return pieces[1]
    return "us-east-1"


def sign_bedrock_request(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    credentials: AwsCredentials,
    region: str,
    now: datetime | None = None,
) -> dict[str, str]:
    """Return SigV4 headers for one Bedrock Runtime request."""

    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    amz_date = timestamp.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = timestamp.strftime("%Y%m%d")
    parsed = urlsplit(url)
    canonical_headers = {
        key.lower(): " ".join(value.strip().split()) for key, value in headers.items()
    }
    canonical_headers["host"] = parsed.netloc
    canonical_headers["x-amz-date"] = amz_date
    if credentials.session_token:
        canonical_headers["x-amz-security-token"] = credentials.session_token
    ordered = sorted(canonical_headers.items())
    canonical_headers_text = "".join(f"{key}:{value}\n" for key, value in ordered)
    signed_headers = ";".join(key for key, _ in ordered)
    payload_hash = hashlib.sha256(body).hexdigest()
    query = "&".join(
        f"{quote(key, safe='-_.~')}={quote(value, safe='-_.~')}"
        for key, value in sorted(_query_pairs(parsed.query))
    )
    canonical_request = "\n".join(
        (
            method.upper(),
            quote(parsed.path or "/", safe="/-_.~"),
            query,
            canonical_headers_text,
            signed_headers,
            payload_hash,
        )
    )
    scope = f"{date_stamp}/{region}/bedrock/aws4_request"
    string_to_sign = "\n".join(
        (
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        )
    )
    signing_key = _signing_key(credentials.secret_access_key, date_stamp, region, "bedrock")
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    authorization = (
        "AWS4-HMAC-SHA256 "
        f"Credential={credentials.access_key_id}/{scope}, SignedHeaders={signed_headers}, Signature={signature}"
    )
    result = {key: value for key, value in canonical_headers.items()}
    result["authorization"] = authorization
    return result


def _query_pairs(value: str) -> list[tuple[str, str]]:
    if not value:
        return []
    pairs: list[tuple[str, str]] = []
    for item in value.split("&"):
        key, separator, item_value = item.partition("=")
        pairs.append((key, item_value if separator else ""))
    return pairs


def _signing_key(secret: str, date: str, region: str, service: str) -> bytes:
    key = hmac.new(("AWS4" + secret).encode(), date.encode(), hashlib.sha256).digest()
    key = hmac.new(key, region.encode(), hashlib.sha256).digest()
    key = hmac.new(key, service.encode(), hashlib.sha256).digest()
    return hmac.new(key, b"aws4_request", hashlib.sha256).digest()


class AwsEventStreamDecoder:
    """Incrementally decode validated AWS EventStream frames."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> tuple[dict[str, object], ...]:
        self._buffer.extend(data)
        events: list[dict[str, object]] = []
        while len(self._buffer) >= 12:
            total_length, header_length, prelude_crc = struct.unpack(">III", self._buffer[:12])
            if total_length < 16 or header_length > total_length - 16:
                raise ValueError("Invalid AWS EventStream frame length.")
            if len(self._buffer) < total_length:
                break
            frame = bytes(self._buffer[:total_length])
            del self._buffer[:total_length]
            if crc32(frame[:8]) & 0xFFFFFFFF != prelude_crc:
                raise ValueError("AWS EventStream prelude checksum mismatch.")
            message_crc = struct.unpack(">I", frame[-4:])[0]
            if crc32(frame[:-4]) & 0xFFFFFFFF != message_crc:
                raise ValueError("AWS EventStream message checksum mismatch.")
            headers = _decode_headers(frame[12 : 12 + header_length])
            payload = frame[12 + header_length : -4]
            if headers.get(":message-type") == "exception":
                message = _safe_message(payload)
                raise ProviderError(message or "Amazon Bedrock returned a stream exception.")
            if not payload:
                continue
            try:
                decoded = json.loads(payload)
            except json.JSONDecodeError as error:
                raise ProviderError(
                    "Amazon Bedrock returned an invalid EventStream payload."
                ) from error
            if not isinstance(decoded, dict):
                raise ProviderError("Amazon Bedrock returned an invalid EventStream event.")
            events.append(decoded)
        return tuple(events)


def _decode_headers(raw: bytes) -> dict[str, object]:
    result: dict[str, object] = {}
    index = 0
    while index < len(raw):
        name_length = raw[index]
        index += 1
        if index + name_length + 1 > len(raw):
            raise ValueError("Invalid AWS EventStream header.")
        name = raw[index : index + name_length].decode("utf-8")
        index += name_length
        value_type = raw[index]
        index += 1
        if value_type == 0:
            result[name] = True
        elif value_type == 1:
            result[name] = False
        elif value_type == 7:
            size = struct.unpack(">H", raw[index : index + 2])[0]
            index += 2
            result[name] = raw[index : index + size].decode("utf-8")
            index += size
        elif value_type == 6:
            size = struct.unpack(">I", raw[index : index + 4])[0]
            index += 4 + size
        elif value_type == 2:
            index += 1
        elif value_type == 3:
            index += 2
        elif value_type == 4:
            index += 4
        elif value_type in {5, 8}:
            index += 8
        elif value_type == 9:
            index += 16
        else:
            raise ValueError("Unsupported AWS EventStream header type.")
        if index > len(raw):
            raise ValueError("Truncated AWS EventStream header.")
    return result


def _safe_message(payload: bytes) -> str | None:
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if isinstance(decoded, dict) and isinstance(decoded.get("message"), str):
        return decoded["message"][:240]
    return None


class BedrockConverseStreamProvider:
    def __init__(
        self,
        credential_resolver: Callable[[str], str | None],
        *,
        chain: AwsCredentialChain | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._credential_resolver = credential_resolver
        self._chain = chain or AwsCredentialChain()
        self._client = client

    async def stream(
        self, request: ModelRequest, cancellation: CancellationToken
    ) -> AsyncIterator[ProviderEvent]:
        cancellation.raise_if_cancelled()
        endpoint = request.profile.base_url.rstrip("/")
        region = resolve_bedrock_region(endpoint)
        if not endpoint:
            endpoint = f"https://bedrock-runtime.{region}.amazonaws.com"
        model = quote(request.profile.model, safe="._-:/")
        url = f"{endpoint}/model/{model}/converse-stream"
        body = json.dumps(
            build_payload("bedrock-converse-stream", request), separators=(",", ":")
        ).encode()
        headers = {
            "accept": "application/vnd.amazon.eventstream",
            "content-type": "application/json",
            "x-amzn-bedrock-accept": "application/json",
            **request.profile.headers,
        }
        bearer = self._credential_resolver(
            request.profile.credential_ref or request.profile.provider_id
        )
        if bearer:
            headers["authorization"] = f"Bearer {bearer}"
        else:
            credentials = await self._chain.resolve_async(region, cancellation)
            if not credentials:
                raise ProviderError("Amazon Bedrock credentials are not configured.")
            headers = sign_bedrock_request("POST", url, headers, body, credentials, region)
        client = self._client or httpx.AsyncClient(timeout=request.profile.timeout_seconds)
        owns_client = self._client is None
        decoder = AwsEventStreamDecoder()
        text: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        usage = Usage()
        stop_reason: Literal["stop", "length", "tool_use"] = "stop"
        try:
            async with client.stream("POST", url, headers=headers, content=body) as response:
                if response.status_code >= 400:
                    detail = (await response.aread()).lower()
                    if b"context" in detail and (b"window" in detail or b"length" in detail):
                        raise ContextOverflowError()
                    raise ProviderError(
                        f"Amazon Bedrock request failed ({response.status_code}).",
                        retryable=response.status_code in {408, 429, 500, 502, 503, 504},
                        status_code=response.status_code,
                    )
                yield ProviderEvent(ProviderEventType.MESSAGE_START)
                async for chunk in response.aiter_bytes():
                    cancellation.raise_if_cancelled()
                    for event in decoder.feed(chunk):
                        yielded, usage, stop_reason = _consume_event(
                            event, calls, text, usage, stop_reason
                        )
                        for item in yielded:
                            yield item
                content: list[TextContent | ToolCall] = [TextContent("".join(text))] if text else []
                for state in calls.values():
                    try:
                        arguments = json.loads(state["arguments"] or "{}")
                    except json.JSONDecodeError as error:
                        raise ProviderError(
                            "Amazon Bedrock returned invalid tool arguments."
                        ) from error
                    if not isinstance(arguments, dict) or not state["name"]:
                        raise ProviderError("Amazon Bedrock returned an incomplete tool call.")
                    content.append(ToolCall(state["id"], state["name"], arguments))
                yield ProviderEvent(
                    ProviderEventType.MESSAGE_END,
                    message=AssistantMessage(
                        tuple(content),
                        api="bedrock-converse-stream",
                        provider=request.profile.provider_id,
                        model=request.profile.model,
                        usage=usage,
                        stop_reason="tool_use" if calls else stop_reason,
                    ),
                )
        except httpx.TimeoutException as error:
            raise ProviderError("Amazon Bedrock request timed out.", retryable=True) from error
        except httpx.HTTPError as error:
            raise ProviderError("Amazon Bedrock transport failed.", retryable=True) from error
        finally:
            if owns_client:
                await client.aclose()


def _consume_event(
    event: dict[str, object],
    calls: dict[int, dict[str, str]],
    text: list[str],
    usage: Usage,
    stop_reason: Literal["stop", "length", "tool_use"],
) -> tuple[list[ProviderEvent], Usage, Literal["stop", "length", "tool_use"]]:
    result: list[ProviderEvent] = []
    start = event.get("contentBlockStart")
    if isinstance(start, dict):
        index = start.get("contentBlockIndex")
        member = start.get("start")
        if (
            isinstance(index, int)
            and isinstance(member, dict)
            and isinstance(member.get("toolUse"), dict)
        ):
            tool = member["toolUse"]
            identifier, name = tool.get("toolUseId"), tool.get("name")
            if isinstance(identifier, str) and isinstance(name, str):
                calls[index] = {"id": identifier, "name": name, "arguments": ""}
                result.append(
                    ProviderEvent(
                        ProviderEventType.TOOL_CALL_START,
                        tool_call=ToolCall(identifier, name, {}),
                        content_index=index,
                    )
                )
    delta_event = event.get("contentBlockDelta")
    if isinstance(delta_event, dict):
        index, delta = delta_event.get("contentBlockIndex"), delta_event.get("delta")
        if isinstance(index, int) and isinstance(delta, dict):
            value = delta.get("text")
            if isinstance(value, str):
                text.append(value)
                result.append(
                    ProviderEvent(ProviderEventType.TEXT_DELTA, delta=value, content_index=index)
                )
            tool = delta.get("toolUse")
            if isinstance(tool, dict) and isinstance(tool.get("input"), str) and index in calls:
                calls[index]["arguments"] += tool["input"]
                result.append(
                    ProviderEvent(
                        ProviderEventType.TOOL_CALL_ARGUMENTS_DELTA,
                        delta=tool["input"],
                        content_index=index,
                    )
                )
    metadata = event.get("metadata")
    if isinstance(metadata, dict):
        raw = metadata.get("usage")
        if isinstance(raw, dict):
            usage = Usage(
                input_tokens=int(raw.get("inputTokens", 0)),
                output_tokens=int(raw.get("outputTokens", 0)),
                cache_read_tokens=int(raw.get("cacheReadInputTokens", 0)),
                cache_write_tokens=int(raw.get("cacheWriteInputTokens", 0)),
                total_tokens=int(
                    raw.get("totalTokens", raw.get("inputTokens", 0) + raw.get("outputTokens", 0))
                ),
            )
            result.append(ProviderEvent(ProviderEventType.USAGE, usage=usage))
    message_stop = event.get("messageStop")
    if isinstance(message_stop, dict):
        reason = message_stop.get("stopReason")
        if reason in {"max_tokens", "model_context_window_exceeded"}:
            stop_reason = "length"
        elif reason == "tool_use":
            stop_reason = "tool_use"
    return result, usage, stop_reason
