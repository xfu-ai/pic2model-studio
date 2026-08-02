from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from aipic_to_model.application.candidate_service import CandidateService
from aipic_to_model.application.host_capabilities import HostCapabilityStore
from aipic_to_model.application.jobs.external_image_handler import (
    ExternalImageJobHandler,
    _provider_image,
)
from aipic_to_model.composition import compose_local_app
from aipic_to_model.domain.prompt_parser import BilingualPrompt, serialize_prompt
from aipic_to_model.domain.provider_models import ProviderResult
from aipic_to_model.infrastructure.sqlite.candidate_repository import CandidateRepository


def _png_base64(color: str) -> str:
    image = Image.new("RGB", (24, 24), color)
    encoded = BytesIO()
    image.save(encoded, "PNG")
    return base64.b64encode(encoded.getvalue()).decode("ascii")


class CapturingImageProvider:
    def __init__(self) -> None:
        self.request: dict[str, object] | None = None

    def generate(self, request: dict[str, object]) -> ProviderResult:
        self.request = request
        return ProviderResult(
            ok=True,
            provider_request_id="provider-request",
            stage="generating",
            retryable=False,
            payload={"images": [{"base64": _png_base64("purple")}]} ,
        )


@pytest.mark.parametrize(
    ("image_format", "mime_type"),
    [("WEBP", "image/webp"), ("BMP", "image/bmp")],
)
def test_provider_image_normalizes_webp_and_bmp_to_jpeg(
    image_format: str, mime_type: str
) -> None:
    source = BytesIO()
    Image.new("RGB", (20, 12), "orange").save(source, image_format)

    content, normalized_mime = _provider_image(source.getvalue(), mime_type)

    assert normalized_mime == "image/jpeg"
    with Image.open(BytesIO(content)) as normalized:
        assert normalized.format == "JPEG"
        assert normalized.size == (20, 12)


def test_boxsplit_sends_a_managed_annotation_and_element_uses_unmarked_source(tmp_path: Path) -> None:
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Selection annotation")
    source_file = tmp_path / "source.png"
    source_file.write_bytes(base64.b64decode(_png_base64("green")))
    source = dependencies.assets.import_file(root, project.id, source_file, "source_image", "source")
    selection = dependencies.selections.save(
        root,
        project.id,
        str(source["id"]),
        [{"rect_id": "target", "x": 2, "y": 3, "width": 12, "height": 10}],
        "target",
        "user",
        request_id="selection",
    )
    confirmed = dependencies.selections.confirm(
        root, project.id, str(selection["id"]), int(selection["revision"]), "confirm"
    )
    prompt_file = tmp_path / "prompt.json"
    prompt_file.write_text(
        serialize_prompt(
            BilingualPrompt(
                "分析红色标记内的目标及其边界。",
                "Analyze the target and its boundary inside the red annotation.",
                "仅提取红色标记内的目标。",
                "Extract only the target inside the red annotation.",
            )
        ),
        encoding="utf-8",
    )
    prompt = dependencies.assets.import_file(root, project.id, prompt_file, "prompt", "prompt")
    provider = CapturingImageProvider()
    handler = ExternalImageJobHandler(
        None,
        dependencies.assets,
        dependencies.selections,
        dependencies.multiview,
        object(),
        CandidateService(dependencies.assets, CandidateRepository()),
        dependencies.prompt_versions,
        object(),
        provider,
    )
    job = SimpleNamespace(id="job-1", job_type="element.split", tool_call_id="call-1")

    output = handler._edit(
        root,
        project.id,
        job,
        {
            "source_asset_id": str(source["id"]),
            "selection_id": str(confirmed["id"]),
            "prompt_asset_id": str(prompt["id"]),
            "provider_profile": "meshy/default",
            "model": "nano-banana",
            "split_mode": "boxsplit",
        },
    )

    assert isinstance(output, list) and len(output) == 1
    assert provider.request is not None
    assert provider.request["provider_profile"] == "image-generation/auto"
    assert provider.request["channel"] == "auto"
    assert provider.request["model"] == "auto"
    assert provider.request["source_asset_id"] != source["id"]
    assert provider.request["source_mime"] == "image/png"
    assert provider.request["source_bytes"] != source_file.read_bytes()
    created = dependencies.assets.get(root, project.id, output[0])
    assert created["parent_asset_id"] == source["id"]
    assert created["provenance"]["selection_ids"] == [confirmed["id"]]

    breakdown_output = handler._edit(
        root,
        project.id,
        SimpleNamespace(id="job-2", job_type="element.split", tool_call_id="call-2"),
        {
            "source_asset_id": str(source["id"]),
            "prompt_asset_id": str(prompt["id"]),
            "provider_profile": "openai/default",
            "model": "gpt-image-2",
            "split_mode": "element",
        },
    )
    assert isinstance(breakdown_output, list) and len(breakdown_output) == 1
    assert provider.request["source_asset_id"] == source["id"]
    assert provider.request["source_bytes"] == source_file.read_bytes()
    assert provider.request["provider_profile"] == "image-generation/auto"
    assert provider.request["channel"] == "auto"
    assert provider.request["model"] == "auto"
