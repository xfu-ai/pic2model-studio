"""Shared paid-submission policy used by dispatch, handlers, and recovery."""

from __future__ import annotations

PAID_SUBMISSION_TOOLS = frozenset(
    {
        "image.generate",
        "image.transform",
        "image.generate_variants",
        "image.inpaint_selection",
        "element.split",
        "multiview.generate",
        "multiview.regenerate_view",
        "model3d.generate",
    }
)
