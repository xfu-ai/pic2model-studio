from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..domain.common import DomainErrorV1, ErrorCode, RiskLevel, canonical_json, new_id
from ..domain.tools import ToolManifestV1, ToolResultV1
from .assets import AssetService
from .ports import SelectionRepositoryPort
from .projects import ProjectService
from .selections import SelectionService
from .tools import JobSubmitter, ToolRegistry

B01_TOOLS = (
    ("project.get_state", RiskLevel.READ_ONLY, "sync"),
    ("project.save_checkpoint", RiskLevel.LOCAL_REVERSIBLE, "sync"),
    ("project.export_package", RiskLevel.LOCAL_REVERSIBLE, "job"),
    ("asset.list", RiskLevel.READ_ONLY, "sync"),
    ("asset.get_metadata", RiskLevel.READ_ONLY, "sync"),
    ("asset.set_current", RiskLevel.LOCAL_REVERSIBLE, "sync"),
    ("asset.compare", RiskLevel.READ_ONLY, "sync"),
    ("asset.hide", RiskLevel.LOCAL_REVERSIBLE, "sync"),
    ("asset.restore_hidden", RiskLevel.LOCAL_REVERSIBLE, "sync"),
    ("asset.move_to_trash", RiskLevel.DESTRUCTIVE, "sync"),
    ("asset.restore_from_trash", RiskLevel.LOCAL_REVERSIBLE, "sync"),
    ("asset.open_output_folder", RiskLevel.LOCAL_REVERSIBLE, "sync"),
    ("selection.get_current", RiskLevel.READ_ONLY, "sync"),
    ("selection.request_user", RiskLevel.LOCAL_REVERSIBLE, "sync"),
    ("selection.set_suggestion", RiskLevel.LOCAL_REVERSIBLE, "sync"),
    ("selection.confirm", RiskLevel.LOCAL_REVERSIBLE, "sync"),
    ("image.crop", RiskLevel.LOCAL_REVERSIBLE, "sync"),
    ("image.render_annotation", RiskLevel.LOCAL_REVERSIBLE, "sync"),
)

# These are the byte-for-byte frozen B01 manifest fixtures.  Any contract
# edit must intentionally update this reviewable baseline and its tests.
MANIFEST_SHA256 = {
    "asset.compare@1.0.0.json": "b30d5c38539d022327136bfa0d60398ce90b6c5a19d8f68422d3b0897369acec",
    "asset.get_metadata@1.0.0.json": "de5cb5fee84f4fb7aa4b989e7efccee11974d7f5b307d4cfa315861ee8273d39",
    "asset.hide@1.0.0.json": "795150f5449a463ca4865a38823acd5a9e193822a339dd9a9a8a4ab9ebfa61a7",
    "asset.list@1.0.0.json": "20927ee88086b4ba3a2dcc879df7c3549e501eeb4fa52e862772d08a9d777c1c",
    "asset.move_to_trash@1.0.0.json": "28683c2df093bd7d5c7e2b8c0de5b45f9e34302e92c995cb48585e2bb13f855a",
    "asset.open_output_folder@1.0.0.json": "878492571c5f4532faa795206a959f073bb42c065980a06c942ab9a296ffefc7",
    "asset.restore_from_trash@1.0.0.json": "09d5a49f14cc643655306a8275ec33a904d1c9d4289829738a5f80bedce2f3d4",
    "asset.restore_hidden@1.0.0.json": "396ea2299e751a4c02eb914b33e0a750db42ff7040bdfcd94e2ef55911a20941",
    "asset.set_current@1.0.0.json": "6f16def0b63ceb061ca7b6ca385356d4370e9b3617832893c78a6e04e142f0ce",
    "image.crop@1.0.0.json": "aafc8bc661268c257853d692e1a4621dd0dd862b6cc9ae9e20cfd4bac49a8594",
    "image.render_annotation@1.0.0.json": "fde0f0563c8b0f0b4eac7e61628fc0805c44784283a4f18352caac27ea38bc22",
    "project.export_package@1.0.0.json": "7be5a83d95b4e87ed6edc2628f3b97acbf6a35e2787ef8866359c60fe339dacb",
    "project.get_state@1.0.0.json": "2dab42fa8831b6f03cf7f2ee1d4cb7661498bf2572962296091f19f656031c55",
    "project.save_checkpoint@1.0.0.json": "2cfe758ab1ab14301eaa2904ab989ab70aacc5ed174c7bdd78f1017f35ba2723",
    "selection.confirm@1.0.0.json": "d7edd88517293fcb326e702763ff59a96745154314a072d31cd57632ae938d99",
    "selection.get_current@1.0.0.json": "56a13baa8f74d243c36e78457f7608e99fa60452aafa157d87ecfd9b5bdd1eed",
    "selection.request_user@1.0.0.json": "fcab3f8bba8a1cb2e22136f946e1d62341e472cdb0bb6f74ebb62ac547d60ccb",
    "selection.set_suggestion@1.0.0.json": "4087ee7cdd581bfaeb8785acd806a704422b13d838d56bb4a9e3bf41316ed171",
}


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required or [],
    }


