from __future__ import annotations

import json
import threading
import time

import httpx
import pytest

from aipic_to_model.domain.local_inference import (
    LocalEngineKind,
    LocalProviderHealth,
    default_local_provider_profiles,
)
from aipic_to_model.infrastructure.local_inference import (
    CapabilityLocalProbe,
    LocalInferenceCancelled,
    LocalInferenceGate,
    LocalProviderDiscoveryError,
    LocalProviderMonitor,
    OllamaOpenAIProbe,
    normalize_loopback_base_url,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://127.0.0.1:11434/v1/", "http://127.0.0.1:11434/v1"),
        ("http://localhost:11434/v1", "http://localhost:11434/v1"),
        ("https://[::1]:11434/v1", "https://[::1]:11434/v1"),
    ],
)
def test_loopback_endpoint_normalization(value: str, expected: str) -> None:
    assert normalize_loopback_base_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com:11434/v1",
        "http://192.168.1.5:11434/v1",
        "http://0.0.0.0:11434/v1",
        "http://localhost.evil:11434/v1",
        "http://user:secret@127.0.0.1:11434/v1",
        "http://127.0.0.1/v1",
        "file:///tmp/model",
        "http://127.0.0.1:11434/v1?token=secret",
        "http://127.0.0.1:11434/v1/../admin",
    ],
)
def test_loopback_endpoint_rejects_nonlocal_or_ambiguous_values(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_loopback_base_url(value)


def test_default_profiles_have_frozen_safe_capabilities() -> None:
    profiles = default_local_provider_profiles()
    assert [profile.profile_id for profile in profiles] == [
        "agent/ollama/qwen3-vl",
        "image/local/z-image-turbo",
        "model3d/local/triposr",
    ]
    assert profiles[0].endpoint == "http://127.0.0.1:11434/v1"
    assert profiles[1].runtime_capability_id == "local-runtime/stable-diffusion-cpp"
    assert profiles[2].runtime_capability_id == "local-runtime/triposr-worker"
    assert profiles[2].transport.value == "controlled_process"
    assert all("path" not in profile.model_dump(mode="json") for profile in profiles)


def test_ollama_probe_requires_the_selected_model() -> None:
    captured: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            content=json.dumps({"data": [{"id": "qwen3-vl:8b"}]}),
            headers={"ollama-version": "0.12.7"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=False)
    profile = default_local_provider_profiles()[0]
    status = OllamaOpenAIProbe(client=client).probe(profile)
    client.close()

    assert status.available is True
    assert status.engine_version == "0.12.7"
    assert captured[0].url == httpx.URL("http://127.0.0.1:11434/v1/models")
    assert "authorization" not in captured[0].headers


def test_ollama_probe_reports_missing_model_without_installing_it() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"data": [{"id": "other:latest"}]})
        )
    )
    status = OllamaOpenAIProbe(client=client).probe(default_local_provider_profiles()[0])
    client.close()
    assert status.configured is True
    assert status.available is False
    assert status.reason == "model_not_installed"


def test_ollama_discovery_returns_installed_models_without_credentials() -> None:
    captured: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={"data": [{"id": "qwen3-vl:8b"}, {"id": "qwen3-vl:4b"}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=False)
    models = OllamaOpenAIProbe(client=client).discover(default_local_provider_profiles()[0])
    client.close()

    assert models == ("qwen3-vl:4b", "qwen3-vl:8b")
    assert captured[0].url == httpx.URL("http://127.0.0.1:11434/v1/models")
    assert "authorization" not in captured[0].headers


def test_ollama_discovery_maps_invalid_responses_without_returning_the_body() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, content=b"secret-invalid-response")
        )
    )
    with pytest.raises(LocalProviderDiscoveryError) as error:
        OllamaOpenAIProbe(client=client).discover(default_local_provider_profiles()[0])
    client.close()

    assert "secret-invalid-response" not in str(error.value)


def test_capability_probe_never_returns_a_resolved_path() -> None:
    profile = default_local_provider_profiles()[1]
    probe = CapabilityLocalProbe(
        lambda _capability_id: {
            "configured": True,
            "available": True,
            "model_present": True,
            "engine_version": "sd.cpp-1",
            "resolved_path": "C:/secret/model.gguf",
        }
    )
    status = probe.probe(profile)
    serialized = status.model_dump_json()
    assert status.available is True
    assert "resolved_path" not in serialized
    assert "C:/" not in serialized


class _FixedProbe:
    def __init__(self, available: bool):
        self.available = available

    def probe(self, profile):
        return LocalProviderHealth(
            profile_id=profile.profile_id,
            engine=profile.engine,
            model_id=profile.model_id,
            configured=self.available,
            available=self.available,
            reason=None if self.available else "runtime_not_configured",
            capabilities=profile.capabilities,
        )


def test_local_provider_monitor_refreshes_without_changing_order() -> None:
    profiles = default_local_provider_profiles()
    monitor = LocalProviderMonitor(
        profiles,
        {profile.profile_id: _FixedProbe(index == 0) for index, profile in enumerate(profiles)},
    )
    assert all(status.reason == "not_checked" for status in monitor.snapshot())
    statuses = monitor.refresh()
    assert [status.profile_id for status in statuses] == [
        profile.profile_id for profile in profiles
    ]
    assert statuses[0].available is True
    assert statuses[1].reason == "runtime_not_configured"


def test_local_provider_monitor_recovers_when_runtime_becomes_available() -> None:
    profile = default_local_provider_profiles()[0]
    probe = _FixedProbe(False)
    monitor = LocalProviderMonitor((profile,), {profile.profile_id: probe})
    monitor.start(interval_seconds=0.05)
    try:
        deadline = time.monotonic() + 1
        while monitor.snapshot()[0].reason == "not_checked" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert monitor.snapshot()[0].available is False

        probe.available = True
        deadline = time.monotonic() + 1
        while not monitor.snapshot()[0].available and time.monotonic() < deadline:
            time.sleep(0.01)
        assert monitor.snapshot()[0].available is True
    finally:
        monitor.stop()


def test_local_inference_gate_serializes_heavy_work() -> None:
    gate = LocalInferenceGate()
    entered: list[str] = []
    release_first = threading.Event()

    def first() -> None:
        with gate.lease("job-1", LocalEngineKind.STABLE_DIFFUSION_CPP):
            entered.append("job-1")
            release_first.wait(2)

    def second() -> None:
        with gate.lease("job-2", LocalEngineKind.TRIPOSR, timeout_seconds=2):
            entered.append("job-2")

    thread_one = threading.Thread(target=first)
    thread_two = threading.Thread(target=second)
    thread_one.start()
    while entered != ["job-1"]:
        time.sleep(0.01)
    thread_two.start()
    time.sleep(0.05)
    assert entered == ["job-1"]
    assert gate.snapshot() == {"busy": True, "engine": "stable_diffusion_cpp"}
    release_first.set()
    thread_one.join(2)
    thread_two.join(2)
    assert entered == ["job-1", "job-2"]
    assert gate.snapshot() == {"busy": False, "engine": None}


def test_local_inference_gate_honours_cancellation_while_waiting() -> None:
    gate = LocalInferenceGate()
    cancelled = False
    with gate.lease("job-1", LocalEngineKind.OLLAMA):
        cancelled = True
        with (
            pytest.raises(LocalInferenceCancelled),
            gate.lease(
                "job-2",
                LocalEngineKind.TRIPOSR,
                cancelled=lambda: cancelled,
                timeout_seconds=0.2,
            ),
        ):
            raise AssertionError("cancelled lease must not be acquired")
