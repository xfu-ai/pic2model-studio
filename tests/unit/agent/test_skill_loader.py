from __future__ import annotations

import pytest

from aipic_to_model.agent.execution import LocalExecutionEnv
from aipic_to_model.agent.skills import SkillLoader


def _skill(path, name: str, description: str, *, required: str = "", resource: str = "") -> None:
    path.mkdir(parents=True)
    path.joinpath("SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nrequired_tools: {required}\nresources: {resource}\n---\nUse the workflow.",
        encoding="utf-8",
    )


@pytest.mark.agent
@pytest.mark.asyncio
async def test_skill_loader_applies_project_user_application_precedence(tmp_path) -> None:
    root = tmp_path / "workspace"
    app, user, project = root / "app", root / "user", root / "project"
    _skill(app / "paint", "paint", "application")
    _skill(user / "paint", "paint", "user")
    _skill(project / "paint", "paint", "project", required="read")
    env = LocalExecutionEnv((root,))
    loader = SkillLoader(
        env, application_roots=(app,), user_roots=(user,), project_roots=(project,)
    )

    discovered = await loader.discover()
    active = await loader.activate("paint", ("read",))

    assert discovered[0].source == "project"
    assert active.instructions == "Use the workflow."
    assert len(loader.diagnostics) == 2


@pytest.mark.agent
@pytest.mark.asyncio
async def test_skill_loader_rejects_invalid_metadata_missing_tool_and_resource(tmp_path) -> None:
    root = tmp_path / "workspace"
    _skill(root / "good", "good", "description", required="write", resource="missing.md")
    (root / "bad").mkdir()
    (root / "bad" / "SKILL.md").write_text("not frontmatter", encoding="utf-8")
    loader = SkillLoader(LocalExecutionEnv((root,)), project_roots=(root,))
    await loader.discover()
    with pytest.raises(ValueError, match="unavailable"):
        await loader.activate("good", ())
    with pytest.raises(FileNotFoundError):
        await loader.activate("good", ("write",))
    assert loader.diagnostics
