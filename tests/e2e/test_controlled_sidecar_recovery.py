from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aipic_to_model.api.app import create_app


def test_controlled_sidecar_can_be_offline_once_then_recover(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AIPIC_CONTROLLED_E2E", "1")
    monkeypatch.setenv("AIPIC_CONTROLLED_E2E_HEALTH_FAILURES", "1")
    token = "t" * 64
    client = TestClient(create_app(token, app_db=tmp_path / "app.sqlite3"))
    headers = {"Authorization": f"Bearer {token}", "Origin": "http://tauri.localhost"}

    offline = client.get("/v1/health", headers=headers)
    recovered = client.get("/v1/health", headers=headers)

    assert offline.status_code == 503
    assert offline.json()["code"] == "CONTROLLED_SIDECAR_OFFLINE"
    assert recovered.status_code == 200
    assert recovered.json()["sidecar"] == "available"
