from __future__ import annotations

import socket
import ssl

import httpx
import pytest

from aipic_to_model.infrastructure.providers.transport_errors import transport_failure


@pytest.mark.parametrize(
    "error_type",
    [httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError, httpx.PoolTimeout],
)
def test_pre_submission_transport_failures_are_safe_to_retry(
    error_type: type[httpx.HTTPError],
) -> None:
    request = httpx.Request("POST", "https://provider.invalid/create")
    result = transport_failure(
        error_type("not connected", request=request),
        operation="creating",
        paid_submission=True,
    )

    assert result.error is not None
    assert result.error.code == "PROVIDER_UNAVAILABLE"
    assert result.error.safe_to_retry is True
    assert result.error.fee_incurred is False
    assert result.error.technical_message is not None
    assert "provider_transport" in result.error.technical_message
    assert "paid_submission=true" in result.error.technical_message
    assert "not connected" not in result.error.technical_message
    assert "https://" not in result.error.technical_message


@pytest.mark.parametrize("error_type", [httpx.ReadError, httpx.WriteError, httpx.ReadTimeout])
def test_in_flight_paid_submission_failures_are_ambiguous(
    error_type: type[httpx.HTTPError],
) -> None:
    request = httpx.Request("POST", "https://provider.invalid/create")
    result = transport_failure(
        error_type("response unavailable", request=request),
        operation="creating",
        paid_submission=True,
    )

    assert result.error is not None
    assert result.error.code == "JOB_UNKNOWN_SUBMISSION"
    assert result.error.safe_to_retry is False
    assert result.error.fee_incurred is True
    assert result.error.technical_message is not None
    assert "paid_submission=true" in result.error.technical_message
    assert "response unavailable" not in result.error.technical_message


def test_connect_error_records_redacted_dns_diagnostics() -> None:
    request = httpx.Request(
        "POST",
        "https://provider.invalid/create?api_key=must-not-leak",
        headers={"Authorization": "Bearer must-not-leak"},
    )
    try:
        try:
            raise socket.gaierror(11001, "host and secret must-not-leak")
        except socket.gaierror as cause:
            raise httpx.ConnectError(
                "raw provider URL and secret must-not-leak",
                request=request,
            ) from cause
    except httpx.ConnectError as error:
        result = transport_failure(error, operation="creating", paid_submission=True)

    assert result.error is not None
    assert result.error.technical_message == (
        "provider_transport; failure=connect_error; phase=connect; "
        "cause=dns_failure; method=POST; host=provider.invalid; "
        "os_code=11001; paid_submission=true"
    )
    serialized = result.error.model_dump_json()
    assert "must-not-leak" not in serialized
    assert "/create" not in serialized


def test_http_response_failure_records_status_without_response_body() -> None:
    from aipic_to_model.infrastructure.providers.http_errors import http_failure

    result = http_failure(
        operation="creating",
        status_code=503,
        request_id="safe-request-id",
    )

    assert result.error is not None
    assert result.error.technical_message == (
        "provider_response; status_code=503; request_id_present=true"
    )


def test_tls_failure_records_only_stable_ssl_metadata() -> None:
    request = httpx.Request("POST", "https://provider.invalid/create")
    try:
        try:
            cause = ssl.SSLCertVerificationError(1, "certificate path must-not-leak")
            cause.library = "SSL"
            cause.reason = "CERTIFICATE_VERIFY_FAILED"
            cause.verify_code = 20
            raise cause
        except ssl.SSLError as cause:
            raise httpx.ConnectError("must-not-leak", request=request) from cause
    except httpx.ConnectError as error:
        result = transport_failure(error, operation="creating", paid_submission=True)

    assert result.error is not None
    diagnostic = result.error.technical_message or ""
    assert "cause=tls_failure" in diagnostic
    assert "tls_library=ssl" in diagnostic
    assert "tls_reason=certificate_verify_failed" in diagnostic
    assert "tls_verify_code=20" in diagnostic
    assert "must-not-leak" not in diagnostic
