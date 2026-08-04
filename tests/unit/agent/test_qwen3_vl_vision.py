from __future__ import annotations

import base64
from io import BytesIO

import pytest
from PIL import Image

import aipic_to_model.agent.integrations.runtime as runtime_module
from aipic_to_model.agent.core.models import (
    ImageContent,
    ManagedAssetAttachment,
    TextContent,
    UserMessage,
)
from aipic_to_model.agent.integrations.runtime import _with_request_images
from aipic_to_model.agent.providers.base import ModelProfile, ModelRequest
from aipic_to_model.agent.providers.qwen3_vl import create_qwen3_vl_profile


def _image_bytes(
    image_format: str = "PNG",
    *,
    size: tuple[int, int] = (12, 10),
) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, "navy").save(output, image_format)
    return output.getvalue()


def _request(*attachments: ManagedAssetAttachment) -> ModelRequest:
    return ModelRequest(
        create_qwen3_vl_profile(),
        (
            UserMessage(
                "inspect",
                attachments=attachments,
            ),
        ),
    )


def test_request_image_hydration_is_transient_and_preserves_the_original_request() -> None:
    content = _image_bytes()
    request = _request(ManagedAssetAttachment("asset-1", "reference.png", "image/png"))

    hydrated = _with_request_images(
        request,
        "project-1",
        lambda project_id, asset_id: (
            (
                content,
                "image/png",
            )
            if (project_id, asset_id) == ("project-1", "asset-1")
            else (b"", "")
        ),
    )

    original_user = request.messages[0]
    hydrated_user = hydrated.messages[0]
    assert isinstance(original_user, UserMessage)
    assert isinstance(hydrated_user, UserMessage)
    assert original_user.content == "inspect"
    assert isinstance(hydrated_user.content, tuple)
    assert isinstance(hydrated_user.content[0], TextContent)
    image = hydrated_user.content[1]
    assert isinstance(image, ImageContent)
    assert base64.b64decode(image.data) == content
    assert hydrated_user.attachments == original_user.attachments


@pytest.mark.parametrize(
    ("content", "reported_mime"),
    (
        (_image_bytes("JPEG"), "image/png"),
        (b"not-an-image", "image/png"),
        (_image_bytes(size=(8_193, 1)), "image/png"),
    ),
)
def test_request_image_hydration_rejects_mismatch_corruption_and_dimensions(
    content: bytes,
    reported_mime: str,
) -> None:
    request = _request(ManagedAssetAttachment("asset-1", "reference.png", "image/png"))

    with pytest.raises(RuntimeError, match="agent_attachment_invalid") as error:
        _with_request_images(
            request,
            "project-1",
            lambda _project_id, _asset_id: (content, reported_mime),
        )

    assert "not-an-image" not in str(error.value)


def test_request_image_hydration_enforces_attachment_and_total_byte_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attachments = tuple(
        ManagedAssetAttachment(f"asset-{index}", f"{index}.png", "image/png") for index in range(5)
    )
    with pytest.raises(RuntimeError, match="agent_attachment_invalid"):
        _with_request_images(_request(*attachments), "project-1", lambda *_: (b"x", "image/png"))

    content = _image_bytes()
    monkeypatch.setattr(runtime_module, "_MAX_AGENT_IMAGE_REQUEST_BYTES", len(content) - 1)
    with pytest.raises(RuntimeError, match="agent_attachment_invalid"):
        _with_request_images(
            _request(ManagedAssetAttachment("asset-1", "reference.png", "image/png")),
            "project-1",
            lambda *_: (content, "image/png"),
        )


@pytest.mark.parametrize(
    "profile",
    (
        ModelProfile("deepseek", "deepseek-v4-flash", "https://api.deepseek.com"),
        ModelProfile("ollama", "text-only-model", "http://127.0.0.1:11434/v1"),
    ),
)
def test_text_only_profiles_never_read_attachment_content(profile: ModelProfile) -> None:
    request = ModelRequest(
        profile,
        (
            UserMessage(
                "legacy",
                attachments=(ManagedAssetAttachment("asset-1", "reference.png", "image/png"),),
            ),
        ),
    )
    called = False

    def content_provider(_project_id: str, _asset_id: str) -> tuple[bytes, str]:
        nonlocal called
        called = True
        return b"", ""

    assert _with_request_images(request, "project-1", content_provider) is request
    assert called is False
