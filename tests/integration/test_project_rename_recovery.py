import json
from pathlib import Path

from aipic_to_model.application.projects import ProjectService
from aipic_to_model.infrastructure.fs.atomic_io import atomic_write_text
from aipic_to_model.infrastructure.sqlite.connection import connect, transaction
from aipic_to_model.infrastructure.sqlite.repositories import OperationRepository


def test_b01_02_open_recovers_file_written_rename(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Old")
    connection = connect(root / "project.sqlite3")
    with transaction(connection):
        OperationRepository(connection).prepare(
            "rename", "request", {"old_name": "Old", "new_name": "New"}
        )
        operation = connection.execute("SELECT id FROM operations").fetchone()[0]
        OperationRepository(connection).mark(operation, "file_written")
    connection.close()
    metadata = json.loads((root / "project.json").read_text(encoding="utf-8"))
    metadata["name"] = "New"
    atomic_write_text(root / "project.json", json.dumps(metadata))

    reopened = ProjectService().open(root)
    assert reopened.id == project.id
    assert reopened.name == "Old"
    assert json.loads((root / "project.json").read_text(encoding="utf-8"))["name"] == "Old"
