"""Deterministic startup recovery classification for B02 Jobs."""

from __future__ import annotations

from dataclasses import dataclass

from ...domain.job_models import ResumeClass


@dataclass(frozen=True)
class RecoveryDecision:
    job_id: str
    action: str


def classify_recovery(
    job_id: str, resume_class: ResumeClass, external_task_id: str | None
) -> RecoveryDecision:
    """Never turn ambiguity into a new paid submission."""
    if resume_class is ResumeClass.REMOTE_POLL:
        if not external_task_id:
            return RecoveryDecision(job_id, "manual_review")
        return RecoveryDecision(job_id, "query_remote")
    if resume_class is ResumeClass.DOWNLOAD_RETRY:
        return RecoveryDecision(job_id, "retry_download")
    if resume_class in {ResumeClass.UNKNOWN_SUBMISSION, ResumeClass.MANUAL_REVIEW}:
        return RecoveryDecision(job_id, "manual_review")
    if resume_class is ResumeClass.STOP_WAITING:
        return RecoveryDecision(job_id, "preserve_stop_waiting")
    return RecoveryDecision(job_id, "enqueue_local")
