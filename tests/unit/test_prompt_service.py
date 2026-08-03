from __future__ import annotations

import pytest

from aipic_to_model.application.prompt_service import extract_bilingual_versions, merge_versions
from aipic_to_model.domain.prompt_parser import (
    BilingualPrompt,
    PromptParseError,
    merge_prompts,
    parse_bilingual,
    serialize_prompt,
)

RESPONSE = serialize_prompt(BilingualPrompt(
    "主体为机械角色，采用正面构图。",
    "The subject is a mechanical character in a frontal composition.",
    "机械角色，正面构图。",
    "mechanical character, frontal composition.",
    ("机械角色", "正面构图"),
    ("文字",),
))


def test_strict_bilingual_parse_and_versioned_merge() -> None:
    content, drafts = extract_bilingual_versions(RESPONSE, kind="content")
    style = BilingualPrompt("油画笔触分析", "oil brushwork analysis", "油画笔触", "oil brushwork")
    merged = merge_versions(content, style)
    assert [item.language for item in drafts] == ["zh", "en"]
    assert "内容规格" in merged[0].body
    assert "Visual specification" in merged[1].body
    assert "内容规格" in merge_prompts("主体。", "风格。", "zh")
    assert "бк" not in merged[1].body
    assert "spatial relationships" in merged[1].body


@pytest.mark.parametrize(
    "response",
    [
        "## ZH\nonly",
        "{}",
        '{"schema":"pic2model.prompt.v1","analysis":{},"generation":{},"constraints":{}}',
    ],
)
def test_malformed_bilingual_responses_are_rejected(response: str) -> None:
    with pytest.raises(PromptParseError):
        parse_bilingual(response)


def test_v3_round_trip_preserves_explicit_constraints() -> None:
    parsed = parse_bilingual(RESPONSE)
    assert parsed.zh_prompt == "机械角色，正面构图。"
    assert parsed.preserve == ("机械角色", "正面构图")
    assert parsed.avoid == ("文字",)


def test_old_markdown_wire_format_is_intentionally_rejected() -> None:
    with pytest.raises(PromptParseError, match="valid JSON"):
        parse_bilingual("## ZH\n```prompt\n旧格式\n```\n## EN\n```prompt\nold format\n```")
