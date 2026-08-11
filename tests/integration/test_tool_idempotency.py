import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from PIL import Image

from aipic_to_model.application.assets import AssetService
from aipic_to_model.application.projects import ProjectService
from aipic_to_model.application.tool_catalog import register_b01_tools
from aipic_to_model.application.tools import ToolRegistry
from aipic_to_model.domain.common import DomainErrorV1, ErrorCode, RiskLevel
from aipic_to_model.domain.tools import ToolManifestV1, ToolResultV1
from aipic_to_model.infrastructure.sqlite.connection import connect


def test_b01_10_tool_idempotency_uses_asset_hash_and_audits_links(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Demo")
    image = tmp_path / "image.png"
    Image.new("RGB", (2, 2)).save(image)
    asset = AssetService().import_file(root, project.id, image, "source_image", "import")
    registry = ToolRegistry()
    register_b01_tools(registry)
    first = registry.execute(
        root, project.id, "asset.hide", "1.0.0", {"asset_id": asset["id"]}, "same"
    )
    second = registry.execute(
        root, project.id, "asset.hide", "1.0.0", {"asset_id": asset["id"]}, "same"
    )
    canonical_reuse = registry.execute(
        root,
        project.id,
        "asset.hide",
        "1.0.0",
        {"asset_id": asset["id"]},
        "different-request",
    )
    assert first.status == "succeeded"
    assert second == first
    assert canonical_reuse.reused is True
    connection = connect(root / "project.sqlite3")
    assert connection.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0] == 1
    assert connection.execute("SELECT direction FROM tool_call_assets").fetchone()[0] == "input"
    connection.close()


