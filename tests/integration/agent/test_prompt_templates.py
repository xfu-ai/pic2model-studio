from __future__ import annotations

import pytest

from aipic_to_model.agent.skills import PromptTemplate, render_template


@pytest.mark.agent
def test_prompt_template_substitution_is_strict() -> None:
    template = PromptTemplate("task", "Do {{action}} on {{target}}", "project")
    assert (
        render_template(template, {"action": "read", "target": "file.txt"}) == "Do read on file.txt"
    )
    with pytest.raises(KeyError, match="target"):
        render_template(template, {"action": "read"})
