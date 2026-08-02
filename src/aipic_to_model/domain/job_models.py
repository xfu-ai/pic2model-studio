"""Frozen B02 job state contract.

These models intentionally have no SQLite, HTTP, or UI dependency.  The job
repository introduced in B02-03 persists this exact vocabulary rather than
deriving state from provider-specific strings.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class JobStage(StrEnum):
    QUEUED = "queued"
    UPLOADING = "uploading"
    CREATING = "creating"
    REMOTE_QUEUED = "remote_queued"
    REMOTE_RUNNING = "remote_running"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    POSTPROCESSING = "postprocessing"
    CANCEL_REQUESTED = "cancel_requested"
    STOP_WAITING = "stop_waiting"
    UNKNOWN_SUBMISSION = "unknown_submission"


class ResumeClass(StrEnum):
    FRESH = "fresh"
    LOCAL_RESTARTABLE = "local_restartable"
    REMOTE_POLL = "remote_poll"
    DOWNLOAD_RETRY = "download_retry"
    UNKNOWN_SUBMISSION = "unknown_submission"
    MANUAL_REVIEW = "manual_review"
    STOP_WAITING = "stop_waiting"


class CancelCapability(StrEnum):
    CANCEL_LOCAL = "cancel_local"
    CANCEL_REMOTE = "cancel_remote"
    STOP_WAITING = "stop_waiting"
    NOT_CANCELLABLE = "not_cancellable"


TERMINAL_JOB_STATUSES = frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED})

_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset(
        {JobStatus.RUNNING, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.INTERRUPTED}
    ),
    JobStatus.RUNNING: frozenset(
        {
            JobStatus.WAITING,
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.INTERRUPTED,
        }
    ),
    JobStatus.WAITING: frozenset(
        {
            JobStatus.RUNNING,
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.INTERRUPTED,
        }
    ),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
    JobStatus.INTERRUPTED: frozenset({JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.WAITING}),
}


def assert_job_transition(current: JobStatus, target: JobStatus) -> None:
    """Reject terminal rollback and every undocumented state edge."""
    if target not in _TRANSITIONS[current]:
        raise ValueError(f"illegal B02 job transition: {current} -> {target}")


class JobView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, frozen=True)
    id: str = Field(min_length=1)
    status: JobStatus
    stage: JobStage
    progress: int | None = Field(default=None, ge=0, le=100)
    elapsed_seconds: int = Field(ge=0)
    estimated_seconds: int | None = Field(default=None, ge=0)
    provider: str | None = None
    cancel_capability: CancelCapability
    can_cancel: bool
    can_stop_waiting: bool
    output_asset_ids: list[str] = Field(default_factory=list)
    error: object | None = None

    @model_validator(mode="after")
    def _flags_match_capability(self) -> JobView:
        if self.can_cancel != (
            self.cancel_capability
            in {CancelCapability.CANCEL_LOCAL, CancelCapability.CANCEL_REMOTE}
        ):
            raise ValueError("can_cancel must be derived from cancel_capability")
        if self.can_stop_waiting != (self.cancel_capability is CancelCapability.STOP_WAITING):
            raise ValueError("can_stop_waiting must be derived from cancel_capability")
        return self
