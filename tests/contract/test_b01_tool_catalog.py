import json

import pytest
from PIL import Image

from aipic_to_model.application.assets import AssetService
from aipic_to_model.application.projects import ProjectService
from aipic_to_model.application.tool_catalog import B01_TOOLS, register_b01_tools
from aipic_to_model.application.tools import ToolRegistry
from aipic_to_model.domain.common import DomainErrorV1, ErrorCode, RiskLevel
from aipic_to_model.domain.tools import ToolManifestV1, ToolResultV1


def test_b01_10_all_frozen_tool_names_are_registered_once():
    registry = ToolRegistry()
    register_b01_tools(registry)
    assert {(manifest.name, manifest.version) for manifest in registry.manifests.values()} == {
        (name, "1.0.0") for name, _, _ in B01_TOOLS
    }


def test_b01_10_manifest_directory_is_exact_and_json_parseable():
    import json
    from pathlib import Path

    directory = (
        Path(__file__).parents[2] / "src" / "aipic_to_model" / "application" / "tool_manifests"
    )
    files = {path.name for path in directory.glob("*.json")}
    assert files == {f"{name}@1.0.0.json" for name, _, _ in B01_TOOLS}
    for path in directory.glob("*.json"):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["risk_level"] in {item.value for item in RiskLevel}
        assert manifest["input_schema"]["additionalProperties"] is False


def test_b01_10_registered_project_tool_executes_and_is_audited(tmp_path):
    project = ProjectService().create(tmp_path / "project", "Demo")
    registry = ToolRegistry()
    register_b01_tools(registry)
    result = registry.execute(
        tmp_path / "project",
        project.id,
        "project.get_state",
        "1.0.0",
        {"project_id": project.id},
        "request",
    )
    assert result.status == "succeeded"
    assert json.loads(result.summary) == {**project.__dict__, "workspace_state": {}}


def test_b01_10_read_only_tool_executes_when_project_writes_are_denied(tmp_path, monkeypatch):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Read only")
    registry = ToolRegistry()
    register_b01_tools(registry)
    monkeypatch.setattr(
        registry._filesystem,
        "require_writable_root",
        lambda _root: (_ for _ in ()).throw(
            DomainErrorV1(ErrorCode.PROJECT_READ_ONLY, "read only")
        ),
    )
    result = registry.execute(
        root,
        project.id,
        "project.get_state",
        "1.0.0",
        {"project_id": project.id},
        "read-only-request",
    )
    assert result.status == "succeeded"
    assert json.loads(result.summary)["id"] == project.id


def test_b01_10_all_read_only_tools_open_query_connections_in_uri_ro_mode(tmp_path, monkeypatch):
    from aipic_to_model.application.selections import SelectionService
    from aipic_to_model.infrastructure.sqlite import repositories

    root = tmp_path / "project"
    project = ProjectService().create(root, "URI read only")
    image = tmp_path / "image.png"
    Image.new("RGB", (4, 4)).save(image)
    assets = AssetService()
    source = assets.import_file(root, project.id, image, "source_image", "source")
    left = assets.register_derived(
        root,
        project.id,
        image,
        "generated_image",
        "left",
    )
    right = assets.register_derived(
        root,
        project.id,
        image,
        "generated_image",
        "right",
        parent_asset_id=left["id"],
        lineage_mode="new_version",
    )
    SelectionService().save(
        root,
        project.id,
        source["id"],
        [{"x": 0, "y": 0, "width": 2, "height": 2}],
        "target",
        "user",
    )
    registry = ToolRegistry()
    register_b01_tools(registry)
    monkeypatch.setattr(
        registry._filesystem,
        "require_writable_root",
        lambda _root: (_ for _ in ()).throw(
            DomainErrorV1(ErrorCode.PROJECT_READ_ONLY, "read only")
        ),
    )
    real_connect = repositories.connect
    modes: list[bool] = []

    def observing_connect(path, *, read_only=False):
        modes.append(read_only)
        return real_connect(path, read_only=read_only)

    monkeypatch.setattr(repositories, "connect", observing_connect)
    calls = [
        ("project.get_state", {"project_id": project.id}),
        ("asset.list", {"project_id": project.id}),
        ("asset.get_metadata", {"asset_id": left["id"]}),
        ("asset.compare", {"left_id": left["id"], "right_id": right["id"]}),
        ("selection.get_current", {"asset_id": source["id"]}),
    ]
    for index, (tool_name, arguments) in enumerate(calls):
        assert registry.execute(
            root,
            project.id,
            tool_name,
            "1.0.0",
            arguments,
            f"read-only-{index}",
        ).ok
    assert modes
    assert all(modes), "a read-only Tool opened SQLite without URI mode=ro"


