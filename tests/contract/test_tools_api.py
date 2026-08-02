from fastapi.testclient import TestClient

from aipic_to_model.api.app import create_app
from aipic_to_model.application.host_capabilities import HostCapabilityStore


def test_b01_11_tool_invoke_uses_registered_manifest(tmp_path):
    capabilities, token = HostCapabilityStore(), "b" * 64
    headers = {
        "Origin": "http://tauri.localhost",
        "Authorization": "Bearer " + token,
        "X-Request-Id": "api-test",
    }
    client = TestClient(create_app(token, capabilities, tmp_path / "app.sqlite3"))
    project = client.post(
        "/v1/projects",
        headers=headers,
        json={
            "name": "Demo",
            "create_capability_id": capabilities.issue(tmp_path / "project", "create"),
        },
    ).json()
    response = client.post(
        "/v1/tools/invoke",
        headers={**headers, "X-Request-Id": "tool-request"},
        json={
            "project_id": project["id"],
            "tool_name": "project.get_state",
            "tool_version": "1.0.0",
            "arguments": {"project_id": project["id"]},
            "request_id": "tool-request",
            "round_index": 0,
        },
    )
    assert response.status_code == 200 and response.json()["status"] == "succeeded"
