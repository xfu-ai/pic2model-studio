from pathlib import Path

import pytest

from aipic_to_model.application.projects import ProjectService
from aipic_to_model.domain.common import DomainErrorV1, ErrorCode
from aipic_to_model.infrastructure.sqlite.connection import connect


def test_b01_02_project_lifecycle_and_relocation(tmp_path: Path):
    service = ProjectService()
    root = tmp_path / "project"
    created = service.create(root, "Demo")
    assert service.open(root).id == created.id
    moved = tmp_path / "moved"
    root.rename(moved)
    assert service.open(moved).root_path == "."
    assert service.rename(moved, created.id, "Renamed", "request-1").name == "Renamed"
    connection = connect(moved / "project.sqlite3")
    assert (
        connection.execute("SELECT state FROM operations WHERE kind='rename'").fetchone()[0]
        == "completed"
    )
    connection.close()


def test_b04_native_picker_can_create_in_existing_empty_directory(tmp_path: Path):
    service = ProjectService()
    root = tmp_path / "selected-empty-folder"
    root.mkdir()

    created = service.create(root, "Desktop project")

    assert service.open(root).id == created.id
    assert (root / "project.json").is_file()
    assert (root / "project.sqlite3").is_file()


def test_b01_02_rename_request_id_replays_or_conflicts(tmp_path: Path):
    service = ProjectService()
    project = service.create(tmp_path / "project", "Demo")
    root = tmp_path / "project"
    assert service.rename(root, project.id, "Renamed", "rename") == service.rename(
        root, project.id, "Renamed", "rename"
    )
    with pytest.raises(DomainErrorV1) as conflict:
        service.rename(root, project.id, "Different", "rename")
    assert conflict.value.code == ErrorCode.IDEMPOTENCY_CONFLICT


def test_b01_02_read_only_open_is_readable_and_rejects_rename(tmp_path: Path, monkeypatch):
    service = ProjectService()
    root = tmp_path / "project"
    project = service.create(root, "Read only")
    monkeypatch.setattr(ProjectService, "_root_state", staticmethod(lambda _: "read_only"))
    assert service.open(root).root_state == "read_only"
    with pytest.raises(DomainErrorV1) as error:
        service.rename(root, project.id, "Denied", "read-only-rename")
    assert error.value.code == ErrorCode.PROJECT_READ_ONLY
