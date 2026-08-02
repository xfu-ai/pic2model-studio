from __future__ import annotations

import importlib.util
from pathlib import Path


def _module():
    path = Path(__file__).parents[2] / "scripts" / "webview2_cdp.py"
    spec = importlib.util.spec_from_file_location("webview2_cdp", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_webview_evidence_redacts_secrets_and_absolute_paths() -> None:
    module = _module()
    record = module.redact(
        {
            "authorization": "Bearer deterministic-secret",
            "api_key": "api_key=another-secret",
            "session_token": "fourth-secret",
            "path": r"C:\\Users\\tester\\project\\asset.png",
            "forward_path": "C:/Users/tester/project/asset.png",
            "nested": ["token=third-secret"],
        }
    )
    rendered = str(record)
    assert "deterministic-secret" not in rendered
    assert "another-secret" not in rendered
    assert "third-secret" not in rendered
    assert "fourth-secret" not in rendered
    assert r"C:\\Users\\tester" not in rendered
    assert "C:/Users/tester" not in rendered
    assert "<redacted>" in rendered
    assert "<local-path>" in rendered


def test_webview_evidence_keeps_loopback_urls_intact() -> None:
    module = _module()
    url = "http://127.0.0.1:14200/v1/assets/import"

    assert module.redact(url) == url
