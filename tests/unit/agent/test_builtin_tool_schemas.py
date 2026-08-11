from pathlib import Path

from jsonschema import Draft202012Validator

from aipic_to_model.agent.execution.local import LocalExecutionEnv
from aipic_to_model.agent.tools.builtin import BashTool, EditTool, ReadTool, WriteTool


def test_builtin_tool_schemas_are_closed_and_reject_invalid_boundaries(tmp_path: Path) -> None:
    env = LocalExecutionEnv((tmp_path,))
    tools = (ReadTool(env), WriteTool(env), EditTool(env), BashTool(env))

    for tool in tools:
        assert tool.parameters["additionalProperties"] is False
        Draft202012Validator.check_schema(dict(tool.parameters))

    read = Draft202012Validator(dict(tools[0].parameters))
    assert list(read.iter_errors({"path": "file.txt", "limit": 0}))
    assert list(read.iter_errors({"path": "file.txt", "unknown": True}))

    edit = Draft202012Validator(dict(tools[2].parameters))
    assert list(edit.iter_errors({"path": "file.txt", "old_text": "", "new_text": "x"}))

    bash = Draft202012Validator(dict(tools[3].parameters))
    assert list(bash.iter_errors({"command": "pwd", "timeout": 0}))
