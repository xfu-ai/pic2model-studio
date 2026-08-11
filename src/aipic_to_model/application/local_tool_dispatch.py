"""Connected implementations for B02 local synchronous canonical Tools."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from ..domain.prompt_parser import BilingualPrompt, merge_prompt_documents
from ..domain.provider_models import AnalysisResult
from ..domain.tools import ToolResultV1
from .image_processing import ImageProcessingService
from .local_image_processing import LocalImageProcessingService
from .prompt_service import PromptVersionService


class LocalToolDispatcher:
    def __init__(
        self,
        assets: Any,
        prompts: PromptVersionService,
        images: ImageProcessingService,
        local_images: LocalImageProcessingService,
        multiview: Any,
        model_assets: Any | None = None,
    ) -> None:
        self._assets = assets
        self._prompts = prompts
        self._images = images
        self._local_images = local_images
        self._multiview = multiview
        self._model_assets = model_assets

    def __call__(
        self,
        name: str,
        root: Path,
        project_id: str,
        arguments: dict[str, Any],
        call_id: str,
    ) -> ToolResultV1:
        try:
            if name == "model3d.inspect":
                if self._model_assets is None:
                    raise ValueError("model inspection is not configured")
                asset_id = str(arguments["asset_id"])
                inspection = self._model_assets.inspect(root, project_id, asset_id)
                return self._success(
                    call_id,
                    [asset_id],
                    json.dumps(
                        {
                            "message": "3D model inspected.",
                            "inspection": inspection.model_dump(mode="json"),
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                )
            if name == "prompt.extract_bilingual":
                source_id = str(arguments["analysis_asset_id"])
                source = self._assets.get(root, project_id, source_id)
                if source["asset_type"] != "analysis":
                    raise ValueError("source asset must be an analysis")
                _, content, mime, _ = self._assets.read_content(root, project_id, source_id, None)
                if mime != "application/json":
                    raise ValueError("analysis asset must be application/json")
                analysis = AnalysisResult.model_validate_json(content)
                kind = str(arguments["kind"])
                if analysis.mode != kind:
                    raise ValueError("analysis mode does not match prompt kind")
                zh_prompt = (analysis.zh_prompt or "").strip()
                en_prompt = (analysis.en_prompt or "").strip()
                if not zh_prompt or not en_prompt:
                    raise ValueError("analysis does not contain complete bilingual prompts")
                if zh_prompt.casefold() == "prompt" or en_prompt.casefold() == "prompt":
                    raise ValueError("analysis contains a prompt fence marker instead of a prompt")
                parsed = BilingualPrompt(
                    analysis.zh_text or zh_prompt,
                    analysis.en_text or en_prompt,
                    zh_prompt,
                    en_prompt,
                    tuple(analysis.preserve),
                    tuple(analysis.avoid),
                )
                result = self._prompts.create_bilingual(
                    root,
                    project_id,
                    kind=kind,
                    bilingual=parsed,
                    request_id=f"tool:{call_id}",
                    parent_asset_id=source_id,
                )
                asset = cast(dict[str, object], result["asset"])
                return self._success(call_id, [str(asset["id"])], "Prompt extracted.")
            if name == "prompt.merge":
                content_id = str(arguments["content_prompt_asset_id"])
                style_id = str(arguments["style_prompt_asset_id"])
                content = self._prompts.parse_asset(root, project_id, content_id)
                style = self._prompts.parse_asset(root, project_id, style_id)
                merged = merge_prompt_documents(content, style)
                result = self._prompts.create_bilingual(
                    root,
                    project_id,
                    kind="merged",
                    bilingual=merged,
                    request_id=f"tool:{call_id}",
                    parent_asset_id=content_id,
                    provenance={"parameters": {"style_prompt_asset_id": style_id}},
                )
                asset = cast(dict[str, object], result["asset"])
                return self._success(call_id, [str(asset["id"])], "Prompts merged.")
            if name == "prompt.get_current":
                asset_id = str(arguments["prompt_asset_id"])
                prompt = self._prompts.parse_asset(root, project_id, asset_id)
                return self._success(
                    call_id,
                    [asset_id],
                    json.dumps(
                        {
                            "message": "Prompt loaded.",
                            "prompt": asdict(prompt),
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                )
            if name == "prompt.validate":
                asset_id = str(arguments["prompt_asset_id"])
                self._prompts.parse_asset(root, project_id, asset_id)
                return self._success(call_id, [asset_id], "Prompt is valid.")
            if name == "image.compress_for_provider":
                result = self._images.compress_asset(
                    root,
                    project_id,
                    str(arguments["asset_id"]),
                    minimum=bool(arguments.get("minimum", False)),
                    request_id=f"tool:{call_id}",
                )
                return self._success(
                    call_id, [str(result["id"])], "Provider-compatible image created."
                )
            if name == "image.trim_transparent":
                result = self._local_images.trim_asset(
                    root,
                    project_id,
                    str(arguments["source_asset_id"]),
                    padding=int(arguments.get("padding", 0)),
                    alpha_threshold=int(arguments.get("alpha_threshold", 0)),
                    request_id=f"tool:{call_id}",
                )
                return self._success(
                    call_id,
                    [str(result["id"])],
                    "Transparent border trimmed.",
                    verification=_verification_from_result(result),
                )
            if name == "image.normalize":
                result = self._local_images.normalize_asset(
                    root,
                    project_id,
                    str(arguments["source_asset_id"]),
                    target_width=(
                        int(arguments["target_width"]) if "target_width" in arguments else None
                    ),
                    target_height=(
                        int(arguments["target_height"]) if "target_height" in arguments else None
                    ),
                    max_long_edge=(
                        int(arguments["max_long_edge"]) if "max_long_edge" in arguments else None
                    ),
                    lock_aspect_ratio=bool(arguments.get("lock_aspect_ratio", True)),
                    rotate_degrees=int(arguments.get("rotate_degrees", 0)),
                    flip=str(arguments.get("flip", "none")),
                    output_format=str(arguments.get("output_format", "png")),
                    quality=int(arguments.get("quality", 90)),
                    preserve_alpha=bool(arguments.get("preserve_alpha", True)),
                    request_id=f"tool:{call_id}",
                )
                return self._success(
                    call_id,
                    [str(result["id"])],
                    "Image normalized locally.",
                    verification=_verification_from_result(result),
                )
            if name == "image.remove_background_local":
                raw_target = arguments.get("target_color")
                target_color = None
                if isinstance(raw_target, list):
                    if len(raw_target) != 3:
                        raise ValueError("target_color must contain exactly three channels")
                    target_color = (
                        int(raw_target[0]),
                        int(raw_target[1]),
                        int(raw_target[2]),
                    )
                min_threshold = int(arguments.get("min_threshold", 0))
                max_threshold = int(arguments.get("max_threshold", 120))
                if min_threshold > max_threshold:
                    raise ValueError("min_threshold must not exceed max_threshold")
                result = self._local_images.remove_background_asset(
                    root,
                    project_id,
                    str(arguments["source_asset_id"]),
                    method=str(arguments["method"]),
                    target_color=target_color,
                    tolerance=int(arguments.get("tolerance", 24)),
                    contiguous_only=bool(arguments.get("contiguous_only", True)),
                    channel=str(arguments.get("channel", "green")),
                    min_threshold=min_threshold,
                    max_threshold=max_threshold,
                    invert=bool(arguments.get("invert", False)),
                    feather=int(arguments.get("feather", 0)),
                    edge_shrink=int(arguments.get("edge_shrink", 0)),
                    request_id=f"tool:{call_id}",
                )
                return self._success(
                    call_id,
                    [str(result["id"])],
                    "Background removed locally.",
                    verification=_verification_from_result(result),
                )
            if name == "image.split_local":
                results = self._local_images.split_asset(
                    root,
                    project_id,
                    str(arguments["source_asset_id"]),
                    mode=str(arguments["mode"]),
                    columns=(int(arguments["columns"]) if "columns" in arguments else None),
                    rows=(int(arguments["rows"]) if "rows" in arguments else None),
                    alpha_threshold=int(arguments.get("alpha_threshold", 0)),
                    min_area=int(arguments.get("min_area", 4)),
                    padding=int(arguments.get("padding", 0)),
                    max_outputs=int(arguments.get("max_outputs", 64)),
                    request_id=f"tool:{call_id}",
                )
                return self._success(
                    call_id,
                    [str(result["id"]) for result in results],
                    f"{len(results)} local image parts created.",
                    verification=_split_verification(results),
                )
            if name == "multiview.request_box_confirmation":
                set_id = str(arguments["multiview_set_id"])
                return ToolResultV1(
                    True,
                    "awaiting_ui_action",
                    call_id,
                    [],
                    "Three-view regions require user confirmation.",
                    [],
                    {"type": "confirm_multiview_regions"},
                    {
                        "action_id": call_id,
                        "type": "confirm_multiview_regions",
                        "workspace_mode": "multiview",
                        "asset_id": set_id,
                    },
                )
            if name == "multiview.set_regions":
                self._multiview.set_regions(
                    root,
                    project_id,
                    set_id=str(arguments["multiview_set_id"]),
                    regions=dict(arguments["regions"]),
                    request_id=f"tool:{call_id}",
                )
                return self._success(call_id, [], "Three-view regions updated.")
            if name == "multiview.crop_views":
                crops = self._multiview.crop_confirmed_views(
                    root,
                    project_id,
                    set_id=str(arguments["multiview_set_id"]),
                    request_id=f"tool:{call_id}",
                )
                return self._success(call_id, list(crops.values()), "Three-view crops created.")
            if name == "multiview.request_quality_confirmation":
                set_id = str(arguments["multiview_set_id"])
                return ToolResultV1(
                    True,
                    "awaiting_ui_action",
                    call_id,
                    [],
                    "Confirm the six three-view quality checks before 3D generation.",
                    [],
                    {"type": "confirm_multiview_quality"},
                    {
                        "action_id": call_id,
                        "type": "confirm_multiview_quality",
                        "workspace_mode": "multiview",
                        "asset_id": set_id,
                    },
                )
            if name == "multiview.set_quality_checks":
                report = self._multiview.validate(
                    root,
                    set_id=str(arguments["multiview_set_id"]),
                    checks=dict(arguments["checks"]),
                )
                return self._success(
                    call_id,
                    [],
                    "Three-view quality confirmed."
                    if report.can_continue
                    else "Three-view quality is blocked and cannot be submitted to 3D generation.",
                )
        except KeyError, UnicodeDecodeError, ValueError:
            return ToolResultV1(
                False,
                "failed",
                call_id,
                [],
                "The local Tool input could not be processed.",
                [],
                error={
                    "code": "TOOL_ARGUMENT_INVALID",
                    "category": "input_invalid",
                    "user_message": "The local Tool input could not be processed.",
                    "recoverable": False,
                    "failed_object": "tool_call",
                    "failed_step": "local_dispatch",
                    "safe_to_retry": False,
                    "recommended_action": "fix_input",
                },
            )
        return ToolResultV1(
            False,
            "failed",
            call_id,
            [],
            "The local Tool is not connected.",
            [],
            error={
                "code": "TOOL_NOT_AVAILABLE",
                "category": "api_not_configured",
                "user_message": "The local Tool is not connected.",
                "recoverable": True,
                "failed_object": "tool_call",
                "failed_step": "local_dispatch",
                "safe_to_retry": True,
                "recommended_action": "configure_provider",
            },
        )

    @staticmethod
    def _success(
        call_id: str,
        asset_ids: list[str],
        summary: str,
        *,
        verification: dict[str, Any] | None = None,
    ) -> ToolResultV1:
        structured_summary = summary
        if verification is not None:
            # Keep ToolResultV1's frozen public schema unchanged.  The Agent
            # adapter recognizes this additive summary envelope and exposes the
            # report through its own internal details field.
            structured_summary = json.dumps(
                {"message": summary, "verification": verification},
                separators=(",", ":"),
            )
        return ToolResultV1(
            True,
            "succeeded",
            call_id,
            asset_ids,
            structured_summary,
            [],
        )


def _verification_from_result(result: dict[str, Any]) -> dict[str, Any] | None:
    verification = result.get("verification")
    return verification if isinstance(verification, dict) else None


def _split_verification(results: list[dict[str, Any]]) -> dict[str, Any]:
    dimensions: list[dict[str, int]] = []
    for result in results:
        report = _verification_from_result(result) or {}
        facts = report.get("facts")
        if isinstance(facts, dict) and isinstance(facts.get("width"), int) and isinstance(
            facts.get("height"), int
        ):
            dimensions.append({"width": facts["width"], "height": facts["height"]})
    uniform = len({(item["width"], item["height"]) for item in dimensions}) <= 1
    outcome = "pass" if uniform else "warn"
    return {
        "schema_version": 1,
        "kind": "image_split",
        "operation": "split_local",
        "disposition": "verified" if uniform else "review_required",
        "facts": {"output_count": len(results), "dimensions": dimensions},
        "checks": [
            {
                "code": "image.split_dimensions_uniform",
                "outcome": outcome,
                "observed": {"dimensions": dimensions},
                **(
                    {}
                    if uniform
                    else {
                        "message": "Split outputs do not all share the same dimensions; normalize if a fixed size is required."
                    }
                ),
            }
        ],
    }
