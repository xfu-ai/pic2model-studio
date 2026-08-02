"""Materialize the isolated fixture root used by controlled Tauri E2E."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from tests.fixtures.controlled_e2e import create_controlled_e2e_fixture_set


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    fixtures = create_controlled_e2e_fixture_set(args.output)
    print(json.dumps({name: str(path) for name, path in fixtures.items()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
