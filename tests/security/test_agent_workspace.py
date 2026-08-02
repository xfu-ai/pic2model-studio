from __future__ import annotations

import pytest

from aipic_to_model.agent.execution import LocalExecutionEnv, WorkspaceAccessError


@pytest.mark.agent
@pytest.mark.asyncio
async def test_workspace_rejects_cross_root_and_filters_secret_environment(
    tmp_path, monkeypatch
) -> None:
    root, other = tmp_path / "root", tmp_path / "other"
    root.mkdir()
    other.mkdir()
    monkeypatch.setenv("DEEPSEEK_API_KEY", "not-for-shell")
    env = LocalExecutionEnv((root,), allowed_env=())
    with pytest.raises(WorkspaceAccessError):
        env.resolve(other / "x.txt")
    result = await env.exec("Write-Output $env:DEEPSEEK_API_KEY")
    assert "not-for-shell" not in result.stdout + result.stderr
