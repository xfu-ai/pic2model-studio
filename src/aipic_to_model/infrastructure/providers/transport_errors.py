"""Provider-neutral HTTP transport failure classification.

Paid create requests need a stricter boundary than ordinary reads. A failure
while establishing a connection proves that the request was not submitted;
a failure after the connection was established can leave the remote outcome
unknown.
"""

from __future__ import annotations

import re
import socket
import ssl

import httpx

from ...domain.provider_models import ProviderResult
from .http_errors import http_failure

_DEFINITELY_NOT_SUBMITTED = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ProxyError,
    httpx.PoolTimeout,
)

_SAFE_HOST = re.compile(r"^[a-z0-9.-]{1,253}$")
_SAFE_TLS_TOKEN = re.compile(r"^[a-z0-9_]{1,80}$")
_OS_CAUSES = {
    11001: "dns_failure",
    10050: "network_unreachable",
    10051: "network_unreachable",
    10053: "connection_aborted",
    10054: "connection_reset",
    10060: "timeout",
    10061: "connection_refused",
    10064: "host_unreachable",
    10065: "host_unreachable",
}


def _exception_chain(error: BaseException) -> tuple[BaseException, ...]:
    """Return a bounded exception chain without serializing exception messages."""

    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and len(chain) < 8 and id(current) not in seen:
        chain.append(current)
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return tuple(chain)


def _os_code(chain: tuple[BaseException, ...]) -> int | None:
    for item in reversed(chain):
        for name in ("winerror", "errno"):
            value = getattr(item, name, None)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
    return None


def _cause_code(
    error: httpx.HTTPError,
    chain: tuple[BaseException, ...],
    os_code: int | None,
) -> str:
    if any(isinstance(item, socket.gaierror) for item in chain):
        return "dns_failure"
    if any(isinstance(item, ssl.SSLError) for item in chain):
        return "tls_failure"
    if os_code in _OS_CAUSES:
        return _OS_CAUSES[os_code]
    if isinstance(error, httpx.ConnectTimeout):
        return "timeout"
    if isinstance(error, httpx.PoolTimeout):
        return "pool_exhausted"
    if isinstance(error, httpx.ProxyError):
        return "proxy_failure"
    if isinstance(error, httpx.ConnectError):
        return "connection_failure"
    if isinstance(error, httpx.TimeoutException):
        return "timeout"
    return "transport_failure"


def _failure_code(error: httpx.HTTPError) -> str:
    checks: tuple[tuple[type[httpx.HTTPError], str], ...] = (
        (httpx.ConnectTimeout, "connect_timeout"),
        (httpx.PoolTimeout, "pool_timeout"),
        (httpx.ReadTimeout, "read_timeout"),
        (httpx.WriteTimeout, "write_timeout"),
        (httpx.ProxyError, "proxy_error"),
        (httpx.ConnectError, "connect_error"),
        (httpx.ReadError, "read_error"),
        (httpx.WriteError, "write_error"),
        (httpx.RemoteProtocolError, "remote_protocol_error"),
        (httpx.LocalProtocolError, "local_protocol_error"),
    )
    for error_type, code in checks:
        if isinstance(error, error_type):
            return code
    if isinstance(error, httpx.TimeoutException):
        return "timeout"
    return "transport_error"


def _phase(error: httpx.HTTPError) -> str:
    if isinstance(error, httpx.PoolTimeout):
        return "pool"
    if isinstance(error, httpx.ProxyError):
        return "proxy"
    if isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout)):
        return "connect"
    if isinstance(error, (httpx.WriteError, httpx.WriteTimeout)):
        return "write"
    if isinstance(error, (httpx.ReadError, httpx.ReadTimeout)):
        return "read"
    if isinstance(error, (httpx.RemoteProtocolError, httpx.LocalProtocolError)):
        return "protocol"
    return "transport"


