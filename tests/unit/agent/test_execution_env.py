from __future__ import annotations

import asyncio

import pytest

from aipic_to_model.agent.execution import LocalExecutionEnv, WorkspaceAccessError


@pytest.mark.agent
@pytest.mark.asyncio
async def test_workspace_env_rejects_escape_and_supports_atomic_unique_edit(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    env = LocalExecutionEnv((root,))
    await env.write_text("a.txt", "one\nother\n")
    await env.edit_text("a.txt", "one", "two")
    assert await env.read_text("a.txt") == "two\nother\n"
    with pytest.raises(ValueError, match="exactly one"):
        await env.edit_text("a.txt", "missing", "three")
    with pytest.raises(WorkspaceAccessError):
        env.resolve("../outside.txt")


@pytest.mark.agent
@pytest.mark.asyncio
async def test_mutation_queue_serializes_writes_and_output_is_artifacted(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    env = LocalExecutionEnv((root,), max_output_bytes=16)
    await asyncio.gather(*(env.write_text("race.txt", str(index)) for index in range(10)))
    assert (await env.read_text("race.txt")).isdigit()
    result = await env.exec("Write-Output ('x' * 100)")
    assert result.artifact_path is not None
    assert env.resolve(result.artifact_path).exists()
