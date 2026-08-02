from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from aipic_to_model.application.analysis import AnalysisService
from aipic_to_model.application.b02_tool_catalog import register_b02_tools
from aipic_to_model.application.tools import ToolRegistry
from aipic_to_model.domain.job_models import (
    CancelCapability,
    JobStage,
    JobStatus,
    JobView,
    ResumeClass,
    assert_job_transition,
)
from aipic_to_model.domain.production_models import (
    CandidateGroupDTO,
    ModelInspection,
    MultiviewValidation,
    SelectionCommand,
    ToolResult,
    TripoGenerationRequest,
)
from aipic_to_model.domain.provider_models import AnalysisRequest, ErrorCategory, ErrorDetail
from aipic_to_model.infrastructure.providers.fake import (
    FakeScenario,
    FakeTripo3DProvider,
    FakeVisionAnalysisProvider,
)

ROOT = Path(__file__).parents[2]


def _error() -> ErrorDetail:
    return ErrorDetail(
        code="PROVIDER_UNAVAILABLE",
        category=ErrorCategory.SERVICE_REJECTED,
        user_message="服务不可用。",
        recoverable=True,
        failed_object="provider",
        failed_step="request",
        safe_to_retry=True,
        recommended_action="retry",
    )


def _job() -> JobView:
    return JobView(
        id="job-1",
        status="queued",
        stage=JobStage.QUEUED,
        elapsed_seconds=0,
        cancel_capability=CancelCapability.CANCEL_LOCAL,
        can_cancel=True,
        can_stop_waiting=False,
    )


def test_b02_manifest_index_is_canonical_and_has_no_aliases() -> None:
    payload = json.loads((ROOT / "contracts/tools/manifest-index.json").read_text(encoding="utf-8"))
    entries = payload["tools"]
    names = [item["name"] for item in entries]
    assert payload["schema_version"] == 1
    assert len(names) == len(set(names)) == 52
    assert set(names) == {
        "image.analyze_content",
        "image.analyze_style",
        "image.evaluate_3d_suitability",
        "prompt.extract_bilingual",
        "prompt.merge",
        "prompt.get_current",
        "prompt.rewrite",
        "prompt.validate",
        "image.generate",
        "image.transform",
        "image.generate_variants",
        "image.upscale",
        "image.remove_background",
        "image.inpaint_selection",
        "image.crop",
        "image.render_annotation",
        "image.compress_for_provider",
        "image.trim_transparent",
        "image.normalize",
        "image.remove_background_local",
        "image.split_local",
        "image.upscale_local",
        "element.split",
        "element.export_transparent",
        "selection.get_current",
        "selection.request_user",
        "selection.set_suggestion",
        "selection.auto_suggest_boxes",
        "selection.confirm",
        "multiview.generate",
        "multiview.detect_regions",
        "multiview.request_box_confirmation",
        "multiview.set_regions",
        "multiview.crop_views",
        "multiview.request_quality_confirmation",
        "multiview.set_quality_checks",
        "multiview.validate",
        "multiview.regenerate_view",
        "model3d.generate",
        "model3d.get_status",
        "model3d.cancel",
        "model3d.download",
        "model3d.import_local",
        "model3d.inspect",
        "model3d.render_preview",
        "model3d.convert",
        "model3d.optimize",
        "model3d.package",
        "job.get_status",
        "job.cancel",
        "job.retry",
        "job.confirm_new_submission",
    }
    assert all(item["version"] == "1.0.0" for item in entries)
    assert all(item["execution"] in {"sync", "job"} for item in entries)
    assert all(
        item["risk_level"]
        in {"read_only", "local_reversible", "external", "external_paid", "destructive"}
        for item in entries
    )


