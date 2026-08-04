"""Offline providers used only by the explicit controlled desktop E2E mode.

They implement the production provider shapes, but never open a socket.  This
lets a WebView-driven approval flow exercise queued, waiting, successful, and
failed jobs without needing a credential or a billable provider request.
"""

from __future__ import annotations

import base64
from collections import defaultdict
from io import BytesIO

from PIL import Image, ImageDraw

from ...application.jobs.secure_download import DownloadResponse
from ...domain.provider_models import (
    AnalysisRequest,
    AnalysisResult,
    ErrorCategory,
    ErrorDetail,
    ProviderResult,
    RecommendedAction,
    RemoteArtifactRef,
    RemoteTaskState,
)
from ...domain.prompt_parser import BilingualPrompt, serialize_prompt


def _controlled_preview_png() -> str:
    """Return a visible, valid offline image so desktop E2E can inspect candidates."""
    image = Image.new("RGB", (512, 512), "#172033")
    draw = ImageDraw.Draw(image)
    for offset in range(0, 512, 8):
        shade = 28 + offset // 8
        draw.rectangle((0, offset, 512, offset + 8), fill=(22, 32 + shade // 3, 51 + shade // 2))
    draw.ellipse((92, 60, 420, 388), fill="#6f9eff", outline="#d8e5ff", width=10)
    draw.polygon(((256, 112), (360, 390), (256, 454), (152, 390)), fill="#ffbb6c")
    draw.rectangle((64, 418, 448, 450), fill="#243451")
    output = BytesIO()
    image.save(output, "PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


_PNG = _controlled_preview_png()


def _controlled_box_split_png(source_bytes: bytes) -> str:
    """Render a deterministic isolated-target stand-in from the green-marked input.

    It deliberately differs from the source fixture so offline desktop E2E proves
    that box-split uses the selected target rather than echoing the source image.
    """
    with Image.open(BytesIO(source_bytes)) as source:
        annotated = source.convert("RGBA")
    pixels = annotated.load()
    green = [
        (x, y)
        for y in range(annotated.height)
        for x in range(annotated.width)
        if pixels[x, y][0] < 100 and pixels[x, y][1] > 150 and pixels[x, y][2] < 190
    ]
    if green:
        left, top = min(x for x, _ in green), min(y for _, y in green)
        right, bottom = max(x for x, _ in green), max(y for _, y in green)
        inset = 4
        crop = annotated.crop((left + inset, top + inset, max(left + inset + 1, right - inset), max(top + inset + 1, bottom - inset)))
    else:
        width, height = annotated.size
        crop = annotated.crop((width // 4, height // 4, width * 3 // 4, height * 3 // 4))
    crop.thumbnail((384, 384), Image.Resampling.LANCZOS)
    result = Image.new("RGBA", (512, 512), "#303030")
    x, y = (512 - crop.width) // 2, (512 - crop.height) // 2
    result.alpha_composite(crop, (x, y))
    draw = ImageDraw.Draw(result)
    draw.rounded_rectangle((20, 20, 492, 492), radius=18, outline="#6f9eff", width=3)
    output = BytesIO()
    result.convert("RGB").save(output, "PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


def _minimal_glb() -> bytes:
    """Use the recovered application's default preview cube in offline E2E."""
    return base64.b64decode(
        "Z2xURgIAAACABgAA3AMAAEpTT057ImFzc2V0Ijp7ImdlbmVyYXRvciI6IkNPTExBREEyR0xURiIsInZlcnNpb24iOiIyLjAifSwic2NlbmUiOjAsInNjZW5lcyI6W3sibm9kZXMiOlswXX1dLCJub2RlcyI6W3siY2hpbGRyZW4iOlsxXSwibWF0cml4IjpbMS4wLDAuMCwwLjAsMC4wLDAuMCwwLjAsLTEuMCwwLjAsMC4wLDEuMCwwLjAsMC4wLDAuMCwwLjAsMC4wLDEuMF19LHsibWVzaCI6MH1dLCJtZXNoZXMiOlt7InByaW1pdGl2ZXMiOlt7ImF0dHJpYnV0ZXMiOnsiTk9STUFMIjoxLCJQT1NJVElPTiI6Mn0sImluZGljZXMiOjAsIm1vZGUiOjQsIm1hdGVyaWFsIjowfV0sIm5hbWUiOiJNZXNoIn1dLCJhY2Nlc3NvcnMiOlt7ImJ1ZmZlclZpZXciOjAsImJ5dGVPZmZzZXQiOjAsImNvbXBvbmVudFR5cGUiOjUxMjMsImNvdW50IjozNiwibWF4IjpbMjNdLCJtaW4iOlswXSwidHlwZSI6IlNDQUxBUiJ9LHsiYnVmZmVyVmlldyI6MSwiYnl0ZU9mZnNldCI6MCwiY29tcG9uZW50VHlwZSI6NTEyNiwiY291bnQiOjI0LCJtYXgiOlsxLjAsMS4wLDEuMF0sIm1pbiI6Wy0xLjAsLTEuMCwtMS4wXSwidHlwZSI6IlZFQzMifSx7ImJ1ZmZlclZpZXciOjEsImJ5dGVPZmZzZXQiOjI4OCwiY29tcG9uZW50VHlwZSI6NTEyNiwiY291bnQiOjI0LCJtYXgiOlswLjUsMC41LDAuNV0sIm1pbiI6Wy0wLjUsLTAuNSwtMC41XSwidHlwZSI6IlZFQzMifV0sIm1hdGVyaWFscyI6W3sicGJyTWV0YWxsaWNSb3VnaG5lc3MiOnsiYmFzZUNvbG9yRmFjdG9yIjpbMC44MDAwMDAwMTE5MjA5MjksMC4wLDAuMCwxLjBdLCJtZXRhbGxpY0ZhY3RvciI6MC4wfSwibmFtZSI6IlJlZCJ9XSwiYnVmZmVyVmlld3MiOlt7ImJ1ZmZlciI6MCwiYnl0ZU9mZnNldCI6NTc2LCJieXRlTGVuZ3RoIjo3MiwidGFyZ2V0IjozNDk2M30seyJidWZmZXIiOjAsImJ5dGVPZmZzZXQiOjAsImJ5dGVMZW5ndGgiOjU3NiwiYnl0ZVN0cmlkZSI6MTIsInRhcmdldCI6MzQ5NjJ9XSwiYnVmZmVycyI6W3siYnl0ZUxlbmd0aCI6NjQ4fV19iAIAAEJJTgAAAAAAAAAAAAAAgD8AAAAAAAAAAAAAgD8AAAAAAAAAAAAAgD8AAAAAAAAAAAAAgD8AAAAAAACAvwAAAAAAAAAAAACAvwAAAAAAAAAAAACAvwAAAAAAAAAAAACAvwAAAAAAAIA/AAAAAAAAAAAAAIA/AAAAAAAAAAAAAIA/AAAAAAAAAAAAAIA/AAAAAAAAAAAAAAAAAACAPwAAAAAAAAAAAACAPwAAAAAAAAAAAACAPwAAAAAAAAAAAACAPwAAAAAAAIC/AAAAAAAAAAAAAIC/AAAAAAAAAAAAAIC/AAAAAAAAAAAAAIC/AAAAAAAAAAAAAAAAAAAAAAAAgL8AAAAAAAAAAAAAgL8AAAAAAAAAAAAAgL8AAAAAAAAAAAAAgL8AAAC/AAAAvwAAAD8AAAA/AAAAvwAAAD8AAAC/AAAAPwAAAD8AAAA/AAAAPwAAAD8AAAA/AAAAvwAAAD8AAAC/AAAAvwAAAD8AAAA/AAAAvwAAAL8AAAC/AAAAvwAAAL8AAAA/AAAAPwAAAD8AAAA/AAAAvwAAAD8AAAA/AAAAPwAAAL8AAAA/AAAAvwAAAL8AAAC/AAAAPwAAAD8AAAA/AAAAPwAAAD8AAAC/AAAAPwAAAL8AAAA/AAAAPwAAAL8AAAC/AAAAvwAAAD8AAAC/AAAAPwAAAD8AAAC/AAAAvwAAAL8AAAC/AAAAPwAAAL8AAAC/AAAAvwAAAL8AAAC/AAAAPwAAAL8AAAA/AAAAvwAAAL8AAAA/AAAAPwAAAL8AAAEAAgADAAIAAQAEAAUABgAHAAYABQAIAAkACgALAAoACQAMAA0ADgAPAA4ADQAQABEAEgATABIAEQAUABUAFgAXABYAFQA="
    )


def _failure(stage: str) -> ProviderResult:
    return ProviderResult(
        ok=False,
        provider_request_id="controlled-failure",
        stage=stage,
        retryable=True,
        error=ErrorDetail(
            code="PROVIDER_UNAVAILABLE",
            category=ErrorCategory.SERVICE_REJECTED,
            user_message="Controlled E2E provider failure.",
            recoverable=True,
            failed_object="provider",
            failed_step=stage,
            safe_to_retry=True,
            recommended_action=RecommendedAction.RETRY,
        ),
    )


class ControlledE2EVisionProvider:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, dict[str, object]]] = []

    def analyze_image(
        self, request: AnalysisRequest, *, image_bytes: bytes, mime_type: str
    ) -> AnalysisResult | ProviderResult:
        self.calls.append(("vision.analyze", {"asset_id": request.asset_id, "mime_type": mime_type}))
        if self.fail:
            return _failure("analyzing")
        return AnalysisResult(
            mode=request.mode,
            zh_text="受控测试素材",
            en_text="controlled test asset",
            zh_prompt="一个用于受控端到端验证的素材",
            en_prompt="an asset for controlled end-to-end validation",
            provider_request_id="controlled-vision-1",
            model=request.model,
        )

    def understand_image(
        self,
        *,
        asset_id: str,
        question: str,
        model: str,
        image_bytes: bytes,
        mime_type: str,
    ) -> ProviderResult:
        del model, image_bytes
        self.calls.append(
            ("vision.understand", {"asset_id": asset_id, "mime_type": mime_type})
        )
        if self.fail:
            return _failure("understanding")
        return ProviderResult(
            ok=True,
            provider_request_id="controlled-vision-understanding-1",
            stage="understanding",
            retryable=False,
            payload={"text": f"Controlled image understanding received: {question}"},
        )

    def rewrite(self, *, prompt: str, instruction: str, model: str) -> ProviderResult:
        self.calls.append(("vision.rewrite", {"model": model}))
        if self.fail:
            return _failure("rewriting")
        return ProviderResult(
            ok=True,
            provider_request_id="controlled-rewrite-1",
            stage="rewriting",
            retryable=False,
            payload={
                "text": serialize_prompt(BilingualPrompt(
                    zh_segment="受控改写分析",
                    en_segment="controlled rewrite analysis",
                    zh_prompt="受控重写提示词",
                    en_prompt="controlled rewritten prompt",
                    preserve=("受控测试主体",),
                    avoid=("无关对象",),
                )),
            },
        )


class ControlledE2EImageProvider:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, object]] = []

    def probe(self) -> ProviderResult:
        if self.fail:
            return _failure("probing")
        return ProviderResult(
            ok=True,
            provider_request_id="controlled-image-probe-1",
            stage="probing",
            retryable=False,
        )

    def generate(self, request: dict[str, object]) -> ProviderResult:
        self.calls.append({key: value for key, value in request.items() if key != "source_bytes"})
        if self.fail:
            return _failure("generating")
        count = int(request.get("candidate_count") or 1)
        source = request.get("source_bytes")
        image = (
            _controlled_box_split_png(source)
            if request.get("split_mode") == "boxsplit" and isinstance(source, bytes)
            else _PNG
        )
        return ProviderResult(
            ok=True,
            provider_request_id="controlled-image-1",
            stage="generating",
            retryable=False,
            payload={
                "images": [
                    {
                        "base64": image,
                        "evaluation_status": "not_evaluated",
                        "short_evaluation": None,
                        "anomalies": [],
                    }
                    for _ in range(count)
                ]
            },
        )


class ControlledE2EFileTransferProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def upload(
        self, *, asset_id: str, content_sha256: str, size_bytes: int, mime_type: str
    ) -> ProviderResult:
        self.calls.append({"asset_id": asset_id, "mime_type": mime_type, "size_bytes": size_bytes})
        return ProviderResult(
            ok=True,
            provider_request_id="controlled-upload-1",
            stage="uploading",
            retryable=False,
            payload={
                "remote_input": {
                    "provider": "controlled-tripo",
                    "opaque_input_id": f"input-{asset_id}",
                    "kind": "upload_token",
                }
            },
        )


class ControlledE2ETripoProvider:
    """Deterministic queued -> running -> succeeded Tripo stand-in."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, dict[str, object]]] = []
        self._polls: defaultdict[str, int] = defaultdict(int)

    def create(self, request: dict[str, object], *, idempotency_key: str) -> ProviderResult:
        self.calls.append(("tripo.create", {"idempotency_key": idempotency_key, **request}))
        if self.fail:
            return _failure("creating")
        return ProviderResult(
            ok=True,
            provider_request_id="controlled-tripo-create-1",
            stage="creating",
            retryable=False,
            payload={"external_task_id": "controlled-tripo-task-1"},
        )

    def get(self, external_task_id: str) -> RemoteTaskState | ProviderResult:
        self.calls.append(("tripo.get", {"external_task_id": external_task_id}))
        if self.fail:
            return _failure("remote_running")
        self._polls[external_task_id] += 1
        poll = self._polls[external_task_id]
        if poll == 1:
            return RemoteTaskState(external_task_id=external_task_id, status="queued", progress=5)
        if poll == 2:
            return RemoteTaskState(external_task_id=external_task_id, status="running", progress=65)
        glb = _minimal_glb()
        return RemoteTaskState(
            external_task_id=external_task_id,
            status="succeeded",
            progress=100,
            artifacts=[
                RemoteArtifactRef(
                    artifact_id="controlled-model",
                    kind="glb",
                    host_fingerprint="controlled-artifact-host",
                    expected_size=len(glb),
                )
            ],
        )

    def cancel(self, external_task_id: str) -> ProviderResult:
        self.calls.append(("tripo.cancel", {"external_task_id": external_task_id}))
        return ProviderResult(
            ok=True,
            provider_request_id="controlled-tripo-cancel-1",
            stage="cancel_requested",
            retryable=False,
        )

    def open_artifact(
        self, *, external_task_id: str, artifact: RemoteArtifactRef, offset: int
    ) -> DownloadResponse:
        self.calls.append(("tripo.download", {"artifact_id": artifact.artifact_id, "offset": offset}))
        content = _minimal_glb()[offset:]
        return DownloadResponse(
            url="https://artifacts.fake.example/controlled.glb",
            resolved_ips=("93.184.216.34",),
            peer_ip="93.184.216.34",
            status_code=206 if offset else 200,
            content_type="model/gltf-binary",
            chunks=(content,),
            content_range=f"bytes {offset}-" if offset else None,
        )