def _result_schema() -> dict[str, Any]:
    return _schema(
        {
            "ok": {"type": "boolean"},
            "status": {"enum": ["succeeded", "queued", "awaiting_ui_action", "failed"]},
            "tool_call_id": {"type": "string"},
            "output_asset_ids": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": "string"},
            "warnings": {"type": "array", "items": {"type": "string"}},
            "expected_action": {"type": ["object", "null"]},
            "ui_action": {"type": ["object", "null"]},
            "job": {"type": ["object", "null"]},
            "error": {"type": ["object", "null"]},
            "reused": {"type": "boolean"},
        },
        ["ok", "status", "tool_call_id", "output_asset_ids", "summary", "warnings"],
    )


ASSET_ID = {"asset_id": {"type": "string"}}
PROJECT_ID = {"project_id": {"type": "string"}}


def _input_schema(name: str) -> dict[str, Any]:
    if name == "project.get_state":
        return _schema(PROJECT_ID, ["project_id"])
    if name == "project.save_checkpoint":
        return _schema(
            {**PROJECT_ID, "request_id": {"type": "string"}}, ["project_id", "request_id"]
        )
    if name in {
        "asset.get_metadata",
        "asset.hide",
        "asset.restore_hidden",
        "asset.restore_from_trash",
        "asset.open_output_folder",
        "selection.get_current",
    }:
        return _schema(ASSET_ID, ["asset_id"])
    if name == "asset.list":
        return _schema({**PROJECT_ID, "group": {"type": "string"}}, ["project_id"])
    if name == "asset.set_current":
        return _schema(
            {
                **ASSET_ID,
                "decision_source": {"enum": ["user", "agent", "import", "system"]},
                "reason": {"type": "string"},
            },
            ["asset_id", "decision_source"],
        )
    if name == "asset.compare":
        return _schema(
            {"left_id": {"type": "string"}, "right_id": {"type": "string"}}, ["left_id", "right_id"]
        )
    if name == "asset.move_to_trash":
        return _schema({**ASSET_ID, "impact_token": {"type": "string"}}, ["asset_id"])
    if name == "selection.request_user":
        return _schema({**ASSET_ID, "run_id": {"type": "string"}}, ["asset_id"])
    if name == "selection.set_suggestion":
        return _schema(
            {
                **ASSET_ID,
                "rects": {"type": "array", "minItems": 1, "items": {"type": "object"}},
                "label": {"type": "string"},
            },
            ["asset_id", "rects"],
        )
    if name in {"selection.confirm", "image.crop", "image.render_annotation"}:
        return _schema(
            {"selection_id": {"type": "string"}, "revision": {"type": "integer"}}, ["selection_id"]
        )
    return _schema(
        {
            "archive_capability_id": {"type": "string"},
            "create_root_capability_id": {"type": "string"},
            "host_capability_id": {"type": "string"},
            "format": {"enum": ["project_v1"]},
        },
        [],
    )


def _success(
    call_id: str,
    summary: str,
    output: list[str] | None = None,
    *,
    ui_action: dict[str, Any] | None = None,
) -> ToolResultV1:
    return ToolResultV1(
        True,
        "awaiting_ui_action" if ui_action else "succeeded",
        call_id,
        output or [],
        summary,
        [],
        expected_action={"type": ui_action["type"]} if ui_action else None,
        ui_action=ui_action,
    )


