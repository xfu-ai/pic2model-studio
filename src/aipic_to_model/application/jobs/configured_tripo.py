"""Composition-friendly production Tripo Job handler."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...infrastructure.providers.tripo_http import (
    HttpFileTransferProvider,
    HttpTripo3DProvider,
    TripoHttpSettings,
)
from ..assets import AssetService
from .tripo_handler import TripoLifecycleHandler


class ConfiguredTripoJobHandler:
    """Bind managed-asset loading to each claimed project without global paths."""

    def __init__(
        self,
        jobs: Any,
        assets: AssetService,
        settings: TripoHttpSettings,
        credential: Callable[[], str | None],
        multiview_repository: Any,
        *,
        provider: Any | None = None,
        transfer_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._jobs = jobs
        self._assets = assets
        self._settings = settings
        self._credential = credential
        self._multiview_repository = multiview_repository
        self._provider = provider or HttpTripo3DProvider(settings, credential)
        self._transfer_factory = transfer_factory

    def run(
        self,
        root: Path,
        project_id: str,
        job: Any,
        *,
        owner: str,
        lease_until: str,
    ) -> Any:
        transfer = (
            self._transfer_factory()
            if self._transfer_factory is not None
            else HttpFileTransferProvider(
                self._settings,
                self._credential,
                lambda asset_id: self._assets.read_content(root, project_id, asset_id, None)[1],
            )
        )
        return TripoLifecycleHandler(
            self._jobs,
            self._assets,
            transfer,
            self._provider,
            allowed_artifact_hosts=self._settings.allowed_artifact_hosts,
            multiview_repository=self._multiview_repository,
        ).run(
            root,
            project_id,
            job,
            owner=owner,
            lease_until=lease_until,
        )
