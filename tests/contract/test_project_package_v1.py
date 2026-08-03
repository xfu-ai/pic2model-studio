import hashlib
import json
import sqlite3
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from PIL import Image

from aipic_to_model.application.archive_import import ProjectPackageService
from aipic_to_model.application.assets import AssetService
from aipic_to_model.application.projects import ProjectService
from aipic_to_model.domain.common import DomainErrorV1, ErrorCode, canonical_json
from aipic_to_model.infrastructure.sqlite.connection import connect, transaction
from aipic_to_model.infrastructure.sqlite.repositories import OperationRepository


def test_b01_08_export_v1_contains_only_relative_safe_assets(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Demo")
    source = tmp_path / "image.png"
    Image.new("RGB", (3, 3)).save(source)
    AssetService().import_file(root, project.id, source, "source_image", "r")
    package = tmp_path / "export.pic2model"
    result = ProjectPackageService().export_v1(root, package)
    assert result["asset_count"] == 2
    with zipfile.ZipFile(package) as archive:
        assert "manifest.json" in archive.namelist()
        assert all(not name.startswith("C:") and ".." not in name for name in archive.namelist())
        assert (
            result["manifest_sha256"] == hashlib.sha256(archive.read("manifest.json")).hexdigest()
        )
    assert ProjectPackageService().inspect_v1(package)["format"] == "Pic2ModelProject"
    imported = ProjectPackageService().import_v1(package, tmp_path / "imported")
    assert imported["project_id"] == project.id
    assert (tmp_path / "imported" / "project.json").exists()


def test_b01_08_fixed_complete_v1_package_imports_with_expected_identity(tmp_path: Path):
    package = (
        Path(__file__).parents[1]
        / "fixtures"
        / "project_packages"
        / "complete-v1.pic2model"
    )
    expected = json.loads(
        package.with_name(f"{package.name}.expected-import.json").read_text("utf-8")
    )
    assert hashlib.sha256(package.read_bytes()).hexdigest() == expected["sha256"]
    imported = ProjectPackageService().import_v1(package, tmp_path / "fixed")
    assert imported["project_id"] == expected["project_id"]
    assert imported["asset_count"] == expected["asset_count"]


def test_b01_08_v1_package_schema_rejects_unknown_manifest_fields(tmp_path: Path):
    package = (
        Path(__file__).parents[1]
        / "fixtures"
        / "project_packages"
        / "complete-v1.pic2model"
    )
    bad = tmp_path / "bad.pic2model"
    with zipfile.ZipFile(package) as source, zipfile.ZipFile(bad, "w") as target:
        manifest = json.loads(source.read("manifest.json"))
        manifest["machine_path"] = "C:\\secret"
        target.writestr("manifest.json", json.dumps(manifest))
        for name in source.namelist():
            if name != "manifest.json":
                target.writestr(name, source.read(name))
    from aipic_to_model.domain.common import DomainErrorV1

    with pytest.raises(DomainErrorV1):
        ProjectPackageService().inspect_v1(bad)


def test_b01_08_package_export_journal_completes_and_recovers_only_project_temp(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Demo")
    source = tmp_path / "image.png"
    Image.new("RGB", (2, 2)).save(source)
    AssetService().import_file(root, project.id, source, "source_image", "import")
    ProjectPackageService().export_v1(root, tmp_path / "package.pic2model", request_id="export")
    connection = sqlite3.connect(root / "project.sqlite3")
    assert (
        connection.execute(
            "SELECT state FROM operations WHERE kind='package_export' AND idempotency_key='export'"
        ).fetchone()[0]
        == "completed"
    )
    connection.close()
    temporary = root / "temp" / "interrupted-export.zip"
    temporary.write_bytes(b"partial")
    connection = connect(root / "project.sqlite3")
    with transaction(connection):
        operation = OperationRepository(connection).prepare(
            "package_export",
            "interrupted-export",
            {"format": "project_v1", "temporary_relative_path": "temp/interrupted-export.zip"},
        )
    with transaction(connection):
        assert OperationRepository(connection).recover(root) == [operation]
    connection.close()
    assert not temporary.exists()


def test_b01_08_export_request_id_replays_after_destination_exists(tmp_path: Path):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Demo")
    source = tmp_path / "image.png"
    Image.new("RGB", (2, 2)).save(source)
    AssetService().import_file(root, project.id, source, "source_image", "import")
    package = tmp_path / "package.pic2model"
    service = ProjectPackageService()
    first = service.export_v1(root, package, request_id="export")
    assert service.export_v1(root, package, request_id="export") == first
    with pytest.raises(DomainErrorV1) as conflict:
        service.export_v1(root, tmp_path / "other.pic2model", request_id="export")
    assert conflict.value.code == ErrorCode.IDEMPOTENCY_CONFLICT


def test_b01_08_concurrent_exports_never_clobber_the_same_destination(
    tmp_path: Path,
):
    root = tmp_path / "project"
    project = ProjectService().create(root, "Demo")
    source = tmp_path / "image.png"
    Image.new("RGB", (2, 2)).save(source)
    AssetService().import_file(root, project.id, source, "source_image", "import")
    destination = tmp_path / "concurrent.pic2model"
    service = ProjectPackageService()

    def export(request_id: str):
        try:
            return service.export_v1(root, destination, request_id=request_id)
        except DomainErrorV1 as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(export, ["first", "second"]))
    successes = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, Exception)]
    assert len(successes) == len(failures) == 1
    assert isinstance(failures[0], DomainErrorV1)
    assert failures[0].code == ErrorCode.INVALID_ARCHIVE
    assert destination.is_file() and destination.stat().st_size > 0


def test_b01_08_import_preserves_unowned_staging_directory(tmp_path: Path):
    package = (
        Path(__file__).parents[1]
        / "fixtures"
        / "project_packages"
        / "complete-v1.pic2model"
    )
    destination = tmp_path / "imported"
    staging = tmp_path / ".imported.importing"
    staging.mkdir()
    (staging / "orphan.bin").write_bytes(b"partial")
    with pytest.raises(DomainErrorV1) as rejected:
        ProjectPackageService().import_v1(package, destination)
    assert rejected.value.code == ErrorCode.INVALID_ARCHIVE
    assert (staging / "orphan.bin").read_bytes() == b"partial"
    assert not destination.exists()


def test_b01_08_import_recovers_only_matching_owned_staging_directory(
    tmp_path: Path,
):
    package = (
        Path(__file__).parents[1]
        / "fixtures"
        / "project_packages"
        / "complete-v1.pic2model"
    )
    destination = tmp_path / "imported"
    staging = tmp_path / ".imported.importing"
    service = ProjectPackageService()
    owner = service._import_owner(package, destination)
    staging.mkdir()
    (staging / service._IMPORT_OWNER).write_text(canonical_json(owner), encoding="utf-8")
    (staging / "partial.bin").write_bytes(b"owned-partial")
    result = service.import_v1(package, destination)
    assert result["asset_count"] == 1
    assert destination.is_dir() and not staging.exists()
    assert not (destination / service._IMPORT_OWNER).exists()
