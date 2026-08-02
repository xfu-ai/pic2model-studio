"""B04-01 black-box proof for the Tauri-owned Python sidecar boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import httpx


def _start_sidecar(tmp_path: Path) -> tuple[subprocess.Popen[str], str, int]:
    token = "t" * 64
    host_control_token = "h" * 64
    tmp_path.mkdir(parents=True, exist_ok=True)
    env = os.environ | {
        "AIPIC_TO_MODEL_SESSION_TOKEN": token,
        "AIPIC_TO_MODEL_HOST_CONTROL_TOKEN": host_control_token,
        "PYTHONPATH": str(Path(__file__).parents[2] / "src"),
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "aipic_to_model.desktop_sidecar", "--app-db", str(tmp_path / "app.sqlite3")],
        cwd=Path(__file__).parents[2],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    ready = json.loads(process.stdout.readline())
    assert ready == {"event": "ready", "port": ready["port"]}
    assert isinstance(ready["port"], int) and ready["port"] > 0
    return process, token, ready["port"]


def _stop(process: subprocess.Popen[str]) -> None:
    process.terminate()
    process.wait(timeout=10)


def test_desktop_sidecar_uses_unique_loopback_ports_and_authenticated_api(tmp_path: Path) -> None:
    first, token, first_port = _start_sidecar(tmp_path / "first")
    second, _, second_port = _start_sidecar(tmp_path / "second")
    try:
        assert first_port != second_port
        response = httpx.get(
            f"http://127.0.0.1:{first_port}/v1/health",
            headers={"Authorization": f"Bearer {token}", "Origin": "http://tauri.localhost"},
            timeout=5,
        )
        assert response.status_code == 200
        assert isinstance(response.json(), dict)
        rejected = httpx.get(
            f"http://127.0.0.1:{first_port}/v1/health",
            headers={"Authorization": f"Bearer {token}", "Origin": "https://example.invalid"},
            timeout=5,
        )
        assert rejected.status_code == 403
    finally:
        _stop(first)
        _stop(second)


def test_desktop_sidecar_does_not_echo_its_session_token(tmp_path: Path) -> None:
    process, token, _ = _start_sidecar(tmp_path)
    try:
        assert token not in " ".join(process.args)
    finally:
        _stop(process)
