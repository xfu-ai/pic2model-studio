"""Tripo payload mapping; only this adapter translates domain ``side`` to ``left``."""

from __future__ import annotations

from ...domain.production_models import TripoGenerationRequest


def build_tripo_payload(
    request: TripoGenerationRequest, remote_inputs: dict[str, str]
) -> dict[str, object]:
    """Build the v3 body from opaque upload references.

    Provider URLs and credentials never enter this function.  The historical
    gateway has separate endpoints for image and multiview work, so the body
    deliberately has no invented ``type`` field.
    """
    params = request.parameters
    payload: dict[str, object] = {
        "model": params.model_version,
        "texture": params.texture,
        "pbr": params.pbr,
        "texture_quality": params.texture_quality,
        "texture_alignment": params.texture_alignment,
        "auto_size": params.auto_size,
        "orientation": params.orientation,
    }
    is_p_series = params.model_version.upper().startswith("P")
    if params.face_limit:
        payload["face_limit"] = params.face_limit
    if not is_p_series and not params.model_version.startswith("v2"):
        payload["geometry_quality"] = params.geometry_quality
    if not is_p_series:
        payload["quad"] = params.quad
        if params.smart_low_poly:
            payload["smart_low_poly"] = True
        if params.generate_parts:
            payload["generate_parts"] = True
    if params.compress:
        payload["compress"] = params.compress
    if params.model_seed:
        payload["model_seed"] = params.model_seed
    if params.texture_seed:
        payload["texture_seed"] = params.texture_seed
    if request.mode == "image":
        image_id = request.image_asset_id
        if not image_id or image_id not in remote_inputs:
            raise ValueError("missing managed image upload")
        payload["input"] = remote_inputs[image_id]
        if params.enable_image_autofix:
            payload["enable_image_autofix"] = True
        return payload
    if set(request.view_asset_ids) != {"front", "side", "back"}:
        raise ValueError("multiview requires front, side, and back asset IDs")
    try:
        payload["inputs"] = [
            {"front": remote_inputs[request.view_asset_ids["front"]]},
            {"left": remote_inputs[request.view_asset_ids["side"]]},
            {"back": remote_inputs[request.view_asset_ids["back"]]},
        ]
    except KeyError as error:
        raise ValueError("missing managed multiview upload") from error
    return payload
