from pathlib import Path

from fastapi.testclient import TestClient

from aipic_to_model.api.app import create_app
from aipic_to_model.application.host_capabilities import HostCapabilityStore
from aipic_to_model.infrastructure.sqlite.repositories import EventRepository


def test_b01_11_sse_reconnect_replays_the_same_durable_event_sequence(tmp_path: Path):
    token, capabilities = "s" * 64, HostCapabilityStore()
    headers = {
        "Origin": "http://tauri.localhost",
        "Authorization": "Bearer " + token,
        "X-Request-Id": "sse-create",
    }
    client = TestClient(create_app(token, capabilities, tmp_path / "app.sqlite3"))
    project = client.post(
        "/v1/projects",
        json={
            "name": "SSE",
            "create_capability_id": capabilities.issue(tmp_path / "project", "create"),
        },
        headers=headers,
    ).json()
    repository = EventRepository()
    for index in range(105):
        repository.append_named_committed(
            tmp_path / "project" / "project.sqlite3",
            project["id"],
            "project.metadata.changed",
            {"changed_fields": [str(index)], "request_id": f"event-{index}"},
        )
    stream_headers = {key: value for key, value in headers.items() if key != "X-Request-Id"}
    first = client.get(
        f"/v1/events?project_id={project['id']}&limit=100",
        headers={**stream_headers, "Accept": "text/event-stream"},
    )
    ids = [line.removeprefix("id: ") for line in first.text.splitlines() if line.startswith("id: ")]
    assert first.status_code == 200 and len(ids) == 100
    second = client.get(
        f"/v1/events?project_id={project['id']}&limit=100",
        headers={**stream_headers, "Accept": "text/event-stream", "Last-Event-ID": ids[-1]},
    )
    assert second.status_code == 200
    assert second.text.count("event: project.metadata.changed") == 5
