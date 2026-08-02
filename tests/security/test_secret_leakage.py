from pathlib import Path

import pytest
from PIL import Image

from aipic_to_model.application.assets import AssetService
from aipic_to_model.application.projects import ProjectService
from aipic_to_model.application.tools import ToolRegistry
from aipic_to_model.domain.common import DomainErrorV1, ErrorCode, RiskLevel
from aipic_to_model.domain.tools import ToolManifestV1, ToolResultV1
from aipic_to_model.infrastructure.diagnostics import export, preview
from aipic_to_model.infrastructure.logging import append_log
from aipic_to_model.infrastructure.sqlite.connection import connect


def test_b01_09_logs_and_diagnostics_redact_secret_and_absolute_path(tmp_path: Path):
    append_log(tmp_path, "app", "Authorization: Bearer sentinel-secret C:\\Users\\private")
    prepared = preview(tmp_path, {"name": "FormWeaver Studio", "version": "0.1.0"})
    output = tmp_path / "diagnostics.zip"
    export(
        tmp_path, output, prepared["manifest_hash"], {"name": "FormWeaver Studio", "version": "0.1.0"}
    )
    assert "sentinel-secret" not in (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")
    assert b"sentinel-secret" not in output.read_bytes()


def test_b01_09_diagnostic_export_rejects_stale_confirmation_with_domain_error(tmp_path: Path):
    with pytest.raises(DomainErrorV1) as error:
        export(tmp_path, tmp_path / "diagnostics.zip", "stale", {"name": "A", "version": "1"})
    assert error.value.code == ErrorCode.IDEMPOTENCY_CONFLICT


def test_b01_09_tool_arguments_and_decision_reason_are_redacted_in_project_database(tmp_path: Path):
    root = tmp_path / "project"
    source = tmp_path / "source.png"
    Image.new("RGB", (2, 2)).save(source)
    project = ProjectService().create(root, "Secrets")
    asset = AssetService().import_file(root, project.id, source, "source_image", "import")
    secret = "Authorization: Bearer audit-sentinel https://example.invalid/?X-Amz-Signature=audit-signature"
    AssetService().set_current(root, project.id, asset["id"], "user", "current", secret)
    registry = ToolRegistry()
    registry.register(
        ToolManifestV1(
            "fake.audit_redaction",
            "1.0.0",
            "Audit redaction",
            "Audit redaction test",
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
            },
            {"type": "object"},
            RiskLevel.LOCAL_REVERSIBLE,
            "sync",
            True,
            False,
            [],
            "test:audit_redaction",
        ),
        lambda _root, _project, _arguments, call_id: ToolResultV1(
            True, "succeeded", call_id, [], "ok", []
        ),
    )
    registry.execute(root, project.id, "fake.audit_redaction", "1.0.0", {"reason": secret}, "audit")
    connection = connect(root / "project.sqlite3")
    try:
        persistent = b"".join(
            row[0].encode("utf-8")
            for row in connection.execute(
                "SELECT arguments_json FROM tool_calls UNION ALL "
                "SELECT payload_json FROM operations UNION ALL "
                "SELECT COALESCE(reason,'') FROM asset_decisions"
            )
        )
    finally:
        connection.close()
    assert b"audit-sentinel" not in persistent
    assert b"audit-signature" not in persistent
