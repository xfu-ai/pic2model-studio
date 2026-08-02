import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from aipic_to_model.api.app import create_app
from aipic_to_model.domain.common import canonical_json


def test_b01_11_openapi_contract_matches_frozen_snapshot():
    token = "f" * 64
    app = create_app(token)
    expected = (
        (Path(__file__).parents[1] / "fixtures" / "schemas" / "openapi-v1.sha256")
        .read_text("utf-8")
        .strip()
    )
    actual = hashlib.sha256(canonical_json(app.openapi()).encode()).hexdigest()
    assert actual == expected


def test_b01_07_openapi_selection_save_cannot_enter_confirmed_state():
    token = "f" * 64
    app = create_app(token)
    schema = app.openapi()
    status = schema["components"]["schemas"]["SelectionSaveRequest"]["properties"]["status"]
    assert status["enum"] == ["draft", "edited"]
    response = TestClient(app).get(
        "/v1/openapi.json",
        headers={"Origin": "http://tauri.localhost", "Authorization": "Bearer " + token},
    )
    assert response.status_code == 200
    assert "/v1/projects/{project_id}/assets/import" in response.json()["paths"]
