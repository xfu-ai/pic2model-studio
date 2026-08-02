from __future__ import annotations

import hashlib
import json
from io import BytesIO

import httpx
from PIL import Image

from aipic_to_model.infrastructure.providers.tripo_http import (
    HttpFileTransferProvider,
    HttpTripo3DProvider,
    TripoHttpSettings,
)


def test_tripo_v3_uses_developer_platform_paths_and_preserves_neutral_payload() -> None:
    seen: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v3/account/balance":
            return httpx.Response(
                200,
                json={"code": 0, "data": {"balance": 540.0, "frozen": 0.0}},
                request=request,
            )
        if request.url.path == "/v3/files":
            assert (
                b'filename="managed-input.png"' in request.content
                and b"Content-Type: image/png" in request.content
            )
            return httpx.Response(
                200,
                json={"code": 0, "data": {"file_token": "file-v3"}},
                request=request,
            )
        if request.url.path == "/v3/generation/image-to-model":
            assert json.loads(request.content) == {
                "input": "file-v3",
                "model": "v3.1-20260211",
                "texture": False,
                "pbr": False,
            }
            return httpx.Response(
                200,
                json={"code": 0, "data": {"task_id": "task-v3"}},
                request=request,
            )
        if request.url.path == "/v3/tasks/task-v3":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "task_id": "task-v3",
                        "status": "success",
                        "progress": 100,
                        "output": {"model_url": "https://cdn.tripo3d.ai/output/model.glb"},
                    },
                },
                request=request,
            )
        raise AssertionError(f"unexpected request path: {request.url.path}")

    client = httpx.Client(transport=httpx.MockTransport(transport))
    settings = TripoHttpSettings(
        "https://openapi.tripo3d.ai",
        frozenset({"cdn.tripo3d.ai"}),
    )
    content = b"managed-image"
    provider = HttpTripo3DProvider(
        settings,
        lambda: "secret",
        client=client,
        resolver=lambda _host: ("203.0.113.10",),
    )
    transfer = HttpFileTransferProvider(
        settings,
        lambda: "secret",
        lambda _asset_id: content,
        client=client,
    )

    balance = provider.balance()
    uploaded = transfer.upload(
        asset_id="asset",
        content_sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        mime_type="image/png",
    )
    created = provider.create(
        {
            "input": uploaded.payload["remote_input"]["opaque_input_id"],
            "model": "v3.1-20260211",
            "texture": False,
            "pbr": False,
        },
        idempotency_key="stable",
    )
    state = provider.get(str(created.payload["external_task_id"]))

    assert balance.payload == {"balance": 540.0, "frozen": 0.0}
    assert uploaded.ok and created.ok
    assert state.status == "succeeded"
    assert len(state.artifacts) == 1
    assert [request.url.path for request in seen] == [
        "/v3/account/balance",
        "/v3/files",
        "/v3/generation/image-to-model",
        "/v3/tasks/task-v3",
    ]


def test_tripo_artifact_wildcard_accepts_dynamic_official_cdn_host() -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "task_id": "task-dynamic-cdn",
                    "status": "success",
                    "progress": 100,
                    "output": {
                        "model_url": (
                            "https://tripo-data.rg1.data.tripo3d.com/output/model.glb"
                            "?signature=private"
                        )
                    },
                },
            },
            request=request,
        )

    provider = HttpTripo3DProvider(
        TripoHttpSettings("https://openapi.tripo3d.ai", frozenset({"*"})),
        lambda: "secret",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )

    state = provider.get("task-dynamic-cdn")

    assert state.status == "succeeded"
    assert len(state.artifacts) == 1
    assert state.artifacts[0].kind == "glb"


def test_tripo_v2_compatibility_translates_v3_model_field() -> None:
    seen_payload: dict[str, object] = {}

    def transport(request: httpx.Request) -> httpx.Response:
        seen_payload.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"code": 0, "data": {"task_id": "task-v2"}},
            request=request,
        )

    provider = HttpTripo3DProvider(
        TripoHttpSettings(
            "https://api.example.com",
            frozenset({"cdn.example.com"}),
            api_version="v2",
            task_path="/v2/openapi/task",
        ),
        lambda: "secret",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )

    result = provider.create(
        {
            "input": "file-token",
            "model": "v3.1-20260211",
            "texture": False,
            "pbr": False,
        },
        idempotency_key="stable",
    )

    assert result.ok
    assert seen_payload["model_version"] == "v3.1-20260211"
    assert "model" not in seen_payload


