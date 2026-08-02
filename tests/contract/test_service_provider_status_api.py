from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aipic_to_model.api.app import create_app


def test_service_provider_status_and_independent_probe_are_guarded_and_complete(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AIPIC_CONTROLLED_E2E", "1")
    token = "s" * 64
    headers = {
        "Origin": "http://tauri.localhost",
        "Authorization": f"Bearer {token}",
        "X-Request-Id": "provider-probe",
    }

    with TestClient(create_app(token, app_db=tmp_path / "app.sqlite3")) as client:
        snapshot = client.get("/v1/settings/service-providers", headers=headers)
        probed = client.post(
            "/v1/settings/service-providers/probe",
            headers=headers,
            json={
                "provider_profile": "gemini/google/default",
                "request_id": "provider-probe",
            },
        )

    assert snapshot.status_code == 200
    assert probed.status_code == 200
    statuses = {item["profile"]: item for item in probed.json()["providers"]}
    assert set(statuses) == {
        "tripo3d/default",
        "meshy/default",
        "gemini/google/default",
        "agent/deepseek/default",
    }
    assert statuses["gemini/google/default"]["available"] is True
    assert statuses["gemini/google/default"]["last_checked_at"] is not None
    assert probed.json()["probes_consume_generation_credits"] is False
