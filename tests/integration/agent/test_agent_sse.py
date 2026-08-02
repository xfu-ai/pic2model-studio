from pathlib import Path

from fastapi.testclient import TestClient

from aipic_to_model.agent.core.models import (
    AssistantMessage,
    ProviderEvent,
    ProviderEventType,
    TextContent,
)
from aipic_to_model.api.app import create_app
from aipic_to_model.application.host_capabilities import HostCapabilityStore


def test_agent_abort_cancels_an_active_provider_stream(tmp_path: Path):
    class WaitingProvider:
        async def stream(self, request, cancellation):
            del request
            yield ProviderEvent(ProviderEventType.MESSAGE_START)
            await cancellation.wait()
            yield ProviderEvent(
                ProviderEventType.MESSAGE_END,
                message=AssistantMessage((TextContent("unreachable"),), stop_reason="aborted"),
            )

    token, caps = "t" * 64, HostCapabilityStore()
    headers = {"Origin": "http://tauri.localhost", "Authorization": f"Bearer {token}"}
    client = TestClient(
        create_app(
            token,
            caps,
            tmp_path / "app.sqlite3",
            agent_provider_factory=lambda _profile: WaitingProvider(),
        )
    )
    project = client.post(
        "/v1/projects",
        json={"name": "Abort", "create_capability_id": caps.issue(tmp_path / "project", "create")},
        headers={**headers, "X-Request-Id": "create-project"},
    ).json()
    conversation = client.post(
        "/v1/agent/conversations",
        json={"project_id": project["id"]},
        headers={**headers, "X-Request-Id": "create-conversation"},
    ).json()["id"]
    queued = client.post(
        f"/v1/agent/conversations/{conversation}/messages",
        json={"project_id": project["id"], "content": "wait", "request_id": "send"},
        headers={**headers, "X-Request-Id": "send"},
    )
    assert queued.status_code == 200 and queued.json()["state"] == "running"
    aborted = client.post(
        f"/v1/agent/conversations/{conversation}/abort",
        json={"project_id": project["id"], "request_id": "abort"},
        headers={**headers, "X-Request-Id": "abort"},
    )
    assert aborted.status_code == 200 and aborted.json()["state"] == "idle"
    events = client.get(
        f"/v1/agent/conversations/{conversation}/events?project_id={project['id']}", headers=headers
    ).json()
    assert any(item["event_type"] == "conversation.abort_requested" for item in events["items"])
    assert any(item["event_type"] == "conversation.cancelled" for item in events["items"])
