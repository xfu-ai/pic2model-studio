"""Ports for the Tripo upload/task/download adapters.

The concrete production implementations live in :mod:`tripo_http`; these
ports keep the lifecycle testable with the offline Fake Provider and ensure
that signed URLs stay adapter-private.
"""

from __future__ import annotations

from typing import Protocol

from ...application.jobs.secure_download import DownloadResponse
from ...domain.provider_models import ProviderResult, RemoteArtifactRef, RemoteTaskState


class FileTransferProvider(Protocol):
    def upload(
        self,
        *,
        asset_id: str,
        content_sha256: str,
        size_bytes: int,
        mime_type: str,
    ) -> ProviderResult: ...


class Tripo3DProvider(Protocol):
    def create(self, payload: dict[str, object], *, idempotency_key: str) -> ProviderResult: ...

    def get(self, external_task_id: str) -> RemoteTaskState | ProviderResult: ...

    def cancel(self, external_task_id: str) -> ProviderResult: ...

    def open_artifact(
        self, *, external_task_id: str, artifact: RemoteArtifactRef, offset: int
    ) -> DownloadResponse: ...
