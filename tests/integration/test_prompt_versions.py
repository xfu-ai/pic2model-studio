from __future__ import annotations

from pathlib import Path

from aipic_to_model.application.host_capabilities import HostCapabilityStore
from aipic_to_model.application.prompt_service import PromptVersionService
from aipic_to_model.composition import compose_local_app
from aipic_to_model.domain.prompt_parser import BilingualPrompt
from aipic_to_model.infrastructure.sqlite.prompt_repository import PromptVersionRepository


def test_prompt_versions_are_new_managed_assets_with_bilingual_rows(tmp_path: Path) -> None:
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Prompt versions")
    service = PromptVersionService(dependencies.assets, PromptVersionRepository())
    first = service.create_bilingual(
        root,
        project.id,
        kind="content",
        bilingual=BilingualPrompt("红色机器人分析", "red robot analysis", "红色机器人", "red robot"),
        request_id="prompt-create-1",
    )
    second = service.create_bilingual(
        root,
        project.id,
        kind="merged",
        bilingual=BilingualPrompt("卡通机器人分析", "cartoon robot analysis", "红色机器人，卡通", "red robot, cartoon"),
        request_id="prompt-create-2",
        parent_asset_id=str(first["asset"]["id"]),
    )
    assert first["asset"]["id"] != second["asset"]["id"]
    rows = PromptVersionRepository().list_for_asset(
        root / "project.sqlite3", project_id=project.id, asset_id=str(second["asset"]["id"])
    )
    assert [(row["kind"], row["language"], row["body"]) for row in rows] == [
        ("merged", "zh", "红色机器人，卡通"),
        ("merged", "en", "red robot, cartoon"),
    ]
    parsed = service.parse_asset(root, project.id, str(second["asset"]["id"]))
    assert parsed.en_prompt == "red robot, cartoon"