def _executor(
    name: str,
    job_submitter: JobSubmitter | None,
    assets: AssetService,
    selections: SelectionService,
    projects: ProjectService,
    selection_repository: SelectionRepositoryPort,
):
    def execute(
        root: Path, project_id: str, arguments: dict[str, Any], call_id: str
    ) -> ToolResultV1:
        if name == "project.get_state":
            project = projects.open(root, force_read_only=True)
            if project.id != project_id or arguments["project_id"] != project_id:
                raise DomainErrorV1(ErrorCode.PROJECT_NOT_FOUND, "Project does not exist.")
            workspace_state = json.loads(
                projects.workspace_state(root, project_id, force_read_only=True)
            )
            return _success(
                call_id,
                canonical_json({**project.__dict__, "workspace_state": workspace_state}),
            )
        if name == "project.save_checkpoint":
            event = projects.save_checkpoint(root, project_id, arguments["request_id"])
            return _success(call_id, canonical_json(event))
        if name == "asset.list":
            items = assets.list_by_group(
                root,
                project_id,
                group=arguments.get("group"),
                read_only=True,
            )
            return _success(
                call_id,
                canonical_json(items),
                [str(item["id"]) for item in items],
            )
        if name == "asset.get_metadata":
            asset_id = arguments["asset_id"]
            payload = {
                "asset": assets.get(root, project_id, asset_id, read_only=True),
                "lineage": assets.lineage(root, project_id, asset_id, read_only=True),
            }
            return _success(
                call_id,
                canonical_json(payload),
                [asset_id],
            )
        if name == "asset.set_current":
            result = assets.set_current(
                root,
                project_id,
                arguments["asset_id"],
                arguments["decision_source"],
                call_id,
                arguments.get("reason"),
            )
            return _success(call_id, canonical_json(result), [arguments["asset_id"]])
        if name == "asset.compare":
            comparison = assets.compare_siblings(
                root,
                project_id,
                arguments["left_id"],
                arguments["right_id"],
                read_only=True,
            )
            return _success(
                call_id,
                canonical_json(comparison),
                [arguments["left_id"], arguments["right_id"]],
                ui_action={
                    "action_id": new_id(),
                    "type": "compare_assets",
                    "workspace_mode": "asset_comparison",
                    "asset_id": arguments["left_id"],
                },
            )
        if name == "asset.hide":
            asset = assets.hide(root, project_id, arguments["asset_id"], True, call_id)
            return _success(call_id, canonical_json(asset), [arguments["asset_id"]])
        if name == "asset.restore_hidden":
            asset = assets.hide(root, project_id, arguments["asset_id"], False, call_id)
            return _success(call_id, canonical_json(asset), [arguments["asset_id"]])
        if name == "asset.move_to_trash":
            asset = assets.trash(
                root,
                project_id,
                arguments["asset_id"],
                arguments.get("impact_token"),
                call_id,
            )
            return _success(call_id, canonical_json(asset), [arguments["asset_id"]])
        if name == "asset.restore_from_trash":
            asset = assets.restore_from_trash(root, project_id, arguments["asset_id"], call_id)
            return _success(call_id, canonical_json(asset), [arguments["asset_id"]])
        if name == "asset.open_output_folder":
            return _success(
                call_id,
                "已请求打开受管输出目录。",
                [arguments["asset_id"]],
                ui_action={
                    "action_id": new_id(),
                    "type": "open_output_folder",
                    "workspace_mode": "image_preview",
                    "asset_id": arguments["asset_id"],
                },
            )
        if name == "selection.get_current":
            selection_id = selection_repository.current_id(
                root / "project.sqlite3",
                project_id,
                arguments["asset_id"],
                read_only=True,
            )
            selection = (
                selections.get(root, project_id, selection_id, read_only=True)
                if selection_id
                else None
            )
            return _success(
                call_id,
                canonical_json(selection),
                [selection_id] if selection_id else [],
            )
        if name == "selection.request_user":
            return _success(
                call_id,
                "等待用户确认选区。",
                [arguments["asset_id"]],
                ui_action={
                    "action_id": new_id(),
                    "type": "select_rectangle",
                    "workspace_mode": "rectangle_selection",
                    "asset_id": arguments["asset_id"],
                    "run_id": arguments.get("run_id"),
                },
            )
        if name == "selection.set_suggestion":
            selection = selections.save(
                root,
                project_id,
                arguments["asset_id"],
                arguments["rects"],
                arguments.get("label", "AI 建议"),
                "agent",
                request_id=call_id,
                confidence=(
                    arguments["rects"][0].get("confidence")
                    if isinstance(arguments["rects"][0], dict)
                    else None
                ),
            )
            return _success(call_id, canonical_json(selection), [selection["id"]])
        if name == "selection.confirm":
            current = selections.get(root, project_id, arguments["selection_id"])
            selection = selections.confirm(
                root,
                project_id,
                arguments["selection_id"],
                arguments.get("revision", current["revision"]),
                call_id,
                include_event=True,
            )
            confirmed = selection["selection"]
            return _success(call_id, canonical_json(selection), [confirmed["id"]])
        if name == "image.crop":
            cropped = selections.crop(root, project_id, arguments["selection_id"], call_id)
            return _success(
                call_id,
                canonical_json(cropped),
                [item["id"] for item in cropped],
            )
        if name == "image.render_annotation":
            annotation = selections.render_annotation(
                root, project_id, arguments["selection_id"], call_id
            )
            return _success(
                call_id,
                canonical_json(annotation),
                [annotation["id"]],
            )
        if job_submitter is None:
            return ToolResultV1(
                False,
                "failed",
                call_id,
                [],
                "The project package exporter is available only through the desktop Export flow.",
                [],
                error={
                    "code": "TOOL_NOT_AVAILABLE",
                    "category": "api_not_configured",
                    "user_message": "Use the desktop Export action to create a project package.",
                    "recoverable": True,
                    "failed_object": "tool_call",
                    "failed_step": "dispatch",
                    "safe_to_retry": False,
                    "recommended_action": "use_desktop_export",
                },
            )
        job = job_submitter.submit(project_id, name, arguments)
        return ToolResultV1(
            True,
            "queued",
            call_id,
            [],
            "本地任务已排队，等待受管执行器处理。",
            [],
            job=job.__dict__,
        )

    return execute


