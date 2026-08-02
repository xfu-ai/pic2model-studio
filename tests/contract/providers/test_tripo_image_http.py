from __future__ import annotations

import base64
import json

import httpx
import pytest

from aipic_to_model.domain.provider_models import ProviderResult
from aipic_to_model.infrastructure.providers.config import TripoImageSettings
from aipic_to_model.infrastructure.providers.tripo_image import TripoTextToImageProvider


def test_tripo_probe_reads_balance_without_creating_a_generation_task() -> None:
    seen: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET"
        assert request.url.path == "/v3/account/balance"
        return httpx.Response(200, json={"code": 0, "data": {"balance": 10}}, request=request)

    provider = TripoTextToImageProvider(
        TripoImageSettings(),
        lambda: "tripo-secret",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )

    result = provider.probe()

    assert result.ok
    assert len(seen) == 1
    assert seen[0].headers["authorization"] == "Bearer tripo-secret"


def test_tripo_text_to_image_materializes_candidates_without_leaking_signed_urls() -> None:
    seen: list[httpx.Request] = []
    created = 0

    def transport(request: httpx.Request) -> httpx.Response:
        nonlocal created
        seen.append(request)
        if request.method == "POST":
            created += 1
            assert request.url.host == "api.tripo3d.ai"
            assert request.url.path == "/v2/openapi/task"
            assert json.loads(request.content) == {
                "type": "text_to_image",
                "prompt": "a red cube",
            }
            return httpx.Response(
                200,
                json={"code": 0, "data": {"task_id": f"task-{created}"}},
                request=request,
            )
        if request.url.host == "api.tripo3d.ai":
            assert request.url.path.startswith("/v2/openapi/task/")
            task_id = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "status": "success",
                        "output": {
                            "generated_image": (
                                f"https://assets.tripo3d.ai/images/{task_id}.png?signature=private"
                            )
                        },
                    },
                },
                request=request,
            )
        assert "authorization" not in request.headers
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=b"tripo-png",
            request=request,
        )

    provider = TripoTextToImageProvider(
        TripoImageSettings(poll_interval_seconds=0),
        lambda: "tripo-secret",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )

    result = provider.generate(
        {
            "provider_profile": "tripo3d/default",
            "channel": "tripo",
            "mode": "t2i",
            "model": "seedream_v5",
            "candidate_count": 2,
            "prompt": "a red cube",
            "size": "2K",
            "output_format": "png",
        }
    )

    assert result.ok
    assert result.payload == {
        "images": [{"base64": base64.b64encode(b"tripo-png").decode("ascii")}] * 2
    }
    serialized = result.model_dump_json()
    assert "tripo-secret" not in serialized and "signature" not in serialized
    assert [request.method for request in seen] == ["POST", "GET", "GET"] * 2


def test_tripo_bounds_long_prompts_at_a_word_boundary_before_submission() -> None:
    submitted_prompt = ""

    def transport(request: httpx.Request) -> httpx.Response:
        nonlocal submitted_prompt
        if request.method == "POST":
            payload = json.loads(request.content)
            submitted_prompt = payload["prompt"]
            return httpx.Response(
                200,
                json={"code": 0, "data": {"task_id": "bounded-task"}},
                request=request,
            )
        if request.url.host == "api.tripo3d.ai":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "status": "success",
                        "output": {
                            "generated_image": "https://cdn.tripo3d.ai/bounded.png"
                        },
                    },
                },
                request=request,
            )
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=b"bounded-png",
            request=request,
        )

    long_prompt = ("intricate brass statue on a neutral background " * 40).strip()
    provider = TripoTextToImageProvider(
        TripoImageSettings(poll_interval_seconds=0),
        lambda: "tripo-secret",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )

    result = provider.generate(
        {
            "provider_profile": "tripo3d/default",
            "channel": "tripo",
            "mode": "t2i",
            "model": "auto",
            "candidate_count": 1,
            "prompt": long_prompt,
        }
    )

    assert result.ok
    assert 0 < len(submitted_prompt) <= 1024
    assert long_prompt.startswith(submitted_prompt)
    assert not submitted_prompt.endswith(" ")
    assert long_prompt[len(submitted_prompt)] == " "


