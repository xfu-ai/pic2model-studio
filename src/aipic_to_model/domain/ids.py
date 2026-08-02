from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any


def new_id() -> str:
    return str(uuid.uuid4())


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    def normalise(item: Any) -> Any:
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("non-finite number")
            return int(item) if item.is_integer() else item
        if isinstance(item, dict):
            return {key: normalise(item[key]) for key in sorted(item)}
        if isinstance(item, list):
            return [normalise(element) for element in item]
        return item

    return json.dumps(normalise(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def idempotency_key(
    name: str,
    version: str,
    arguments: Mapping[str, Any],
    asset_hashes: list[str],
    provider_profile: str | None,
) -> str:
    material = "\n".join(
        [
            name,
            version,
            canonical_json(arguments),
            "\n".join(sorted(asset_hashes)),
            provider_profile or "",
        ]
    )
    return hashlib.sha256(material.encode()).hexdigest()