def test_b01_10_real_query_executors_return_frozen_semantic_payloads(tmp_path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Semantic outputs")
    image = tmp_path / "image.png"
    Image.new("RGB", (4, 4)).save(image)
    assets = AssetService()
    source = assets.import_file(root, project.id, image, "source_image", "import")
    left = assets.register_derived(
        root,
        project.id,
        image,
        "generated_image",
        "left",
        input_asset_ids=[source["id"]],
    )
    right = assets.register_derived(
        root,
        project.id,
        image,
        "generated_image",
        "right",
        parent_asset_id=left["id"],
        input_asset_ids=[source["id"]],
        lineage_mode="new_version",
    )
    registry = ToolRegistry()
    register_b01_tools(registry)
    ProjectService().update_workspace_state(
        root,
        project.id,
        {
            "workflow_contexts": {
                "multiview": {
                    "selected": {
                        "source": str(source["id"]),
                        "front": str(left["id"]),
                        "side": str(left["id"]),
                        "back": str(right["id"]),
                    },
                    "regions": {},
                    "checks": {},
                    "quality_confirmed": True,
                    "set_id": "confirmed-set",
                    "job_id": None,
                }
            }
        },
        "persist-confirmed-multiview",
    )
    state = registry.execute(
        root,
        project.id,
        "project.get_state",
        "1.0.0",
        {"project_id": project.id},
        "state-request",
    )
    listed = registry.execute(
        root,
        project.id,
        "asset.list",
        "1.0.0",
        {"project_id": project.id, "group": "generated_images"},
        "list-request",
    )
    metadata = registry.execute(
        root,
        project.id,
        "asset.get_metadata",
        "1.0.0",
        {"asset_id": left["id"]},
        "metadata-request",
    )
    compared = registry.execute(
        root,
        project.id,
        "asset.compare",
        "1.0.0",
        {"left_id": left["id"], "right_id": right["id"]},
        "compare-request",
    )
    assert {item["id"] for item in json.loads(listed.summary)} >= {
        left["id"],
        right["id"],
    }
    metadata_payload = json.loads(metadata.summary)
    assert metadata_payload["asset"]["id"] == left["id"]
    assert "children" in metadata_payload["lineage"]
    comparison = json.loads(compared.summary)
    assert comparison["same_family"] is True
    assert compared.status == "awaiting_ui_action"
    assert compared.ui_action["type"] == "compare_assets"
    state_payload = json.loads(state.summary)
    assert state_payload["workspace_state"]["workflow_contexts"]["multiview"] == {
        "selected": {
            "source": str(source["id"]),
            "front": str(left["id"]),
            "side": str(left["id"]),
            "back": str(right["id"]),
        },
        "regions": {},
        "checks": {},
        "quality_confirmed": True,
        "set_id": "confirmed-set",
        "job_id": None,
    }


def test_b01_10_real_mutation_executors_return_every_frozen_semantic_dto(
    tmp_path,
):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Mutation outputs")
    image = tmp_path / "image.png"
    Image.new("RGB", (4, 4)).save(image)
    source = AssetService().import_file(root, project.id, image, "source_image", "import")
    registry = ToolRegistry()
    register_b01_tools(registry)

    def invoke(name, arguments):
        return registry.execute(
            root,
            project.id,
            name,
            "1.0.0",
            arguments,
            f"semantic-{name}",
        )

    checkpoint = json.loads(
        invoke(
            "project.save_checkpoint",
            {"project_id": project.id, "request_id": "checkpoint"},
        ).summary
    )
    assert checkpoint["event_type"] == "project.metadata.changed"

    current = json.loads(
        invoke(
            "asset.set_current",
            {"asset_id": source["id"], "decision_source": "user"},
        ).summary
    )
    assert current["decision"]["asset_id"] == source["id"]
    assert current["event"]["event_type"] == "asset.current_changed"

    hidden = json.loads(invoke("asset.hide", {"asset_id": source["id"]}).summary)
    assert hidden["is_hidden"] is True
    visible = json.loads(invoke("asset.restore_hidden", {"asset_id": source["id"]}).summary)
    assert visible["is_hidden"] is False

    suggestion = json.loads(
        invoke(
            "selection.set_suggestion",
            {
                "asset_id": source["id"],
                "rects": [
                    {
                        "x": 0,
                        "y": 0,
                        "width": 2,
                        "height": 2,
                        "label": "target",
                        "confidence": 0.75,
                    }
                ],
                "label": "target",
            },
        ).summary
    )
    assert suggestion["confidence"] == 0.75
    confirmed_payload = json.loads(
        invoke(
            "selection.confirm",
            {
                "selection_id": suggestion["id"],
                "revision": suggestion["revision"],
            },
        ).summary
    )
    assert confirmed_payload["selection"]["status"] == "confirmed"
    assert confirmed_payload["event"]["event_type"] == "selection.changed"

    crops = json.loads(invoke("image.crop", {"selection_id": suggestion["id"]}).summary)
    assert crops and crops[0]["asset_type"] == "crop"
    annotation = json.loads(
        invoke("image.render_annotation", {"selection_id": suggestion["id"]}).summary
    )
    assert annotation["asset_type"] == "annotation"

    impact = AssetService().impact(root, project.id, source["id"])
    trashed = json.loads(
        invoke(
            "asset.move_to_trash",
            {
                "asset_id": source["id"],
                "impact_token": impact["impact_token"],
            },
        ).summary
    )
    assert trashed["trashed_at"] is not None
    restored = json.loads(invoke("asset.restore_from_trash", {"asset_id": source["id"]}).summary)
    assert restored["trashed_at"] is None

    assert (
        invoke("asset.open_output_folder", {"asset_id": source["id"]}).ui_action["type"]
        == "open_output_folder"
    )
    assert (
        invoke("selection.request_user", {"asset_id": source["id"]}).ui_action["type"]
        == "select_rectangle"
    )
    for name, arguments in (
        (
            "project.export_package",
            {
                "host_capability_id": "destination",
                "format": "project_v1",
            },
        ),
    ):
        queued = invoke(name, arguments)
        assert queued.status == "queued"
        assert queued.job["status"] == "queued"


@pytest.mark.parametrize("forbidden", ["path", "url", "command"])
def test_b01_10_tool_rejects_unknown_arguments_before_execution(tmp_path, forbidden):
    project = ProjectService().create(tmp_path / "project", "Demo")
    registry = ToolRegistry()
    register_b01_tools(registry)
    with pytest.raises(DomainErrorV1):
        registry.execute(
            tmp_path / "project",
            project.id,
            "project.get_state",
            "1.0.0",
            {"project_id": project.id, forbidden: "C:/forbidden"},
            "request",
        )


def test_b01_10_external_failure_is_unknown_but_local_failure_is_terminal(tmp_path):
    project = ProjectService().create(tmp_path / "project", "Demo")
    schema = {"type": "object", "additionalProperties": False, "properties": {}}
    output = {"type": "object"}
    registry = ToolRegistry()
    registry.register(
        ToolManifestV1(
            "fake.external",
            "1.0.0",
            "fake",
            "fake",
            schema,
            output,
            RiskLevel.EXTERNAL,
            "sync",
            True,
            False,
            [],
            "external",
        ),
        lambda *_: (_ for _ in ()).throw(RuntimeError("sent")),
    )
    registry.register(
        ToolManifestV1(
            "fake.local",
            "1.0.0",
            "fake",
            "fake",
            schema,
            output,
            RiskLevel.LOCAL_REVERSIBLE,
            "sync",
            False,
            False,
            [],
            "local",
        ),
        lambda *_: (_ for _ in ()).throw(RuntimeError("local")),
    )
    with pytest.raises(RuntimeError):
        registry.execute(tmp_path / "project", project.id, "fake.external", "1.0.0", {}, "external")
    with pytest.raises(RuntimeError):
        registry.execute(tmp_path / "project", project.id, "fake.local", "1.0.0", {}, "local")
    from aipic_to_model.infrastructure.sqlite.connection import connect

    connection = connect(tmp_path / "project" / "project.sqlite3")
    states = dict(connection.execute("SELECT tool_name,status FROM tool_calls").fetchall())
    connection.close()
    assert states == {"fake.external": "unknown_submission", "fake.local": "failed"}


def test_b01_10_every_frozen_manifest_has_an_independently_invoked_executor(tmp_path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "All tools")
    image = tmp_path / "image.png"
    Image.new("RGB", (3, 3)).save(image)
    asset = AssetService().import_file(root, project.id, image, "source_image", "import")
    registry, counts = ToolRegistry(), {}
    register_b01_tools(registry)

    def fake(_root, _project_id, _arguments, call_id):
        counts[call_id] = counts.get(call_id, 0) + 1
        return ToolResultV1(True, "succeeded", call_id, [], "fixture", [])

    for manifest in registry.manifests.values():
        registry.executors[manifest.executor_key] = fake
    for index, manifest in enumerate(registry.manifests.values()):
        props = manifest.input_schema.get("properties", {})
        arguments = {}
        for name in manifest.input_schema.get("required", []):
            if name.endswith("asset_id") or name == "asset_id" or name in {"left_id", "right_id"}:
                arguments[name] = asset["id"]
            elif name == "project_id":
                arguments[name] = project.id
            elif name == "selection_id":
                arguments[name] = "selection-fixture"
            elif props[name].get("type") == "integer":
                arguments[name] = 1
            elif props[name].get("type") == "array":
                arguments[name] = [{"x": 0, "y": 0, "width": 1, "height": 1}]
            elif "enum" in props[name]:
                arguments[name] = props[name]["enum"][0]
            else:
                arguments[name] = "fixture"
        result = registry.execute(
            root, project.id, manifest.name, manifest.version, arguments, f"all-{index}"
        )
        assert result.status == "succeeded"
    assert len(counts) == len(B01_TOOLS) and set(counts.values()) == {1}
