import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from aipic_to_model.application.assets import AssetService
from aipic_to_model.application.projects import ProjectService
from aipic_to_model.domain.errors import DomainErrorV1
from aipic_to_model.infrastructure.fs import asset_files as asset_files_module
from aipic_to_model.infrastructure.sqlite.connection import connect, transaction
from aipic_to_model.infrastructure.sqlite.repositories import OperationRepository


def test_b01_10_open_recovers_file_written_trash_without_database_commit(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Demo")
    image = tmp_path / "image.png"
    Image.new("RGB", (2, 2)).save(image)
    asset = AssetService().import_file(root, project.id, image, "source_image", "import")
    connection = connect(root / "project.sqlite3")
    relative = connection.execute(
        "SELECT relative_path FROM assets WHERE id=?", (asset["id"],)
    ).fetchone()[0]
    trash = f"assets/trash/{asset['id']}/{Path(relative).name}"
    with transaction(connection):
        operation = OperationRepository(connection).prepare(
            "trash",
            "request",
            {
                "asset_id": asset["id"],
                "source_relative_path": relative,
                "trash_relative_path": trash,
            },
        )
        OperationRepository(connection).mark(operation, "file_written")
    connection.close()
    destination = root / trash
    destination.parent.mkdir(parents=True)
    (root / relative).replace(destination)
    ProjectService().open(root)
    assert (root / relative).is_file()
    connection = connect(root / "project.sqlite3")
    assert (
        connection.execute("SELECT state FROM operations WHERE id=?", (operation,)).fetchone()[0]
        == "completed"
    )
    connection.close()


def test_b01_10_real_trash_records_completed_operation(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Demo")
    image = tmp_path / "image.png"
    Image.new("RGB", (2, 2)).save(image)
    asset = AssetService().import_file(root, project.id, image, "source_image", "import")
    service = AssetService()
    impact = service.impact(root, project.id, asset["id"])
    service.trash(root, project.id, asset["id"], impact["impact_token"], "trash-request")
    connection = connect(root / "project.sqlite3")
    row = connection.execute(
        "SELECT state,payload_json FROM operations WHERE kind='trash' AND idempotency_key='trash-request'"
    ).fetchone()
    connection.close()
    assert row[0] == "completed"
    assert asset["id"] in row[1]


def test_b01_10_open_rolls_back_file_written_restore_without_database_commit(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Demo")
    image = tmp_path / "image.png"
    Image.new("RGB", (2, 2)).save(image)
    asset = AssetService().import_file(root, project.id, image, "source_image", "import")
    service = AssetService()
    impact = service.impact(root, project.id, asset["id"])
    service.trash(root, project.id, asset["id"], impact["impact_token"], "trash")
    connection = connect(root / "project.sqlite3")
    row = connection.execute(
        "SELECT relative_path,original_relative_path FROM assets WHERE id=?", (asset["id"],)
    ).fetchone()
    source, target = root / row["relative_path"], root / row["original_relative_path"]
    with transaction(connection):
        operation = OperationRepository(connection).prepare(
            "restore",
            "restore-request",
            {
                "asset_id": asset["id"],
                "trash_relative_path": row["relative_path"],
                "restored_relative_path": row["original_relative_path"],
            },
        )
        OperationRepository(connection).mark(operation, "file_written")
    connection.close()
    target.parent.mkdir(parents=True, exist_ok=True)
    source.replace(target)
    ProjectService().open(root)
    assert source.is_file() and not target.exists()
    connection = connect(root / "project.sqlite3")
    assert (
        connection.execute("SELECT state FROM operations WHERE id=?", (operation,)).fetchone()[0]
        == "completed"
    )
    connection.close()


def test_b01_10_open_removes_orphan_asset_write_after_crash(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Demo")
    source = tmp_path / "prompt.txt"
    source.write_text("retry-safe", encoding="utf-8")
    asset_id = "orphan-asset"
    relative = f"assets/generated/{asset_id}.txt"
    orphan = root / relative
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_text("interrupted", encoding="utf-8")
    connection = connect(root / "project.sqlite3")
    with transaction(connection):
        operation = OperationRepository(connection).prepare(
            "asset_write",
            "import-crash",
            {
                "asset_id": asset_id,
                "relative_paths": [relative],
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "asset_type": "prompt",
                "parent_asset_id": None,
            },
        )
        OperationRepository(connection).mark(operation, "file_written")
    connection.close()
    ProjectService().open(root)
    assert not orphan.exists()
    connection = connect(root / "project.sqlite3")
    assert (
        connection.execute("SELECT state FROM operations WHERE id=?", (operation,)).fetchone()[0]
        == "failed"
    )
    connection.close()
    retried = AssetService().import_file(root, project.id, source, "prompt", "import-crash")
    assert retried["id"] != asset_id
    connection = connect(root / "project.sqlite3")
    assert (
        connection.execute(
            "SELECT state FROM operations WHERE idempotency_key='import-crash'"
        ).fetchone()[0]
        == "completed"
    )
    connection.close()


def test_b01_10_open_marks_prepared_asset_write_retryable_without_touching_source(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Prepared")
    connection = connect(root / "project.sqlite3")
    with transaction(connection):
        operation = OperationRepository(connection).prepare(
            "asset_write",
            "prepared-write",
            {"asset_id": "not-written", "relative_paths": ["assets/generated/not-written.txt"]},
        )
    connection.close()
    assert ProjectService().open(root).id == project.id
    connection = connect(root / "project.sqlite3")
    try:
        state, recovery = connection.execute(
            "SELECT state,recovery_json FROM operations WHERE id=?", (operation,)
        ).fetchone()
    finally:
        connection.close()
    assert state == "failed" and json.loads(recovery)["safe_to_retry"] is True


def test_b01_10_open_completes_rename_after_database_commit_phase(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Old")
    connection = connect(root / "project.sqlite3")
    with transaction(connection):
        operation = OperationRepository(connection).prepare(
            "rename",
            "rename-db-committed",
            {
                "old_name": "Old",
                "new_name": "New",
                "old_updated_at": "2026-01-01T00:00:00.000Z",
                "new_updated_at": "2026-01-02T00:00:00.000Z",
            },
        )
        connection.execute(
            "UPDATE projects SET name=?,updated_at=? WHERE id=?",
            ("New", "2026-01-02T00:00:00.000Z", project.id),
        )
        OperationRepository(connection).mark(operation, "db_committed")
    connection.close()
    reopened = ProjectService().open(root)
    assert reopened.name == "New"
    assert '"name":"New"' in (root / "project.json").read_text(encoding="utf-8")


def test_b01_10_derived_registration_records_completed_file_journal(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Demo")
    image = tmp_path / "derived.png"
    Image.new("RGB", (3, 3)).save(image)
    asset = AssetService().register_derived(root, project.id, image, "generated_image", "derived")
    connection = connect(root / "project.sqlite3")
    operation = connection.execute(
        "SELECT state,payload_json FROM operations WHERE idempotency_key='derived'"
    ).fetchone()
    connection.close()
    assert operation["state"] == "completed"
    assert asset["id"] in operation["payload_json"]


def test_b01_10_import_disk_full_is_retryable_and_leaves_no_asset_or_temp(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Disk full")
    source = tmp_path / "source.txt"
    source.write_text("unchanged", encoding="utf-8")
    original = source.read_bytes()

    def disk_full(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(asset_files_module.shutil, "copyfile", disk_full)
    with pytest.raises(DomainErrorV1) as error:
        AssetService().import_file(root, project.id, source, "prompt", "disk-full")
    assert error.value.code == "LOCAL_STORAGE_UNAVAILABLE" and error.value.recoverable
    assert source.read_bytes() == original
    connection = connect(root / "project.sqlite3")
    try:
        assert connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0
    finally:
        connection.close()
    assert not list((root / "temp").glob("*.part"))
    monkeypatch.undo()
    retried = AssetService().import_file(root, project.id, source, "prompt", "disk-full")
    assert retried["name"] == "source.txt"


def test_b01_10_derived_disk_full_is_retryable_and_leaves_no_asset_or_temp(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Disk full derived")
    source = tmp_path / "derived.txt"
    source.write_text("derived", encoding="utf-8")

    def disk_full(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(asset_files_module.shutil, "copyfile", disk_full)
    with pytest.raises(DomainErrorV1) as error:
        AssetService().register_derived(root, project.id, source, "analysis", "derived-disk-full")
    assert error.value.code == "LOCAL_STORAGE_UNAVAILABLE" and error.value.recoverable
    connection = connect(root / "project.sqlite3")
    try:
        assert connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0
    finally:
        connection.close()
    assert not list((root / "temp").glob("*.part"))
    monkeypatch.undo()
    retried = AssetService().register_derived(
        root, project.id, source, "analysis", "derived-disk-full"
    )
    assert retried["name"] == "derived.txt"
