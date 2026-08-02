from __future__ import annotations

import hashlib
import os
import time
from io import BytesIO
from typing import Literal, cast

import pytest
from PIL import Image, ImageDraw

from aipic_to_model.application.jobs.secure_download import (
    APPROVED_GLB_ARTIFACT_MIME_TYPES,
    download_glb_to_part,
)
from aipic_to_model.domain.production_models import TripoGenerationRequest, TripoParameters
from aipic_to_model.domain.provider_models import ProviderResult
from aipic_to_model.infrastructure.keyring_store import OSKeyringStore
from aipic_to_model.infrastructure.providers.config import (
    TRIPO_PROFILE,
    CredentialResolver,
)
from aipic_to_model.infrastructure.providers.tripo_http import (
    HttpFileTransferProvider,
    HttpTripo3DProvider,
    TripoHttpSettings,
)
from aipic_to_model.infrastructure.providers.tripo_payloads import build_tripo_payload


def _minimal_png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (64, 64), "gray").save(output, "PNG")
    return output.getvalue()


def _minimal_multiview_pngs() -> dict[str, bytes]:
    """Create three consistent, non-production views of one simple block."""
    views: dict[str, bytes] = {}
    faces = {
        "front": ((72, 52), (184, 52), (184, 202), (72, 202)),
        "side": ((92, 52), (164, 78), (164, 202), (92, 176)),
        "back": ((72, 52), (184, 52), (184, 202), (72, 202)),
    }
    colors = {"front": "#7f8c8d", "side": "#95a5a6", "back": "#6c7a7a"}
    for view, polygon in faces.items():
        image = Image.new("RGB", (256, 256), "white")
        draw = ImageDraw.Draw(image)
        draw.polygon(polygon, fill=colors[view], outline="#34495e", width=4)
        output = BytesIO()
        image.save(output, "PNG")
        views[view] = output.getvalue()
    return views


def _tripo_settings() -> tuple[TripoHttpSettings, frozenset[str]]:
    allowed_hosts = frozenset(
        item.strip().lower()
        for item in os.environ.get(
            "TRIPO_ARTIFACT_HOSTS",
            ("cdn.tripo3d.ai,tripo-data.rg1.data.tripo3d.com,tripo-data.cdn.rg1.data.tripo3d.com"),
        ).split(",")
        if item.strip()
    )
    return (
        TripoHttpSettings(
            os.environ.get("TRIPO_BASE_URL", "https://openapi.tripo3d.ai"),
            allowed_hosts,
            timeout_seconds=60,
        ),
        allowed_hosts,
    )


def _tripo_smoke_model_version() -> Literal[
    "v3.1-20260211", "v3.0-20250812", "v2.5-20250123", "P1-20260311"
]:
    value = os.environ.get("TRIPO_SMOKE_MODEL_VERSION", "v3.1-20260211")
    allowed = {"v3.1-20260211", "v3.0-20250812", "v2.5-20250123", "P1-20260311"}
    if value not in allowed:
        raise ValueError("TRIPO_SMOKE_MODEL_VERSION is not an approved Tripo model")
    return cast(
        Literal["v3.1-20260211", "v3.0-20250812", "v2.5-20250123", "P1-20260311"],
        value,
    )


