from pathlib import Path

from fastapi.testclient import TestClient

from aipic_to_model.api.app import create_app
from aipic_to_model.api.security import OriginGuard
from aipic_to_model.application.host_capabilities import HostCapabilityStore


def test_cors_preflight_allows_only_configured_renderer_origin(tmp_path: Path):
    token = "c" * 64
    renderer_origin = "http://127.0.0.1:14200"
    client = TestClient(
        create_app(
            token,
            app_db=tmp_path / "app.sqlite3",
            renderer_origin=renderer_origin,
        )
    )

    allowed = client.options(
        "/v1/health",
        headers={
            "Origin": renderer_origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        },
    )
    rejected = client.options(
        "/v1/health",
        headers={
            "Origin": "http://untrusted.invalid",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        },
    )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == renderer_origin
    assert "access-control-allow-origin" not in rejected.headers


def test_b01_11_health_requires_exact_token_and_origin():
    client = TestClient(create_app("a" * 64))
    assert client.get("/v1/health").status_code == 403
    assert client.get("/v1/health", headers={"Origin": "http://tauri.localhost"}).status_code == 401
    for origin in ("null", "http://tauri.localhost.evil", "http://tauri.localhost:5173"):
        assert (
            client.get(
                "/v1/health",
                headers={"Origin": origin, "Authorization": "Bearer " + "a" * 64},
            ).status_code
            == 403
        )
    assert (
        client.get(
            "/v1/health",
            headers={"Origin": "http://tauri.localhost", "Authorization": "Bearer " + "b" * 64},
        ).status_code
        == 401
    )
    assert (
        client.get(
            "/v1/health",
            headers={"Origin": "http://evil.localhost", "Authorization": "Bearer " + "a" * 64},
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/v1/health",
            headers={"Origin": "http://tauri.localhost", "Authorization": "Bearer " + "a" * 64},
        ).status_code
        == 200
    )


def test_b01_11_origin_guard_retains_only_a_token_digest():
    token = "nonpersistent-token"
    guard = OriginGuard(token)
    assert "token" not in guard.__dict__
    assert token.encode("utf-8") not in repr(guard.__dict__).encode("utf-8")


def test_b01_11_health_reports_runtime_values_not_placeholders(tmp_path: Path):
    token = "d" * 64
    client = TestClient(create_app(token, app_db=tmp_path / "app.sqlite3"))
    result = client.get(
        "/v1/health",
        headers={"Origin": "http://tauri.localhost", "Authorization": "Bearer " + token},
    )
    assert result.status_code == 200
    assert result.json()["disk_free_bytes"] > 0
    assert result.json()["project_db"] == "not_opened"


def test_b01_11_api_uses_capability_not_raw_project_path(tmp_path: Path):
    capabilities = HostCapabilityStore()
    token = "a" * 64
    client = TestClient(create_app(token, capabilities, tmp_path / "app.sqlite3"))
    headers = {
        "Origin": "http://tauri.localhost",
        "Authorization": "Bearer " + token,
        "X-Request-Id": "api-test",
    }
    created = client.post(
        "/v1/projects",
        json={
            "name": "Demo",
            "create_capability_id": capabilities.issue(tmp_path / "project", "create"),
        },
        headers=headers,
    )
    assert created.status_code == 200
    project_id = created.json()["id"]
    assert (
        client.get(f"/v1/events?project_id={project_id}&root=C:\\leak", headers=headers).status_code
        == 400
    )


def test_b01_11_project_create_replays_before_consuming_a_capability(tmp_path: Path):
    token = "f" * 64
    capabilities = HostCapabilityStore()
    client = TestClient(create_app(token, capabilities, tmp_path / "app.sqlite3"))
    headers = {
        "Origin": "http://tauri.localhost",
        "Authorization": "Bearer " + token,
        "X-Request-Id": "create-replay",
    }
    capability = capabilities.issue(tmp_path / "project", "create")
    body = {"name": "Demo", "create_capability_id": capability}
    first = client.post("/v1/projects", json=body, headers=headers)
    second = client.post("/v1/projects", json=body, headers=headers)
    assert first.status_code == second.status_code == 200
    assert second.json() == first.json()
    conflict = client.post(
        "/v1/projects",
        json={"name": "Other", "create_capability_id": capability},
        headers=headers,
    )
    assert conflict.status_code == 409 and conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"


