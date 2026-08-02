"""Frozen Banana/GPT Image request payload construction without HTTP or secrets."""

from __future__ import annotations

from ...domain.provider_models import GenerationRequest

_GPT_MODELS = frozenset({"gpt-image-2", "gpt-image-2-stb", "gpt-image-2-r2", "gpt-image-2-r1"})


def structure_strength_directive(strength: float) -> str:
    """Translate the UI continuity control into provider-neutral editing guidance."""
    if strength >= 0.9:
        return "Structure lock: retain silhouette, proportions, camera framing, and part placement; change only attributes explicitly requested."
    if strength >= 0.65:
        return "Subject lock: retain identity, overall geometry, and major spatial relationships while allowing requested local edits."
    if strength >= 0.35:
        return "Guided variation: keep the recognizable subject and key composition cues, while allowing substantial requested changes."
    return "Exploratory variation: use the reference for subject context only and prioritize the written request."


def banana_payload(
    request: GenerationRequest, *, prompt: str, remote_input_id: str | None = None
) -> dict[str, object]:
    if request.channel != "banana":
        raise ValueError("banana payload requires banana channel")
    if request.mode == "t2i":
        return {
            "model": request.model,
            "prompt": prompt,
            "n": request.candidate_count,
            "aspect_ratio": request.aspect_ratio,
            "imageSize": request.size,
        }
    if not remote_input_id:
        raise ValueError("image-to-image requires a provider-managed input reference")
    directive = structure_strength_directive(
        request.structure_strength if request.structure_strength is not None else 0.85
    )
    return {
        "model": request.model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"{directive}\n\n{prompt}"},
                    {"type": "provider_file", "file_id": remote_input_id},
                ],
            }
        ],
        "image_config": {"aspect_ratio": request.aspect_ratio, "image_size": request.size},
        "n": request.candidate_count,
        "response_modalities": ["IMAGE", "TEXT"],
    }


def gpt_image_payload(
    request: GenerationRequest, *, prompt: str, remote_input_id: str | None = None
) -> dict[str, object]:
    if request.channel != "gpt_image":
        raise ValueError("GPT Image payload requires gpt_image channel")
    model = request.model if request.model in _GPT_MODELS else "gpt-image-2-r1"
    if request.mode == "t2i":
        return {
            "model": model,
            "prompt": prompt,
            "n": request.candidate_count,
            "size": request.size,
            "quality": request.quality,
            "output_format": request.output_format,
        }
    if not remote_input_id:
        raise ValueError("image-to-image requires a provider-managed input reference")
    return {
        "model": model,
        "image_file_id": remote_input_id,
        "prompt": prompt,
        "n": request.candidate_count,
        "size": request.size,
        "quality": request.quality,
        "output_format": request.output_format,
    }
