from __future__ import annotations

import base64
import json

import httpx

from aipic_to_model.infrastructure.providers.config import MeshyImageSettings
from aipic_to_model.infrastructure.providers.meshy_image import MeshyTextToImageProvider


def test_meshy_text_to_image_materializes_each_candidate_without_leaking_secret_or_urls() -> None:
    secret = "meshy-test-secret"
    seen: list[httpx.Request] = []
    created = 0

    def transport(request: httpx.Request) -> httpx.Response:
        nonlocal created
        seen.append(request)
        if request.method == "POST":
            created += 1
            assert request.url.path == "/openapi/v1/text-to-image"
            assert json.loads(request.content) == {
                "ai_model": "nano-banana",
                "prompt": "a red cube",
                "aspect_ratio": "1:1",
            }
            return httpx.Response(200, json={"result": f"task-{created}"}, request=request)
        if request.url.host == "api.meshy.ai":
            task_id = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(
                200,
                json={
                    "id": task_id,
                    "status": "SUCCEEDED",
                    "image_urls": [f"https://assets.meshy.ai/results/{task_id}.png?signature=private"],
                },
                request=request,
            )
        if request.url.host == "assets.meshy.ai":
            return httpx.Response(
                200,
                headers={"content-type": "image/png"},
                content=b"png-bytes",
                request=request,
            )
        raise AssertionError(f"unexpected URL: {request.url}")

    provider = MeshyTextToImageProvider(
        MeshyImageSettings(poll_interval_seconds=0),
        lambda: secret,
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )

    result = provider.generate(
        {
            "provider_profile": "meshy/default",
            "channel": "meshy",
            "mode": "t2i",
            "model": "nano-banana",
            "candidate_count": 2,
            "aspect_ratio": "1:1",
            "prompt": "a red cube",
        }
    )

    assert result.ok
    assert result.payload == {
        "images": [{"base64": base64.b64encode(b"png-bytes").decode("ascii")}] * 2
    }
    serialized = result.model_dump_json()
    assert secret not in serialized and "signature" not in serialized
    assert all(request.headers["authorization"] == f"Bearer {secret}" for request in seen)


def test_meshy_image_to_image_uses_a_data_uri_reference() -> None:
    seen: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST":
            assert request.url.path == "/openapi/v1/image-to-image"
            body = json.loads(request.content)
            assert body["reference_image_urls"] == [
                "data:image/png;base64," + base64.b64encode(b"source").decode("ascii")
            ]
            return httpx.Response(200, json={"result": "task-edit"}, request=request)
        if request.url.host == "api.meshy.ai":
            return httpx.Response(
                200,
                json={
                    "status": "SUCCEEDED",
                    "image_urls": ["https://assets.meshy.ai/results/edit.png"],
                },
                request=request,
            )
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=b"edited-png",
            request=request,
        )

    provider = MeshyTextToImageProvider(
        MeshyImageSettings(poll_interval_seconds=0),
        lambda: "secret",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )

    result = provider.generate(
        {
            "channel": "meshy",
            "mode": "i2i",
            "model": "nano-banana",
            "candidate_count": 1,
            "prompt": "a red cube",
            "source_bytes": b"source",
            "source_mime": "image/png",
        }
    )

    assert result.ok
    assert result.payload == {
        "images": [{"base64": base64.b64encode(b"edited-png").decode("ascii")}]
    }


def test_meshy_cancel_during_poll_stops_before_another_poll_or_candidate() -> None:
    seen: list[httpx.Request] = []
    cancel_checks = 0
    heartbeats = 0

    def transport(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"result": "task-1"}, request=request)
        return httpx.Response(
            200,
            json={"id": "task-1", "status": "IN_PROGRESS"},
            request=request,
        )

    def cancelled() -> bool:
        nonlocal cancel_checks
        cancel_checks += 1
        return cancel_checks >= 4

    def heartbeat() -> None:
        nonlocal heartbeats
        heartbeats += 1

    provider = MeshyTextToImageProvider(
        MeshyImageSettings(poll_interval_seconds=0),
        lambda: "secret",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )
    result = provider.generate(
        {
            "provider_profile": "meshy/default",
            "channel": "meshy",
            "mode": "t2i",
            "model": "nano-banana",
            "candidate_count": 2,
            "prompt": "a red cube",
            "_cancelled": cancelled,
            "_heartbeat": heartbeat,
        }
    )

    assert not result.ok
    assert result.stage == "cancelled"
    assert [request.method for request in seen] == ["POST", "GET"]
    assert heartbeats >= 3


def test_meshy_connect_failure_is_safe_to_retry_without_a_preflight_request() -> None:
    seen: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        raise httpx.ConnectError("tls route unavailable", request=request)

    provider = MeshyTextToImageProvider(
        MeshyImageSettings(),
        lambda: "secret",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )

    result = provider.generate(
        {
            "provider_profile": "meshy/default",
            "channel": "meshy",
            "mode": "t2i",
            "model": "nano-banana",
            "candidate_count": 2,
            "prompt": "a red cube",
        }
    )

    assert not result.ok
    assert result.error is not None
    assert result.error.code == "PROVIDER_UNAVAILABLE"
    assert result.error.safe_to_retry is True
    assert result.error.fee_incurred is False
    assert [request.method for request in seen] == ["POST"]


def test_meshy_read_failure_during_create_requires_manual_confirmation() -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("response lost", request=request)

    provider = MeshyTextToImageProvider(
        MeshyImageSettings(),
        lambda: "secret",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )

    result = provider.generate(
        {
            "provider_profile": "meshy/default",
            "channel": "meshy",
            "mode": "t2i",
            "model": "nano-banana",
            "candidate_count": 2,
            "prompt": "a red cube",
        }
    )

    assert not result.ok
    assert result.error is not None
    assert result.error.code == "JOB_UNKNOWN_SUBMISSION"
    assert result.error.safe_to_retry is False
    assert result.error.fee_incurred is True
    assert result.error.recommended_action.value == "query_remote"
