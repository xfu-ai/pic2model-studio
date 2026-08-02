from __future__ import annotations

import httpx

from aipic_to_model.infrastructure.providers.config import GeminiSettings
from aipic_to_model.infrastructure.providers.credential_probe import (
    DeepSeekCredentialProbe,
    DeepSeekProbeSettings,
    GeminiCredentialProbe,
)


def test_gemini_probe_reads_model_metadata_without_generation() -> None:
    seen: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET"
        assert request.url.path.endswith("/models/gemini-flash-lite-latest")
        assert request.headers["x-goog-api-key"] == "gemini-secret"
        return httpx.Response(200, json={"name": "models/gemini-flash-lite-latest"}, request=request)

    probe = GeminiCredentialProbe(
        GeminiSettings(),
        lambda: "gemini-secret",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )

    result = probe.probe()

    assert result.ok
    assert len(seen) == 1


def test_deepseek_probe_reads_models_without_starting_a_chat() -> None:
    seen: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET"
        assert request.url.path == "/models"
        assert request.headers["authorization"] == "Bearer deepseek-secret"
        return httpx.Response(200, json={"data": []}, request=request)

    probe = DeepSeekCredentialProbe(
        DeepSeekProbeSettings(),
        lambda: "deepseek-secret",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )

    result = probe.probe()

    assert result.ok
    assert len(seen) == 1


def test_independent_probes_distinguish_missing_and_invalid_credentials() -> None:
    missing = GeminiCredentialProbe(
        GeminiSettings(),
        lambda: None,
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
    ).probe()
    invalid = DeepSeekCredentialProbe(
        DeepSeekProbeSettings(),
        lambda: "invalid",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(401, json={"error": "redacted"}, request=request)
            )
        ),
    ).probe()

    assert missing.error is not None and missing.error.code == "PROVIDER_NOT_CONFIGURED"
    assert invalid.error is not None and invalid.error.code == "PROVIDER_AUTH_FAILED"
