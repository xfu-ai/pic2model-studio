from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..domain.common import DomainErrorV1, ErrorCode, new_id


@dataclass
class HostFileCapabilityV1:
    id: str
    operation: str
    path: Path
    project_id: str | None
    expires_at: datetime
    consumed_at: datetime | None = None


class HostCapabilityStore:
    def __init__(self) -> None:
        self._items: dict[str, HostFileCapabilityV1] = {}

    def issue(self, path: Path, operation: str, project_id: str | None = None) -> str:
        item = HostFileCapabilityV1(
            new_id(),
            operation,
            path.resolve(),
            project_id,
            datetime.now(UTC) + timedelta(seconds=60),
        )
        self._items[item.id] = item
        return item.id

    def resolve_once(
        self, capability_id: str, operation: str, project_id: str | None = None
    ) -> Path:
        item = self._items.get(capability_id)
        if (
            not item
            or item.operation != operation
            or item.project_id != project_id
            or item.consumed_at
            or item.expires_at <= datetime.now(UTC)
        ):
            raise DomainErrorV1(ErrorCode.SECURITY_CAPABILITY_INVALID, "文件授权已失效。")
        item.consumed_at = datetime.now(UTC)
        return item.path

    def peek(self, capability_id: str, operation: str, project_id: str | None = None) -> Path:
        """Validate a one-time capability without consuming it for idempotency lookup."""
        item = self._items.get(capability_id)
        if (
            not item
            or item.operation != operation
            or item.project_id != project_id
            or item.consumed_at
            or item.expires_at <= datetime.now(UTC)
        ):
            raise DomainErrorV1(ErrorCode.SECURITY_CAPABILITY_INVALID, "文件授权已失效。")
        return item.path
