import io
import zipfile

import pytest

from aipic_to_model.application.projects import ProjectService
from aipic_to_model.application.tools import ToolRegistry
from aipic_to_model.domain.common import DomainErrorV1, RiskLevel
from aipic_to_model.domain.tools import ToolManifestV1
from aipic_to_model.infrastructure.archive_safety import validate_zip


def test_b01_08_zip_traversal_is_rejected():
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        z.writestr("../escape.txt", "no")
    with zipfile.ZipFile(data) as z, pytest.raises(DomainErrorV1):
        validate_zip(z)


@pytest.mark.parametrize("name", ["/absolute.txt", "C:/drive.txt", "folder/../../escape.txt"])
def test_b01_08_absolute_and_drive_zip_paths_are_rejected(name: str):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr(name, "no")
    with zipfile.ZipFile(data) as archive, pytest.raises(DomainErrorV1):
        validate_zip(archive)


def test_b01_08_symlink_and_zip_bomb_ratio_are_rejected():
    symlink = io.BytesIO()
    with zipfile.ZipFile(symlink, "w") as archive:
        info = zipfile.ZipInfo("link")
        info.external_attr = 0o120777 << 16
        archive.writestr(info, "target")
    with zipfile.ZipFile(symlink) as archive, pytest.raises(DomainErrorV1):
        validate_zip(archive)
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("compressed.txt", b"x" * 200_000)
    with zipfile.ZipFile(bomb) as archive, pytest.raises(DomainErrorV1):
        validate_zip(archive)


@pytest.mark.parametrize("field", ["path", "command", "url"])
def test_b01_10_forbidden_tool_transport_input_never_reaches_executor_or_project_files(
    tmp_path, field: str
):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Tool input")
    sentinel = tmp_path / "outside.txt"
    sentinel.write_text("unchanged", encoding="utf-8")
    called = 0

    def executor(*_args):
        nonlocal called
        called += 1
        raise AssertionError("must not execute")

    registry = ToolRegistry()
    registry.register(
        ToolManifestV1(
            f"fake.forbidden_{field}",
            "1",
            "Forbidden",
            "Forbidden",
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {field: {"type": "string"}},
                "required": [field],
            },
            {"type": "object"},
            RiskLevel.EXTERNAL,
            "sync",
            True,
            False,
            [],
            f"forbidden:{field}",
        ),
        executor,
    )
    with pytest.raises(DomainErrorV1):
        registry.execute(
            root, project.id, f"fake.forbidden_{field}", "1", {field: str(sentinel)}, field
        )
    assert called == 0 and sentinel.read_text(encoding="utf-8") == "unchanged"
