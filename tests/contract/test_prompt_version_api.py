from pathlib import Path

from fastapi.testclient import TestClient

from aipic_to_model.api.app import create_app
from aipic_to_model.application.host_capabilities import HostCapabilityStore


def test_prompt_parameter_drawer_creates_immutable_managed_versions(tmp_path: Path) -> None:
    capabilities, token = HostCapabilityStore(), "p" * 64
    client = TestClient(create_app(token, capabilities, tmp_path / "app.sqlite3"))
    headers = {
        "Origin": "http://tauri.localhost",
        "Authorization": f"Bearer {token}",
        "X-Request-Id": "create",
    }
    project = client.post(
        "/v1/projects",
        headers=headers,
        json={
            "name": "Prompt versions",
            "create_capability_id": capabilities.issue(tmp_path / "project", "create"),
        },
    ).json()

    first = client.post(
        f"/v1/projects/{project['id']}/prompts",
        headers={**headers, "X-Request-Id": "prompt-1"},
        json={
            "zh_prompt": "银色骑士，正面全身",
            "en_prompt": "silver knight, full body front view",
            "kind": "merged",
            "parent_asset_id": None,
            "request_id": "prompt-1",
        },
    )
    assert first.status_code == 200
    first_asset = first.json()["asset"]
    assert first_asset["asset_type"] == "prompt"

    second = client.post(
        f"/v1/projects/{project['id']}/prompts",
        headers={**headers, "X-Request-Id": "prompt-2"},
        json={
            "zh_prompt": "银色骑士，正面全身，电影级灯光",
            "en_prompt": "silver knight, full body front view, cinematic lighting",
            "kind": "merged",
            "parent_asset_id": first_asset["id"],
            "request_id": "prompt-2",
        },
    )
    assert second.status_code == 200
    second_asset = second.json()["asset"]
    assert second_asset["id"] != first_asset["id"]
    assert second_asset["parent_asset_id"] == first_asset["id"]

    first_content = client.get(
        f"/v1/assets/{first_asset['id']}/content?project_id={project['id']}",
        headers=headers,
    ).text
    second_content = client.get(
        f"/v1/assets/{second_asset['id']}/content?project_id={project['id']}",
        headers=headers,
    ).text
    assert "电影级灯光" not in first_content
    assert "电影级灯光" in second_content
