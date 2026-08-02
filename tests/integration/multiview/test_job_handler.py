from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from aipic_to_model.application.candidate_service import CandidateService
from aipic_to_model.application.host_capabilities import HostCapabilityStore
from aipic_to_model.application.jobs.external_image_handler import ExternalImageJobHandler
from aipic_to_model.application.multiview import MultiviewService
from aipic_to_model.composition import compose_local_app
from aipic_to_model.domain.provider_models import ProviderResult
from aipic_to_model.infrastructure.sqlite.candidate_repository import CandidateRepository
from aipic_to_model.infrastructure.sqlite.multiview_repository import MultiviewRepository


class SingleSheetProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.requests: list[dict[str, object]] = []

    def generate(self, request: dict[str, object]) -> ProviderResult:
        self.requests.append(request)
        self.calls += 1
        image = Image.new("RGB", (72, 20), "white")
        for index, colour in enumerate(("red", "green", "blue")):
            image.paste(colour, (index * 24, 0, (index + 1) * 24, 20))
        encoded = BytesIO()
        image.save(encoded, "PNG")
        return ProviderResult(
            ok=True,
            provider_request_id=f"request-{self.calls}",
            stage="generating",
            retryable=False,
            payload={
                "images": [
                    {"base64": base64.b64encode(encoded.getvalue()).decode("ascii")}
                ]
            },
        )


def test_meshy_multiview_job_returns_one_horizontal_sheet_for_manual_cropping(
    tmp_path: Path,
) -> None:
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Multiview order")
    source_path = tmp_path / "source.png"
    Image.new("RGB", (32, 32), "white").save(source_path, "PNG")
    source = dependencies.assets.import_file(
        root,
        project.id,
        source_path,
        "source_image",
        "source",
    )
    repository = MultiviewRepository()
    provider = SingleSheetProvider()
    handler = ExternalImageJobHandler(
        dependencies.jobs,
        dependencies.assets,
        dependencies.selections,
        MultiviewService(dependencies.assets, dependencies.selections, repository),
        repository,
        CandidateService(dependencies.assets, CandidateRepository()),
        dependencies.prompt_versions,
        object(),
        provider,
    )

    output = handler._generate_multiview(
        root,
        project.id,
        SimpleNamespace(id="job-multiview", tool_call_id="call-multiview"),
        {
            "source_asset_id": str(source["id"]),
            "provider_profile": "meshy/default",
            "channel": "meshy",
            "model": "nano-banana",
        },
    )

    assert isinstance(output, list)
    assert len(output) == 1
    assert provider.calls == 1
    assert provider.requests[0]["candidate_count"] == 1
    rendered_prompt = str(provider.requests[0]["prompt"])
    assert "Return one horizontal image" in rendered_prompt
    assert "ordered front, left side, then rear" in rendered_prompt
    sheet = dependencies.assets.get(root, project.id, output[0])
    assert sheet["asset_type"] == "multiview"
    assert sheet["parent_asset_id"] == source["id"]
    assert sheet["metadata"]["width"] == 72
    assert sheet["metadata"]["height"] == 20
