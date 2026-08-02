"""Generate or validate the versioned Pi provider/model catalog.

The application only reads its checked-in JSON.  Pi is consulted explicitly by
this maintenance command and never at runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aipic_to_model.agent.providers.catalog import (
    CHAT_PROVIDER_IDS,
    FROZEN_PI_COMMIT,
    frozen_descriptors,
)
from aipic_to_model.agent.providers.model_catalog import (
    CATALOG_SCHEMA_VERSION,
    CATALOG_SHA256,
    load_frozen_catalog,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PI_SOURCE = Path("E:/UGit/pi/packages/ai/src/providers")
CHECKED_IN = ROOT / "src/aipic_to_model/agent/providers/data/frozen_pi_models.json"
MANIFEST = ROOT / "src/aipic_to_model/agent/providers/data/frozen_pi_models.manifest.json"


def provider_ids(source: Path) -> set[str]:
    return {
        file.stem
        for file in source.glob("*.ts")
        if not file.name.endswith(".models.ts") and file.name not in {"all.ts", "faux.ts"}
    }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_pi_source(source: Path) -> None:
    found, expected = provider_ids(source), set(CHAT_PROVIDER_IDS)
    missing, extra = expected - found, (found & expected) - expected
    if missing or extra:
        raise ValueError(
            f"Pi provider inventory mismatch; missing={sorted(missing)} extra={sorted(extra)}"
        )
    repository = source.parents[3]
    commit = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if commit != FROZEN_PI_COMMIT:
        raise ValueError(f"Pi source commit mismatch: expected {FROZEN_PI_COMMIT}, got {commit}")


def verify_checked_in_catalog() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise ValueError("Catalog manifest schema version mismatch.")
    if manifest.get("source_pi_commit") != FROZEN_PI_COMMIT:
        raise ValueError("Catalog manifest Pi commit mismatch.")
    digest = sha256(CHECKED_IN)
    if digest != CATALOG_SHA256 or digest != manifest.get("content_sha256"):
        raise ValueError("Catalog content hash mismatch.")
    catalog = load_frozen_catalog()
    if int(manifest.get("model_count", -1)) != len(catalog.models):
        raise ValueError("Catalog manifest model count mismatch.")
    descriptor_ids = {item.provider_id for item in frozen_descriptors()}
    if descriptor_ids != {*CHAT_PROVIDER_IDS, "openrouter-images"}:
        raise ValueError("Provider descriptor inventory mismatch.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pi-source", type=Path, default=DEFAULT_PI_SOURCE)
    parser.add_argument(
        "--catalog-file", type=Path, help="Explicit generated Pi models.json to import."
    )
    parser.add_argument("--output", type=Path, help="Destination for an explicit catalog import.")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.catalog_file:
        if not args.output:
            parser.error(
                "--catalog-file requires --output; this command never overwrites the checked-in catalog."
            )
        decoded = json.loads(args.catalog_file.read_text(encoding="utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("Imported catalog must be a JSON object.")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.catalog_file, args.output)
        print(f"wrote catalog candidate: {args.output}")
    if args.check:
        verify_pi_source(args.pi_source)
        verify_checked_in_catalog()
        print(
            f"catalog check passed: 38 chat providers + 1 image provider, frozen Pi {FROZEN_PI_COMMIT}"
        )
    elif not args.catalog_file:
        parser.error("choose --check or --catalog-file with --output")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
