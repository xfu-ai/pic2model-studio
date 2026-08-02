from __future__ import annotations

import ssl

import truststore

from aipic_to_model.infrastructure.providers.tls import provider_ssl_context


def test_provider_tls_uses_native_trust_without_weakening_verification() -> None:
    context = provider_ssl_context()

    assert isinstance(context, truststore.SSLContext)
    assert context.check_hostname is True
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.minimum_version is ssl.TLSVersion.TLSv1_2
