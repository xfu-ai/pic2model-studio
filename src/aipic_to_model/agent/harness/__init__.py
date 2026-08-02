"""Stateful Agent harness and automatic context compaction."""

from .context import (
    DEFAULT_COMPACTION_SETTINGS,
    CompactionSettings,
    estimate_context_tokens,
    find_safe_cut,
    find_turn_prefix_cut,
    project_context,
    should_compact,
)
from .harness import AgentHarness, HarnessPhase

__all__ = [
    "DEFAULT_COMPACTION_SETTINGS",
    "AgentHarness",
    "CompactionSettings",
    "HarnessPhase",
    "estimate_context_tokens",
    "find_safe_cut",
    "find_turn_prefix_cut",
    "project_context",
    "should_compact",
]
