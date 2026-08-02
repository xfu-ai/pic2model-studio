from __future__ import annotations

import base64
from io import BytesIO

import pytest
from PIL import Image

from aipic_to_model.application.candidate_service import CandidateService
from aipic_to_model.application.host_capabilities import HostCapabilityStore
from aipic_to_model.composition import compose_local_app
from aipic_to_model.domain.provider_models import GenerationRequest, ProviderResult
from aipic_to_model.domain.prompt_parser import BilingualPrompt, serialize_prompt
from aipic_to_model.infrastructure.sqlite.candidate_repository import CandidateRepository


def _png_base64(colour: str) -> str:
    image = BytesIO()
    Image.new("RGB", (24, 24), colour).save(image, "PNG")
    return base64.b64encode(image.getvalue()).decode("ascii")


def _request(
    prompt_asset_id: str,
    source_asset_id: str | None = None,
    *,
    candidate_count: int = 2,
) -> GenerationRequest:
    return GenerationRequest(
        prompt_asset_id=prompt_asset_id,
        source_asset_id=source_asset_id,
        provider_profile="test",
        channel="banana",
        mode="i2i" if source_asset_id else "t2i",
        model="fake-image",
        candidate_count=candidate_count,
    )


def test_generation_result_creates_new_assets_candidate_group_and_selection_event(tmp_path) -> None:
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Candidates")
    prompt_file = tmp_path / "prompt.json"
    prompt_file.write_text(serialize_prompt(BilingualPrompt(
        "猫的分析", "analysis of a cat", "猫", "cat",
    )), encoding="utf-8")
    prompt = dependencies.assets.import_file(
        root, project.id, prompt_file, "prompt", "prompt-import"
    )
    result = ProviderResult(
        ok=True,
        provider_request_id="fake-request",
        stage="generating",
        retryable=False,
        payload={"images": [{"base64": _png_base64("red")}, {"base64": _png_base64("blue")}]},
    )
    service = CandidateService(dependencies.assets, CandidateRepository())
    created = service.materialize_group(
        root, project.id, _request(str(prompt["id"])), result, request_id="generate-candidates"
    )
    assert len(created["asset_ids"]) == 2
    group = CandidateRepository().get(root / "project.sqlite3", str(created["candidate_group_id"]))
    assert group.status == "ready" and group.requested_count == 2
    assert all(item.asset_id != prompt["id"] for item in group.items)
    service.select(
        root,
        project.id,
        str(created["candidate_group_id"]),
        [str(created["asset_ids"][0])],
        "single_continue",
    )
    assert CandidateRepository().get(
        root / "project.sqlite3", str(created["candidate_group_id"])
    ).selected_asset_ids == [created["asset_ids"][0]]


def test_single_generation_result_creates_a_ready_candidate_group(tmp_path) -> None:
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "single-project"
    project = dependencies.projects.create(root, "Single candidate")
    prompt_file = tmp_path / "single-prompt.json"
    prompt_file.write_text(
        serialize_prompt(BilingualPrompt("单图分析", "single analysis", "单图", "single image")),
        encoding="utf-8",
    )
    prompt = dependencies.assets.import_file(
        root, project.id, prompt_file, "prompt", "single-prompt-import"
    )
    result = ProviderResult(
        ok=True,
        provider_request_id="fake-single-request",
        stage="generating",
        retryable=False,
        payload={"images": [{"base64": _png_base64("red")}]},
    )

    created = CandidateService(dependencies.assets, CandidateRepository()).materialize_group(
        root,
        project.id,
        _request(str(prompt["id"]), candidate_count=1),
        result,
        request_id="generate-single-candidate",
    )

    assert len(created["asset_ids"]) == 1
    group = CandidateRepository().get(root / "project.sqlite3", str(created["candidate_group_id"]))
    assert group.status == "ready"
    assert group.requested_count == 1
    assert len(group.items) == 1


def test_invalid_provider_image_creates_no_candidate_group(tmp_path) -> None:
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Bad candidates")
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("prompt", encoding="utf-8")
    prompt = dependencies.assets.import_file(
        root, project.id, prompt_file, "prompt", "prompt-import"
    )
    result = ProviderResult(
        ok=True,
        provider_request_id="fake-request",
        stage="generating",
        retryable=False,
        payload={"images": [{"base64": "not-base64"}, {"base64": "still-not-base64"}]},
    )
    with pytest.raises(ValueError, match="invalid image"):
        CandidateService(dependencies.assets, CandidateRepository()).materialize_group(
            root, project.id, _request(str(prompt["id"])), result, request_id="bad-candidates"
        )
    assert not any(
        item["asset_type"] == "generated_image"
        for item in dependencies.assets.list_by_group(root, project.id)
    )


@pytest.mark.parametrize(
    "operation", ["upscale", "remove_background", "inpaint_selection", "element_split"]
)
def test_image_edits_create_a_new_asset_without_overwriting_the_source(
    tmp_path, operation: str
) -> None:
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Image edits")
    source_file = tmp_path / "source.png"
    source_file.write_bytes(base64.b64decode(_png_base64("green")))
    source = dependencies.assets.import_file(
        root, project.id, source_file, "source_image", "source"
    )
    created = CandidateService(dependencies.assets, CandidateRepository()).materialize_edit(
        root,
        project.id,
        operation=operation,
        source_asset_id=str(source["id"]),
        provider_profile="test",
        model="fake-image",
        result=ProviderResult(
            ok=True,
            provider_request_id="fake-edit",
            stage="generating",
            retryable=False,
            payload={"images": [{"base64": _png_base64("purple")}]},
        ),
        request_id=f"{operation}-request",
        selection_id="selection-1" if operation == "inpaint_selection" else None,
        parameters={"scale": 2} if operation == "upscale" else None,
    )
    assert created["id"] != source["id"]
    assert (
        dependencies.assets.get(root, project.id, str(source["id"]))["sha256"] == source["sha256"]
    )
    assert (
        dependencies.assets.lineage(root, project.id, str(created["id"]))["parent_asset_id"]
        == source["id"]
    )
