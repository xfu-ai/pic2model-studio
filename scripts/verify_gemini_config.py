"""Verify the repository-local Gemini configuration without revealing credentials."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aipic_to_model.infrastructure.keyring_store import OSKeyringStore

DEFAULT_CONFIG = ROOT / ".local" / "gemini.local.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    return parser.parse_args()


def load_config(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "base_url",
        "credential_ref",
        "default_text_model",
        "image_generation_model",
    }
    missing = required - value.keys()
    if missing:
        raise ValueError(f"Gemini config is missing fields: {sorted(missing)}")
    if value.get("provider") != "google" or value.get("protocol") != "google_generative_ai":
        raise ValueError("Gemini config must use the official Google provider.")
    if value.get("xais_fallback", {}).get("enabled") is not False:
        raise ValueError("Xais fallback must be disabled by default.")
    return value


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    api_key = OSKeyringStore().get(str(config["credential_ref"]))
    if not api_key:
        print("Gemini credential is not configured.", file=sys.stderr)
        return 2

    headers = {"x-goog-api-key": api_key}
    base_url = str(config["base_url"]).rstrip("/")
    text_model = str(config["default_text_model"])
    image_model = str(config["image_generation_model"])
    image_backend = str(config.get("image_backend") or "native")
    if image_backend not in {"native", "text_render"}:
        raise ValueError("Gemini image_backend must be native or text_render.")
    timeout = float(config.get("timeout_seconds", 60))
    with httpx.Client(headers=headers, timeout=timeout) as client:
        text_response = client.post(
            f"{base_url}/models/{text_model}:generateContent",
            json={
                "contents": [{"role": "user", "parts": [{"text": "Reply with exactly: OK"}]}],
                "generationConfig": {"temperature": 0, "maxOutputTokens": 16},
            },
        )
        text_response.raise_for_status()
        text_data = text_response.json()
        text = "".join(
            part.get("text", "")
            for candidate in text_data.get("candidates", [])
            for part in candidate.get("content", {}).get("parts", [])
            if isinstance(part, dict)
        ).strip()
        if text != "OK":
            raise RuntimeError("Gemini text probe returned an unexpected response.")

        image_response = client.post(
            f"{base_url}/models/{image_model}:countTokens",
            json={"contents": [{"role": "user", "parts": [{"text": "Generate a banana."}]}]},
        )
        image_response.raise_for_status()

    print("config=ok")
    print(f"text_model={text_model}")
    print(f"text_model_version={text_data.get('modelVersion', '')}")
    print(f"image_model={image_model}")
    print(f"image_backend={image_backend}")
    print("xais_fallback=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