def test_tripo_image_to_image_uploads_once_and_uses_advanced_generate_image_tasks() -> None:
    seen: list[httpx.Request] = []
    created = 0
    edit_prompt = (
        "replace the red cube with a blue cube while preserving the composition " * 20
    ).strip()

    def transport(request: httpx.Request) -> httpx.Response:
        nonlocal created
        seen.append(request)
        if request.url.path == "/v2/openapi/upload/sts":
            assert request.method == "POST"
            assert b'filename="managed-input.png"' in request.content
            assert b"Content-Type: image/png" in request.content
            return httpx.Response(
                200,
                json={"code": 0, "data": {"image_token": "image-token"}},
                request=request,
            )
        if request.url.path == "/v2/openapi/task" and request.method == "POST":
            created += 1
            payload = json.loads(request.content)
            submitted_prompt = payload.pop("prompt")
            assert payload == {
                "type": "generate_image",
                "model_version": "gemini_3.1_flash_image_preview",
                "file": {"type": "png", "file_token": "image-token"},
            }
            assert 0 < len(submitted_prompt) <= 1024
            assert edit_prompt.startswith(submitted_prompt)
            return httpx.Response(
                200,
                json={"code": 0, "data": {"task_id": f"edit-{created}"}},
                request=request,
            )
        if request.url.host == "api.tripo3d.ai":
            task_id = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "status": "success",
                        "output": {
                            "generated_image": f"https://foo.data.tripo3d.com/edit/{task_id}.png"
                        },
                    },
                },
                request=request,
            )
        assert request.url.host == "foo.data.tripo3d.com"
        assert "authorization" not in request.headers
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=b"tripo-edit-png",
            request=request,
        )

    provider = TripoTextToImageProvider(
        TripoImageSettings(poll_interval_seconds=0),
        lambda: "tripo-secret",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )

    result = provider.generate(
        {
            "provider_profile": "tripo3d/default",
            "channel": "tripo",
            "mode": "i2i",
            "model": "gemini_3.1_flash_image_preview",
            "candidate_count": 2,
            "prompt": edit_prompt,
            "source_bytes": b"managed-png",
            "source_mime": "image/png",
        }
    )

    assert result.ok
    assert result.payload == {
        "images": [
            {"base64": base64.b64encode(b"tripo-edit-png").decode("ascii")}
        ]
        * 2
    }
    assert [request.url.path for request in seen] == [
        "/v2/openapi/upload/sts",
        "/v2/openapi/task",
        "/v2/openapi/task/edit-1",
        "/edit/edit-1.png",
        "/v2/openapi/task",
        "/v2/openapi/task/edit-2",
        "/edit/edit-2.png",
    ]


def test_tripo_download_rejects_lookalike_hosts_after_paid_generation() -> None:
    requested_hosts: list[str] = []

    def transport(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(str(request.url.host))
        if request.method == "POST":
            return httpx.Response(
                200,
                json={"code": 0, "data": {"task_id": "unsafe-result"}},
                request=request,
            )
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "status": "success",
                    "output": {
                        "generated_image": "https://eviltripo3d.ai/stolen.png"
                    },
                },
            },
            request=request,
        )

    provider = TripoTextToImageProvider(
        TripoImageSettings(poll_interval_seconds=0),
        lambda: "tripo-secret",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )

    result = provider.generate(
        {
            "provider_profile": "tripo3d/default",
            "channel": "tripo",
            "mode": "t2i",
            "model": "auto",
            "candidate_count": 1,
            "prompt": "a safe prompt",
        }
    )

    assert not result.ok
    assert result.error is not None
    assert result.error.failed_step == "downloading"
    assert result.error.fee_incurred is True
    assert result.error.safe_to_retry is False
    assert requested_hosts == ["api.tripo3d.ai", "api.tripo3d.ai"]


def test_tripo_download_rejects_parent_domain_suffix_tricks() -> None:
    provider = TripoTextToImageProvider(
        TripoImageSettings(),
        lambda: "tripo-secret",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: pytest.fail(f"unexpected request to {request.url.host}")
            )
        ),
    )

    result = provider._download("https://tripo3d.ai.evil.example/stolen.png")

    assert isinstance(result, ProviderResult)
    assert result.error is not None
    assert result.error.fee_incurred is True
    assert result.error.technical_message == (
        "provider_result_url; scheme=https; "
        "host=tripo3d.ai.evil.example; allowed=false"
    )
    assert "stolen.png" not in result.error.model_dump_json()


@pytest.mark.parametrize(
    "provider_url",
    [
        "http://assets.tripo3d.ai/result.png?signature=private",
        "//assets.tripo3d.ai/result.png?signature=private",
        " assets.tripo3d.ai/result.png?signature=private ",
    ],
)
def test_tripo_normalizes_official_result_urls_to_https(provider_url: str) -> None:
    seen: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=b"normalized-png",
            request=request,
        )

    provider = TripoTextToImageProvider(
        TripoImageSettings(),
        lambda: "tripo-secret",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )

    result = provider._download(provider_url)

    assert result == b"normalized-png"
    assert len(seen) == 1
    assert seen[0].url.scheme == "https"
    assert seen[0].url.host == "assets.tripo3d.ai"
    assert "authorization" not in seen[0].headers


def test_tripo_accepts_real_binary_octet_stream_when_bytes_are_a_jpeg() -> None:
    jpeg = b"\xff\xd8\xff\xe0" + b"real-tripo-jpeg" + b"\xff\xd9"

    provider = TripoTextToImageProvider(
        TripoImageSettings(),
        lambda: "tripo-secret",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "binary/octet-stream"},
                    content=jpeg,
                    request=request,
                )
            )
        ),
    )

    assert provider._download("https://tripo-data.rg1.data.tripo3d.com/result.jpeg") == jpeg


def test_tripo_rejects_binary_octet_stream_without_an_image_signature() -> None:
    provider = TripoTextToImageProvider(
        TripoImageSettings(),
        lambda: "tripo-secret",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "binary/octet-stream"},
                    content=b"not-an-image",
                    request=request,
                )
            )
        ),
    )

    result = provider._download("https://tripo-data.rg1.data.tripo3d.com/result.jpeg")

    assert isinstance(result, ProviderResult)
    assert result.error is not None
    assert result.error.failed_step == "downloading"
