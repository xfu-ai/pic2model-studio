"""Local Z-Image-Turbo Provider backed by stable-diffusion.cpp."""

from __future__ import annotations

import base64
from collections.abc import Callable
from pathlib import Path

from ...domain.provider_models import (
    ErrorCategory,
    ErrorDetail,
    ProviderResult,
    RecommendedAction,
)
from ..stable_diffusion_cpp import (
    StableDiffusionCppRunner,
    ZImageCancelled,
    ZImageExecutionError,
    ZImageGenerationSpec,
    ZImageOutputInvalid,
    ZImageRuntimeConfig,
    ZImageTimedOut,
)

Z_IMAGE_PROFILE = "image/local/z-image-turbo"
Z_IMAGE_MODEL = "Z-Image-Turbo"


class ZImageTurboProvider:
    def __init__(
        self,
        runner: StableDiffusionCppRunner,
        config: ZImageRuntimeConfig | None = None,
    ) -> None:
        self._runner = runner
        self._config = config or ZImageRuntimeConfig()

    def probe(self) -> ProviderResult:
        if self._runner.probe(self._config):
            return ProviderResult(ok=True, stage="ready", retryable=False)
        return _failure(
            "LOCAL_PROVIDER_NOT_CONFIGURED",
            ErrorCategory.API_NOT_CONFIGURED,
            "Z-Image-Turbo local runtime is not configured.",
            stage="probing",
            retryable=False,
            recoverable=True,
            action=RecommendedAction.CONFIGURE_PROVIDER,
        )

    def generate(
        self,
        *,
        owner: str,
        temporary_root: Path,
        prompt: str,
        width: int,
        height: int,
        candidate_count: int,
        seed: int,
        steps: int,
        timeout_seconds: float,
        cancelled: Callable[[], bool],
        heartbeat: Callable[[], bool],
    ) -> ProviderResult:
        try:
            output = self._runner.generate(
                owner,
                self._config,
                ZImageGenerationSpec(
                    prompt,
                    width,
                    height,
                    candidate_count,
                    seed,
                    steps,
                    timeout_seconds=timeout_seconds,
                ),
                temporary_root,
                cancelled=cancelled,
                heartbeat=heartbeat,
            )
        except ZImageCancelled:
            return ProviderResult(ok=False, stage="cancelled", retryable=False)
        except ZImageTimedOut:
            return _failure(
                "LOCAL_IMAGE_TIMEOUT",
                ErrorCategory.TIMEOUT,
                "Z-Image-Turbo exceeded the local generation timeout.",
                stage="generating",
                retryable=True,
            )
        except ZImageOutputInvalid:
            return _failure(
                "LOCAL_IMAGE_OUTPUT_INVALID",
                ErrorCategory.UNKNOWN,
                "Z-Image-Turbo produced an invalid image output.",
                stage="verifying",
                retryable=True,
            )
        except ZImageExecutionError:
            return _failure(
                "LOCAL_IMAGE_EXECUTION_FAILED",
                ErrorCategory.UNKNOWN,
                "Z-Image-Turbo local generation failed.",
                stage="generating",
                retryable=True,
            )
        except ValueError:
            return _failure(
                "LOCAL_IMAGE_INPUT_INVALID",
                ErrorCategory.INPUT_INVALID,
                "The Z-Image-Turbo generation parameters are invalid.",
                stage="validating",
                retryable=False,
                recoverable=True,
                action=RecommendedAction.FIX_INPUT,
            )
        return ProviderResult(
            ok=True,
            stage="generated",
            retryable=False,
            payload={
                "images": [
                    {
                        "base64": base64.b64encode(content).decode("ascii"),
                        "evaluation_status": "not_evaluated",
                        "short_evaluation": None,
                        "anomalies": [],
                    }
                    for content in output.images
                ],
                "routing": {
                    "provider_profile": Z_IMAGE_PROFILE,
                    "channel": "z_image",
                    "model": Z_IMAGE_MODEL,
                },
                "parameters": {
                    "seed": output.seed,
                    "steps": output.steps,
                    "width": output.width,
                    "height": output.height,
                },
            },
        )


def _failure(
    code: str,
    category: ErrorCategory,
    message: str,
    *,
    stage: str,
    retryable: bool,
    recoverable: bool = True,
    action: RecommendedAction = RecommendedAction.RETRY,
) -> ProviderResult:
    return ProviderResult(
        ok=False,
        stage=stage,
        retryable=retryable,
        error=ErrorDetail(
            code=code,
            category=category,
            user_message=message,
            recoverable=recoverable,
            failed_object="provider",
            failed_step=stage,
            fee_incurred=False,
            safe_to_retry=retryable,
            recommended_action=action,
        ),
    )