def _request_metadata(error: httpx.HTTPError) -> tuple[str | None, str | None]:
    try:
        request = error.request
    except RuntimeError:
        return None, None
    method = request.method.upper()
    if method not in {"DELETE", "GET", "HEAD", "PATCH", "POST", "PUT"}:
        method = "OTHER"
    host = request.url.host
    if host is not None:
        try:
            host = host.encode("idna").decode("ascii").lower()
        except UnicodeError:
            host = None
    if host is not None and _SAFE_HOST.fullmatch(host) is None:
        host = None
    return method, host


def _tls_metadata(
    chain: tuple[BaseException, ...],
) -> tuple[str | None, str | None, int | None]:
    for item in chain:
        if not isinstance(item, ssl.SSLError):
            continue
        library = getattr(item, "library", None)
        reason = getattr(item, "reason", None)
        verify_code = getattr(item, "verify_code", None)
        safe_library = library.lower() if isinstance(library, str) else None
        safe_reason = reason.lower() if isinstance(reason, str) else None
        if safe_library is not None and _SAFE_TLS_TOKEN.fullmatch(safe_library) is None:
            safe_library = None
        if safe_reason is not None and _SAFE_TLS_TOKEN.fullmatch(safe_reason) is None:
            safe_reason = None
        if not isinstance(verify_code, int) or isinstance(verify_code, bool):
            verify_code = None
        return safe_library, safe_reason, verify_code
    return None, None, None


def _technical_message(error: httpx.HTTPError, *, paid_submission: bool) -> str:
    """Build bounded diagnostic tokens without raw messages, URLs, or headers."""

    chain = _exception_chain(error)
    os_code = _os_code(chain)
    method, host = _request_metadata(error)
    tls_library, tls_reason, tls_verify_code = _tls_metadata(chain)
    parts = [
        "provider_transport",
        f"failure={_failure_code(error)}",
        f"phase={_phase(error)}",
        f"cause={_cause_code(error, chain, os_code)}",
    ]
    if method is not None:
        parts.append(f"method={method}")
    if host is not None:
        parts.append(f"host={host}")
    if os_code is not None:
        parts.append(f"os_code={os_code}")
    if tls_library is not None:
        parts.append(f"tls_library={tls_library}")
    if tls_reason is not None:
        parts.append(f"tls_reason={tls_reason}")
    if tls_verify_code is not None:
        parts.append(f"tls_verify_code={tls_verify_code}")
    parts.append(f"paid_submission={str(paid_submission).lower()}")
    return "; ".join(parts)


def _with_diagnostic(
    result: ProviderResult,
    error: httpx.HTTPError,
    *,
    paid_submission: bool,
) -> ProviderResult:
    if result.error is None:
        return result
    detail = result.error.model_copy(
        update={"technical_message": _technical_message(error, paid_submission=paid_submission)}
    )
    return result.model_copy(update={"error": detail})


def transport_failure(
    error: httpx.HTTPError,
    *,
    operation: str,
    paid_submission: bool = False,
    fee_incurred: bool = False,
) -> ProviderResult:
    """Map a redacted httpx failure without persisting exception text."""

    if isinstance(error, _DEFINITELY_NOT_SUBMITTED):
        result = http_failure(
            operation=operation,
            status_code=503,
            fee_incurred=fee_incurred,
        )
        return _with_diagnostic(result, error, paid_submission=paid_submission)
    if paid_submission:
        result = http_failure(operation=operation, submission_ambiguous=True)
        return _with_diagnostic(result, error, paid_submission=paid_submission)
    if isinstance(error, httpx.TimeoutException):
        result = http_failure(
            operation=operation,
            timed_out=True,
            fee_incurred=fee_incurred,
        )
        return _with_diagnostic(result, error, paid_submission=paid_submission)
    result = http_failure(
        operation=operation,
        status_code=503,
        fee_incurred=fee_incurred,
    )
    return _with_diagnostic(result, error, paid_submission=paid_submission)