def test_tripo_task_poll_retries_transient_transport_or_service_failures() -> None:
    calls = 0
    delays: list[float] = []

    def transport(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(503, request=request)
        return httpx.Response(
            200,
            json={"data": {"task_id": "task", "status": "success", "progress": 100}},
            request=request,
        )

    provider = HttpTripo3DProvider(
        TripoHttpSettings("https://openapi.tripo3d.ai", frozenset({"cdn.tripo3d.ai"})),
        lambda: "secret",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
        poll_retry_attempts=3,
        poll_retry_initial_delay_seconds=0.25,
        sleep=delays.append,
    )

    state = provider.get("task")

    assert state.status == "succeeded"
    assert calls == 3
    assert delays == [0.25, 0.5]


def test_http_adapters_keep_credentials_urls_and_raw_responses_private() -> None:
    secret = "secret-test-token"
    signed_url = "https://cdn.example.com/model.glb?signature=private"
    seen: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/upload"):
            return httpx.Response(
                200,
                headers={"x-request-id": "upload-request"},
                json={"data": {"image_token": "opaque-upload"}},
            )
        if request.method == "POST":
            return httpx.Response(
                200,
                headers={"x-request-id": "create-request"},
                json={"data": {"task_id": "remote-task"}},
            )
        return httpx.Response(
            200,
            json={
                "data": {
                    "status": "success",
                    "artifacts": [
                        {
                            "id": "artifact-1",
                            "kind": "glb",
                            "url": signed_url,
                            "size_bytes": 123,
                        }
                    ],
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(transport))
    settings = TripoHttpSettings(
        "https://api.example.com",
        frozenset({"cdn.example.com"}),
        upload_path="/upload",
        task_path="/task",
    )
    content = b"managed-image"
    transfer = HttpFileTransferProvider(
        settings, lambda: secret, lambda _asset_id: content, client=client
    )
    uploaded = transfer.upload(
        asset_id="asset-1",
        content_sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        mime_type="image/png",
    )
    assert uploaded.ok and uploaded.payload["remote_input"]["opaque_input_id"] == "opaque-upload"

    provider = HttpTripo3DProvider(
        settings,
        lambda: secret,
        client=client,
        resolver=lambda _host: ("93.184.216.34",),
    )
    created = provider.create({"input": "opaque-upload"}, idempotency_key="idem")
    assert created.payload == {"external_task_id": "remote-task"}
    state = provider.get("remote-task")
    assert not hasattr(state, "error")
    serialized = state.model_dump_json()
    assert secret not in serialized
    assert signed_url not in serialized
    assert "signature" not in serialized
    assert all(request.headers["authorization"] == f"Bearer {secret}" for request in seen)


def test_tripo_upload_normalizes_webp_to_jpeg_after_validating_managed_bytes() -> None:
    encoded = BytesIO()
    Image.new("RGB", (16, 12), "purple").save(encoded, "WEBP")
    content = encoded.getvalue()

    def transport(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v3/files"
        assert b'filename="managed-input.jpg"' in request.content
        assert b"Content-Type: image/jpeg" in request.content
        assert b"RIFF" not in request.content
        return httpx.Response(
            200,
            json={"code": 0, "data": {"file_token": "normalized-file"}},
            request=request,
        )

    transfer = HttpFileTransferProvider(
        TripoHttpSettings(
            "https://openapi.tripo3d.ai",
            frozenset({"cdn.tripo3d.ai"}),
        ),
        lambda: "secret",
        lambda _asset_id: content,
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )
    result = transfer.upload(
        asset_id="webp-asset",
        content_sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        mime_type="image/webp",
    )

    assert result.ok
    assert result.payload["remote_input"]["opaque_input_id"] == "normalized-file"
