import hashlib
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from aipic_to_model.api.app import create_app
from aipic_to_model.application.host_capabilities import HostCapabilityStore


def test_b01_11_asset_content_supports_safe_range(tmp_path: Path):
    capabilities, token = HostCapabilityStore(), "a" * 64
    client = TestClient(create_app(token, capabilities, tmp_path / "app.sqlite3"))
    headers = {
        "Origin": "http://tauri.localhost",
        "Authorization": "Bearer " + token,
        "X-Request-Id": "api-test",
    }
    project = client.post(
        "/v1/projects",
        json={"name": "Demo", "create_capability_id": capabilities.issue(tmp_path / "p", "create")},
        headers=headers,
    ).json()
    image = tmp_path / "source.png"
    Image.new("RGB", (4, 4)).save(image)
    asset = client.post(
        f"/v1/projects/{project['id']}/assets/import",
        json={
            "file_capability_id": capabilities.issue(image, "import", project["id"]),
            "asset_type": "source_image",
            "request_id": "import",
        },
        headers={**headers, "X-Request-Id": "import"},
    ).json()
    ranged = client.get(
        f"/v1/assets/{asset['id']}/content?project_id={project['id']}",
        headers={**headers, "Range": "bytes=0-3"},
    )
    assert ranged.status_code == 206 and len(ranged.content) == 4
    assert (
        client.get(
            f"/v1/assets/{asset['id']}/content?project_id={project['id']}",
            headers={**headers, "Range": "bytes=999-1000"},
        ).status_code
        == 416
    )
    full = client.get(
        f"/v1/assets/{asset['id']}/content?project_id={project['id']}", headers=headers
    )
    assert full.status_code == 200
    assert hashlib.sha256(full.content).hexdigest() == asset["sha256"]
    second_image = tmp_path / "second.png"
    Image.new("RGB", (4, 4), "red").save(second_image)
    sibling = client.post(
        f"/v1/projects/{project['id']}/assets/import",
        json={"file_capability_id": capabilities.issue(second_image, "import", project["id"]), "asset_type": "source_image", "request_id": "sibling"},
        headers={**headers, "X-Request-Id": "sibling"},
    ).json()
    compared = client.post(
        "/v1/assets/compare",
        json={"project_id": project["id"], "left_id": asset["id"], "right_id": sibling["id"], "request_id": "compare"},
        headers={**headers, "X-Request-Id": "compare"},
    )
    assert compared.status_code == 400
    glb = tmp_path / "model.glb"
    glb.write_bytes(b"glTF" + (2).to_bytes(4, "little") + (12).to_bytes(4, "little"))
    imported_glb = client.post(
        f"/v1/projects/{project['id']}/assets/import",
        json={"file_capability_id": capabilities.issue(glb, "import", project["id"]), "asset_type": "glb", "request_id": "glb"},
        headers={**headers, "X-Request-Id": "glb"},
    )
    assert imported_glb.status_code == 200 and imported_glb.json()["asset_type"] == "glb"
    other = client.post(
        "/v1/projects",
        json={
            "name": "Other",
            "create_capability_id": capabilities.issue(tmp_path / "other", "create"),
        },
        headers={**headers, "X-Request-Id": "other"},
    ).json()
    denied = client.get(
        f"/v1/assets/{asset['id']}/content?project_id={other['id']}", headers=headers
    )
    assert denied.status_code == 404
    assert str(tmp_path).encode() not in denied.content and token.encode() not in denied.content
