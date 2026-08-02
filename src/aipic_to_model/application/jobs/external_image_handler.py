"""Production Job handlers for Vision and image generation Providers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from ...domain.job_models import JobStage, JobStatus, ResumeClass
from ...domain.multiview_rules import default_regions
from ...domain.provider_models import AnalysisRequest, GenerationRequest, ProviderResult
from ...domain.production_prompts import (
    MULTIVIEW_BASE_PROMPT,
    MULTIVIEW_SHEET_REQUIREMENTS,
    regenerate_view_prompt,
)
from ..analysis import AnalysisAssetService, ProviderAnalysisError
from ..candidate_service import CandidateService, ProviderGenerationError
from ..image_provider_routing import AUTO_IMAGE_PROFILE
from ..image_processing import compress_for_provider
from .submission_policy import PAID_SUBMISSION_TOOLS

_ANALYSIS_MODES = {
    "image.analyze_content": "content",
    "image.analyze_style": "style",
    "image.evaluate_3d_suitability": "3d_suitability",
}
_GENERATION_JOBS = {"image.generate", "image.transform", "image.generate_variants"}
_MESHY_PROFILE = "meshy/default"
_MESHY_CHANNEL = "meshy"
_MESHY_DEFAULT_MODEL = "nano-banana"
_EDIT_JOBS = {
    "image.upscale": "upscale",
    "image.remove_background": "remove_background",
    "image.inpaint_selection": "inpaint_selection",
    "element.split": "element_split",
    "element.export_transparent": "export_transparent",
}


def _provider_image(content: bytes, mime: str) -> tuple[bytes, str]:
    """Normalize formats rejected by image-to-image provider boundaries."""

    if mime in {"image/png", "image/jpeg"}:
        return content, mime
    normalized = compress_for_provider(content)
    return normalized.content, normalized.mime_type


def _controls_cancelled(controls: dict[str, object] | None) -> bool:
    callback = (controls or {}).get("_cancelled")
    return bool(callback()) if callable(callback) else False


def _cancelled_result() -> ProviderResult:
    return ProviderResult(ok=False, stage="cancelled", retryable=False)


class ExternalImageJobHandler:
    """Advance one external image Job to a durable terminal state."""

    @staticmethod
    def _meshy_model(arguments: dict[str, Any]) -> str:
        model = arguments.get("model")
        if (
            not isinstance(model, str)
            or not model.strip()
            or model.lower().startswith("gpt-image")
        ):
            return _MESHY_DEFAULT_MODEL
        return model.strip()

    @staticmethod
    def _routing(
        result: ProviderResult, arguments: dict[str, Any]
    ) -> tuple[str, Literal["meshy", "tripo"], str] | None:
        routing = result.payload.get("routing")
        if not isinstance(routing, dict):
            # Direct Provider test doubles predate the routing wrapper. Keep a
            # deterministic Meshy fallback only for those isolated tests.
            return (
                _MESHY_PROFILE,
                _MESHY_CHANNEL,
                ExternalImageJobHandler._meshy_model(arguments),
            )
        profile = routing.get("provider_profile")
        channel = routing.get("channel")
        model = routing.get("model")
        if (
            not isinstance(profile, str)
            or channel not in {"meshy", "tripo"}
            or not isinstance(model, str)
        ):
            return None
        return profile, cast(Literal["meshy", "tripo"], channel), model

    def __init__(
        self,
        jobs: Any,
        assets: Any,
        selections: Any,
        multiview: Any,
        multiview_repository: Any,
        candidates: CandidateService,
        prompt_versions: Any,
        vision_provider: Any,
        image_provider: Any,
    ) -> None:
        self._jobs = jobs
        self._assets = assets
        self._selections = selections
        self._multiview = multiview
        self._multiview_repository = multiview_repository
        self._candidates = candidates
        self._prompt_versions = prompt_versions
        self._vision = vision_provider
        self._images = image_provider

    def run(
        self,
        root: Path,
        project_id: str,
        job: Any,
        *,
        owner: str,
        lease_until: str,
    ) -> Any:
        database = root / "project.sqlite3"
        if not self._jobs.heartbeat(database, job_id=job.id, owner=owner, lease_until=lease_until):
            raise RuntimeError("Job lease was lost")
        arguments = self._jobs.retry_context(database, job_id=job.id)["arguments"]
        if job.job_type in PAID_SUBMISSION_TOOLS:
            job = self._jobs.mark_submission_started(
                database,
                job_id=job.id,
                owner=owner,
            )
        controls = {
            "_heartbeat": lambda: self._jobs.heartbeat(
                database,
                job_id=job.id,
                owner=owner,
                lease_until=(datetime.now(UTC) + timedelta(seconds=60))
                .isoformat()
                .replace("+00:00", "Z"),
            ),
            "_cancelled": lambda: self._jobs.get(database, job_id=job.id).status
            == JobStatus.CANCELLED,
        }
        if job.job_type in _ANALYSIS_MODES:
            output = self._analysis(root, project_id, job, arguments)
        elif job.job_type == "prompt.rewrite":
            output = self._rewrite_prompt(root, project_id, job, arguments)
        elif job.job_type in _GENERATION_JOBS:
            output = self._generation(root, project_id, job, arguments, controls)
        elif job.job_type in _EDIT_JOBS:
            output = self._edit(root, project_id, job, arguments, controls)
        elif job.job_type == "multiview.generate":
            output = self._generate_multiview(root, project_id, job, arguments, controls)
        elif job.job_type == "multiview.regenerate_view":
            output = self._regenerate_view(root, project_id, job, arguments, controls)
        elif job.job_type == "selection.auto_suggest_boxes":
            output = self._suggest_selection(root, project_id, job, arguments)
        elif job.job_type == "multiview.detect_regions":
            output = self._detect_regions(root, project_id, job, arguments)
        elif job.job_type == "multiview.validate":
            output = self._validate_multiview(root, project_id, job, arguments)
        else:
            return self._fail(
                database,
                job.id,
                ProviderResult(
                    ok=False,
                    stage="dispatch",
                    retryable=False,
                    error=None,
                ),
                code="TOOL_NOT_AVAILABLE",
            )
        current = self._jobs.get(database, job_id=job.id)
        if current.status == JobStatus.CANCELLED:
            return current
        if isinstance(output, ProviderResult):
            return self._fail(database, job.id, output)
        return self._jobs.update(
            database,
            job_id=job.id,
            target=JobStatus.SUCCEEDED,
            stage=JobStage.POSTPROCESSING,
            result_asset_ids=output,
        )

    def _analysis(
        self, root: Path, project_id: str, job: Any, arguments: dict[str, Any]
    ) -> list[str] | ProviderResult:
        request = AnalysisRequest(
            asset_id=str(arguments["asset_id"]),
            provider_profile=str(arguments["provider_profile"]),
            model=str(arguments["model"]),
            mode=cast(
                Literal["content", "style", "3d_suitability"],
                _ANALYSIS_MODES[job.job_type],
            ),
        )
        try:
            asset = AnalysisAssetService(self._assets, self._vision).analyze_to_asset(
                root,
                project_id,
                request,
                request_id=f"job:{job.id}",
                tool_call_id=job.tool_call_id,
            )
        except ProviderAnalysisError as error:
            return error.result
        except TypeError:
            return ProviderResult(ok=False, stage="analyzing", retryable=False, error=None)
        return [str(asset["id"])]

    def _rewrite_prompt(
        self, root: Path, project_id: str, job: Any, arguments: dict[str, Any]
    ) -> list[str] | ProviderResult:
        prompt_id = str(arguments["prompt_asset_id"])
        _, content, mime, _ = self._assets.read_content(root, project_id, prompt_id, None)
        if mime not in {"application/json", "text/plain"}:
            return ProviderResult(ok=False, stage="rewriting", retryable=False)
        result = self._vision.rewrite(
            prompt=content.decode("utf-8"),
            instruction=str(arguments["instruction"]),
            model=str(arguments["model"]),
        )
        if not result.ok:
            return result
        text = result.payload.get("text")
        if not isinstance(text, str):
            return ProviderResult(ok=False, stage="rewriting", retryable=False)
        from ...domain.prompt_parser import parse_bilingual

        prompt_kind = self._prompt_versions.kind_for_asset(root, project_id, prompt_id)
        created = self._prompt_versions.create_bilingual(
            root,
            project_id,
            kind=prompt_kind,
            bilingual=parse_bilingual(text),
            request_id=f"job:{job.id}",
            parent_asset_id=prompt_id,
            provenance={
                "tool_call_id": job.tool_call_id,
                "provider_profile": str(arguments["provider_profile"]),
                "model": str(arguments["model"]),
                "parameters": {"operation": "prompt.rewrite"},
            },
        )
        asset = cast(dict[str, object], created["asset"])
        return [str(asset["id"])]

    def _generation(
        self,
        root: Path,
        project_id: str,
        job: Any,
        arguments: dict[str, Any],
        controls: dict[str, object] | None = None,
    ) -> list[str] | ProviderResult:
        mode = "t2i" if job.job_type == "image.generate" else "i2i"
        prompt_asset_id = str(arguments["prompt_asset_id"])
        source_asset_id = (
            str(arguments["source_asset_id"]) if arguments.get("source_asset_id") else None
        )
        provider_request: dict[str, object] = {
            "prompt_asset_id": prompt_asset_id,
            "source_asset_id": source_asset_id,
            "provider_profile": AUTO_IMAGE_PROFILE,
            "channel": "auto",
            "mode": mode,
            "model": "auto",
            "candidate_count": int(arguments["candidate_count"]),
            "aspect_ratio": arguments.get("aspect_ratio"),
            "size": arguments.get("size"),
            "quality": arguments.get("quality"),
            "output_format": arguments.get("output_format"),
            "structure_strength": arguments.get("structure_strength"),
            "prompt": self._prompt(root, project_id, prompt_asset_id),
            **(controls or {}),
        }
        if source_asset_id:
            _, content, mime, _ = self._assets.read_content(
                root, project_id, source_asset_id, None
            )
            content, mime = _provider_image(content, mime)
            provider_request.update({"source_bytes": content, "source_mime": mime})
        result = self._images.generate(provider_request)
        if not result.ok:
            return result
        if controls is not None:
            self._mark_provider_result_received(root / "project.sqlite3", job.id)
        routed = self._routing(result, arguments)
        if routed is None:
            return ProviderResult(ok=False, stage="routing", retryable=False)
        selected_profile, selected_channel, selected_model = routed
        request = GenerationRequest(
            prompt_asset_id=prompt_asset_id,
            source_asset_id=source_asset_id,
            provider_profile=selected_profile,
            channel=cast(Literal["meshy", "tripo"], selected_channel),
            mode=mode,
            model=selected_model,
            candidate_count=int(arguments["candidate_count"]),
            aspect_ratio=arguments.get("aspect_ratio"),
            size=arguments.get("size"),
            quality=arguments.get("quality"),
            output_format=arguments.get("output_format"),
            structure_strength=arguments.get("structure_strength"),
        )
        if _controls_cancelled(controls):
            return _cancelled_result()
        try:
            created = self._candidates.materialize_group(
                root,
                project_id,
                request,
                result,
                request_id=f"job:{job.id}",
                tool_call_id=job.tool_call_id,
            )
        except ProviderGenerationError:
            return result
        return [str(item) for item in cast(list[object], created["asset_ids"])]

    def _edit(
        self,
        root: Path,
        project_id: str,
        job: Any,
        arguments: dict[str, Any],
        controls: dict[str, object] | None = None,
    ) -> list[str] | ProviderResult:
        source_id = str(arguments["source_asset_id"])
        provider_source_id = source_id
        selection_id = str(arguments["selection_id"]) if arguments.get("selection_id") else None
        annotation_id: str | None = None
        # A selection must affect the pixels sent to an image-edit provider, not
        # merely appear in provenance.  The deterministic annotation is kept as
        # a managed input asset so the result remains reproducible and auditable.
        split_mode = str(arguments.get("split_mode") or "element")
        should_annotate = (
            job.job_type == "image.inpaint_selection"
            or (job.job_type == "element.split" and split_mode == "boxsplit")
        )
        if selection_id and should_annotate:
            annotation = self._selections.render_annotation(
                root,
                project_id,
                selection_id,
                f"job:{job.id}:annotation",
                outline=(52, 211, 153, 255) if split_mode == "boxsplit" else (255, 64, 64, 255),
            )
            annotation_id = str(annotation["id"])
            provider_source_id = annotation_id
        _, content, mime, _ = self._assets.read_content(
            root, project_id, provider_source_id, None
        )
        content, mime = _provider_image(content, mime)
        prompt_id = arguments.get("prompt_asset_id")
        prompt = (
            self._prompt(root, project_id, str(prompt_id))
            if prompt_id
            else f"Perform the requested {job.job_type} operation."
        )
        result = self._images.generate(
            {
                "prompt_asset_id": str(prompt_id or source_id),
                "source_asset_id": provider_source_id,
                "provider_profile": AUTO_IMAGE_PROFILE,
                "channel": "auto",
                "mode": "i2i",
                "model": "auto",
                "candidate_count": 1,
                "operation": job.job_type,
                "split_mode": split_mode,
                "prompt": prompt,
                "source_bytes": content,
                "source_mime": mime,
                **(controls or {}),
            }
        )
        if not result.ok:
            return result
        if controls is not None:
            self._mark_provider_result_received(root / "project.sqlite3", job.id)
        routed = self._routing(result, arguments)
        if routed is None:
            return ProviderResult(ok=False, stage="routing", retryable=False)
        selected_profile, _, selected_model = routed
        if _controls_cancelled(controls):
            return _cancelled_result()
        created = self._candidates.materialize_edit(
            root,
            project_id,
            operation=_EDIT_JOBS[job.job_type],
            source_asset_id=source_id,
            provider_profile=selected_profile,
            model=selected_model,
            result=result,
            request_id=f"job:{job.id}",
            prompt_asset_id=str(prompt_id) if prompt_id else None,
            selection_id=selection_id,
            additional_input_asset_ids=[annotation_id] if annotation_id else None,
            parameters={
                key: value
                for key, value in arguments.items()
                if key not in {"provider_profile", "source_asset_id", "prompt_asset_id"}
            },
            tool_call_id=job.tool_call_id,
        )
        return [str(created["id"])]

    def _generate_multiview(
        self,
        root: Path,
        project_id: str,
        job: Any,
        arguments: dict[str, Any],
        controls: dict[str, object] | None = None,
    ) -> list[str] | ProviderResult:
        source_id = str(arguments["source_asset_id"])
        prompt_id = str(arguments.get("prompt_asset_id") or source_id)
        _, content, mime, _ = self._assets.read_content(root, project_id, source_id, None)
        content, mime = _provider_image(content, mime)
        prompt = (
            self._prompt(root, project_id, prompt_id)
            if arguments.get("prompt_asset_id")
            else MULTIVIEW_BASE_PROMPT
        )
        request = {
            "prompt_asset_id": prompt_id,
            "source_asset_id": source_id,
            "provider_profile": AUTO_IMAGE_PROFILE,
            "channel": "auto",
            "mode": "i2i",
            "model": "auto",
            "candidate_count": 1,
            "prompt": f"{prompt}\n\n{MULTIVIEW_SHEET_REQUIREMENTS}",
            "source_bytes": content,
            "source_mime": mime,
            **(controls or {}),
        }
        result = self._images.generate(request)
        if not result.ok:
            return result
        if controls is not None:
            self._mark_provider_result_received(root / "project.sqlite3", job.id)
        routed = self._routing(result, arguments)
        if routed is None:
            return ProviderResult(ok=False, stage="routing", retryable=False)
        selected_profile, _, selected_model = routed
        images = result.payload.get("images")
        if (
            not isinstance(images, list)
            or len(images) != 1
            or not isinstance(images[0], dict)
            or not isinstance(images[0].get("base64"), str)
        ):
            return ProviderResult(ok=False, stage="generating", retryable=False)
        if _controls_cancelled(controls):
            return _cancelled_result()
        sheet_id = self._multiview.create_sheet_from_base64(
            root,
            project_id,
            source_asset_id=source_id,
            image_base64=str(images[0]["base64"]),
            request_id=f"job:{job.id}",
            prompt_asset_id=str(arguments["prompt_asset_id"])
            if arguments.get("prompt_asset_id")
            else None,
            provider_profile=selected_profile,
            model=selected_model,
            tool_call_id=job.tool_call_id,
        )
        return [sheet_id]

    def _regenerate_view(
        self,
        root: Path,
        project_id: str,
        job: Any,
        arguments: dict[str, Any],
        controls: dict[str, object] | None = None,
    ) -> list[str] | ProviderResult:
        set_id = str(arguments["multiview_set_id"])
        view = str(arguments["view"])
        members = self._multiview_repository.current_assets(root / "project.sqlite3", set_id)
        source_id = members[view]
        _, content, mime, _ = self._assets.read_content(root, project_id, source_id, None)
        content, mime = _provider_image(content, mime)
        result = self._images.generate(
            {
                "prompt_asset_id": source_id,
                "source_asset_id": source_id,
                "provider_profile": AUTO_IMAGE_PROFILE,
                "channel": "auto",
                "mode": "i2i",
                "model": "auto",
                "candidate_count": 1,
                "operation": "multiview.regenerate_view",
                "prompt": regenerate_view_prompt(view),
                "source_bytes": content,
                "source_mime": mime,
                **(controls or {}),
            }
        )
        if not result.ok:
            return result
        if controls is not None:
            self._mark_provider_result_received(root / "project.sqlite3", job.id)
        routed = self._routing(result, arguments)
        if routed is None:
            return ProviderResult(ok=False, stage="routing", retryable=False)
        selected_profile, _, selected_model = routed
        if _controls_cancelled(controls):
            return _cancelled_result()
        created = self._candidates.materialize_edit(
            root,
            project_id,
            operation="multiview_regenerate",
            source_asset_id=source_id,
            provider_profile=selected_profile,
            model=selected_model,
            result=result,
            request_id=f"job:{job.id}",
            parameters={"view": view, "multiview_set_id": set_id},
            tool_call_id=job.tool_call_id,
        )
        new_id = str(created["id"])
        self._multiview_repository.regenerate_view(
            root / "project.sqlite3", set_id=set_id, view_name=view, asset_id=new_id
        )
        return [new_id]

    def _suggest_selection(
        self, root: Path, project_id: str, job: Any, arguments: dict[str, Any]
    ) -> list[str] | ProviderResult:
        asset_id = str(arguments["asset_id"])
        result = self._analyze_for_gate(root, project_id, arguments, asset_id, "content")
        if isinstance(result, ProviderResult):
            return result
        asset = self._assets.get(root, project_id, asset_id)
        metadata = asset["metadata"]
        width, height = int(metadata["width"]), int(metadata["height"])
        selection = self._selections.save(
            root,
            project_id,
            asset_id,
            [
                {
                    "rect_id": "subject",
                    "x": round(width * 0.1),
                    "y": round(height * 0.1),
                    "width": round(width * 0.8),
                    "height": round(height * 0.8),
                }
            ],
            "subject",
            "agent",
            request_id=f"job:{job.id}",
            confidence=0.5,
        )
        return [str(selection["id"])]

    def _detect_regions(
        self, root: Path, project_id: str, job: Any, arguments: dict[str, Any]
    ) -> list[str] | ProviderResult:
        set_id = str(arguments["multiview_set_id"])
        members = self._multiview_repository.current_assets(root / "project.sqlite3", set_id)
        first = members["front"]
        gate = self._analyze_for_gate(root, project_id, arguments, first, "content")
        if isinstance(gate, ProviderResult):
            return gate
        selection_ids: list[str] = []
        for view, asset_id in members.items():
            asset = self._assets.get(root, project_id, asset_id)
            metadata = asset["metadata"]
            region = default_regions(int(metadata["width"]), int(metadata["height"]))[view]
            selection = self._selections.save(
                root,
                project_id,
                asset_id,
                [{"rect_id": view, **region.model_dump()}],
                view,
                "agent",
                request_id=f"job:{job.id}:{view}",
                confidence=0.5,
            )
            selection_ids.append(str(selection["id"]))
        self._multiview_repository.attach_regions(
            root / "project.sqlite3",
            set_id=set_id,
            selection_ids=dict(zip(("front", "side", "back"), selection_ids, strict=True)),
        )
        return selection_ids

    def _validate_multiview(
        self, root: Path, project_id: str, job: Any, arguments: dict[str, Any]
    ) -> list[str] | ProviderResult:
        set_id = str(arguments["multiview_set_id"])
        members = self._multiview_repository.current_assets(root / "project.sqlite3", set_id)
        # Gemini analysis is not a quality decision during the low-cost test phase.
        # The user must persist each check through multiview.set_quality_checks.
        del project_id, job, arguments
        return list(members.values())

    def _analyze_for_gate(
        self,
        root: Path,
        project_id: str,
        arguments: dict[str, Any],
        asset_id: str,
        mode: str,
    ) -> object | ProviderResult:
        _, content, mime, _ = self._assets.read_content(root, project_id, asset_id, None)
        result = self._vision.analyze_image(
            AnalysisRequest(
                asset_id=asset_id,
                provider_profile=str(arguments["provider_profile"]),
                model=str(arguments["model"]),
                mode=cast(Literal["content", "style", "3d_suitability"], mode),
            ),
            image_bytes=content,
            mime_type=mime,
        )
        return result

    def _prompt(self, root: Path, project_id: str, asset_id: str) -> str:
        _, content, mime, _ = self._assets.read_content(root, project_id, asset_id, None)
        if mime not in {"application/json", "text/plain"}:
            raise ValueError("prompt asset must be an aipic.prompt.v3 JSON document")
        text = content.decode("utf-8")
        from ...domain.prompt_parser import parse_bilingual

        return parse_bilingual(text).en_prompt

    def _mark_provider_result_received(self, database: Path, job_id: str) -> None:
        """Leave the ambiguous paid-create boundary before local materialization."""

        self._jobs.update(
            database,
            job_id=job_id,
            target=JobStatus.RUNNING,
            stage=JobStage.POSTPROCESSING,
            resume_class=ResumeClass.MANUAL_REVIEW,
        )

    def _fail(
        self,
        database: Path,
        job_id: str,
        result: ProviderResult,
        *,
        code: str = "PROVIDER_RESPONSE_INVALID",
    ) -> Any:
        detail = result.error.model_dump(mode="json") if result.error is not None else {}
        unknown_submission = detail.get("code") == "JOB_UNKNOWN_SUBMISSION"
        return self._jobs.update(
            database,
            job_id=job_id,
            target=(
                JobStatus.INTERRUPTED
                if result.retryable or unknown_submission
                else JobStatus.FAILED
            ),
            stage=(
                JobStage.UNKNOWN_SUBMISSION
                if unknown_submission
                else JobStage.POSTPROCESSING
            ),
            resume_class=(
                ResumeClass.UNKNOWN_SUBMISSION
                if unknown_submission
                else ResumeClass.LOCAL_RESTARTABLE
                if result.retryable
                else ResumeClass.MANUAL_REVIEW
            ),
            error=detail
            or {
                "code": code,
                "category": "api_not_configured" if code == "TOOL_NOT_AVAILABLE" else "unknown",
                "user_message": "The Provider operation could not be completed.",
                "recoverable": result.retryable,
                "failed_object": "provider",
                "failed_step": result.stage,
                "safe_to_retry": result.retryable,
                "recommended_action": "retry" if result.retryable else "open_details",
            },
        )
