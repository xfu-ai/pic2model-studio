from dataclasses import dataclass


@dataclass(frozen=True)
class ProjectRef:
    id: str
    name: str
    root_path: str
    root_state: str
    format_version: int
    updated_at: str
