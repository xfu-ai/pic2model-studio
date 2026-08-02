from pathlib import Path

from fastapi.testclient import TestClient

from aipic_to_model.api.app import create_app
from aipic_to_model.application.host_capabilities import HostCapabilityStore


def test_workspace_state_is_whitelisted_persisted_and_idempotent(tmp_path: Path) -> None:
    caps, token = HostCapabilityStore(), "w" * 64
    client = TestClient(create_app(token, caps, tmp_path / "app.sqlite3"))
    headers = {"Origin": "http://tauri.localhost", "Authorization": f"Bearer {token}", "X-Request-Id": "create"}
    project = client.post("/v1/projects", headers=headers, json={"name": "Demo", "create_capability_id": caps.issue(tmp_path / "project", "create")}).json()
    recents = client.get("/v1/projects/recent", headers=headers)
    assert recents.status_code == 200
    assert recents.json()["projects"][0] == {
        "id": project["id"], "name": "Demo", "availability": "available",
        "last_opened_at": recents.json()["projects"][0]["last_opened_at"],
    }
    payload = {"state": {"workspace_mode": "selection", "agent_panel_width": 420, "canvas": {"zoom": 1.2, "pan_x": 10, "pan_y": 12}}, "request_id": "workspace-1"}
    response = client.patch(f"/v1/projects/{project['id']}/workspace-state", headers={**headers, "X-Request-Id": "workspace-1"}, json=payload)
    assert response.status_code == 200 and response.json()["workspace_mode"] == "selection"
    assert client.patch(f"/v1/projects/{project['id']}/workspace-state", headers={**headers, "X-Request-Id": "workspace-1"}, json=payload).json() == response.json()
    restored = client.get(f"/v1/projects/{project['id']}", headers=headers)
    assert restored.status_code == 200
    assert restored.json()["workspace_state_json"] == '{"agent_panel_width":420,"canvas":{"pan_x":10,"pan_y":12,"zoom":1.2},"workspace_mode":"selection"}'
    rejected = client.patch(f"/v1/projects/{project['id']}/workspace-state", headers={**headers, "X-Request-Id": "workspace-2"}, json={"state": {"api_key": "nope"}, "request_id": "workspace-2"})
    assert rejected.status_code == 400
    prompt_handoff = {
        "state": {
            "workflow_contexts": {
                "prompt_image": {
                    "prompt": "new merged prompt",
                    "zh_prompt": "新的合并提示词",
                    "en_prompt": "new merged prompt",
                    "display_language": "zh",
                    "source_prompt_asset_id": "merged-prompt-2",
                    "candidate_count": 2,
                    "aspect_ratio": "1:1",
                    "selected_candidate_id": None,
                    "job_id": None,
                    "rewrite_job_id": "rewrite-job-1",
                }
            }
        },
        "request_id": "workspace-3",
    }
    handoff_response = client.patch(
        f"/v1/projects/{project['id']}/workspace-state",
        headers={**headers, "X-Request-Id": "workspace-3"},
        json=prompt_handoff,
    )
    assert handoff_response.status_code == 200
    assert (
        handoff_response.json()["workflow_contexts"]["prompt_image"]["source_prompt_asset_id"]
        == "merged-prompt-2"
    )
    assert (
        handoff_response.json()["workflow_contexts"]["prompt_image"]["en_prompt"]
        == "new merged prompt"
    )
    assert (
        handoff_response.json()["workflow_contexts"]["prompt_image"]["rewrite_job_id"]
        == "rewrite-job-1"
    )
    model_handoff = {
        "state": {
            "workspace_mode": "model3d",
            "workflow_contexts": {
                "model3d": {
                    "asset_id": "previous-model",
                    "target_triangles": 50_000,
                    "generation_job_id": "generation-job-1",
                }
            },
        },
        "request_id": "workspace-model-1",
    }
    model_response = client.patch(
        f"/v1/projects/{project['id']}/workspace-state",
        headers={**headers, "X-Request-Id": "workspace-model-1"},
        json=model_handoff,
    )
    assert model_response.status_code == 200
    assert model_response.json()["workflow_contexts"]["model3d"] == {
        "asset_id": "previous-model",
        "target_triangles": 50_000,
        "generation_job_id": "generation-job-1",
    }
    extraction_handoff = {
        "state": {
            "workspace_mode": "target_extract",
            "workflow_contexts": {
                "target_extract": {
                    "method": "direct",
                    "stage": "select_target",
                    "source_asset_id": "source-1",
                    "source_selection_id": None,
                    "source_selection_rect": {
                        "rect_id": "rect",
                        "label": "目标",
                        "x": 0,
                        "y": 0,
                        "width": 1,
                        "height": 1,
                    },
                    "preset": "scene",
                    "custom_prompt": "",
                    "prompt_asset_id": None,
                    "breakdown_asset_id": None,
                    "breakdown_selection_id": None,
                    "breakdown_selection_rect": None,
                    "result_asset_ids": [],
                    "active_result_asset_id": None,
                    "job_id": None,
                    "pending_action_id": None,
                    "agent_action_id": None,
                    "agent_run_id": None,
                    "agent_instruction": "",
                }
            },
        },
        "request_id": "workspace-4",
    }
    extraction_response = client.patch(
        f"/v1/projects/{project['id']}/workspace-state",
        headers={**headers, "X-Request-Id": "workspace-4"},
        json=extraction_handoff,
    )
    assert extraction_response.status_code == 200
    assert extraction_response.json()["workspace_mode"] == "target_extract"
