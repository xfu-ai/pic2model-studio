from pathlib import Path
from datetime import UTC, datetime, timedelta
from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from aipic_to_model.api.app import create_app
from aipic_to_model.application.host_capabilities import HostCapabilityStore


def test_desktop_asset_routes_are_capability_only_and_recoverable(tmp_path: Path) -> None:
    caps, token = HostCapabilityStore(), "d" * 64
    client = TestClient(create_app(token, caps, tmp_path / "app.sqlite3"))
    headers = {"Origin": "http://tauri.localhost", "Authorization": f"Bearer {token}"}
    project = client.post(
        "/v1/projects", headers={**headers, "X-Request-Id": "create"},
        json={"name": "Demo", "create_capability_id": caps.issue(tmp_path / "project", "create")},
    ).json()
    image = tmp_path / "character.png"
    Image.new("RGBA", (32, 24), (255, 0, 0, 0)).save(image)
    image_capability = caps.issue(image, "import", project["id"])
    imported = client.post(
        f"/v1/projects/{project['id']}/assets/import", headers={**headers, "X-Request-Id": "import"},
        json={"file_capability_id": image_capability, "asset_type": "source_image", "request_id": "import"},
    )
    assert imported.status_code == 200
    asset = imported.json()
    assert asset["metadata"] == {"width": 32, "height": 24, "format": "PNG"}
    assert asset["thumbnail_asset_id"]
    thumbnail = client.get(
        f"/v1/assets/{asset['id']}/thumbnail",
        headers=headers,
        params={"project_id": project["id"]},
    )
    assert thumbnail.status_code == 200
    assert thumbnail.headers["content-type"].startswith("image/")
    assert thumbnail.headers["cache-control"] == "private, max-age=31536000, immutable"
    with Image.open(BytesIO(thumbnail.content)) as rendered:
        assert rendered.size == (32, 24)
    assert client.post(
        f"/v1/projects/{project['id']}/assets/import", headers={**headers, "X-Request-Id": "reuse"},
        json={"file_capability_id": image_capability, "asset_type": "source_image", "request_id": "reuse"},
    ).status_code == 403
    branch_image = tmp_path / "character-v2.png"
    Image.new("RGBA", (32, 24), (0, 255, 0, 0)).save(branch_image)
    branch = client.post(
        f"/v1/projects/{project['id']}/assets/import", headers={**headers, "X-Request-Id": "branch"},
        json={"file_capability_id": caps.issue(branch_image, "import", project["id"]), "asset_type": "source_image", "parent_asset_id": asset["id"], "request_id": "branch"},
    )
    assert branch.status_code == 200
    compared = client.post(
        "/v1/assets/compare", headers={**headers, "X-Request-Id": "compare"},
        json={"project_id": project["id"], "left_id": asset["id"], "right_id": branch.json()["id"], "request_id": "compare"},
    )
    assert compared.status_code == 200 and compared.json()["same_family"] is True
    current = client.post(
        f"/v1/assets/{asset['id']}/set-current", headers={**headers, "X-Request-Id": "current"},
        json={"project_id": project["id"], "decision_source": "import", "request_id": "current"},
    )
    assert current.status_code == 200 and current.json()["decision"]["asset_id"] == asset["id"]
    export_directory = tmp_path / "asset-exports"
    export_directory.mkdir()
    exported_asset = client.post(
        f"/v1/assets/{asset['id']}/export",
        headers={**headers, "X-Request-Id": "asset-export"},
        json={
            "project_id": project["id"],
            "export_capability_id": caps.issue(export_directory, "export", project["id"]),
            "request_id": "asset-export",
        },
    )
    assert exported_asset.status_code == 200
    assert (export_directory / asset["name"]).read_bytes() == image.read_bytes()
    hidden = client.post(
        f"/v1/assets/{asset['id']}/hide", headers={**headers, "X-Request-Id": "hide"},
        json={"project_id": project["id"], "request_id": "hide"},
    )
    assert hidden.status_code == 200 and hidden.json()["is_hidden"] is True
    restored_hidden = client.post(
        f"/v1/assets/{asset['id']}/restore-hidden", headers={**headers, "X-Request-Id": "restore-hidden"},
        json={"project_id": project["id"], "request_id": "restore-hidden"},
    )
    assert restored_hidden.status_code == 200 and restored_hidden.json()["is_hidden"] is False
    impact = client.get(f"/v1/assets/{asset['id']}/impact?project_id={project['id']}", headers=headers)
    assert impact.status_code == 200 and impact.json()["impact_token"]
    mismatch = client.post(
        f"/v1/assets/{asset['id']}/trash", headers={**headers, "X-Request-Id": "wrong-impact"},
        json={"project_id": project["id"], "impact_token": "not-the-issued-impact", "request_id": "wrong-impact"},
    )
    assert mismatch.status_code == 409
    assert client.get(f"/v1/assets/{asset['id']}", headers=headers, params={"project_id": project["id"]}).json()["trashed_at"] is None
    trashed = client.post(
        f"/v1/assets/{asset['id']}/trash", headers={**headers, "X-Request-Id": "trash"},
        json={"project_id": project["id"], "impact_token": impact.json()["impact_token"], "request_id": "trash"},
    )
    assert trashed.status_code == 200 and trashed.json()["trashed_at"] is not None
    restored = client.post(
        f"/v1/assets/{asset['id']}/restore", headers={**headers, "X-Request-Id": "restore"},
        json={"project_id": project["id"], "request_id": "restore"},
    )
    assert restored.status_code == 200 and restored.json()["trashed_at"] is None
    invalid = tmp_path / "invalid.png"
    invalid.write_bytes(b"not an image")
    rejected = client.post(
        f"/v1/projects/{project['id']}/assets/import", headers={**headers, "X-Request-Id": "invalid"},
        json={"file_capability_id": caps.issue(invalid, "import", project["id"]), "asset_type": "source_image", "request_id": "invalid"},
    )
    assert rejected.status_code == 400
    listed = client.get(f"/v1/projects/{project['id']}/assets", headers=headers).json()
    assert {item["id"] for item in listed if item["asset_type"] == "source_image"} == {asset["id"], branch.json()["id"]}
    assert str(tmp_path) not in str(listed)
    injected_path = client.post(
        f"/v1/projects/{project['id']}/assets/import", headers={**headers, "X-Request-Id": "path-injection"},
        json={"file_capability_id": caps.issue(image, "import", project["id"]), "asset_type": "source_image", "request_id": "path-injection", "path": str(image)},
    )
    assert injected_path.status_code == 400
    expired = caps.issue(image, "import", project["id"])
    caps._items[expired].expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert client.post(
        f"/v1/projects/{project['id']}/assets/import", headers={**headers, "X-Request-Id": "expired"},
        json={"file_capability_id": expired, "asset_type": "source_image", "request_id": "expired"},
    ).status_code == 403


def test_desktop_package_export_uses_project_bound_capability(tmp_path: Path) -> None:
    caps, token = HostCapabilityStore(), "p" * 64
    client = TestClient(create_app(token, caps, tmp_path / "app.sqlite3"))
    headers = {"Origin": "http://tauri.localhost", "Authorization": f"Bearer {token}"}
    root = tmp_path / "project"
    created = client.post(
        "/v1/projects", headers={**headers, "X-Request-Id": "project-create"},
        json={"create_capability_id": caps.issue(root, "create"), "name": "Demo"},
    )
    assert created.status_code == 200
    project_id = created.json()["id"]
    export_directory = tmp_path / "exports"
    export_directory.mkdir()
    package = export_directory / "Demo-backup.pic2model"
    package.write_bytes(b"previous backup")
    exported = client.post(
        f"/v1/projects/{project_id}/export", headers={**headers, "X-Request-Id": "project-export"},
        json={"export_capability_id": caps.issue(export_directory, "export", project_id), "format": "project_v1", "request_id": "project-export"},
    )
    assert exported.status_code == 200
    assert exported.json()["path"] == package.name
    assert package.read_bytes().startswith(b"PK")
