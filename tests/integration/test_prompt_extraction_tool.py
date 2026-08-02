from __future__ import annotations

from pathlib import Path

from PIL import Image

from aipic_to_model.application.host_capabilities import HostCapabilityStore
from aipic_to_model.composition import compose_local_app
from aipic_to_model.domain.provider_models import AnalysisResult


def _analysis_asset(
    tmp_path: Path,
    *,
    mode: str = "content",
    zh_prompt: str = "银色骑士，正面构图",
    en_prompt: str = "silver knight, frontal composition",
):
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Prompt extraction")
    image_path = tmp_path / "source.png"
    Image.new("RGB", (8, 8), "gray").save(image_path)
    image = dependencies.assets.import_file(
        root,
        project.id,
        image_path,
        "source_image",
        "source-image",
    )
    analysis_path = tmp_path / "analysis.json"
    analysis_path.write_text(
        AnalysisResult(
            mode=mode,
            zh_text="主体是一名银色骑士。",
            en_text="The subject is a silver knight.",
            zh_prompt=zh_prompt,
            en_prompt=en_prompt,
            provider_request_id="provider-request",
            model="vision-model",
        ).model_dump_json(),
        encoding="utf-8",
    )
    analysis = dependencies.assets.register_derived(
        root,
        project.id,
        analysis_path,
        "analysis",
        f"analysis-{mode}",
        parent_asset_id=str(image["id"]),
        input_asset_ids=[str(image["id"])],
    )
    return dependencies, root, project, analysis


def test_prompt_extract_bilingual_reads_structured_analysis_asset(tmp_path: Path) -> None:
    dependencies, root, project, analysis = _analysis_asset(tmp_path)

    result = dependencies.registry.execute(
        root,
        project.id,
        "prompt.extract_bilingual",
        "1.0.0",
        {"analysis_asset_id": analysis["id"], "kind": "content"},
        "extract-content",
    )

    assert result.ok is True
    assert result.status == "succeeded"
    assert len(result.output_asset_ids) == 1
    prompt = dependencies.prompt_versions.parse_asset(
        root, project.id, result.output_asset_ids[0]
    )
    assert prompt.zh_prompt == "银色骑士，正面构图"
    assert prompt.en_prompt == "silver knight, frontal composition"


def test_prompt_extract_bilingual_rejects_mode_mismatch(tmp_path: Path) -> None:
    dependencies, root, project, analysis = _analysis_asset(tmp_path)

    result = dependencies.registry.execute(
        root,
        project.id,
        "prompt.extract_bilingual",
        "1.0.0",
        {"analysis_asset_id": analysis["id"], "kind": "style"},
        "extract-mismatched-style",
    )

    assert result.ok is False
    assert result.status == "failed"
    assert result.output_asset_ids == []
    assert result.error is not None
    assert result.error["code"] == "TOOL_ARGUMENT_INVALID"


def test_prompt_extract_bilingual_rejects_fence_marker_as_prompt(tmp_path: Path) -> None:
    dependencies, root, project, analysis = _analysis_asset(
        tmp_path,
        mode="style",
        zh_prompt="prompt",
        en_prompt="prompt",
    )

    result = dependencies.registry.execute(
        root,
        project.id,
        "prompt.extract_bilingual",
        "1.0.0",
        {"analysis_asset_id": analysis["id"], "kind": "style"},
        "extract-invalid-style",
    )

    assert result.ok is False
    assert result.status == "failed"
    assert result.output_asset_ids == []
