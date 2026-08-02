"""Lease-based B02 Job dispatch shared by startup recovery and background workers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ...domain.job_models import JobStage, JobStatus, ResumeClass
from .submission_policy import PAID_SUBMISSION_TOOLS

JobHandler = Callable[..., object]


class ProductionJobWorker:
    def __init__(self, jobs: Any, handlers: Mapping[str, JobHandler]) -> None:
        self._jobs = jobs
        self._handlers = dict(handlers)

    @property
    def job_types(self) -> frozenset[str]:
        """Expose only handler names for health checks and composition tests."""
        return frozenset(self._handlers)

    def run_once(self, root: Path, project_id: str, *, owner: str) -> str | None:
        """Claim and advance one safe Job; never claims unknown submissions."""
        database = root / "project.sqlite3"
        lease_until = (datetime.now(UTC) + timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
        job = self._jobs.claim(database, owner=owner, lease_until=lease_until)
        if job is None:
            job = self._jobs.claim_remote(database, owner=owner, lease_until=lease_until)
        if job is None:
            return None
        handler = self._handlers.get(job.job_type)
        if handler is None:
            self._jobs.update(
                database,
                job_id=job.id,
                target=JobStatus.FAILED,
                stage=job.stage,
                resume_class=ResumeClass.MANUAL_REVIEW,
                error={
                    "code": "TOOL_NOT_AVAILABLE",
                    "category": "api_not_configured",
                    "user_message": "The Job handler is not configured.",
                    "recoverable": True,
                    "failed_object": "job",
                    "failed_step": "dispatch",
                    "safe_to_retry": True,
                    "recommended_action": "configure_provider",
                },
            )
            return job.id
        try:
            handler(
                root,
                project_id,
                job,
                owner=owner,
                lease_until=lease_until,
            )
        except Exception:  # noqa: BLE001 - this is the redaction boundary
            # Raw exceptions may contain paths, commands, URLs, or Provider
            # response data, so only this stable boundary error is persisted.
            current = self._jobs.get(database, job_id=job.id)
            if current.status is JobStatus.RUNNING:
                ambiguous_submission = (
                    current.resume_class is ResumeClass.UNKNOWN_SUBMISSION
                    or current.stage is JobStage.CREATING
                )
                paid_postprocessing = (
                    current.job_type in PAID_SUBMISSION_TOOLS
                    and current.resume_class is ResumeClass.MANUAL_REVIEW
                    and current.stage is JobStage.POSTPROCESSING
                )
                self._jobs.update(
                    database,
                    job_id=job.id,
                    target=(
                        JobStatus.FAILED if paid_postprocessing else JobStatus.INTERRUPTED
                    ),
                    stage=(
                        JobStage.UNKNOWN_SUBMISSION
                        if ambiguous_submission
                        else current.stage
                    ),
                    resume_class=(
                        ResumeClass.UNKNOWN_SUBMISSION
                        if ambiguous_submission
                        else ResumeClass.MANUAL_REVIEW
                        if paid_postprocessing
                        else
                        ResumeClass.DOWNLOAD_RETRY
                        if current.resume_class is ResumeClass.DOWNLOAD_RETRY
                        else ResumeClass.LOCAL_RESTARTABLE
                    ),
                    error={
                        "code": (
                            "JOB_UNKNOWN_SUBMISSION"
                            if ambiguous_submission
                            else "JOB_POSTPROCESSING_FAILED"
                            if paid_postprocessing
                            else "JOB_HANDLER_INTERRUPTED"
                        ),
                        "category": "unknown",
                        "user_message": (
                            "The paid submission may have reached the Provider. "
                            "Automatic retry is disabled pending an account check."
                            if ambiguous_submission
                            else "The Provider result was received, but local post-processing failed."
                            if paid_postprocessing
                            else "The Job was interrupted at a safe boundary."
                        ),
                        "recoverable": not ambiguous_submission and not paid_postprocessing,
                        "failed_object": "job",
                        "failed_step": "dispatch",
                        "fee_incurred": paid_postprocessing,
                        "safe_to_retry": not ambiguous_submission and not paid_postprocessing,
                        "recommended_action": (
                            "confirm_new_submission"
                            if ambiguous_submission
                            else "open_details"
                            if paid_postprocessing
                            else "resume"
                        ),
                    },
                )
        return job.id
