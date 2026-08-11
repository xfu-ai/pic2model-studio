"""Stable model-facing facades over the frozen AIPic atomic Tool registry."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal, cast

from ...application.tools import ToolRegistry as AIPicToolRegistry
from ...domain.errors import DomainErrorV1
from ...domain.tools import ToolResultV1
from ..core.events import CancellationToken
from ..core.models import JsonValue, TextContent, ToolResult
from ..core.tool import ToolContext, ToolUpdateCallback
from .aipic_tools import AIPicToolInvocation, _agent_result, _tool_request_id

_GEMINI_PROFILE = "gemini/google/default"
_GEMINI_MODEL = "gemini-flash-lite-latest"
_AUTO_IMAGE_PROFILE = "image-generation/auto"
_AUTO_IMAGE_MODEL = "auto"
_TRIPO_PROFILE = "tripo3d/default"
_TRIPO_MODEL = "v3.1-20260211"
RuntimeContext = Callable[[], Mapping[str, object]]
PromptCreator = Callable[[AIPicToolInvocation, str, str], str]

FACADE_TOOL_NAMES = (
    "inspect_workspace",
    "select_asset",
    "analyze_image",
    "understand_image",
    "generate_images",
    "edit_image",
    "split_image",
    "prepare_multiview",
    "generate_model3d",
    "process_model3d",
    "control_job",
)


def _facade_agent_result(
    result: ToolResultV1,
    tool_call_id: str,
    *,
    prompt_asset_id: str | None = None,
    source_tool: str | None = None,
) -> ToolResult:
    """Expose continuation-critical opaque refs in model-visible Tool content.

    ``ToolResult.details`` is durable UI metadata, but provider protocol adapters
    send only visible content back to the model.  A plain summary such as
    ``Prompt extracted.`` therefore made a successful facade call impossible to
    chain into the next facade call.  Keep the human summary while appending the
    minimum structured continuation data the model is allowed to reuse.
    """

    converted = _agent_result(result, tool_call_id)
    job = result.job if isinstance(result.job, dict) else {}
    job_ref = job.get("job_id")
    continuation: dict[str, object] = {
        "status": result.status,
        "output_asset_refs": list(result.output_asset_ids),
        "output_count": len(result.output_asset_ids),
    }
    if source_tool:
        continuation["source_tool"] = source_tool
    if isinstance(job_ref, str) and job_ref:
        continuation["job_ref"] = job_ref
    if prompt_asset_id:
        continuation["prompt_asset_ref"] = prompt_asset_id
    if result.reused:
        continuation["reused"] = True
    if not continuation["output_asset_refs"] and "job_ref" not in continuation and not prompt_asset_id:
        return converted
    details = dict(cast(dict[str, object], converted.details or {}))
    if prompt_asset_id:
        data = dict(cast(dict[str, object], details.get("data", {})))
        data["prompt_asset_id"] = prompt_asset_id
        details["data"] = data
    verification = details.get("verification")
    if isinstance(verification, dict):
        continuation["verification"] = verification
    result_data = details.get("data")
    if isinstance(result_data, dict) and isinstance(result_data.get("inspection"), dict):
        continuation["inspection"] = result_data["inspection"]
    visible_summary = next(
        (
            block.text
            for block in converted.content
            if isinstance(block, TextContent)
        ),
        result.summary,
    )
    return replace(
        converted,
        details=details,
        content=(
            TextContent(
                f"{visible_summary}\nFacade result: "
                f"{json.dumps(continuation, ensure_ascii=False, separators=(',', ':'))}"
            ),
        ),
    )


def _object(
    properties: dict[str, Any],
    required: tuple[str, ...] = (),
    *,
    all_of: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(required),
    }
    if all_of:
        schema["allOf"] = all_of
    return schema


_REF = {"type": "string", "minLength": 1}
_REFS_2 = {
    "type": "array",
    "maxItems": 2,
    "uniqueItems": True,
    "items": _REF,
}
_MODEL_PARAMETERS = _object(
    {
        "model_version": _REF,
        "texture_quality": {"enum": ["standard", "detailed", "extreme"]},
        "geometry_quality": {"enum": ["standard", "detailed"]},
        "texture_alignment": {"enum": ["original_image", "geometry"]},
        "texture": {"type": "boolean", "default": True},
        "pbr": {"type": "boolean", "default": True},
        "quad": {"type": "boolean"},
        "face_limit": {"type": "integer", "minimum": 1, "default": 100_000},
        "auto_size": {"type": "boolean"},
        "orientation": {"enum": ["default", "align_image"]},
        "smart_low_poly": {"type": "boolean"},
        "generate_parts": {"type": "boolean"},
        "compress": {"enum": ["", "geometry"]},
        "enable_image_autofix": {"type": "boolean"},
        "model_seed": {"type": "integer", "minimum": 0},
        "texture_seed": {"type": "integer", "minimum": 0},
    }
)


@dataclass(frozen=True)
class FacadeToolSpec:
    name: str
    label: str
    description: str
    parameters: Mapping[str, object]


FACADE_TOOL_SPECS = (
    FacadeToolSpec(
        "inspect_workspace",
        "Inspect workspace",
        (
            "Inspect the current managed workspace. Use summary for project state, assets for "
            "a paged managed-asset list, asset_details for one asset, compare for two sibling "
            "assets, jobs for one known job, and capabilities for the fixed facade inventory. "
            "Do not use it to rediscover an output_asset_ref already returned by a Tool result, "
            "or to change projects, assets, settings, approvals, or files."
        ),
        _object(
            {
                "view": {
                    "enum": [
                        "summary",
                        "assets",
                        "asset_details",
                        "compare",
                        "jobs",
                        "capabilities",
                    ]
                },
                "asset_refs": _REFS_2,
                "job_ref": _REF,
                "group": {
                    "enum": [
                        "input_images",
                        "generated_images",
                        "split_elements",
                        "multiview_and_crops",
                        "models",
                        "exports",
                    ]
                },
            },
            ("view",),
        ),
    ),
    FacadeToolSpec(
        "select_asset",
        "Select asset",
        (
            "Set one managed asset as current only when the user explicitly selected it or a "
            "workflow produced exactly one unambiguous result. Do not choose among candidates "
            "for the user, hide, restore, trash, import, export, or open files."
        ),
        _object(
            {
                "asset_ref": _REF,
                "reason": {"type": "string", "minLength": 1, "maxLength": 500},
            },
            ("asset_ref", "reason"),
        ),
    ),
    FacadeToolSpec(
        "analyze_image",
        "Analyze image",
        (
            "Analyze one managed image for exactly one requested purpose: content, style, or "
            "3D suitability. Use only when the user explicitly requests a persisted workflow "
            "analysis or that analysis is explicitly required as prompt-workflow input. Reuse "
            "existing analysis unless refresh is true because the user explicitly requested a "
            "fresh analysis. Do not call after understand_image for the same purpose, and do "
            "not generate or edit images."
        ),
        _object(
            {
                "source_asset_ref": _REF,
                "analysis_type": {"enum": ["content", "style", "3d_suitability"]},
                "refresh": {"type": "boolean", "default": False},
            },
            ("source_asset_ref", "analysis_type"),
        ),
    ),
    FacadeToolSpec(
        "understand_image",
        "Understand image",
        (
            "Answer one concrete question about a managed image for the text-only Agent. "
            "It returns grounded plain text directly to the Agent and does not create an analysis "
            "asset, prompt, Job, comparison, or workspace change. Use this for ordinary visual "
            "understanding. Use analyze_image only when the user explicitly wants a persisted "
            "content/style/3D-suitability workflow analysis. Do not call analyze_image for the "
            "same image and purpose afterwards. Do not use this Tool to create workflow analysis, "
            "prompts, or visual assets."
        ),
        _object(
            {
                "source_asset_ref": _REF,
                "question": {"type": "string", "minLength": 1, "maxLength": 4000},
            },
            ("source_asset_ref", "question"),
        ),
    ),
    FacadeToolSpec(
        "generate_images",
        "Generate images",
        (
            "Generate managed image candidates. Use from_prompt without a source, from_image "
            "to transform one source, and variants for alternatives of one source. This is a "
            "paid external operation requiring parameter-bound approval. Do not use it for "
            "upscale, background removal, inpainting, splitting, or multiview generation. A "
            "reference-image style change keeps its source_asset_ref and uses from_image unless "
            "the user explicitly removes the reference image."
        ),
        _object(
            {
                "mode": {"enum": ["from_prompt", "from_image", "variants"]},
                "prompt": {"type": "string", "minLength": 1, "maxLength": 20_000},
                "prompt_asset_ref": _REF,
                "source_asset_ref": _REF,
                "candidate_count": {"type": "integer", "enum": [1, 2, 4]},
                "aspect_ratio": {"type": "string", "minLength": 1, "maxLength": 32},
                "size": {"type": "string", "minLength": 1, "maxLength": 32},
                "quality": {"type": "string", "minLength": 1, "maxLength": 32},
                "output_format": {"enum": ["png", "jpg", "webp"]},
                "structure_strength": {"type": "number", "minimum": 0, "maximum": 1},
                "seed": {"type": "integer", "minimum": 0, "maximum": 2_147_483_647},
                "steps": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            ("mode", "candidate_count"),
        ),
    ),
    FacadeToolSpec(
        "edit_image",
        "Edit image",
        (
            "Apply one managed image edit. Local offline operations include trim_transparent, "
            "normalize, remove_background_local, and upscale_local. Provider operations remain "
            "upscale, remove_background, inpaint, and export_transparent. Inpaint requires a "
            "confirmed selection and managed prompt. Direct background removal is a valid one-step "
            "operation. Use local color_key only for a flat keyed background, local channel for a "
            "separable channel range, Provider remove_background for a complex background, and "
            "export_transparent for an already extracted element. Do not guess target_color or "
            "tolerance from appearance; omit target_color to derive it from corners. When continuing "
            "a Tool result, copy its exact output_asset_ref. Never silently replace a local operation "
            "with a Provider operation. Do not use it for candidate generation or multiview work."
        ),
        _object(
            {
                "operation": {
                    "enum": [
                        "upscale",
                        "remove_background",
                        "inpaint",
                        "export_transparent",
                        "trim_transparent",
                        "normalize",
                        "remove_background_local",
                        "upscale_local",
                    ]
                },
                "source_asset_ref": _REF,
                "selection_ref": _REF,
                "prompt_asset_ref": _REF,
                "scale": {"enum": [2, 4]},
                "padding": {"type": "integer", "minimum": 0, "maximum": 256},
                "alpha_threshold": {"type": "integer", "minimum": 0, "maximum": 255},
                "target_width": {"type": "integer", "minimum": 1, "maximum": 16384},
                "target_height": {"type": "integer", "minimum": 1, "maximum": 16384},
                "max_long_edge": {"type": "integer", "minimum": 1, "maximum": 16384},
                "lock_aspect_ratio": {"type": "boolean"},
                "rotate_degrees": {"enum": [0, 90, 180, 270]},
                "flip": {"enum": ["none", "horizontal", "vertical"]},
                "output_format": {"enum": ["png", "jpeg", "webp"]},
                "quality": {"type": "integer", "minimum": 1, "maximum": 100},
                "preserve_alpha": {"type": "boolean"},
                "background_method": {"enum": ["color_key", "channel"]},
                "target_color": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {"type": "integer", "minimum": 0, "maximum": 255},
                },
                "tolerance": {"type": "integer", "minimum": 0, "maximum": 255},
                "contiguous_only": {"type": "boolean"},
                "channel": {"enum": ["red", "green", "blue", "luminance", "saturation"]},
                "min_threshold": {"type": "integer", "minimum": 0, "maximum": 255},
                "max_threshold": {"type": "integer", "minimum": 0, "maximum": 255},
                "invert": {"type": "boolean"},
                "feather": {"type": "integer", "minimum": 0, "maximum": 20},
                "edge_shrink": {"type": "integer", "minimum": 0, "maximum": 20},
            },
            ("operation", "source_asset_ref"),
        ),
    ),
    FacadeToolSpec(
        "split_image",
        "Split image",
        (
            "Split one managed image. Use alpha_components or grid for deterministic local "
            "offline splitting without a prompt. Use element for semantic Provider breakdown "
            "with a managed prompt. Direct splitting is a valid one-step operation. Use boxsplit for a confirmed selection; if it is omitted, "
            "the desktop opens target extraction for the user. Do not claim a user selection "
            "was completed by the Agent."
        ),
        _object(
            {
                "source_asset_ref": _REF,
                "selection_ref": _REF,
                "prompt_asset_ref": _REF,
                "split_mode": {"enum": ["element", "boxsplit", "alpha_components", "grid"]},
                "columns": {"type": "integer", "minimum": 1, "maximum": 64},
                "rows": {"type": "integer", "minimum": 1, "maximum": 64},
                "alpha_threshold": {"type": "integer", "minimum": 0, "maximum": 255},
                "min_area": {"type": "integer", "minimum": 1, "maximum": 100000000},
                "padding": {"type": "integer", "minimum": 0, "maximum": 256},
                "max_outputs": {"type": "integer", "minimum": 1, "maximum": 256},
            },
            ("source_asset_ref", "split_mode"),
        ),
    ),
    FacadeToolSpec(
        "prepare_multiview",
        "Prepare multiview",
        (
            "Create a front-side-back sheet, request persisted user crop confirmation, or inspect or repair a managed multiview set. Use create "
            "from one source image; its result is a sheet asset, not a multiview-set reference. "
            "Use detect_regions only when the user explicitly asks for experimental automatic "
            "region detection on an existing persisted multiview set that has no saved front, "
            "side, and back crops, and "
            "request_region_confirmation with the generated sheet asset to open the desktop and pause until three persisted crops are returned. Use "
            "regenerate_view for exactly one view. Region confirmation remains a user action; "
            "a separate quality checkbox is not required. A confirmed set with distinct front, side, and back crop assets must "
            "go directly to 3D generation without detection. Do not use it to generate a 3D model."
        ),
        _object(
            {
                "operation": {"enum": ["create", "request_region_confirmation", "detect_regions", "regenerate_view"]},
                "source_asset_ref": _REF,
                "prompt_asset_ref": _REF,
                "multiview_ref": _REF,
                "target_view": {"enum": ["front", "side", "back"]},
            },
            ("operation",),
        ),
    ),
    FacadeToolSpec(
        "generate_model3d",
        "Generate 3D model",
        (
            "Generate one managed 3D model from exactly one suitable image or one confirmed "
            "front-side-back multiview set. The selected backend determines material output: "
            "local TripoSR produces vertex colors without PBR maps, while remote backends may "
            "produce textures and PBR. Paid external execution requires parameter-bound approval. "
            "For multiview mode, multiview_ref is the persisted set_id from the workspace "
            "summary, never the source sheet asset reference; view_asset_refs are the three "
            "distinct confirmed crop asset references. "
            "Do not inspect, preview, convert, optimize, package, import, or export models "
            "with this tool."
        ),
        _object(
            {
                "mode": {"enum": ["image", "multiview"]},
                "image_asset_ref": _REF,
                "multiview_ref": _REF,
                "view_asset_refs": _object(
                    {"front": _REF, "side": _REF, "back": _REF},
                    ("front", "side", "back"),
                ),
                "parameters": _MODEL_PARAMETERS,
            },
            ("mode", "parameters"),
        ),
    ),
    FacadeToolSpec(
        "process_model3d",
        "Process 3D model",
        (
            "Process existing managed 3D assets. Use inspect for local inspection, "
            "open_preview for desktop handoff, convert for GLB to FBX, optimize for local "
            "geometry reduction, and package for a managed delivery package. Do not import "
            "local files, generate models, or export to arbitrary paths."
        ),
        _object(
            {
                "operation": {
                    "enum": ["inspect", "open_preview", "convert", "optimize", "package"]
                },
                "asset_refs": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 32,
                    "uniqueItems": True,
                    "items": _REF,
                },
                "target_format": {"const": "fbx"},
                "target_triangles": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10_000_000,
                },
                "max_texture_bytes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 209_715_200,
                },
            },
            ("operation", "asset_refs"),
        ),
    ),
    FacadeToolSpec(
        "control_job",
        "Control job",
        (
            "Read or control one known durable job. Use status only when the user asks for "
            "progress and no fresh event is in context. Use cancel, retry, or "
            "confirm_new_submission only for explicit user intent. The confirmation action is "
            "only for an interrupted paid Job with unknown submission state. Never poll "
            "repeatedly. Do not invent a job reference."
        ),
        _object(
            {
                "action": {
                    "enum": ["status", "cancel", "retry", "confirm_new_submission"]
                },
                "job_ref": _REF,
            },
            ("action", "job_ref"),
        ),
    ),
)

_PARAMETER_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "inspect_workspace": {
        "view": "Required query to run: summary=current project and persisted workflow state (including confirmed multiview crops); assets=managed assets; asset_details=one asset; compare=exactly two assets; jobs=one known job; capabilities=current non-secret runtime readiness.",
        "asset_refs": "Opaque managed asset references. Required only for asset_details (exactly one) or compare (exactly two); omit for every other view.",
        "job_ref": "Opaque durable job reference. Required only when view is jobs; omit otherwise.",
        "group": "Optional exact managed asset group filter. Valid only when view is assets. Use generated_images (plural) for generated pictures.",
    },
    "select_asset": {
        "asset_ref": "Opaque reference of the one managed asset that should become current.",
        "reason": "Brief factual reason this choice is unambiguous; state the user's selection or the single-result workflow fact.",
    },
    "analyze_image": {
        "source_asset_ref": "Opaque reference of the managed source image to analyze.",
        "analysis_type": "Exactly one analysis purpose: visible content, visual style, or suitability for 3D generation.",
        "refresh": "Set true only when the user explicitly asks to replace/re-run an existing matching analysis; otherwise omit or false.",
    },
    "understand_image": {
        "source_asset_ref": "Opaque reference of the managed image the text-only Agent must understand.",
        "question": "A concrete visual question to answer from this image. The result is plain text for Agent reasoning only; it is not saved as a content/style analysis.",
    },
    "generate_images": {
        "mode": "Generation route: from_prompt has no source image; from_image transforms one source; variants creates alternatives of one source.",
        "prompt": "Write the complete generation instruction directly. The desktop stores it as an immutable managed Prompt and shows it with the generated images in the image-generation workspace. Do not call a separate prompt-preparation tool.",
        "prompt_asset_ref": "Existing managed prompt reference. Use only when intentionally reusing a user-authored or previously generated Prompt; otherwise write prompt directly.",
        "source_asset_ref": "Required for from_image and variants; forbidden for from_prompt. Opaque reference of one managed image.",
        "candidate_count": "Number of candidates to create in this approved request: 1, 2, or 4.",
        "aspect_ratio": "Optional provider-supported aspect ratio such as 1:1, 16:9, or 9:16.",
        "size": "Optional provider-supported output size label.",
        "quality": "Optional provider-supported output quality label.",
        "output_format": "Optional managed image encoding: png, jpg, or webp.",
        "structure_strength": "Optional source-structure adherence from 0 (loose) through 1 (strict); only meaningful with a source image.",
        "seed": "Optional deterministic generation seed from 0 through 2147483647.",
        "steps": "Optional local denoising step count from 1 through 20.",
    },
    "edit_image": {
        "operation": "Exactly one edit. Names ending in _local and trim_transparent/normalize are offline; the original upscale/remove_background/inpaint/export_transparent routes remain Provider operations.",
        "source_asset_ref": "Opaque reference of the one managed image to edit.",
        "selection_ref": "Required only for inpaint: opaque reference of a user-confirmed selection on the source image.",
        "prompt_asset_ref": "Required only for inpaint: opaque reference of the managed edit prompt.",
        "scale": "Required for upscale or upscale_local: integer scale factor 2 or 4.",
        "padding": "Optional only for trim_transparent: transparent pixels retained around detected content.",
        "alpha_threshold": "Optional only for trim_transparent: alpha values at or below this value count as transparent.",
        "background_method": "Required for remove_background_local: use color_key only for a flat keyed background and channel only when one channel/luminance/saturation range separates foreground. For gradients, shadows, texture, or foreground-like background colors use Provider remove_background instead.",
        "target_color": "Optional only for local color_key: exact RGB triplet. Omit to derive the background from image corners.",
        "target_width": "Optional only for normalize: positive target width.",
        "target_height": "Optional only for normalize: positive target height.",
        "max_long_edge": "Optional only for normalize: cap the final longest edge while preserving aspect ratio.",
        "lock_aspect_ratio": "Optional only for normalize; defaults true.",
        "rotate_degrees": "Optional only for normalize: clockwise rotation of 0, 90, 180, or 270 degrees.",
        "flip": "Optional only for normalize: none, horizontal, or vertical.",
        "output_format": "Optional only for normalize: png, jpeg, or webp.",
        "quality": "Optional only for normalize JPEG/WebP encoding, from 1 through 100.",
        "preserve_alpha": "Optional only for normalize PNG/WebP; defaults true. JPEG always flattens on white.",
        "tolerance": "Optional only for local color_key: RGB distance tolerance from 0 through 255.",
        "contiguous_only": "Optional only for local color_key; when true remove matching background connected to image edges.",
        "channel": "Optional only for local channel matting: red, green, blue, luminance, or saturation.",
        "min_threshold": "Optional only for local channel matting: inclusive lower kept channel value.",
        "max_threshold": "Optional only for local channel matting: inclusive upper kept channel value.",
        "invert": "Optional only for local channel matting: invert the generated opacity.",
        "feather": "Optional only for remove_background_local: blur alpha edges by 0 through 20 pixels.",
        "edge_shrink": "Optional only for remove_background_local: shrink opaque edges by 0 through 20 pixels.",
    },
    "split_image": {
        "source_asset_ref": "Opaque reference of the managed source image.",
        "selection_ref": "Optional for boxsplit: opaque reference of a user-confirmed selection belonging to the source image. If omitted, the desktop opens target extraction for the user. Omit for element.",
        "prompt_asset_ref": "Required only for element or boxsplit: opaque reference of the managed prompt describing the element(s) to extract.",
        "split_mode": "alpha_components and grid are local offline modes; element performs semantic Provider breakdown; boxsplit uses the already-confirmed rectangle.",
        "columns": "Required only for local grid splitting.",
        "rows": "Required only for local grid splitting.",
        "alpha_threshold": "Optional only for alpha_components: pixels above this alpha form components.",
        "min_area": "Optional only for alpha_components: discard smaller connected regions as noise.",
        "padding": "Optional only for alpha_components: transparent pixels retained around each component.",
        "max_outputs": "Optional local safety limit for the number of created managed assets.",
    },
    "prepare_multiview": {
        "operation": "create generates a multiview sheet; request_region_confirmation opens the desktop confirmation flow for that sheet; detect_regions analyzes an existing set; regenerate_view replaces exactly one view.",
        "source_asset_ref": "Required for create (modeling source image) and request_region_confirmation (generated sheet asset).",
        "prompt_asset_ref": "Optional only for create: opaque reference of a managed multiview prompt.",
        "multiview_ref": "Required for detect_regions and regenerate_view: opaque reference of the existing managed multiview set.",
        "target_view": "Required only for regenerate_view: the one front, side, or back view to replace.",
    },
    "generate_model3d": {
        "mode": "Input mode: image uses one image_asset_ref; multiview uses one confirmed multiview_ref plus all three view_asset_refs.",
        "image_asset_ref": "Required only for image mode: opaque reference of one suitable managed image.",
        "multiview_ref": "Required only for multiview mode: the confirmed set_id exposed by the persisted workspace summary. This is not the source sheet asset reference and not any crop asset reference.",
        "view_asset_refs": "Required only for multiview mode: exact front, side, and back managed image references from that set.",
        "parameters": "Complete parameter object to bind into user approval. Use an empty object to accept application defaults.",
    },
    "process_model3d": {
        "operation": "inspect checks a model; open_preview hands off to the desktop preview; convert creates FBX; optimize reduces a model; package bundles managed assets.",
        "asset_refs": "Opaque managed asset references. Package accepts 1-32 related assets; every other operation requires exactly one model asset.",
        "target_format": "Required only for convert and must be fbx; omit otherwise.",
        "target_triangles": "Optional only for optimize: desired positive triangle count.",
        "max_texture_bytes": "Optional only for optimize: maximum total texture bytes.",
    },
    "control_job": {
        "action": "status reads once; cancel requests cancellation; retry creates a safe retry when allowed; confirm_new_submission starts the separately approved recovery path for an unknown paid submission.",
        "job_ref": "Opaque reference of an existing durable job returned by a prior Tool result or runtime event; never invent it.",
    },
}

_MODEL_PARAMETER_DESCRIPTIONS = {
    "model_version": "Optional Provider model-version parameter; omit to use the application-approved default.",
    "texture_quality": "Requested texture quality.",
    "geometry_quality": "Requested geometry detail.",
    "texture_alignment": "Align texture to the source image or generated geometry.",
    "texture": "Request textured/material color output when the selected backend supports it.",
    "pbr": "Request PBR maps when supported; local TripoSR overrides this to false and uses vertex colors.",
    "quad": "Whether to request quad topology.",
    "face_limit": "Maximum face count. Default to 100,000; use 50,000 for real-time/game use only with smart_low_poly=false. When smart_low_poly=true, use 500-20,000 for triangle output or 500-10,000 with quad=true. Never use zero or an unlimited Provider budget.",
    "auto_size": "Whether the Provider should determine model scale automatically.",
    "orientation": "Use Provider default orientation or align to the input image.",
    "smart_low_poly": "Use clean Smart Low-poly topology only with a compatible explicit face_limit: 500-20,000 for triangles or 500-10,000 with quad=true. For a 50,000-face game model set this to false.",
    "generate_parts": "Whether to request separable model parts.",
    "compress": "Optional Provider compression mode; empty means no explicit compression.",
    "enable_image_autofix": "Whether the Provider may repair the input image before generation.",
    "model_seed": "Optional deterministic geometry seed.",
    "texture_seed": "Optional deterministic texture seed.",
}


def _with_parameter_descriptions(spec: FacadeToolSpec) -> FacadeToolSpec:
    schema = dict(spec.parameters)
    raw_properties = schema.get("properties", {})
    if not isinstance(raw_properties, Mapping):
        raise TypeError(f"{spec.name} properties must be an object.")
    properties: dict[str, dict[str, Any]] = {
        str(name): dict(cast(Mapping[str, object], value))
        for name, value in raw_properties.items()
        if isinstance(value, Mapping)
    }
    for name, description in _PARAMETER_DESCRIPTIONS[spec.name].items():
        properties[name]["description"] = description
    properties["plan_step_id"] = {
        "type": "string",
        "minLength": 1,
        "maxLength": 80,
        "description": (
            "Optional exact id of the current AI-authored Plan step this Tool call advances. "
            "It updates display progress only and never grants or blocks Tool permission."
        ),
    }
    if spec.name == "generate_model3d":
        parameters = dict(properties["parameters"])
        raw_parameters = parameters.get("properties", {})
        if not isinstance(raw_parameters, Mapping):
            raise TypeError("generate_model3d parameters must be an object.")
        nested = {
            str(name): {
                **dict(cast(Mapping[str, object], value)),
                "description": _MODEL_PARAMETER_DESCRIPTIONS[str(name)],
            }
            for name, value in raw_parameters.items()
            if isinstance(value, Mapping)
        }
        parameters["properties"] = nested
        properties["parameters"] = parameters
        view_refs = dict(properties["view_asset_refs"])
        raw_view_refs = view_refs.get("properties", {})
        if not isinstance(raw_view_refs, Mapping):
            raise TypeError("generate_model3d view_asset_refs must be an object.")
        view_refs["properties"] = {
            str(name): {
                **dict(cast(Mapping[str, object], value)),
                "description": f"Opaque managed asset reference for the {name} view.",
            }
            for name, value in raw_view_refs.items()
            if isinstance(value, Mapping)
        }
        properties["view_asset_refs"] = view_refs
    schema["properties"] = properties
    description = (
        spec.description
        + " When this call advances the current execution Plan, pass that step's exact "
        "plan_step_id; omit it for unplanned support work."
    )
    return FacadeToolSpec(spec.name, spec.label, description, schema)


FACADE_TOOL_SPECS = tuple(
    _with_parameter_descriptions(spec) for spec in FACADE_TOOL_SPECS
)


def _newest_assets_first(result: ToolResultV1) -> ToolResultV1:
    """Give the model deterministic recency semantics without changing desktop asset order."""
    try:
        assets = json.loads(result.summary)
    except json.JSONDecodeError:
        return result
    if not isinstance(assets, list) or not all(isinstance(item, dict) for item in assets):
        return result
    ordered = sorted(
        assets,
        key=lambda item: (
            str(item.get("created_at") or ""),
            str(item.get("id") or ""),
        ),
        reverse=True,
    )
    return replace(
        result,
        summary=json.dumps(ordered, ensure_ascii=False, separators=(",", ":")),
        output_asset_ids=[
            str(item["id"])
            for item in ordered
            if isinstance(item.get("id"), str)
        ],
    )


class AIPicFacadeTool:
    """One fixed model-facing facade backed by the canonical AIPic registry."""

    execution_mode: Literal["sequential"] = "sequential"

    def __init__(
        self,
        registry: AIPicToolRegistry,
        spec: FacadeToolSpec,
        invocation: Callable[[], AIPicToolInvocation],
        runtime_context: RuntimeContext | None = None,
        prompt_creator: PromptCreator | None = None,
        job_completion_broker: Any | None = None,
    ) -> None:
        self._dispatcher = _FacadeDispatcher(
            registry, invocation, runtime_context, prompt_creator, job_completion_broker
        )
        self.name = spec.name
        self.label = spec.label
        self.description = spec.description
        self.parameters = spec.parameters

    async def execute(
        self,
        tool_call_id: str,
        arguments: dict[str, object],
        context: ToolContext,
        cancellation: CancellationToken,
        on_update: ToolUpdateCallback | None = None,
    ) -> ToolResult:
        del context
        cancellation.raise_if_cancelled()
        result = await self._dispatcher.execute(
            self.name, tool_call_id, dict(arguments), cancellation
        )
        if on_update is not None:
            update = on_update(result)
            if update is not None:
                await update
        return result


class _FacadeDispatcher:
    def __init__(
        self,
        registry: AIPicToolRegistry,
        invocation: Callable[[], AIPicToolInvocation],
        runtime_context: RuntimeContext | None = None,
        prompt_creator: PromptCreator | None = None,
        job_completion_broker: Any | None = None,
    ) -> None:
        self._registry = registry
        self._invocation = invocation
        self._runtime_context = runtime_context
        self._prompt_creator = prompt_creator
        self._job_completion_broker = job_completion_broker

    async def execute(
        self,
        facade_name: str,
        tool_call_id: str,
        arguments: dict[str, object],
        cancellation: CancellationToken,
    ) -> ToolResult:
        try:
            materialized_prompt_id: str | None = None
            prompt = arguments.get("prompt")
            if prompt is not None:
                if not isinstance(prompt, str) or not prompt.strip():
                    raise ValueError("prompt must be a non-empty string.")
                if facade_name != "generate_images":
                    raise ValueError("Direct prompt text is supported only for generate_images.")
                if arguments.get("prompt_asset_ref"):
                    raise ValueError("Use prompt or prompt_asset_ref, not both.")
                if self._prompt_creator is None:
                    raise ValueError("Direct prompt generation is unavailable.")
                invocation = self._invocation()
                materialized_prompt_id = await cancellation.wait_for(
                    asyncio.to_thread(
                        self._prompt_creator,
                        invocation,
                        prompt.strip(),
                        _tool_request_id(invocation.request_id, tool_call_id),
                    )
                )
                arguments = {**arguments, "prompt_asset_ref": materialized_prompt_id}
            if facade_name == "prepare_multiview" and arguments.get("operation") == "create":
                await self._require_image_asset(
                    _required_str(arguments, "source_asset_ref"),
                    tool_call_id,
                    cancellation,
                )
            if facade_name == "generate_model3d" and arguments.get("mode") == "image":
                await self._require_image_asset(
                    _required_str(arguments, "image_asset_ref"),
                    tool_call_id,
                    cancellation,
                )
            internal_name, internal_arguments = self._translate(facade_name, arguments)
            if internal_name == "__facade_capabilities__":
                payload = dict(self._runtime_context()) if self._runtime_context else {
                    "schema_version": 1,
                    "capabilities": {},
                    "configuration_state": "unavailable",
                }
                summary = "Current non-secret runtime capabilities were inspected."
                return ToolResult(
                    (
                        TextContent(
                            json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                        ),
                    ),
                    details=cast(
                        JsonValue,
                        {
                            "schema_version": 1,
                            "ok": True,
                            "status": "succeeded",
                            "tool_call_id": tool_call_id,
                            "summary": summary,
                            "data": payload,
                            "output_asset_ids": [],
                            "output_refs": [],
                            "warnings": [],
                            "retry": {
                                "allowed": False,
                                "automatic": False,
                                "requires_approval": False,
                                "after_seconds": None,
                                "reason": None,
                            },
                            "reused": False,
                        },
                    ),
                )
            result = await self._call(
                internal_name,
                internal_arguments,
                tool_call_id,
                cancellation,
            )
            if result.status == "queued" and self._job_completion_broker is not None:
                job = result.job or {}
                job_id = job.get("job_id")
                if isinstance(job_id, str) and job_id:
                    invocation = self._invocation()
                    terminal = await cancellation.wait_for(
                        self._job_completion_broker.wait_for_terminal(
                            invocation.root / "project.sqlite3", job_id, timeout_seconds=180.0
                        )
                    )
                    if terminal is None:
                        return _waiting_external_agent_result(tool_call_id, job_id)
                    return _terminal_job_agent_result(terminal, tool_call_id)
            if facade_name == "inspect_workspace" and arguments.get("view") == "assets":
                result = _newest_assets_first(result)
            return _facade_agent_result(
                result,
                tool_call_id,
                prompt_asset_id=materialized_prompt_id,
                source_tool=facade_name,
            )
        except DomainErrorV1 as error:
            payload = error.as_dict()
            summary = str(payload.get("user_message") or "Tool execution failed.")
            return ToolResult(
                (TextContent(summary),),
                details={
                    "schema_version": 1,
                    "ok": False,
                    "status": "failed",
                    "tool_call_id": tool_call_id,
                    "summary": summary,
                    "data": {},
                    "output_asset_ids": [],
                    "output_refs": [],
                    "warnings": [],
                    "error": payload,
                    "retry": {
                        "allowed": bool(
                            payload.get("recoverable", False)
                            and payload.get("safe_to_retry", False)
                        ),
                        "automatic": False,
                        "requires_approval": False,
                        "after_seconds": payload.get("retry_after_seconds"),
                        "reason": payload.get("recommended_action"),
                    },
                    "reused": False,
                },
                is_error=True,
            )
        except (KeyError, TypeError, ValueError) as error:
            summary = str(error)
            payload = {
                "code": "TOOL_ARGUMENT_INVALID",
                "category": "validation",
                "user_message": summary,
                "recoverable": True,
                "safe_to_retry": False,
            }
            return ToolResult(
                (TextContent(summary),),
                details={
                    "schema_version": 1,
                    "ok": False,
                    "status": "failed",
                    "tool_call_id": tool_call_id,
                    "summary": summary,
                    "data": {},
                    "output_asset_ids": [],
                    "output_refs": [],
                    "warnings": [],
                    "error": payload,
                    "retry": {
                        "allowed": False,
                        "automatic": False,
                        "requires_approval": False,
                        "after_seconds": None,
                        "reason": None,
                    },
                    "reused": False,
                },
                is_error=True,
            )

    async def _call(
        self,
        name: str,
        arguments: dict[str, Any],
        tool_call_id: str,
        cancellation: CancellationToken,
    ) -> ToolResultV1:
        invocation = self._invocation()
        request_id = _tool_request_id(invocation.request_id, tool_call_id)
        return await cancellation.wait_for(
            asyncio.to_thread(
                self._registry.execute,
                invocation.root,
                invocation.project_id,
                name,
                "1.0.0",
                arguments,
                request_id,
                invocation.run_id,
                invocation.round_index,
                arguments.get("provider_profile"),
            )
        )

    async def _require_image_asset(
        self,
        asset_ref: str,
        tool_call_id: str,
        cancellation: CancellationToken,
    ) -> None:
        """Reject non-image inputs before generation policy or approval runs."""

        metadata = await self._call(
            "asset.get_metadata",
            {"asset_id": asset_ref},
            f"{tool_call_id}-input-metadata",
            cancellation,
        )
        if not metadata.ok:
            raise ValueError("The source asset could not be inspected as an image.")
        try:
            payload = json.loads(metadata.summary)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("The source asset metadata is invalid.") from error
        asset = payload.get("asset") if isinstance(payload, dict) else None
        asset_type = asset.get("asset_type") if isinstance(asset, dict) else None
        mime_type = asset.get("mime_type") if isinstance(asset, dict) else None
        if asset_type not in {
            "source_image",
            "generated_image",
            "annotation",
            "crop",
            "multiview",
        } or not isinstance(mime_type, str) or not mime_type.startswith("image/"):
            raise ValueError(
                "The source asset must be a managed image, not a prompt, model, or other asset."
            )

    def _translate(
        self, facade_name: str, arguments: dict[str, object]
    ) -> tuple[str, dict[str, Any]]:
        if facade_name == "inspect_workspace":
            return self._inspect(arguments)
        if facade_name == "select_asset":
            return (
                "asset.set_current",
                {
                    "asset_id": _required_str(arguments, "asset_ref"),
                    "decision_source": "agent",
                    "reason": _required_str(arguments, "reason"),
                },
            )
        if facade_name == "analyze_image":
            analysis_type = _required_str(arguments, "analysis_type")
            name = {
                "content": "image.analyze_content",
                "style": "image.analyze_style",
                "3d_suitability": "image.evaluate_3d_suitability",
            }[analysis_type]
            payload: dict[str, Any] = {
                "asset_id": _required_str(arguments, "source_asset_ref"),
                "provider_profile": _GEMINI_PROFILE,
                "model": _GEMINI_MODEL,
            }
            if bool(arguments.get("refresh")) and analysis_type in {"content", "style"}:
                payload["analysis_revision"] = "user-requested-refresh"
            return name, payload
        if facade_name == "understand_image":
            return (
                "image.understand_for_agent",
                {
                    "asset_id": _required_str(arguments, "source_asset_ref"),
                    "question": _required_str(arguments, "question"),
                    "provider_profile": _GEMINI_PROFILE,
                    "model": _GEMINI_MODEL,
                },
            )
        if facade_name == "prepare_prompt":
            return self._prepare_prompt(arguments)
        if facade_name == "generate_images":
            return self._generate_images(arguments)
        if facade_name == "edit_image":
            return self._edit_image(arguments)
        if facade_name == "split_image":
            split_mode = _required_str(arguments, "split_mode")
            source_asset_id = _required_str(arguments, "source_asset_ref")
            if split_mode in {"alpha_components", "grid"}:
                payload: dict[str, Any] = {
                    "source_asset_id": source_asset_id,
                    "mode": split_mode,
                }
                for key in (
                    "columns",
                    "rows",
                    "alpha_threshold",
                    "min_area",
                    "padding",
                    "max_outputs",
                ):
                    if key in arguments:
                        payload[key] = arguments[key]
                if split_mode == "grid":
                    _required_int(arguments, "columns")
                    _required_int(arguments, "rows")
                return "image.split_local", payload
            if split_mode == "boxsplit" and not arguments.get("selection_ref"):
                return "selection.request_user", {"asset_id": source_asset_id}
            payload = {
                "source_asset_id": source_asset_id,
                "prompt_asset_id": _required_str(arguments, "prompt_asset_ref"),
                "provider_profile": _AUTO_IMAGE_PROFILE,
                "channel": "auto",
                "model": _AUTO_IMAGE_MODEL,
                "split_mode": split_mode,
            }
            if split_mode == "boxsplit":
                payload["selection_id"] = _required_str(arguments, "selection_ref")
            return (
                "element.split",
                payload,
            )
        if facade_name == "prepare_multiview":
            return self._prepare_multiview(arguments)
        if facade_name == "generate_model3d":
            return self._generate_model3d(arguments)
        if facade_name == "process_model3d":
            return self._process_model3d(arguments)
        if facade_name == "control_job":
            action = _required_str(arguments, "action")
            return (
                {
                    "status": "job.get_status",
                    "cancel": "job.cancel",
                    "retry": "job.retry",
                    "confirm_new_submission": "job.confirm_new_submission",
                }[action],
                {"job_id": _required_str(arguments, "job_ref")},
            )
        raise ValueError(f"Unknown facade tool: {facade_name}")

    def _inspect(self, arguments: dict[str, object]) -> tuple[str, dict[str, Any]]:
        invocation = self._invocation()
        view = _required_str(arguments, "view")
        if view == "summary":
            return "project.get_state", {"project_id": invocation.project_id}
        if view == "assets":
            payload: dict[str, Any] = {"project_id": invocation.project_id}
            if "group" in arguments:
                payload["group"] = _required_str(arguments, "group")
            return "asset.list", payload
        if view == "asset_details":
            refs = _refs(arguments, "asset_refs", exact=1)
            return "asset.get_metadata", {"asset_id": refs[0]}
        if view == "compare":
            refs = _refs(arguments, "asset_refs", exact=2)
            return "asset.compare", {"left_id": refs[0], "right_id": refs[1]}
        if view == "jobs":
            return "job.get_status", {"job_id": _required_str(arguments, "job_ref")}
        if view == "capabilities":
            return "__facade_capabilities__", {}
        raise ValueError("Unsupported inspect_workspace view.")

    @staticmethod
    def _prepare_prompt(arguments: dict[str, object]) -> tuple[str, dict[str, Any]]:
        task = _required_str(arguments, "task")
        if task == "extract":
            return (
                "prompt.extract_bilingual",
                {
                    "analysis_asset_id": _required_str(arguments, "analysis_asset_ref"),
                    "kind": _required_str(arguments, "analysis_kind"),
                },
            )
        if task == "merge":
            return (
                "prompt.merge",
                {
                    "content_prompt_asset_id": _required_str(
                        arguments, "content_prompt_ref"
                    ),
                    "style_prompt_asset_id": _required_str(arguments, "style_prompt_ref"),
                },
            )
        if task == "rewrite":
            return (
                "prompt.rewrite",
                {
                    "prompt_asset_id": _required_str(arguments, "prompt_asset_ref"),
                    "provider_profile": _GEMINI_PROFILE,
                    "model": _GEMINI_MODEL,
                    "instruction": _required_str(arguments, "instruction"),
                },
            )
        if task == "validate":
            return (
                "prompt.validate",
                {"prompt_asset_id": _required_str(arguments, "prompt_asset_ref")},
            )
        raise ValueError("Unsupported prepare_prompt task.")

    @staticmethod
    def _generate_images(arguments: dict[str, object]) -> tuple[str, dict[str, Any]]:
        mode = _required_str(arguments, "mode")
        name = {
            "from_prompt": "image.generate",
            "from_image": "image.transform",
            "variants": "image.generate_variants",
        }[mode]
        payload: dict[str, Any] = {
            "prompt_asset_id": _required_str(arguments, "prompt_asset_ref"),
            "provider_profile": _AUTO_IMAGE_PROFILE,
            "channel": "auto",
            "model": _AUTO_IMAGE_MODEL,
            "candidate_count": _required_int(arguments, "candidate_count"),
        }
        if mode != "from_prompt":
            payload["source_asset_id"] = _required_str(arguments, "source_asset_ref")
        elif "source_asset_ref" in arguments:
            raise ValueError("from_prompt does not accept source_asset_ref.")
        for source, target in (
            ("aspect_ratio", "aspect_ratio"),
            ("size", "size"),
            ("quality", "quality"),
            ("output_format", "output_format"),
            ("structure_strength", "structure_strength"),
            ("seed", "seed"),
            ("steps", "steps"),
        ):
            if source in arguments:
                payload[target] = arguments[source]
        return name, payload

    @staticmethod
    def _edit_image(arguments: dict[str, object]) -> tuple[str, dict[str, Any]]:
        operation = _required_str(arguments, "operation")
        source = _required_str(arguments, "source_asset_ref")
        if operation == "trim_transparent":
            payload: dict[str, Any] = {"source_asset_id": source}
            for key in ("padding", "alpha_threshold"):
                if key in arguments:
                    payload[key] = arguments[key]
            return "image.trim_transparent", payload
        if operation == "normalize":
            payload = {"source_asset_id": source}
            for key in (
                "target_width",
                "target_height",
                "max_long_edge",
                "lock_aspect_ratio",
                "rotate_degrees",
                "flip",
                "output_format",
                "quality",
                "preserve_alpha",
            ):
                if key in arguments:
                    payload[key] = arguments[key]
            return "image.normalize", payload
        if operation == "remove_background_local":
            payload = {
                "source_asset_id": source,
                "method": _required_str(arguments, "background_method"),
            }
            for source_key, target_key in (
                ("target_color", "target_color"),
                ("tolerance", "tolerance"),
                ("contiguous_only", "contiguous_only"),
                ("channel", "channel"),
                ("min_threshold", "min_threshold"),
                ("max_threshold", "max_threshold"),
                ("invert", "invert"),
                ("feather", "feather"),
                ("edge_shrink", "edge_shrink"),
            ):
                if source_key in arguments:
                    payload[target_key] = arguments[source_key]
            return "image.remove_background_local", payload
        if operation == "upscale_local":
            return (
                "image.upscale_local",
                {
                    "source_asset_id": source,
                    "scale": _required_int(arguments, "scale"),
                },
            )
        if operation == "upscale":
            return (
                "image.upscale",
                {
                    "source_asset_id": source,
                    "provider_profile": _AUTO_IMAGE_PROFILE,
                    "scale": _required_int(arguments, "scale"),
                },
            )
        if operation == "remove_background":
            return (
                "image.remove_background",
                {"source_asset_id": source, "provider_profile": _AUTO_IMAGE_PROFILE},
            )
        if operation == "inpaint":
            return (
                "image.inpaint_selection",
                {
                    "source_asset_id": source,
                    "selection_id": _required_str(arguments, "selection_ref"),
                    "prompt_asset_id": _required_str(arguments, "prompt_asset_ref"),
                    "provider_profile": _AUTO_IMAGE_PROFILE,
                },
            )
        if operation == "export_transparent":
            return (
                "element.export_transparent",
                {"source_asset_id": source, "provider_profile": _AUTO_IMAGE_PROFILE},
            )
        raise ValueError("Unsupported edit_image operation.")

    @staticmethod
    def _prepare_multiview(
        arguments: dict[str, object],
    ) -> tuple[str, dict[str, Any]]:
        operation = _required_str(arguments, "operation")
        if operation == "create":
            payload: dict[str, Any] = {
                "source_asset_id": _required_str(arguments, "source_asset_ref"),
                "provider_profile": _AUTO_IMAGE_PROFILE,
                "channel": "auto",
                "model": _AUTO_IMAGE_MODEL,
            }
            if "prompt_asset_ref" in arguments:
                payload["prompt_asset_id"] = _required_str(arguments, "prompt_asset_ref")
            return "multiview.generate", payload
        if operation == "detect_regions":
            return (
                "multiview.detect_regions",
                {
                    "multiview_set_id": _required_str(arguments, "multiview_ref"),
                    "provider_profile": _GEMINI_PROFILE,
                    "model": _GEMINI_MODEL,
                },
            )
        if operation == "request_region_confirmation":
            return (
                "multiview.request_box_confirmation",
                {"multiview_set_id": _required_str(arguments, "source_asset_ref")},
            )
        if operation == "regenerate_view":
            return (
                "multiview.regenerate_view",
                {
                    "multiview_set_id": _required_str(arguments, "multiview_ref"),
                    "view": _required_str(arguments, "target_view"),
                    "provider_profile": _AUTO_IMAGE_PROFILE,
                    "channel": "auto",
                    "model": _AUTO_IMAGE_MODEL,
                },
            )
        raise ValueError("Unsupported prepare_multiview operation.")

    @staticmethod
    def _generate_model3d(
        arguments: dict[str, object],
    ) -> tuple[str, dict[str, Any]]:
        mode = _required_str(arguments, "mode")
        parameters = arguments.get("parameters")
        if not isinstance(parameters, dict):
            raise TypeError("parameters must be an object.")
        normalized_parameters = dict(parameters)
        # Safe remote defaults are requested here. The application generation
        # policy overrides backend-incompatible options (notably local TripoSR
        # PBR) before the request is persisted.
        normalized_parameters.setdefault("face_limit", 100_000)
        normalized_parameters.setdefault("texture", True)
        normalized_parameters.setdefault("pbr", True)
        payload: dict[str, Any] = {
            "mode": mode,
            "provider_profile": _TRIPO_PROFILE,
            "model": _TRIPO_MODEL,
            "parameters": normalized_parameters,
        }
        if mode == "image":
            payload["image_asset_id"] = _required_str(arguments, "image_asset_ref")
            if "multiview_ref" in arguments or "view_asset_refs" in arguments:
                raise ValueError("image mode does not accept multiview inputs.")
        elif mode == "multiview":
            payload["multiview_set_id"] = _required_str(arguments, "multiview_ref")
            refs = arguments.get("view_asset_refs")
            if not isinstance(refs, dict):
                raise ValueError("view_asset_refs must be an object.")
            payload["view_asset_ids"] = {
                key: _required_str(refs, key) for key in ("front", "side", "back")
            }
            if "image_asset_ref" in arguments:
                raise ValueError("multiview mode does not accept image_asset_ref.")
        else:
            raise ValueError("Unsupported generate_model3d mode.")
        return "model3d.generate", payload

    @staticmethod
    def _process_model3d(arguments: dict[str, object]) -> tuple[str, dict[str, Any]]:
        operation = _required_str(arguments, "operation")
        refs = _refs(arguments, "asset_refs")
        if operation == "package":
            if any(
                key in arguments
                for key in ("target_format", "target_triangles", "max_texture_bytes")
            ):
                raise ValueError("package does not accept conversion or optimization parameters.")
            return "model3d.package", {"asset_ids": refs}
        if len(refs) != 1:
            raise ValueError(f"{operation} requires exactly one asset_ref.")
        asset_id = refs[0]
        if operation == "inspect":
            return "model3d.inspect", {"asset_id": asset_id}
        if operation == "open_preview":
            return "model3d.render_preview", {"asset_id": asset_id}
        if operation == "convert":
            if arguments.get("target_format") != "fbx":
                raise ValueError("convert requires target_format=fbx.")
            return "model3d.convert", {"asset_id": asset_id, "target_format": "fbx"}
        if operation == "optimize":
            payload: dict[str, Any] = {"asset_id": asset_id}
            for key in ("target_triangles", "max_texture_bytes"):
                if key in arguments:
                    payload[key] = arguments[key]
            return "model3d.optimize", payload
        raise ValueError("Unsupported process_model3d operation.")


def facade_tools(
    registry: AIPicToolRegistry,
    invocation: Callable[[], AIPicToolInvocation],
    runtime_context: RuntimeContext | None = None,
    prompt_creator: PromptCreator | None = None,
    job_completion_broker: Any | None = None,
) -> tuple[AIPicFacadeTool, ...]:
    """Return the fixed business facades in documented order."""

    return tuple(
        AIPicFacadeTool(
            registry,
            spec,
            invocation,
            runtime_context,
            prompt_creator,
            job_completion_broker,
        )
        for spec in FACADE_TOOL_SPECS
    )

def _terminal_job_agent_result(job: Any, tool_call_id: str) -> ToolResult:
    status = getattr(getattr(job, "status", None), "value", getattr(job, "status", "failed"))
    output_asset_refs = list(getattr(job, "result_asset_ids", []))
    succeeded = status == "succeeded"
    error = getattr(job, "error", None)
    summary = (
        "The Job completed successfully."
        if succeeded
        else f"The Job ended with status {status}."
    )
    continuation = {
        "status": status,
        "output_asset_refs": output_asset_refs,
    }
    job_type = str(getattr(job, "job_type", ""))
    provider = str(getattr(job, "provider", ""))
    artifact_facts: dict[str, JsonValue] | None = None
    warnings: list[str] = []
    if succeeded and job_type == "model3d.generate":
        local = provider == "model3d/local/triposr"
        artifact_facts = {
            "file_created": bool(output_asset_refs),
            "semantic_match": "not_verified",
            "pbr": False if local else "backend_report_required",
            "material_mode": "vertex_color" if local else "backend_report_required",
        }
        warnings.append(
            "File creation succeeded, but subject identity, style, and localized image edits "
            "have not been visually verified."
        )
        continuation["artifact_facts"] = artifact_facts
    details: dict[str, JsonValue] = {
        "schema_version": 1,
        "ok": succeeded,
        "status": status,
        "tool_call_id": tool_call_id,
        "summary": summary,
        "data": {
            "output_asset_refs": output_asset_refs,
            **({"artifact_facts": artifact_facts} if artifact_facts is not None else {}),
        },
        "output_asset_ids": output_asset_refs,
        "output_refs": output_asset_refs,
        "warnings": warnings,
        "reused": False,
    }
    if artifact_facts is not None:
        details["artifact_facts"] = artifact_facts
    if not succeeded:
        details["error"] = cast(JsonValue, error or {"code": "JOB_NOT_SUCCEEDED"})
    return ToolResult(
        (TextContent(f"{summary}\nFacade result: {json.dumps(continuation, separators=(',', ':'))}"),),
        details=details,
        is_error=not succeeded,
    )


def _waiting_external_agent_result(tool_call_id: str, job_id: str) -> ToolResult:
    continuation = {
        "status": "waiting_external",
        "job_ref": job_id,
        "waited_seconds": 180,
        "output_asset_refs": [],
    }
    return ToolResult(
        (TextContent(f"The Job is still processing in the background.\nFacade result: {json.dumps(continuation, separators=(',', ':'))}"),),
        details=cast(
            JsonValue,
            {
                "schema_version": 1,
                "ok": True,
                "status": "waiting_external",
                "tool_call_id": tool_call_id,
                "summary": "The Job is still processing in the background.",
                "data": continuation,
                "output_asset_ids": [],
                "output_refs": [],
                "warnings": [],
                "reused": False,
            },
        ),
    )


def _required_str(arguments: Mapping[str, object], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string.")
    return value


def _required_int(arguments: Mapping[str, object], key: str) -> int:
    value = arguments.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{key} must be an integer.")
    return value


def _refs(
    arguments: Mapping[str, object], key: str, *, exact: int | None = None
) -> list[str]:
    value = arguments.get(key)
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{key} must be an array of non-empty strings.")
    if exact is not None and len(value) != exact:
        raise ValueError(f"{key} must contain exactly {exact} item(s).")
    return list(value)
