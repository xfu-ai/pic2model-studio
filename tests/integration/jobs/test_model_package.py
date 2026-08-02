from __future__ import annotations

import zipfile

from test_conversion import _model

from aipic_to_model.application.jobs.model_conversion import ModelPackageService


def test_model_package_uses_fixed_safe_archive_paths(tmp_path) -> None:
    dependencies, project, model = _model(tmp_path)
    package = ModelPackageService(dependencies.assets).package(
        tmp_path / "project", project.id, [str(model["id"])], request_id="package"
    )
    assert package["asset_type"] == "export"
    status, content, _, _ = dependencies.assets.read_content(
        tmp_path / "project", project.id, str(package["id"]), None
    )
    assert status == 200
    archive = tmp_path / "package.zip"
    archive.write_bytes(content)
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.namelist()[0] == "manifest.json"
        assert all(
            ".." not in name and not name.startswith(("/", "\\")) for name in bundle.namelist()
        )