def test_b01_11_export_replay_does_not_require_an_unconsumed_capability(tmp_path: Path):
    token = "h" * 64
    capabilities = HostCapabilityStore()
    client = TestClient(create_app(token, capabilities, tmp_path / "app.sqlite3"))
    headers = {
        "Origin": "http://tauri.localhost",
        "Authorization": "Bearer " + token,
        "X-Request-Id": "create",
    }
    project = client.post(
        "/v1/projects",
        json={
            "name": "Demo",
            "create_capability_id": capabilities.issue(tmp_path / "project", "create"),
        },
        headers=headers,
    ).json()
    export_directory = tmp_path / "exports"
    export_directory.mkdir()
    export_id = capabilities.issue(export_directory, "export", project["id"])
    export_headers = {**headers, "X-Request-Id": "export"}
    body = {"export_capability_id": export_id, "format": "project_v1", "request_id": "export"}
    first = client.post(f"/v1/projects/{project['id']}/export", json=body, headers=export_headers)
    second = client.post(f"/v1/projects/{project['id']}/export", json=body, headers=export_headers)
    assert first.status_code == second.status_code == 200
    assert second.json() == first.json()
    assert (export_directory / "Demo-backup.formweaver").is_file()


def test_b01_11_openapi_and_validation_use_the_same_local_security_contract():
    token = "c" * 64
    client = TestClient(create_app(token))
    headers = {
        "Origin": "http://tauri.localhost",
        "Authorization": "Bearer " + token,
        "X-Request-Id": "api-test",
    }
    assert client.get("/v1/openapi.json").status_code == 403
    assert client.get("/docs").status_code == 404
    invalid = client.post("/v1/projects", json={}, headers=headers)
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "SCHEMA_VALIDATION_FAILED"


def test_b01_11_commands_require_x_request_id(tmp_path: Path):
    token = "e" * 64
    capabilities = HostCapabilityStore()
    client = TestClient(create_app(token, capabilities, tmp_path / "app.sqlite3"))
    response = client.post(
        "/v1/projects",
        json={
            "name": "Demo",
            "create_capability_id": capabilities.issue(tmp_path / "project", "create"),
        },
        headers={"Origin": "http://tauri.localhost", "Authorization": "Bearer " + token},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "SCHEMA_VALIDATION_FAILED"


def test_b01_11_diagnostics_export_writes_zip_inside_authorized_folder(tmp_path: Path):
    token = "i" * 64
    capabilities = HostCapabilityStore()
    client = TestClient(create_app(token, capabilities, tmp_path / "app.sqlite3"))
    headers = {
        "Origin": "http://tauri.localhost",
        "Authorization": "Bearer " + token,
        "X-Request-Id": "create",
    }
    project = client.post(
        "/v1/projects",
        json={
            "name": "Demo",
            "create_capability_id": capabilities.issue(tmp_path / "project", "create"),
        },
        headers=headers,
    ).json()
    preview_headers = {**headers, "X-Request-Id": "diagnostics-preview"}
    preview = client.post(
        "/v1/diagnostics/preview",
        json={"project_id": project["id"]},
        headers=preview_headers,
    )
    assert preview.status_code == 200
    destination = tmp_path / "support"
    destination.mkdir()
    export_headers = {**headers, "X-Request-Id": "diagnostics-export"}
    exported = client.post(
        "/v1/diagnostics/export",
        json={
            "project_id": project["id"],
            "export_capability_id": capabilities.issue(
                destination, "diagnostic_export", project["id"]
            ),
            "confirmed_manifest_hash": preview.json()["manifest_hash"],
            "request_id": "diagnostics-export",
        },
        headers=export_headers,
    )
    assert exported.status_code == 200
    assert exported.json()["path"] == "FormWeaver-Studio-diagnostics.zip"
    assert (destination / "FormWeaver-Studio-diagnostics.zip").is_file()


def test_b01_11_header_request_id_must_bind_body_request_id(tmp_path: Path):
    token = "g" * 64
    capabilities = HostCapabilityStore()
    client = TestClient(create_app(token, capabilities, tmp_path / "app.sqlite3"))
    headers = {
        "Origin": "http://tauri.localhost",
        "Authorization": "Bearer " + token,
        "X-Request-Id": "create",
    }
    project = client.post(
        "/v1/projects",
        json={
            "name": "Demo",
            "create_capability_id": capabilities.issue(tmp_path / "project", "create"),
        },
        headers=headers,
    ).json()
    image = tmp_path / "source.txt"
    image.write_text("prompt", encoding="utf-8")
    mismatch = client.post(
        f"/v1/projects/{project['id']}/assets/import",
        json={
            "file_capability_id": capabilities.issue(image, "import", project["id"]),
            "asset_type": "prompt",
            "request_id": "body-id",
        },
        headers=headers,
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["code"] == "IDEMPOTENCY_CONFLICT"
