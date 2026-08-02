from pathlib import Path

from PIL import Image

from aipic_to_model.application.assets import AssetService
from aipic_to_model.application.projects import ProjectService
from aipic_to_model.application.tools import ToolRegistry
from aipic_to_model.domain.common import RiskLevel
from aipic_to_model.domain.tools import ToolManifestV1, ToolResultV1
from aipic_to_model.infrastructure.sqlite.connection import connect


def test_b01_10_tool_audit_records_arguments_duration_and_asset_directions(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Audit")
    image = tmp_path / "image.png"
    Image.new("RGB", (2, 2)).save(image)
    asset = AssetService().import_file(root, project.id, image, "source_image", "import")
    registry = ToolRegistry()
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"asset_id": {"type": "string"}},
        "required": ["asset_id"],
    }
    registry.register(
        ToolManifestV1(
            "fake.audit",
            "1.0.0",
            "Audit",
            "audit",
            schema,
            {"type": "object"},
            RiskLevel.READ_ONLY,
            "sync",
            True,
            False,
            [],
            "fake.audit",
        ),
        lambda _root, _project, arguments, call_id: ToolResultV1(
            True, "succeeded", call_id, [arguments["asset_id"]], "ok", []
        ),
    )
    registry.execute(root, project.id, "fake.audit", "1.0.0", {"asset_id": asset["id"]}, "audit")
    connection = connect(root / "project.sqlite3")
    call = connection.execute("SELECT arguments_json,duration_ms,status FROM tool_calls").fetchone()
    links = {row[0] for row in connection.execute("SELECT direction FROM tool_call_assets")}
    connection.close()
    assert (
        asset["id"] in call["arguments_json"]
        and call["status"] == "succeeded"
        and call["duration_ms"] is not None
    )
    assert links == {"input", "output"}
