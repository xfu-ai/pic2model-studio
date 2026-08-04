from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from aipic_to_model.domain.provider_models import ErrorCategory
from aipic_to_model.infrastructure.providers.z_image_turbo import (
    Z_IMAGE_MODEL,
    Z_IMAGE_PROFILE,
    ZImageTurboProvider,
)
from aipic_to_model.infrastructure.stable_diffusion_cpp import (
    ZImageCancelled,
    ZImageExecutionError,
    ZImageGenerationOutput,
    ZImageOutputInvalid,
    ZImageTimedOut,
)


def _png() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (512, 512), "purple").save(stream, format="PNG")
    return stream.getvalue()


class _Runner:
    def __init__(self, outcome: object = None, *, ready: bool = True) -> None:
        self.outcome = outcome
        self.ready = ready
        self.generate_calls = 0
        self.probe_calls = 0

    def probe(self, _config: object) -> bool:
        self.probe_calls += 1
        return self.ready

    def generate(self, *_args: object, **_kwargs: object) -> ZImageGenerationOutput:
        self.generate_calls += 1
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        if isinstance(self.outcome, ZImageGenerationOutput):
            return self.outcome
        return ZImageGenerationOutput((_png(),), 7, 8, 512, 512)


def _generate(provider: ZImageTurboProvider, tmp_path: Path):
    return provider.generate(
        owner="job:test",
        temporary_root=tmp_path,
        prompt="a harmless prompt that must not leak",
        width=512,
        height=512,
        candidate_count=1,
        seed=7,
        steps=8,
        timeout_seconds=30,
        cancelled=lambda: False,
        heartbeat=lambda: True,
    )


def test_probe_only_checks_configuration_and_never_generates() -> None:
    runner = _Runner(ready=True)
    result = ZImageTurboProvider(runner).probe()  # type: ignore[arg-type]
    assert result.ok and result.stage == "ready"
    assert runner.probe_calls == 1 and runner.generate_calls == 0

    missing = _Runner(ready=False)
    result = ZImageTurboProvider(missing).probe()  # type: ignore[arg-type]
    assert not result.ok and result.error is not None
    assert result.error.code == "LOCAL_PROVIDER_NOT_CONFIGURED"
    assert result.error.fee_incurred is False
    assert missing.generate_calls == 0


def test_success_returns_transient_candidates_and_frozen_local_routing(tmp_path: Path) -> None:
    result = _generate(ZImageTurboProvider(_Runner()), tmp_path)  # type: ignore[arg-type]
    assert result.ok
    assert result.payload["routing"] == {
        "provider_profile": Z_IMAGE_PROFILE,
        "channel": "z_image",
        "model": Z_IMAGE_MODEL,
    }
    assert result.payload["parameters"] == {
        "seed": 7,
        "steps": 8,
        "width": 512,
        "height": 512,
    }
    encoded = result.payload["images"][0]["base64"]
    assert base64.b64decode(encoded, validate=True).startswith(b"\x89PNG")


@pytest.mark.parametrize(
    ("error", "code", "category", "retryable"),
    [
        (ZImageTimedOut("secret path"), "LOCAL_IMAGE_TIMEOUT", ErrorCategory.TIMEOUT, True),
        (
            ZImageOutputInvalid("secret output"),
            "LOCAL_IMAGE_OUTPUT_INVALID",
            ErrorCategory.UNKNOWN,
            True,
        ),
        (
            ZImageExecutionError("secret prompt and stderr"),
            "LOCAL_IMAGE_EXECUTION_FAILED",
            ErrorCategory.UNKNOWN,
            True,
        ),
        (
            ValueError("secret input"),
            "LOCAL_IMAGE_INPUT_INVALID",
            ErrorCategory.INPUT_INVALID,
            False,
        ),
    ],
)
def test_structured_failures_are_fee_free_and_do_not_leak_runner_details(
    tmp_path: Path,
    error: BaseException,
    code: str,
    category: ErrorCategory,
    retryable: bool,
) -> None:
    result = _generate(ZImageTurboProvider(_Runner(error)), tmp_path)  # type: ignore[arg-type]
    assert not result.ok and result.retryable is retryable and result.error is not None
    assert result.error.code == code and result.error.category is category
    assert result.error.fee_incurred is False
    serialized = str(result.error.model_dump(mode="json")).lower()
    assert "secret" not in serialized and "stderr" not in serialized


def test_cancellation_is_not_reported_as_a_provider_failure(tmp_path: Path) -> None:
    result = _generate(
        ZImageTurboProvider(_Runner(ZImageCancelled("do not expose"))),  # type: ignore[arg-type]
        tmp_path,
    )
    assert not result.ok and result.stage == "cancelled"
    assert result.error is None and not result.retryable
