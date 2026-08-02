"""Configure the Google Gemini API key without exposing it in shell history."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aipic_to_model.infrastructure.keyring_store import OSKeyringStore

PROFILE = "gemini/google/default"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--status",
        action="store_true",
        help="Report whether the credential is configured without revealing it.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    store = OSKeyringStore()
    if args.status:
        print(f"profile={PROFILE}")
        print(f"configured={str(bool(store.get(PROFILE))).lower()}")
        return 0

    secret = getpass.getpass("Google Gemini API Key（输入不会回显）: ").strip()
    if not secret:
        print("API Key 不能为空。", file=sys.stderr)
        return 2
    store.set(PROFILE, secret)
    print(f"已安全保存凭据：{PROFILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