def test_every_b02_manifest_has_a_closed_distinct_schema_and_no_false_success() -> None:
    registry = ToolRegistry(object(), object())
    register_b02_tools(registry)
    schemas = {
        name: registry.manifests[(name, "1.0.0")].input_schema
        for name, *_ in registry.manifests
        if name.startswith(("image.", "prompt.", "element.", "multiview.", "model3d.", "job."))
    }
    assert schemas["image.generate"] != schemas["model3d.generate"]
    assert schemas["image.generate"]["required"] == [
        "prompt_asset_id",
        "provider_profile",
        "channel",
        "model",
        "candidate_count",
    ]
    assert schemas["image.generate"]["properties"]["candidate_count"] == {
        "type": "integer",
        "enum": [1, 2, 4],
    }
    assert schemas["image.analyze_style"]["properties"]["analysis_revision"] == {
        "type": "string",
        "minLength": 1,
        "maxLength": 128,
    }
    assert "analysis_revision" not in schemas["image.analyze_style"]["required"]
    assert "analysis_revision" not in schemas["image.evaluate_3d_suitability"]["properties"]
    assert schemas["job.get_status"]["required"] == ["job_id"]
    assert schemas["model3d.convert"]["required"] == ["asset_id", "target_format"]
    assert schemas["model3d.generate"]["allOf"][1]["then"]["required"] == [
        "multiview_set_id",
        "view_asset_ids",
    ]
    split_base = {
        "source_asset_id": "source",
        "prompt_asset_id": "prompt",
        "provider_profile": "meshy/default",
        "channel": "meshy",
        "model": "nano-banana",
    }
    split_validator = Draft202012Validator(schemas["element.split"])
    assert not list(split_validator.iter_errors({**split_base, "split_mode": "element"}))
    assert list(split_validator.iter_errors({**split_base, "split_mode": "boxsplit"}))
    assert not list(split_validator.iter_errors({
        **split_base,
        "split_mode": "boxsplit",
        "selection_id": "selection",
    }))
    result = registry.executors["b02:prompt.get_current"](
        None, "project", {"prompt_asset_id": "missing"}, "call"
    )
    assert result.status == "failed"


def test_job_state_machine_rejects_terminal_rollback_and_unsafe_resume() -> None:
    assert_job_transition(JobStatus.QUEUED, JobStatus.RUNNING)
    assert_job_transition(JobStatus.RUNNING, JobStatus.WAITING)
    assert_job_transition(JobStatus.INTERRUPTED, JobStatus.WAITING)
    with pytest.raises(ValueError):
        assert_job_transition(JobStatus.SUCCEEDED, JobStatus.RUNNING)
    with pytest.raises(ValueError):
        assert_job_transition(JobStatus.QUEUED, JobStatus.SUCCEEDED)
    assert ResumeClass.UNKNOWN_SUBMISSION.value == "unknown_submission"


def test_tool_result_one_of_branches_and_schema_round_trip() -> None:
    queued = ToolResult(ok=True, status="queued", tool_call_id="call-1", summary="排队", job=_job())
    assert ToolResult.model_validate_json(queued.model_dump_json()) == queued
    failed = ToolResult(
        ok=False, status="failed", tool_call_id="call-2", summary="失败", error=_error()
    )
    assert ToolResult.model_validate_json(failed.model_dump_json()) == failed
    with pytest.raises(ValidationError):
        ToolResult(ok=True, status="queued", tool_call_id="call-3", summary="bad")


def test_dto_forbids_unknown_fields_and_preserves_provider_request_id() -> None:
    request = AnalysisRequest(
        asset_id="asset-1", provider_profile="test", model="vision", mode="content"
    )
    assert AnalysisRequest.model_validate_json(request.model_dump_json()) == request
    with pytest.raises(ValidationError):
        AnalysisRequest.model_validate({**request.model_dump(), "url": "https://invalid.example"})


def test_frozen_dto_json_schemas_compile() -> None:
    for model in (
        AnalysisRequest,
        CandidateGroupDTO,
        SelectionCommand,
        TripoGenerationRequest,
        MultiviewValidation,
        ModelInspection,
        ToolResult,
    ):
        Draft202012Validator.check_schema(model.model_json_schema())


def test_fake_provider_has_no_network_and_scripts_faults() -> None:
    vision = FakeVisionAnalysisProvider(
        [FakeScenario("vision.analyze", "success", {"zh_text": "角色"})]
    )
    value = vision.analyze(
        AnalysisRequest(asset_id="asset-1", provider_profile="test", model="vision", mode="content")
    )
    assert value.provider_request_id == "fake-request-1"
    assert value.zh_text == "角色"
    tripo = FakeTripo3DProvider([FakeScenario("tripo.create", "unknown_submission")])
    outcome = tripo.create({"mode": "image"})
    assert not outcome.ok
    assert outcome.error is not None
    assert outcome.error.code == "JOB_UNKNOWN_SUBMISSION"
    assert outcome.error.safe_to_retry is False
    no_cancel = FakeTripo3DProvider([FakeScenario("tripo.cancel", "cancel_unsupported")])
    cancelled = no_cancel.cancel("task-1")
    assert cancelled.error is not None
    assert cancelled.error.recommended_action == "stop_waiting"


def test_analysis_service_does_not_turn_provider_failures_into_empty_results() -> None:
    provider = FakeVisionAnalysisProvider([FakeScenario("vision.analyze", "missing_config")])
    with pytest.raises(TypeError, match="PROVIDER_NOT_CONFIGURED"):
        AnalysisService(provider).analyze(
            AnalysisRequest(
                asset_id="asset-1", provider_profile="test", model="vision", mode="style"
            )
        )
