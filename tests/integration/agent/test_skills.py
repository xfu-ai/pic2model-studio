from __future__ import annotations

import pytest

from aipic_to_model.agent.core.models import SystemMessage
from aipic_to_model.agent.execution import LocalExecutionEnv, WorkspaceAccessError
from aipic_to_model.agent.harness import AgentHarness
from aipic_to_model.agent.providers.base import ModelProfile
from aipic_to_model.agent.providers.fake import FakeProvider
from aipic_to_model.agent.session.sqlite import LinearSessionRepository
from aipic_to_model.agent.skills import SkillLoader


@pytest.mark.agent
@pytest.mark.asyncio
async def test_skill_resources_remain_inside_execution_workspace(tmp_path) -> None:
    root = tmp_path / "workspace"
    skill = root / "skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: skill\ndescription: safe\nresources: ../outside.md\n---\nbody", encoding="utf-8"
    )
    loader = SkillLoader(LocalExecutionEnv((root,)), project_roots=(root,))
    await loader.discover()
    with pytest.raises((FileNotFoundError, WorkspaceAccessError)):
        await loader.activate("skill", ())


@pytest.mark.agent
@pytest.mark.asyncio
async def test_harness_skill_persists_name_hash_and_injects_instructions(tmp_path) -> None:
    root = tmp_path / "workspace"
    skill = root / "read-workflow"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: read-workflow\ndescription: read files\n---\nUse read.", encoding="utf-8"
    )
    env = LocalExecutionEnv((root,))
    loader = SkillLoader(env, project_roots=(root,))
    repository = LinearSessionRepository(root / "agent.sqlite3")
    session = repository.create()
    harness = AgentHarness(
        FakeProvider(()),
        ModelProfile("fake", "fake", "http://fake"),
        repository,
        session.id,
        skill_loader=loader,
    )

    invocation = await harness.skill("read-workflow", "inspect x")

    first = harness.snapshot().context[0]
    assert isinstance(first, SystemMessage)
    assert "inspect x" in invocation and "Use read." in first.content
    assert repository.open(session.id).active_skills[0].startswith("read-workflow@")
