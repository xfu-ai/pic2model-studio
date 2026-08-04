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


def test_local_provider_status_is_redacted_and_refresh_never_generates(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AIPIC_CONTROLLED_E2E", "1")
    monkeypatch.setenv("AIPIC_ZIMAGE_SD_CLI", "C:/private/sd.exe")
    token = "l" * 64
    headers = {
        "Origin": "http://tauri.localhost",
        "Authorization": f"Bearer {token}",
        "X-Request-Id": "local-provider-refresh",
    }

    with TestClient(create_app(token, app_db=tmp_path / "app.sqlite3")) as client:
        snapshot = client.get("/v1/settings/local-providers", headers=headers)
        refreshed = client.post(
            "/v1/settings/local-providers/refresh",
            headers=headers,
        )

    assert snapshot.status_code == 200 and refreshed.status_code == 200
    payload = refreshed.json()
    assert payload["probes_download_models"] is False
    assert payload["probes_create_generation_jobs"] is False
    providers = {item["profile"]: item for item in payload["providers"]}
    assert set(providers) == {
        "agent/ollama/qwen3-vl",
        "image/local/z-image-turbo",
        "model3d/local/triposr",
    }
    assert providers["agent/ollama/qwen3-vl"]["capabilities"] == [
        "agent_chat",
        "image_analysis",
        "tool_calling",
    ]
    assert providers["model3d/local/triposr"]["license"]["identifier"] == "MIT"
    serialized = refreshed.text
    assert "C:/private" not in serialized
    assert "runtime_capability_id" not in serialized
