import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from aipic_to_model.agent.providers.fake import FakeProvider
from aipic_to_model.agent.session.sqlite import LinearSessionRepository
from aipic_to_model.api.app import create_app
from aipic_to_model.application.host_capabilities import HostCapabilityStore


def test_agent_sidecar_restart_marks_unfinished_operation_interrupted(tmp_path: Path):
    token, caps = "r" * 64, HostCapabilityStore()
    headers = {"Origin": "http://tauri.localhost", "Authorization": f"Bearer {token}"}
    root, app_db = tmp_path / "project", tmp_path / "app.sqlite3"
    client = TestClient(
        create_app(token, caps, app_db, agent_provider_factory=lambda _profile: FakeProvider(()))
    )
    project = client.post(
        "/v1/projects",
        json={"name": "Restart", "create_capability_id": caps.issue(root, "create")},
        headers={**headers, "X-Request-Id": "project-create"},
    ).json()
    conversation = client.post(
        "/v1/agent/conversations",
        json={"project_id": project["id"]},
        headers={**headers, "X-Request-Id": "conversation-create"},
    ).json()["id"]
    repository = LinearSessionRepository(root / "agent.sqlite3")
    repository.start_operation(conversation)

    restarted_caps = HostCapabilityStore()
    restarted = TestClient(
        create_app(
            token, restarted_caps, app_db, agent_provider_factory=lambda _profile: FakeProvider(())
        )
    )
    restarted.post(
        "/v1/projects/open",
        json={"open_capability_id": restarted_caps.issue(root, "open")},
        headers={**headers, "X-Request-Id": "project-open"},
    )
    response = restarted.get(
        f"/v1/agent/conversations/{conversation}?project_id={project['id']}", headers=headers
    )
    assert response.status_code == 200
    with sqlite3.connect(root / "agent.sqlite3") as connection:
        state = connection.execute(
            "SELECT state FROM agent_operations ORDER BY started_at DESC LIMIT 1"
        ).fetchone()[0]
    assert state == "interrupted"
