from __future__ import annotations

import pytest

from aipic_to_model.infrastructure.providers.http_errors import http_failure


@pytest.mark.parametrize(
    ("status", "code", "recoverable", "action"),
    [
        (401, "PROVIDER_AUTH_FAILED", False, "configure_provider"),
        (403, "PROVIDER_AUTH_FAILED", False, "configure_provider"),
        (429, "PROVIDER_RATE_LIMITED", True, "retry"),
        (503, "PROVIDER_UNAVAILABLE", True, "retry"),
    ],
)
def test_http_errors_have_complete_stable_redacted_fields(
    status: int, code: str, recoverable: bool, action: str
) -> None:
    result = http_failure(
        operation="remote_running",
        status_code=status,
        request_id="safe-request-id",
        retry_after_seconds=7,
    )
    error = result.error
    assert error is not None
    assert error.code == code
    assert error.category.value
    assert error.user_message
    assert error.recoverable is recoverable
    assert error.retry_after_seconds == (7 if status == 429 else None)
    assert error.details_ref == "provider:remote_running:safe-request-id"
    assert error.fee_incurred is False
    assert error.safe_to_retry is recoverable
    assert error.recommended_action.value == action
    assert "http" not in error.model_dump_json().lower()


def test_ambiguous_create_is_never_safe_to_retry_or_repost() -> None:
    result = http_failure(operation="creating", submission_ambiguous=True)
    assert result.error is not None
    assert result.error.code == "JOB_UNKNOWN_SUBMISSION"
    assert result.error.fee_incurred is True
    assert result.error.safe_to_retry is False
    assert result.error.recommended_action.value == "query_remote"
