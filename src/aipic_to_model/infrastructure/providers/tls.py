"""Verified TLS configuration shared by live Provider HTTP clients."""

from __future__ import annotations

import ssl

import httpx
import truststore


def provider_ssl_context() -> ssl.SSLContext:
    """Use the native OS trust store without weakening certificate checks."""

    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def provider_http_client(
    *,
    timeout_seconds: float,
    follow_redirects: bool = False,
) -> httpx.Client:
    """Create the common live Provider client with native certificate trust."""

    return httpx.Client(
        timeout=timeout_seconds,
        follow_redirects=follow_redirects,
        verify=provider_ssl_context(),
    )
