from __future__ import annotations

import pytest

from aipic_to_model.agent.core.events import CancellationToken
from aipic_to_model.agent.core.models import TextContent
from aipic_to_model.agent.core.tool import ToolContext
from aipic_to_model.agent.execution import LocalExecutionEnv
from aipic_to_model.agent.tools import BashTool, EditTool, ReadTool, WriteTool


@pytest.mark.agent
@pytest.mark.asyncio
async def test_builtin_tools_only_use_execution_env(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    env = LocalExecutionEnv((root,))
    token = CancellationToken()
    context = ToolContext(())
    write = WriteTool(env)
    edit = EditTool(env)
    read = ReadTool(env)
    bash = BashTool(env)
    await write.execute("1", {"path": "note.txt", "content": "old\nnext"}, context, token)
    await edit.execute(
        "2", {"path": "note.txt", "old_text": "old", "new_text": "new"}, context, token
    )
    result = await read.execute("3", {"path": "note.txt", "offset": 1, "limit": 1}, context, token)
    assert isinstance(result.content[0], TextContent)
    assert "1: new" in result.content[0].text
    shell = await bash.execute("4", {"command": "Write-Output ok"}, context, token)
    assert isinstance(shell.content[0], TextContent)
    assert "ok" in shell.content[0].text