def register_b01_tools(
    registry: ToolRegistry,
    assets: AssetService,
    selections: SelectionService,
    projects: ProjectService,
    selection_repository: SelectionRepositoryPort,
    job_submitter: JobSubmitter | None = None,
) -> None:
    directory = Path(__file__).with_name("tool_manifests")
    expected = {f"{name}@1.0.0.json" for name, _, _ in B01_TOOLS}
    actual = {path.name for path in directory.glob("*.json")}
    if actual != expected or set(MANIFEST_SHA256) != expected:
        raise RuntimeError(
            f"B01 manifest set mismatch: missing={expected - actual}, extra={actual - expected}"
        )
    fields = {
        "name",
        "version",
        "human_name",
        "description",
        "input_schema",
        "output_schema",
        "risk_level",
        "execution",
        "idempotency",
        "supports_cancel",
        "allowed_asset_types",
        "executor_key",
    }
    expected_runtime = {name: (risk, execution) for name, risk, execution in B01_TOOLS}
    for path in sorted(directory.glob("*.json")):
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256[path.name]:
            raise RuntimeError(f"manifest SHA-256 drift: {path.name}")
        data = json.loads(raw)
        if set(data) != fields:
            raise RuntimeError(f"invalid frozen manifest fields: {path.name}")
        name = data["name"]
        if (
            name not in expected_runtime
            or path.name != f"{name}@{data['version']}.json"
            or data["version"] != "1.0.0"
            or data["risk_level"] != expected_runtime[name][0].value
            or data["execution"] != expected_runtime[name][1]
            or data["executor_key"] != f"b01:{name}"
        ):
            raise RuntimeError(
                f"manifest contract drift: {path.name}:{hashlib.sha256(raw).hexdigest()}"
            )
        data["risk_level"] = RiskLevel(data["risk_level"])
        registry.register(
            ToolManifestV1(**data),
            _executor(name, job_submitter, assets, selections, projects, selection_repository),
        )
