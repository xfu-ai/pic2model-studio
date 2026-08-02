from pathlib import Path

from fastapi.testclient import TestClient

from aipic_to_model.api.app import create_app
from aipic_to_model.application.host_capabilities import HostCapabilityStore


def test_renderer_cannot_issue_native_file_capability(tmp_path: Path) -> None:
    session, control = "s" * 64, "h" * 64
    client = TestClient(create_app(session, app_db=tmp_path / "app.sqlite3", host_control_token=control))
    headers = {"Origin": "http://tauri.localhost", "Authorization": f"Bearer {session}", "X-Request-Id": "host-1"}
    payload = {"path": str(tmp_path), "operation": "create", "request_id": "host-1"}
    assert client.post("/v1/host/capabilities", headers=headers, json=payload).status_code == 403
    response = client.post("/v1/host/capabilities", headers={**headers, "X-Host-Control-Token": control}, json=payload)
    assert response.status_code == 200
    assert response.json()["capability_id"]


def test_renderer_cannot_reissue_a_recent_project_capability(tmp_path: Path) -> None:
    session, control, caps = "s" * 64, "h" * 64, HostCapabilityStore()
    client = TestClient(create_app(session, caps, tmp_path / "app.sqlite3", host_control_token=control))
    headers = {"Origin": "http://tauri.localhost", "Authorization": f"Bearer {session}", "X-Request-Id": "create"}
    project = client.post("/v1/projects", headers=headers, json={"name": "Demo", "create_capability_id": caps.issue(tmp_path / "project", "create")}).json()
    request = {"recent_project_id": project["id"], "request_id": "recent-1"}
    assert client.post("/v1/host/recent-capabilities", headers={**headers, "X-Request-Id": "recent-1"}, json=request).status_code == 403
    response = client.post("/v1/host/recent-capabilities", headers={**headers, "X-Request-Id": "recent-1", "X-Host-Control-Token": control}, json=request)
    assert response.status_code == 200 and response.json()["capability_id"]
