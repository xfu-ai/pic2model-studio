from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AssetRefV1:
    id: str
    asset_type: str
    name: str
    version_no: int
    group: str | None
    metadata: dict[str, Any]
    provenance: dict[str, Any]


@dataclass(frozen=True)
class UsageSummaryV1:
    child_count: int
    input_link_count: int
    output_link_count: int
    active_run_count: int
    active_job_count: int
    is_project_current: bool
