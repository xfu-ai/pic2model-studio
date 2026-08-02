from __future__ import annotations

import os
import ssl

import pytest


@pytest.fixture(autouse=True)
def require_real_provider_opt_in(request: pytest.FixtureRequest) -> None:
    """Require an intentional, provider-specific switch before any paid call.

    ``ALLOW_REAL_PROVIDER_SMOKE`` remains a second, broad acknowledgement so a
    developer cannot accidentally enable every paid smoke simply by exporting
    one provider flag.  The per-provider flags are deliberately named after
    the service, making them suitable for focused CI/manual invocations.
    """
    if os.environ.get("ALLOW_REAL_PROVIDER_SMOKE") != "1":
        pytest.skip("set ALLOW_REAL_PROVIDER_SMOKE=1 and the provider flag for paid smoke")

    provider_flag = {
        "test_tripo.py": "RUN_LIVE_TRIPO",
        "test_gemini.py": "RUN_LIVE_GEMINI",
        "test_gpt_image.py": "RUN_LIVE_OPENAI",
        "test_meshy.py": "RUN_LIVE_MESHY",
        "test_nanobanana_browser_preflight.py": "RUN_LIVE_NANOBANANA",
    }.get(request.path.name)
    if provider_flag and os.environ.get(provider_flag) != "1":
        pytest.skip(f"set {provider_flag}=1 for this paid Provider smoke")

    affected_providers = {"test_meshy.py", "test_gpt_image.py", "test_tripo.py"}
    if request.path.name in affected_providers and ssl.OPENSSL_VERSION_INFO[:2] == (3, 5):
        pytest.fail(
            "This Python runtime uses OpenSSL 3.5.x, which fails TLS record-layer "
            "renegotiation with the configured Meshy/OpenAI/Tripo endpoints. "
            "Run this smoke through scripts/run_real_provider_smoke.ps1 so it "
            "uses official Python 3.14 with OpenSSL 3.0.x."
        )
