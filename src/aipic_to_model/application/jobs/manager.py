"""State guard for the SQLite Job Manager introduced in B02-03."""

from __future__ import annotations

import re

from ...domain.job_models import JobStatus, ResumeClass, assert_job_transition

AUTO_CLAIMABLE_RESUME_CLASSES = frozenset(
    {ResumeClass.FRESH, ResumeClass.LOCAL_RESTARTABLE, ResumeClass.DOWNLOAD_RETRY}
)


def is_auto_claimable(status: JobStatus, resume_class: ResumeClass) -> bool:
    return (
        status in {JobStatus.QUEUED, JobStatus.INTERRUPTED}
        and resume_class in AUTO_CLAIMABLE_RESUME_CLASSES
    )


def transition(
    current: JobStatus, target: JobStatus, resume_class: ResumeClass
) -> tuple[JobStatus, ResumeClass]:
    assert_job_transition(current, target)
    if (
        resume_class
        in {ResumeClass.UNKNOWN_SUBMISSION, ResumeClass.MANUAL_REVIEW, ResumeClass.STOP_WAITING}
        and target is JobStatus.RUNNING
    ):
        raise ValueError("unsafe job recovery must not be automatically claimed")
    return target, resume_class


def validate_resume_payload(payload: object) -> None:
    """Keep recovery metadata free of URLs, credentials, and absolute paths."""
    forbidden = {
        "url",
        "uri",
        "authorization",
        "token",
        "api_key",
        "password",
        "absolute_path",
        "path",
        "signed_url",
        "presigned_url",
        "download_url",
    }

    def unsafe_key(key: str) -> bool:
        lowered = key.lower()
        return (
            lowered in forbidden
            or lowered.endswith(("_url", "_uri", "_path", "_token", "_secret"))
            or "authorization" in lowered
            or "password" in lowered
        )

    def unsafe_string(value: str) -> bool:
        return value.startswith(("http://", "https://", "/", "\\\\", "~/")) or bool(
            re.match(r"^[A-Za-z]:[\\/]", value)
        )

    def visit(value: object, key: str | None = None) -> None:
        if key is not None and unsafe_key(key):
            raise ValueError("resume payload contains a forbidden secret or path field")
        if isinstance(value, dict):
            for child_key, child in value.items():
                if not isinstance(child_key, str):
                    raise TypeError("resume payload keys must be strings")
                visit(child, child_key)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str) and unsafe_string(value):
            raise ValueError("resume payload must not contain URLs or absolute paths")

    if not isinstance(payload, dict):
        raise TypeError("resume payload must be an object")
    visit(payload)
