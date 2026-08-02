from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from aipic_to_model.application.analysis import AnalysisAssetService, ProviderAnalysisError
from aipic_to_model.application.host_capabilities import HostCapabilityStore
from aipic_to_model.application.jobs.external_image_handler import ExternalImageJobHandler
from aipic_to_model.composition import compose_local_app
from aipic_to_model.domain.provider_models import AnalysisRequest, ProviderResult
from aipic_to_model.infrastructure.providers.fake import FakeScenario, FakeVisionAnalysisProvider


def _source_image(path) -> None:
    buffer = BytesIO()
    Image.new("RGB", (32, 32), "red").save(buffer, "PNG")
    path.write_bytes(buffer.getvalue())


@pytest.mark.parametrize("mode", ["content", "style", "3d_suitability"])
def test_analysis_registers_a_complete_new_asset_for_each_mode(tmp_path, mode: str) -> None:
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Analysis")
    source = tmp_path / "source.png"
    _source_image(source)
    image = dependencies.assets.import_file(
        root, project.id, source, "source_image", "import-image"
    )
    provider = FakeVisionAnalysisProvider(
        [FakeScenario("vision.analyze", "success", {"zh_text": "主体", "en_text": "subject"})]
    )
    service = AnalysisAssetService(dependencies.assets, provider)
    result = service.analyze_to_asset(
        root,
        project.id,
        AnalysisRequest(asset_id=image["id"], provider_profile="test", model="vision", mode=mode),
        request_id=f"analysis-{mode}",
    )
    assert result["id"] != image["id"]
    stored = service.read_result(root, project.id, str(result["id"]))
    assert stored.mode == mode and stored.provider_request_id == "fake-request-1"


def test_analysis_failure_creates_no_empty_asset(tmp_path) -> None:
    dependencies = compose_local_app(HostCapabilityStore(), tmp_path / "app.sqlite3")
    root = tmp_path / "project"
    project = dependencies.projects.create(root, "Analysis failure")
    source = tmp_path / "source.png"
    _source_image(source)
    image = dependencies.assets.import_file(
        root, project.id, source, "source_image", "import-image"
    )
    service = AnalysisAssetService(
        dependencies.assets,
        FakeVisionAnalysisProvider([FakeScenario("vision.analyze", "missing_config")]),
    )
    with pytest.raises(
        ProviderAnalysisError, match="PROVIDER_NOT_CONFIGURED"
    ) as captured:
        service.analyze_to_asset(
            root,
            project.id,
            AnalysisRequest(
                asset_id=image["id"], provider_profile="test", model="vision", mode="content"
            ),
            request_id="analysis-failure",
        )
    assert captured.value.result.error is not None
    assert captured.value.result.error.code == "PROVIDER_NOT_CONFIGURED"
    assert captured.value.result.error.recommended_action == "configure_provider"

    handler = ExternalImageJobHandler(
        object(),
        dependencies.assets,
        object(),
        object(),
        object(),
        object(),
        object(),
        FakeVisionAnalysisProvider([FakeScenario("vision.analyze", "missing_config")]),
        object(),
    )
    output = handler._analysis(
        root,
        project.id,
        SimpleNamespace(
            id="analysis-job",
            job_type="image.analyze_content",
            tool_call_id="analysis-call",
        ),
        {
            "asset_id": image["id"],
            "provider_profile": "gemini/google/default",
            "model": "gemini-flash-lite-latest",
        },
    )
    assert isinstance(output, ProviderResult)
    assert output.error is not None
    assert output.error.code == "PROVIDER_NOT_CONFIGURED"
    assert output.error.recommended_action == "configure_provider"
    assert not any(
        item["asset_type"] == "analysis"
        for item in dependencies.assets.list_by_group(root, project.id)
    )