@pytest.mark.real_provider
def test_tripo_minimal_single_image_lifecycle(tmp_path) -> None:
    credentials = CredentialResolver(OSKeyringStore())
    assert credentials.get(TRIPO_PROFILE), "tripo3d/default is not configured"
    settings, allowed_hosts = _tripo_settings()
    provider = HttpTripo3DProvider(settings, credentials.callback(TRIPO_PROFILE))
    balance = provider.balance()
    assert balance.ok, balance.error.code if balance.error else "balance response invalid"
    assert float(balance.payload["balance"]) > 0, "Tripo OpenAPI balance is zero"
    image = _minimal_png()
    transfer = HttpFileTransferProvider(
        settings,
        credentials.callback(TRIPO_PROFILE),
        lambda _asset_id: image,
    )
    uploaded = transfer.upload(
        asset_id="smoke-image",
        content_sha256=hashlib.sha256(image).hexdigest(),
        size_bytes=len(image),
        mime_type="image/png",
    )
    assert uploaded.ok, uploaded.error.code if uploaded.error else "upload response invalid"
    token = uploaded.payload["remote_input"]["opaque_input_id"]
    request = TripoGenerationRequest(
        mode="image",
        provider_profile=TRIPO_PROFILE,
        model="tripo",
        image_asset_id="smoke-image",
        parameters=TripoParameters(
            model_version=_tripo_smoke_model_version(),
            texture=False,
            pbr=False,
        ),
    )
    created = provider.create(
        build_tripo_payload(request, {"smoke-image": str(token)}),
        idempotency_key=f"b02-smoke-{hashlib.sha256(image).hexdigest()}",
    )
    assert created.ok, created.error.code if created.error else "create response invalid"
    task_id = created.payload["external_task_id"]
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        state = provider.get(str(task_id))
        assert not isinstance(state, ProviderResult), (
            state.error.code if isinstance(state, ProviderResult) and state.error else "poll failed"
        )
        if state.status in {"succeeded", "failed", "cancelled", "unknown"}:
            break
        time.sleep(5)
    assert state.status == "succeeded"
    artifact = next((item for item in state.artifacts if item.kind == "glb"), None)
    assert artifact is not None, "Tripo succeeded without an approved GLB artifact host"
    part = tmp_path / "model.glb.part"
    response = provider.open_artifact(
        external_task_id=str(task_id),
        artifact=artifact,
        offset=0,
    )
    observed_mime = (response.content_type or "").split(";", 1)[0].strip().lower()
    assert observed_mime in APPROVED_GLB_ARTIFACT_MIME_TYPES, (
        "Tripo returned an unapproved GLB MIME type: " + repr(observed_mime)
    )
    receipt = download_glb_to_part(
        response,
        part_path=part,
        part_root=tmp_path,
        allowed_hosts=allowed_hosts,
        maximum_bytes=500 * 1024 * 1024,
        expected_size=artifact.expected_size,
    )
    assert receipt.size_bytes > 20
    assert len(receipt.sha256) == 64


@pytest.mark.real_provider
def test_tripo_minimal_multiview_lifecycle(tmp_path) -> None:
    credentials = CredentialResolver(OSKeyringStore())
    assert credentials.get(TRIPO_PROFILE), "tripo3d/default is not configured"
    settings, allowed_hosts = _tripo_settings()
    provider = HttpTripo3DProvider(settings, credentials.callback(TRIPO_PROFILE))
    balance = provider.balance()
    assert balance.ok, balance.error.code if balance.error else "balance response invalid"
    assert float(balance.payload["balance"]) > 0, "Tripo OpenAPI balance is zero"
    views = _minimal_multiview_pngs()
    transfer = HttpFileTransferProvider(
        settings,
        credentials.callback(TRIPO_PROFILE),
        lambda asset_id: views[asset_id],
    )
    tokens: dict[str, str] = {}
    for view, content in views.items():
        uploaded = transfer.upload(
            asset_id=view,
            content_sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            mime_type="image/png",
        )
        assert uploaded.ok, uploaded.error.code if uploaded.error else "upload response invalid"
        tokens[view] = str(uploaded.payload["remote_input"]["opaque_input_id"])
    request = TripoGenerationRequest(
        mode="multiview",
        multiview_set_id="smoke-multiview-set",
        provider_profile=TRIPO_PROFILE,
        model="tripo",
        view_asset_ids={"front": "front", "side": "side", "back": "back"},
        parameters=TripoParameters(
            model_version=_tripo_smoke_model_version(),
            texture=False,
            pbr=False,
        ),
    )
    payload = build_tripo_payload(request, tokens)
    assert payload["inputs"] == [
        {"front": tokens["front"]},
        {"left": tokens["side"]},
        {"back": tokens["back"]},
    ]
    idempotency_material = "".join(
        hashlib.sha256(views[view]).hexdigest() for view in sorted(views)
    )
    created = provider.create(
        payload,
        idempotency_key="b02-smoke-multiview-"
        + hashlib.sha256(idempotency_material.encode()).hexdigest(),
    )
    assert created.ok, created.error.code if created.error else "create response invalid"
    task_id = created.payload["external_task_id"]
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        state = provider.get(str(task_id))
        assert not isinstance(state, ProviderResult), (
            state.error.code if isinstance(state, ProviderResult) and state.error else "poll failed"
        )
        if state.status in {"succeeded", "failed", "cancelled", "unknown"}:
            break
        time.sleep(5)
    assert state.status == "succeeded"
    artifact = next((item for item in state.artifacts if item.kind == "glb"), None)
    assert artifact is not None, "Tripo succeeded without an approved GLB artifact host"
    response = provider.open_artifact(
        external_task_id=str(task_id),
        artifact=artifact,
        offset=0,
    )
    receipt = download_glb_to_part(
        response,
        part_path=tmp_path / "multiview.glb.part",
        part_root=tmp_path,
        allowed_hosts=allowed_hosts,
        maximum_bytes=500 * 1024 * 1024,
        expected_size=artifact.expected_size,
    )
    assert receipt.content_type == "binary/octet-stream"
    assert receipt.size_bytes > 20
    assert len(receipt.sha256) == 64
