from __future__ import annotations

import pytest

from aipic_to_model.application.jobs.tripo_handler import (
    generation_idempotency_key,
    handle_submission_result,
)
from aipic_to_model.domain.production_models import TripoGenerationRequest, TripoParameters
from aipic_to_model.domain.provider_models import ProviderResult
from aipic_to_model.infrastructure.providers.tripo_payloads import build_tripo_payload


def test_domain_side_is_mapped_only_to_tripo_left_payload_key() -> None:
    request = TripoGenerationRequest(
        mode="multiview",
        multiview_set_id="set-1",
        provider_profile="test",
        model="tripo",
        view_asset_ids={"front": "front-id", "side": "side-id", "back": "back-id"},
        parameters=TripoParameters(),
    )
    payload = build_tripo_payload(
        request, {"front-id": "upload-front", "side-id": "upload-side", "back-id": "upload-back"}
    )
    assert set(payload) >= {"inputs", "model"}
    assert payload["model"] == "v3.1-20260211"
    assert payload["face_limit"] == 100_000
    assert "model_version" not in payload
    assert "side" not in payload
    assert payload["inputs"] == [
        {"front": "upload-front"},
        {"left": "upload-side"},
        {"back": "upload-back"},
    ]


def test_tripo_payload_obeys_version_specific_parameter_compatibility() -> None:
    request = TripoGenerationRequest(
        mode="image",
        provider_profile="test",
        model="tripo",
        image_asset_id="image-id",
        parameters=TripoParameters(
            model_version="P1-20260311",
            geometry_quality="detailed",
            quad=True,
            smart_low_poly=True,
            generate_parts=True,
            face_limit=1234,
            compress="geometry",
            model_seed=4,
            texture_seed=5,
            enable_image_autofix=True,
        ),
    )
    payload = build_tripo_payload(request, {"image-id": "opaque-upload"})
    assert payload["input"] == "opaque-upload"
    assert payload["model"] == "P1-20260311"
    assert "model_version" not in payload
    assert payload["face_limit"] == 1234
    assert payload["enable_image_autofix"] is True
    assert {"geometry_quality", "quad", "smart_low_poly", "generate_parts"}.isdisjoint(payload)


def test_unknown_submission_is_never_treated_as_safe_to_repost() -> None:
    known = handle_submission_result(
        ProviderResult(
            ok=True,
            provider_request_id="request",
            stage="creating",
            payload={"external_task_id": "task-1"},
            retryable=False,
        )
    )
    assert known.external_task_id == "task-1"
    assert known.resume_class.value == "remote_poll"
    unknown = handle_submission_result(
        ProviderResult(ok=False, provider_request_id="request", stage="creating", retryable=True)
    )
    assert unknown.resume_class.value == "unknown_submission"
    assert unknown.requires_manual_review


def test_tripo_idempotency_uses_managed_content_hashes_and_canonical_inputs() -> None:
    request = TripoGenerationRequest(
        mode="multiview",
        multiview_set_id="set-1",
        provider_profile="test",
        model="tripo",
        view_asset_ids={"back": "back-id", "front": "front-id", "side": "side-id"},
        parameters=TripoParameters(),
    )
    first = generation_idempotency_key(
        request,
        asset_hashes={"front-id": "front-hash", "side-id": "side-hash", "back-id": "back-hash"},
    )
    second = generation_idempotency_key(
        request,
        asset_hashes={"back-id": "back-hash", "front-id": "front-hash", "side-id": "side-hash"},
    )
    assert first == second


def test_tripo_request_rejects_missing_managed_input_for_its_mode() -> None:
    with pytest.raises(ValueError, match="front, side, and back"):
        TripoGenerationRequest(
            mode="multiview",
            provider_profile="test",
            model="tripo",
            parameters=TripoParameters(),
        )