def test_b01_11_tool_request_id_replays_or_conflicts(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Request id")
    image = tmp_path / "image.png"
    Image.new("RGB", (2, 2)).save(image)
    asset = AssetService().import_file(root, project.id, image, "source_image", "import")
    registry = ToolRegistry()
    register_b01_tools(registry)
    first = registry.execute(
        root,
        project.id,
        "asset.hide",
        "1.0.0",
        {"asset_id": asset["id"]},
        "bound-request",
    )
    assert (
        registry.execute(
            root,
            project.id,
            "asset.hide",
            "1.0.0",
            {"asset_id": asset["id"]},
            "bound-request",
        )
        == first
    )
    with pytest.raises(DomainErrorV1) as conflict:
        registry.execute(
            root,
            project.id,
            "asset.restore_hidden",
            "1.0.0",
            {"asset_id": asset["id"]},
            "bound-request",
        )
    assert conflict.value.code == ErrorCode.IDEMPOTENCY_CONFLICT


def test_read_only_asset_list_is_fresh_for_a_new_request_id(tmp_path: Path) -> None:
    root = tmp_path / "project"
    project = ProjectService().create(root, "Fresh read")
    registry = ToolRegistry()
    register_b01_tools(registry)

    first = registry.execute(
        root, project.id, "asset.list", "1.0.0", {"project_id": project.id}, "list-1"
    )
    image = tmp_path / "new.png"
    Image.new("RGB", (2, 2)).save(image)
    asset = AssetService().import_file(root, project.id, image, "source_image", "import")
    fresh = registry.execute(
        root, project.id, "asset.list", "1.0.0", {"project_id": project.id}, "list-2"
    )
    replay = registry.execute(
        root, project.id, "asset.list", "1.0.0", {"project_id": project.id}, "list-2"
    )

    assert first.reused is False
    assert fresh.reused is False
    assert str(asset["id"]) in fresh.output_asset_ids
    assert replay == fresh


def test_external_paid_idempotency_is_scoped_to_each_explicit_request(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Paid")
    calls = 0

    def execute(*args):
        nonlocal calls
        calls += 1
        time.sleep(0.05)
        return ToolResultV1(True, "succeeded", args[-1], [], "done", [])

    manifest = ToolManifestV1(
        "fake.paid",
        "1",
        "Paid",
        "Paid",
        {"type": "object", "additionalProperties": False, "properties": {}},
        {"type": "object"},
        RiskLevel.EXTERNAL_PAID,
        "sync",
        True,
        False,
        [],
        "fake.paid",
    )
    registries = [ToolRegistry(), ToolRegistry()]
    for registry in registries:
        registry.register(manifest, execute)

    def call(index: int):
        return registries[index].execute(
            root,
            project.id,
            "fake.paid",
            "1",
            {},
            f"request-{index}",
            f"run-{index}",
            index,
            "profile-a",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(call, range(2)))
    assert calls == 2 and {result.status for result in results} == {"succeeded"}
    replay = registries[0].execute(
        root,
        project.id,
        "fake.paid",
        "1",
        {},
        "request-0",
        "run-0",
        0,
        "profile-a",
    )
    assert calls == 2
    assert replay.tool_call_id == results[0].tool_call_id
    registries[0].execute(
        root, project.id, "fake.paid", "1", {}, "profile-change", "run-3", 0, "profile-b"
    )
    assert calls == 3


def test_b01_10_queued_tool_reuse_preserves_the_canonical_job_id(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Queued")
    calls = 0

    def execute(*args):
        nonlocal calls
        calls += 1
        call_id = args[-1]
        return ToolResultV1(
            True,
            "queued",
            call_id,
            [],
            "Job queued.",
            [],
            job={
                "job_id": "canonical-job-id",
                "status": "queued",
                "job_type": "fake.job",
                "stage": "queued",
                "elapsed_seconds": 0,
                "provider": "profile-a",
                "can_cancel": True,
                "can_stop_waiting": False,
            },
        )

    manifest = ToolManifestV1(
        "fake.job",
        "1",
        "Job",
        "Job",
        {"type": "object", "additionalProperties": False, "properties": {}},
        {"type": "object"},
        RiskLevel.EXTERNAL,
        "job",
        True,
        True,
        [],
        "fake.job",
    )
    registry = ToolRegistry()
    registry.register(manifest, execute)

    first = registry.execute(
        root, project.id, "fake.job", "1", {}, "first-request", None, 0, "profile-a"
    )
    reused = registry.execute(
        root, project.id, "fake.job", "1", {}, "second-request", None, 0, "profile-a"
    )

    assert calls == 1
    assert first.job is not None and first.job["job_id"] == "canonical-job-id"
    assert reused.reused is True
    assert reused.job is not None and reused.job["job_id"] == "canonical-job-id"


@pytest.mark.parametrize("tool_name", ["job.get_status", "job.retry"])
def test_mutable_job_action_is_fresh_for_each_request_but_replays_the_same_request(
    tmp_path: Path,
    tool_name: str,
):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Mutable job status")
    calls = 0

    def execute(*args):
        nonlocal calls
        calls += 1
        return ToolResultV1(
            True,
            "succeeded",
            args[-1],
            [],
            "queued" if calls == 1 else "succeeded",
            [],
        )

    manifest = ToolManifestV1(
        tool_name,
        "1.0.0",
        "Job status",
        "Read mutable job status.",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["job_id"],
            "properties": {"job_id": {"type": "string"}},
        },
        {"type": "object"},
        RiskLevel.READ_ONLY,
        "sync",
        False,
        False,
        [],
        "fake.job_status",
    )
    registry = ToolRegistry()
    registry.register(manifest, execute)

    first = registry.execute(
        root,
        project.id,
        tool_name,
        "1.0.0",
        {"job_id": "job-1"},
        "status-request-1",
    )
    replayed = registry.execute(
        root,
        project.id,
        tool_name,
        "1.0.0",
        {"job_id": "job-1"},
        "status-request-1",
    )
    refreshed = registry.execute(
        root,
        project.id,
        tool_name,
        "1.0.0",
        {"job_id": "job-1"},
        "status-request-2",
    )

    assert calls == 2
    assert replayed == first
    assert first.summary == "queued"
    assert refreshed.summary == "succeeded"
    assert refreshed.reused is False
    connection = connect(root / "project.sqlite3")
    assert connection.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0] == 2
    connection.close()


def test_model_preview_handoff_is_fresh_for_each_request(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Repeat preview handoff")
    source = tmp_path / "model-input.png"
    Image.new("RGB", (2, 2)).save(source)
    asset = AssetService().import_file(
        root,
        project.id,
        source,
        "source_image",
        "preview-input",
    )
    calls = 0

    def execute(*args):
        nonlocal calls
        calls += 1
        call_id = args[-1]
        return ToolResultV1(
            True,
            "awaiting_ui_action",
            call_id,
            [],
            "Open preview.",
            [],
            {"type": "capture_model_preview"},
            {
                "action_id": call_id,
                "type": "capture_model_preview",
                "workspace_mode": "model3d",
            },
        )

    manifest = ToolManifestV1(
        "model3d.render_preview",
        "1.0.0",
        "Model preview",
        "Open the managed model preview.",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["asset_id"],
            "properties": {"asset_id": {"type": "string"}},
        },
        {"type": "object"},
        RiskLevel.LOCAL_REVERSIBLE,
        "sync",
        False,
        False,
        [],
        "fake.model_preview",
    )
    registry = ToolRegistry()
    registry.register(manifest, execute)

    first = registry.execute(
        root,
        project.id,
        "model3d.render_preview",
        "1.0.0",
        {"asset_id": asset["id"]},
        "preview-request-1",
    )
    second = registry.execute(
        root,
        project.id,
        "model3d.render_preview",
        "1.0.0",
        {"asset_id": asset["id"]},
        "preview-request-2",
    )

    assert calls == 2
    assert first.ui_action is not None
    assert second.ui_action is not None
    assert first.ui_action["action_id"] != second.ui_action["action_id"]
    assert second.reused is False


def test_explicit_analysis_revision_bypasses_only_the_completed_result_reuse(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Reanalysis")
    calls = 0

    def execute(*args):
        nonlocal calls
        calls += 1
        return ToolResultV1(True, "succeeded", args[-1], [], f"analysis-{calls}", [])

    manifest = ToolManifestV1(
        "fake.analysis",
        "1",
        "Analysis",
        "Analysis",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "analysis_revision": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                }
            },
        },
        {"type": "object"},
        RiskLevel.EXTERNAL,
        "job",
        False,
        True,
        [],
        "fake.analysis",
    )
    registry = ToolRegistry()
    registry.register(manifest, execute)

    first = registry.execute(
        root, project.id, "fake.analysis", "1", {}, "first-analysis", None, 0, "profile-a"
    )
    reused = registry.execute(
        root, project.id, "fake.analysis", "1", {}, "duplicate-analysis", None, 0, "profile-a"
    )
    refreshed = registry.execute(
        root,
        project.id,
        "fake.analysis",
        "1",
        {"analysis_revision": "user-requested-reanalysis-1"},
        "fresh-analysis",
        None,
        0,
        "profile-a",
    )

    assert calls == 2
    assert first.summary == "analysis-1"
    assert reused.reused is True
    assert refreshed.reused is False
    assert refreshed.summary == "analysis-2"
