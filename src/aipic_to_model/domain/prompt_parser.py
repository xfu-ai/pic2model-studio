"""Structured, versioned prompt documents for the Pic2Model Studio workflow."""

from __future__ import annotations

import json
from dataclasses import dataclass

PARSER_VERSION = 3
PROMPT_SCHEMA = "pic2model.prompt.v1"


class PromptParseError(ValueError):
    """A response cannot safely become a managed Prompt version."""


@dataclass(frozen=True)
class BilingualPrompt:
    """Editable analysis and generation text plus explicit generation constraints."""

    zh_segment: str
    en_segment: str
    zh_prompt: str
    en_prompt: str
    preserve: tuple[str, ...] = ()
    avoid: tuple[str, ...] = ()
    parser_version: int = PARSER_VERSION


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PromptParseError(f"{field} must be a non-empty string")
    return value.strip()


def _string_list(value: object, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PromptParseError(f"{field} must be an array of strings")
    return tuple(item.strip() for item in value if item.strip())


def parse_bilingual(response: str) -> BilingualPrompt:
    """Parse the strict ``pic2model.prompt.v1`` JSON contract.

    The historical Markdown wire format is intentionally unsupported. Provider
    responses, managed Prompt assets, and rewrite results all use one schema.
    """

    try:
        payload = json.loads(response)
    except json.JSONDecodeError as error:
        raise PromptParseError("Prompt must be valid JSON") from error
    if not isinstance(payload, dict) or payload.get("schema") != PROMPT_SCHEMA:
        raise PromptParseError(f"Prompt schema must be {PROMPT_SCHEMA}")
    analysis = payload.get("analysis")
    generation = payload.get("generation")
    constraints = payload.get("constraints", {})
    if not isinstance(analysis, dict) or not isinstance(generation, dict):
        raise PromptParseError("Prompt requires analysis and generation objects")
    if not isinstance(constraints, dict):
        raise PromptParseError("Prompt constraints must be an object")
    return BilingualPrompt(
        _text(analysis.get("zh"), "analysis.zh"),
        _text(analysis.get("en"), "analysis.en"),
        _text(generation.get("zh"), "generation.zh"),
        _text(generation.get("en"), "generation.en"),
        _string_list(constraints.get("preserve"), "constraints.preserve"),
        _string_list(constraints.get("avoid"), "constraints.avoid"),
    )


def serialize_prompt(bilingual: BilingualPrompt) -> str:
    """Serialize one managed Prompt using a stable, readable JSON shape."""

    return json.dumps(
        {
            "schema": PROMPT_SCHEMA,
            "analysis": {"zh": bilingual.zh_segment, "en": bilingual.en_segment},
            "generation": {"zh": bilingual.zh_prompt, "en": bilingual.en_prompt},
            "constraints": {
                "preserve": list(bilingual.preserve),
                "avoid": list(bilingual.avoid),
            },
        },
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def merge_prompt_documents(content: BilingualPrompt, style: BilingualPrompt) -> BilingualPrompt:
    """Compose content and visual direction without using a frozen prose template."""

    zh_prompt = (
        f"内容规格：{content.zh_prompt}。视觉规格：{style.zh_prompt}。"
        "生成时以内容规格确定主体、姿态、构图、镜头和空间关系；"
        "以视觉规格确定色彩、光照、表面质感、细节密度与表现媒介。"
    )
    en_prompt = (
        f"Content specification: {content.en_prompt}. Visual specification: {style.en_prompt}. "
        "Use the content specification for subject identity, pose, composition, camera, and spatial "
        "relationships. Use the visual specification for palette, lighting, surface treatment, detail "
        "density, and medium."
    )
    preserve = tuple(dict.fromkeys((*content.preserve, *style.preserve)))
    avoid = tuple(dict.fromkeys((*content.avoid, *style.avoid)))
    return BilingualPrompt(
        f"内容分析：{content.zh_segment}\n视觉分析：{style.zh_segment}",
        f"Content analysis: {content.en_segment}\nVisual analysis: {style.en_segment}",
        zh_prompt,
        en_prompt,
        preserve,
        avoid,
    )


def merge_prompts(content_prompt: str, style_prompt: str, language: str) -> str:
    """Compatibility helper for callers that only need one composed language."""

    if not content_prompt.strip() or not style_prompt.strip():
        raise PromptParseError("Content and visual prompts must both be non-empty")
    if language.lower() == "zh":
        return (
            f"内容规格：{content_prompt.strip()}。视觉规格：{style_prompt.strip()}。"
            "主体、构图、镜头与空间关系遵循内容规格；色彩、光照、质感与媒介遵循视觉规格。"
        )
    return (
        f"Content specification: {content_prompt.strip()}. Visual specification: {style_prompt.strip()}. "
        "Follow the content specification for subject, composition, camera, and spatial relationships; "
        "follow the visual specification for palette, lighting, texture, and medium."
    )
