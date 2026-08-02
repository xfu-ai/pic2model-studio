from __future__ import annotations

import pytest

from aipic_to_model.infrastructure.providers.image_payloads import structure_strength_directive


@pytest.mark.parametrize(
    ("strength", "prefix"),
    [
        (0.0, "Exploratory variation:"),
        (0.34, "Exploratory variation:"),
        (0.35, "Guided variation:"),
        (0.64, "Guided variation:"),
        (0.65, "Subject lock:"),
        (0.89, "Subject lock:"),
        (0.9, "Structure lock:"),
        (1.0, "Structure lock:"),
    ],
)
def test_structure_strength_mapping_is_prompt_only(strength: float, prefix: str) -> None:
    assert structure_strength_directive(strength).startswith(prefix)
