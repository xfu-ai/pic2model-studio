from __future__ import annotations

import hashlib
import json

from aipic_to_model.infrastructure.realesrgan import bundled_model_path


def test_bundled_model_and_wasm_resources_match_frozen_manifest() -> None:
    root = bundled_model_path().parents[1]
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["schema_version"] == 1
    assert {item["license"] for item in manifest["resources"]} == {
        "BSD-3-Clause-Clear",
        "MIT",
    }
    assert any(item["path"].endswith(".onnx") for item in manifest["resources"])
    assert sum(item["path"].endswith(".wasm") for item in manifest["resources"]) == 2

    for item in manifest["resources"]:
        path = root / item["path"]
        assert path.is_file()
        assert path.stat().st_size == item["bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
