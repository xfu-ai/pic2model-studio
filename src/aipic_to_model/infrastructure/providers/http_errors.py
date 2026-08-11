"""Stable, redacted HTTP-to-provider error mapping."""

from __future__ import annotations

from ...domain.provider_models import (
    ErrorCategory,
    ErrorDetail,
    ProviderResult,
    RecommendedAction,
)


def http_failure(
    *,
    operation: str,
    status_code: int | None = None,
    request_id: str | None = None,
    retry_after_seconds: int | None = None,
    timed_out: bool = False,
    configuration_missing: bool = False,
    request_invalid: bool = False,
    submission_ambiguous: bool = False,
    fee_incurred: bool = False,
    credits_exhausted: bool = False,
    model_unavailable: bool = False,
) -> ProviderResult:
    """Map only transport metadata; response bodies and URLs are never accepted."""
    if configuration_missing:
        code, category, message, recoverable, action = (
            "PROVIDER_NOT_CONFIGURED",
            ErrorCategory.API_NOT_CONFIGURED,
            "The Provider profile is not configured.",
            False,
            RecommendedAction.CONFIGURE_PROVIDER,
        )
    elif credits_exhausted:
        code, category, message, recoverable, action = (
            "PROVIDER_CREDITS_EXHAUSTED",
            ErrorCategory.SERVICE_REJECTED,
            "The Provider account does not have enough credits for this request.",
            False,
            RecommendedAction.OPEN_DETAILS,
        )
    elif model_unavailable:
        code, category, message, recoverable, action = (
            "PROVIDER_MODEL_UNAVAILABLE",
            ErrorCategory.SERVICE_REJECTED,
            "The configured Provider model is unavailable.",
            False,
            RecommendedAction.CONFIGURE_PROVIDER,
        )
    elif request_invalid:
        code, category, message, recoverable, action = (
            "PROVIDER_REQUEST_INVALID",
            ErrorCategory.INPUT_INVALID,
            "The Provider rejected an unsupported parameter combination.",
            False,
            RecommendedAction.FIX_INPUT,
        )
    elif submission_ambiguous:
        code, category, message, recoverable, action = (
            "JOB_UNKNOWN_SUBMISSION",
            ErrorCategory.UNKNOWN,
            "The submission result is unknown and requires a remote-account check.",
            False,
            RecommendedAction.QUERY_REMOTE,
        )
    elif timed_out:
        code, category, message, recoverable, action = (
            "JOB_TIMEOUT",
            ErrorCategory.TIMEOUT,
            "The Provider did not respond in time.",
            True,
            RecommendedAction.RESUME,
        )
    elif status_code in {401, 403}:
        code, category, message, recoverable, action = (
            "PROVIDER_AUTH_FAILED",
            ErrorCategory.SERVICE_REJECTED,
            "The Provider rejected the configured credentials.",
            False,
            RecommendedAction.CONFIGURE_PROVIDER,
        )
    elif status_code == 429:
        code, category, message, recoverable, action = (
            "PROVIDER_RATE_LIMITED",
            ErrorCategory.SERVICE_REJECTED,
            "The Provider rate limit was reached.",
            True,
            RecommendedAction.RETRY,
        )
    elif status_code is not None and status_code >= 500:
        code, category, message, recoverable, action = (
            "PROVIDER_UNAVAILABLE",
            ErrorCategory.SERVICE_REJECTED,
            "The Provider is temporarily unavailable.",
            True,
            RecommendedAction.RETRY,
        )
    else:
        code, category, message, recoverable, action = (
            "PROVIDER_RESPONSE_INVALID",
            ErrorCategory.UNKNOWN,
            "The Provider returned an unsupported response.",
            False,
            RecommendedAction.OPEN_DETAILS,
        )
    technical_message: str | None = None
    if status_code is not None:
        technical_message = (
            "provider_response; "
            f"status_code={status_code}; "
            f"request_id_present={str(bool(request_id)).lower()}"
        )
    elif timed_out:
        technical_message = "provider_timeout; response_received=false"
    elif request_invalid:
        technical_message = "provider_response; result=rejected"
    elif submission_ambiguous:
        technical_message = "provider_submission; result=ambiguous"

    detail = ErrorDetail(
        code=code,
        category=category,
        user_message=message,
        recoverable=recoverable,
        retry_after_seconds=retry_after_seconds if code == "PROVIDER_RATE_LIMITED" else None,
        technical_message=technical_message,
        details_ref=f"provider:{operation}:{request_id}" if request_id else f"provider:{operation}",
        failed_object="provider",
        failed_step=operation,
        fee_incurred=bool(submission_ambiguous or fee_incurred),
        safe_to_retry=recoverable and not submission_ambiguous and not fee_incurred,
        recommended_action=action,
    )
    return ProviderResult(
        ok=False,
        provider_request_id=request_id,
        stage=operation,
        retryable=detail.safe_to_retry,
        error=detail,
    )
