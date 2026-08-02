"""Per-sidecar composition state; no router or API handler opens SQLite directly."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..application.app_state import AppStateService
from ..application.archive_import import ProjectPackageService
from ..application.assets import AssetService
from ..application.b02_runtime import PersistentB02ToolRuntime
from ..application.diagnostics import DiagnosticsService
from ..application.events import EventService
from ..application.host_capabilities import HostCapabilityStore
from ..application.jobs.recovery_service import JobRecoveryService
from ..application.jobs.runner import BackgroundJobRunner
from ..application.jobs.worker import ProductionJobWorker
from ..application.multiview import MultiviewService
from ..application.projects import ProjectService
from ..application.prompt_service import PromptVersionService
from ..application.selections import SelectionService
from ..application.settings import SecretStore, SettingsService
from ..application.tools import ToolRegistry
from ..domain.errors import DomainErrorV1, ErrorCode
from ..infrastructure.sqlite.job_repository import SqliteJobRepository


@dataclass
class AppDependencies:
    capabilities: HostCapabilityStore
    app_db: Path
    app_state: AppStateService
    events: EventService
    projects: ProjectService
    prompt_versions: PromptVersionService
    assets: AssetService
    selections: SelectionService
    multiview: MultiviewService
    packages: ProjectPackageService
    registry: ToolRegistry
    settings: SettingsService
    secret_store: SecretStore
    diagnostics: DiagnosticsService
    jobs: SqliteJobRepository
    job_recovery: JobRecoveryService
    b02_runtime: PersistentB02ToolRuntime
    job_worker: ProductionJobWorker
    roots: dict[str, Path] = field(default_factory=dict)
    job_runner: BackgroundJobRunner | None = None
    image_provider_monitor: Any | None = None

    def root_for(self, project_id: str) -> Path:
        root = self.roots.get(project_id)
        if root is None:
            raise DomainErrorV1(ErrorCode.PROJECT_NOT_FOUND, "项目尚未由本机 Host 打开。")
        return root
