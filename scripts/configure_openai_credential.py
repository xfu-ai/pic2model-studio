"""Configure the OpenAI-compatible Provider credential in the OS keyring."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aipic_to_model.infrastructure.keyring_store import OSKeyringStore
from aipic_to_model.infrastructure.providers.config import OPENAI_PROFILE


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true")
    parser.add_argument(
        "--import-local",
        action="store_true",
        help="Copy a local development credential into the OS keyring.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    store = OSKeyringStore()
    if args.status:
        print(f"profile={OPENAI_PROFILE}")
        print(f"configured={str(bool(store.get(OPENAI_PROFILE))).lower()}")
        return 0
    if args.import_local:
        try:
            data = json.loads((ROOT / ".local" / "openaimodel.local.json").read_text("utf-8-sig"))
            secret = data.get("openai_api_key") if isinstance(data, dict) else None
        except OSError, ValueError:
            secret = None
        if not isinstance(secret, str) or not secret.strip():
            print("本地兼容配置中没有可迁移的凭据。", file=sys.stderr)
            return 2
    else:
        secret = getpass.getpass("OpenAI-compatible API Key（输入不会回显）: ").strip()
    if not secret:
        print("API Key 不能为空。", file=sys.stderr)
        return 2
    store.set(OPENAI_PROFILE, secret)
    print(f"已安全保存凭据：{OPENAI_PROFILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
