"""Test-only composition compatibility for explicit B01 dependency injection.

Production services require their ports in constructors.  The historical test
suite intentionally exercises the services directly, so this file supplies
the same real local adapters before tests are imported; it is neither a mock
nor a production fallback/service locator.
"""

from __future__ import annotations

import os

import pytest

from aipic_to_model.application.archive_import import ProjectPackageService
from aipic_to_model.application.assets import AssetService
from aipic_to_model.application.diagnostics import DiagnosticsService
from aipic_to_model.application.operations import OperationService
from aipic_to_model.application.projects import ProjectService
from aipic_to_model.application.selections import SelectionService
from aipic_to_model.application.settings import SettingsService
from aipic_to_model.application.tool_catalog import register_b01_tools
from aipic_to_model.application.tools import ToolRegistry
from aipic_to_model.infrastructure.runtime import InfrastructureRuntime
from aipic_to_model.infrastructure.sqlite.repositories import (
    AssetRepository,
    PackageRepository,
    ProjectRepository,
    SelectionRepository,
    SettingsRepository,
    ToolRepository,
)


def _filesystem() -> InfrastructureRuntime:
    return InfrastructureRuntime()


def _assets() -> AssetService:
    return AssetService(AssetRepository(), _filesystem(), OperationService())


def _selections() -> SelectionService:
    return SelectionService(SelectionRepository(), _filesystem(), _assets())


def pytest_configure() -> None:
    project_init = ProjectService.__init__
    asset_init = AssetService.__init__
    selection_init = SelectionService.__init__
    package_init = ProjectPackageService.__init__
    diagnostics_init = DiagnosticsService.__init__
    settings_init = SettingsService.__init__
    registry_init = ToolRegistry.__init__

    def project(self, *args, **kwargs):
        if not args and not kwargs:
            return project_init(self, ProjectRepository, _filesystem(), OperationService())
        return project_init(self, *args, **kwargs)

    def asset(self, *args, **kwargs):
        if not args and not kwargs:
            return asset_init(self, AssetRepository(), _filesystem(), OperationService())
        return asset_init(self, *args, **kwargs)

    def selection(self, *args, **kwargs):
        if not args and not kwargs:
            return selection_init(self, SelectionRepository(), _filesystem(), _assets())
        return selection_init(self, *args, **kwargs)

    def package(self, *args, **kwargs):
        if not args and not kwargs:
            return package_init(
                self,
                PackageRepository(),
                _filesystem(),
                OperationService(),
            )
        return package_init(self, *args, **kwargs)

    def diagnostics(self, *args, **kwargs):
        if not args and not kwargs:
            return diagnostics_init(self, _filesystem())
        return diagnostics_init(self, *args, **kwargs)

    def settings(self, *args, **kwargs):
        if len(args) == 1 and not kwargs:
            return settings_init(self, args[0], _filesystem())
        return settings_init(self, *args, **kwargs)

    def registry(self, *args, **kwargs):
        if not args and not kwargs:
            return registry_init(self, ToolRepository(), _filesystem())
        return registry_init(self, *args, **kwargs)

    ProjectService.__init__ = project
    AssetService.__init__ = asset
    SelectionService.__init__ = selection
    ProjectPackageService.__init__ = package
    DiagnosticsService.__init__ = diagnostics
    SettingsService.__init__ = settings
    ToolRegistry.__init__ = registry

    def register(registry: ToolRegistry, *args, **kwargs) -> None:
        job_submitter = args[0] if args else kwargs.pop("job_submitter", None)
        return register_b01_tools(
            registry,
            _assets(),
            _selections(),
            ProjectService(),
            SelectionRepository(),
            job_submitter,
        )

    import aipic_to_model.application.tool_catalog as catalog

    catalog.register_b01_tools = register


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Keep real-model tests opt-in even when a developer has credentials configured."""

    del config
    if os.environ.get("RUN_LIVE_LLM_TESTS") == "1":
        return
    skip_live_llm = pytest.mark.skip(
        reason="real LLM tests require RUN_LIVE_LLM_TESTS=1",
    )
    for item in items:
        if "live_llm" in item.keywords:
            item.add_marker(skip_live_llm)
