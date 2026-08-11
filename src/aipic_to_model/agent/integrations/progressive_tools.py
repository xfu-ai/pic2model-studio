"""Pi-style progressive disclosure for model-facing AIPic Tools.

The application registry remains the execution source of truth.  This module
only replaces the former multi-operation model facades with narrow, searchable
single-operation schemas and keeps the full catalog outside model context.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from ...application.tools import ToolRegistry as AIPicToolRegistry
from ..core.events import CancellationToken
from ..core.models import TextContent, ToolResult
from ..core.tool import (
    AgentTool,
    AgentToolCatalog,
    ToolContext,
    ToolUpdateCallback,
)
from ..planning import ExecutionPlan
from .aipic_tools import AIPicToolInvocation
from .facade_tools import (
    FACADE_TOOL_SPECS,
    PromptCreator,
    RuntimeContext,
    _FacadeDispatcher,
)

AGGREGATE_TOOL_NAMES = (
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

PERMANENT_TOOL_NAMES = (
    "read",
    "write",
    "edit",
    "bash",
    "toolbox.status",
    "toolbox.load",
    "project.get_state",
    "image.understand_for_agent",
    "image.remove_background_local",
    "model3d.generate_from_image",
)

_BUILTIN_TOOL_NAMES = ("read", "write", "edit", "bash")
_FACADE_SPECS = {spec.name: spec for spec in FACADE_TOOL_SPECS}


@dataclass(frozen=True)
class OperationToolSpec:
    name: str
    label: str
    description: str
    parameters: Mapping[str, object]
    dispatch_name: str
    fixed_arguments: Mapping[str, object]
    search_terms: tuple[str, ...]
    planner_operations: tuple[str, ...]


# Model-facing selection guidance belongs to the narrow operation, not to the
# aggregate dispatcher it happens to reuse.  The Chinese aliases are search
# metadata for toolbox.status; they are not sent as arguments or permissions.
_OPERATION_GUIDANCE: dict[str, tuple[str, tuple[str, ...]]] = {
    "project.get_state": (
        "Read current managed project and persisted workflow state only when no exact Tool Result already supplies the needed references. It does not replace a confirmed multiview set-and-crops binding. Do not use it for live Job progress or unsaved UI state.",
        ("project state", "workspace state", "项目状态", "工作区状态", "三视图状态"),
    ),
    "asset.list": (
        "List managed project assets, optionally by one exact group, newest first. Use it to discover an unknown asset reference. Do not poll it for Job completion or assume a listed asset is selected.",
        ("find assets", "browse assets", "资产列表", "查找资产", "浏览资产"),
    ),
    "asset.get_metadata": (
        "Read metadata and lineage for exactly one known managed asset. Use it after asset.list or to verify a Tool output. Do not use it as a project-wide search or pass more than one asset.",
        ("asset details", "lineage", "metadata", "资产详情", "资产元数据", "血缘"),
    ),
    "asset.compare": (
        "Compare exactly two known sibling asset versions and open the comparison workspace. Use it for version comparison. Do not use it for unrelated assets or semantic image analysis.",
        ("compare versions", "asset comparison", "对比资产", "版本比较", "候选比较"),
    ),
    "runtime.get_capabilities": (
        "Read non-secret runtime and provider capability readiness without changing state. Use it before an optional local or external capability when availability is uncertain. Do not use it as a Provider health poll.",
        ("capabilities", "availability", "runtime readiness", "能力状态", "可用能力", "运行时能力"),
    ),
    "asset.set_current": (
        "Set exactly one managed asset as current when the user selected it or a workflow produced one unambiguous result. Use it to record that decision. Do not choose among candidates for the user or use it merely to preview an asset.",
        ("select asset", "current asset", "选择资产", "设为当前", "当前资产"),
    ),
    "image.analyze_content": (
        "Create a persisted external Vision analysis of image subject, composition, and scene content. Use it only when a saved workflow analysis is requested or required downstream. Do not use it for ordinary visual questions; use image.understand_for_agent instead.",
        ("content analysis", "composition", "内容分析", "主体分析", "构图分析", "持久化分析"),
    ),
    "image.analyze_style": (
        "Create a persisted external Vision analysis of image style, palette, lighting, texture, and rendering language. Use it when a saved style analysis is needed. Do not use it for subject identity or ordinary visual questions.",
        ("style analysis", "palette", "lighting", "风格分析", "色板", "光照", "材质分析"),
    ),
    "image.evaluate_3d_suitability": (
        "Create a persisted external Vision assessment of one image's geometry visibility and 3D reconstruction suitability. Use it before single-image 3D generation when suitability is uncertain. Do not use it as a general quality score or after confirmed multiview crops exist.",
        ("3d suitability", "reconstruction", "3D适配", "三维适配", "重建评估", "几何可见性"),
    ),
    "image.understand_for_agent": (
        "Answer one grounded visual question through the configured external Vision provider without creating an analysis asset. Use it for ordinary Agent image understanding. Do not use it when the user explicitly needs a persisted content, style, or 3D-suitability analysis.",
        ("understand image", "visual question", "看图", "图片理解", "视觉问答", "识图"),
    ),
    "image.generate_from_prompt": (
        "Create managed image candidates from new prompt text; the text is first stored as a managed Prompt. Use it for text-to-image when no existing Prompt asset should be reused. Do not use it to transform an existing image. This is a paid external operation requiring parameter-bound approval.",
        ("text to image", "t2i", "文生图", "文字生成图片", "提示词生图"),
    ),
    "image.generate_from_prompt_asset": (
        "Create managed image candidates from one existing managed Prompt asset. Use it only when intentionally reusing that Prompt. Do not use it for new direct prompt text or reference-image transformation. This is a paid external operation requiring parameter-bound approval.",
        ("prompt asset generation", "reuse prompt", "复用提示词", "提示词资产生图"),
    ),
    "image.transform_from_reference": (
        "Transform one managed reference image using new or existing prompt text while retaining source-image conditioning. Use it for reference-guided image-to-image changes. Do not use it for pure text-to-image, repeated alternatives, or selected-area inpainting; use image.generate_variants or image.inpaint_selection where appropriate. This is a paid external operation requiring approval.",
        ("image to image", "i2i", "reference transform", "图生图", "参考图变换", "风格转换"),
    ),
    "image.generate_variants": (
        "Create multiple alternative candidates derived from one managed source image and a prompt. Use it when the user wants variations rather than one directed transformation. Do not use it for an identical retry, upscale, or selected-area edit. This is a paid external operation requiring approval.",
        ("variants", "alternatives", "变体", "生成变体", "候选方案", "多个版本"),
    ),
    "image.trim_transparent": (
        "Trim transparent outer bounds locally and synchronously, creating a new managed image with verification. Use it only when the source already has transparency. Do not use it for a user-selected crop or background removal.",
        ("trim alpha", "transparent bounds", "裁透明边", "去透明空边", "透明边界"),
    ),
    "image.normalize": (
        "Normalize image dimensions, orientation, encoding, and alpha locally and synchronously, creating a new managed image with verification. Use it for deterministic format preparation. Do not use it for semantic enhancement, upscaling, or background removal.",
        (
            "resize",
            "rotate",
            "convert image",
            "规范化图片",
            "调整尺寸",
            "改变图片尺寸",
            "修改分辨率",
            "缩放图片",
            "resize图片",
            "旋转图片",
            "转换格式",
        ),
    ),
    "image.remove_background_local": (
        "Remove a flat keyed or channel-separable background locally and synchronously, creating a verified managed transparent image. Use color_key for a uniform background and channel for a separable range; use image.remove_background_provider for complex natural backgrounds. Do not guess target_color; omit it to derive the color from corners.",
        ("local background removal", "color key", "chroma key", "本地去背景", "本地抠图", "色键", "绿幕"),
    ),
    "image.upscale_local": (
        "Upscale one managed image by 2x or 4x with the bundled offline model through a local durable Job. Use it when offline processing is preferred. Do not silently substitute image.upscale_provider when the local capability is unavailable.",
        (
            "local upscale",
            "offline super resolution",
            "本地放大",
            "放大图片",
            "超分放大",
            "离线超分",
            "本地超分",
        ),
    ),
    "image.upscale_provider": (
        "Upscale one managed image by 2x or 4x with the configured external Provider. Use it when the user requests Provider processing or the local model is unsuitable. Do not use it to change composition, style, or background.",
        ("provider upscale", "cloud super resolution", "云端放大", "Provider超分", "在线超分"),
    ),
    "image.remove_background_provider": (
        "Remove a complex image background with the configured external Provider and create a managed transparent result. Use it for gradients, shadows, hair, or textured backgrounds. Do not use it for a flat keyed background when image.remove_background_local is sufficient.",
        ("provider background removal", "complex background", "云端去背景", "复杂背景抠图", "在线抠图"),
    ),
    "image.inpaint_selection": (
        "Edit only one confirmed selection in a managed image using a managed Prompt and the configured external Provider. Use it for localized replacement or repair. Do not use it without a confirmed selection or for whole-image transformation. It requires parameter-bound approval.",
        ("inpaint", "selection edit", "局部重绘", "选区修补", "选区编辑"),
    ),
    "element.export_transparent": (
        "Create a transparent Provider result from an already extracted managed element. Use it after element extraction when transparent delivery is required. Do not use it as general background removal or before an extracted element exists.",
        ("transparent element", "element export", "透明元素", "导出透明元素", "元素透明图"),
    ),
    "image.split_alpha_components": (
        "Split disconnected alpha components locally and synchronously into managed images with verification. Use it when transparency already separates elements. Do not use it for an opaque scene, a regular grid, or semantic Provider extraction.",
        ("alpha components", "connected components", "透明组件拆分", "连通域拆分", "alpha拆图"),
    ),
    "image.split_grid": (
        "Split a verified regular image grid locally and synchronously using explicit rows and columns. Use it for sprite sheets or known tile layouts. Do not use it to guess an irregular layout or semantically identify elements.",
        ("grid split", "sprite sheet", "tiles", "网格拆分", "宫格切图", "精灵图拆分"),
    ),
    "selection.request_user": (
        "Open the desktop target-extraction workspace so the user can draw or adjust a rectangle for splitting. Use it only when a confirmed selection is missing. Do not claim the selection is complete or continue with element.split_selection before the user confirms it.",
        ("request selection", "draw box", "请求选区", "用户框选", "画框", "矩形选区"),
    ),
    "element.split_semantic": (
        "Extract image elements semantically with a managed Prompt and the configured external Provider. Use it when alpha components or a known grid cannot determine the elements. Do not use it for a simple crop or without a Prompt. This is a paid operation requiring approval.",
        ("semantic split", "element extraction", "语义拆分", "元素提取", "智能拆图"),
    ),
    "element.split_selection": (
        "Generate an extracted element from one user-confirmed selection using a managed Prompt and the configured external Provider. Use it after selection.request_user and user confirmation. Do not use it for a deterministic pixel crop; this is a paid operation requiring approval.",
        ("selection extraction", "box split", "选区提取", "框选拆分", "按框抽取"),
    ),
    "multiview.generate": (
        "Generate a managed front-side-back sheet from one source image through the configured external Provider. Use it to start a new multiview workflow. Do not use it when a complete sheet or confirmed crop set already exists. This is a paid operation requiring approval.",
        ("multiview generation", "front side back", "生成三视图", "正侧背", "三视图图纸"),
    ),
    "multiview.detect_regions": (
        "Run experimental external Vision detection of front, side, and back regions on one persisted multiview set. Use it only when the user explicitly requests detection and no confirmed crops exist. Do not call it after confirmed crops or automatically confirm its suggestions.",
        ("detect multiview regions", "three view boxes", "检测三视图区域", "三视图框", "自动框选"),
    ),
    "multiview.request_region_confirmation": (
        "Open the desktop multiview workspace for the user to adjust and confirm front, side, and back crop regions on one generated sheet. This Tool pauses until the desktop returns three distinct persisted confirmed crops. Use it after multiview.generate and before multiview 3D generation. A chat message saying 'confirm' is not a substitute for the persisted desktop result.",
        ("confirm multiview regions", "crop three views", "确认三视图区域", "三视图裁图", "确认正侧背"),
    ),
    "multiview.regenerate_view": (
        "Regenerate exactly one named front, side, or back view in an existing multiview set with the external Provider. Use it to repair one direction. Do not regenerate the whole sheet or call it without an existing set. This is a paid operation requiring approval.",
        ("repair multiview", "regenerate direction", "修复三视图", "重生成单视图", "修复正面", "修复侧面", "修复背面"),
    ),
    "model3d.generate_from_image": (
        "Generate one 3D model from exactly one suitable managed image, using a safe explicit face budget. For remote Smart Low-poly, use 500-20,000 faces for triangles or 500-10,000 with quad=true; a 50,000-face game model must set smart_low_poly=false. Material output is backend-dependent: local TripoSR uses vertex colors without PBR maps; a remote backend may provide textures/PBR and requires parameter-bound approval. Use it only when no confirmed multiview set is available. Do not use it for model inspection or post-processing.",
        ("image to 3d", "single image 3d", "单图生成3D", "图片转模型", "图生三维"),
    ),
    "model3d.generate_from_multiview": (
        "Generate one remote-backend 3D model from a persisted multiview set and its three distinct confirmed crop assets, using a safe explicit face budget. For Smart Low-poly, use 500-20,000 faces for triangles or 500-10,000 with quad=true; a 50,000-face game model must set smart_low_poly=false. Use it in preference to single-image generation when confirmed views exist. Do not substitute a sheet or crop asset ID for multiview_ref. TripoSR is single-image only; this paid route requires approval and its Tool Result is the source of truth for materials.",
        ("multiview to 3d", "three view 3d", "三视图生成3D", "三视图转模型", "正侧背建模"),
    ),
    "model3d.inspect": (
        "Inspect exactly one managed GLB locally for authenticity, geometry, materials, and capabilities, and return the structured report. Use it after generation or import. Do not use it as visual approval, preview capture, or Provider validation.",
        ("inspect model", "glb inspection", "检查模型", "模型检查", "GLB检查", "几何检查"),
    ),
    "model3d.render_preview": (
        "Request the desktop to open exactly one managed GLB and explicitly capture a preview image. Use it when a managed preview screenshot is required. Do not treat it as a background render Job or claim a preview exists before the UI action completes.",
        ("capture model preview", "3d screenshot", "捕获模型预览", "3D截图", "模型预览图"),
    ),
    "model3d.convert": (
        "Convert exactly one managed GLB to FBX through an approved local converter Job. Use it only when FBX delivery is requested. Do not use it for optimization, packaging, or remote generation.",
        ("glb to fbx", "model conversion", "GLB转FBX", "模型格式转换", "转FBX"),
    ),
    "model3d.optimize": (
        "Optimize exactly one managed 3D model locally through a durable Job, optionally reducing triangles and texture bytes. Use it for delivery or runtime budgets. Do not use it as format conversion and check runtime capabilities when availability is uncertain.",
        ("optimize model", "reduce polygons", "decimate", "模型优化", "减面", "降低面数", "压缩纹理"),
    ),
    "model3d.package": (
        "Create a managed delivery package from one or more completed 3D assets through a local Job. Use it for model delivery. Do not use it as a whole-project package export or before required model assets exist.",
        ("package model", "delivery package", "模型打包", "交付包", "3D资产打包"),
    ),
    "job.get_status": (
        "Read the persisted status of exactly one known durable Job. Use it once when the user asks for progress and no fresh terminal event is already in context. Do not poll repeatedly, sleep, or inspect asset lists to guess completion.",
        ("job status", "progress", "任务状态", "查看进度", "后台任务进度"),
    ),
    "job.cancel": (
        "Cancel or stop waiting for exactly one known non-terminal Job according to its persisted capability. Use it only for explicit user intent. Do not call it on a terminal Job or assume every Provider supports remote cancellation.",
        ("cancel job", "stop waiting", "取消任务", "停止等待", "终止任务"),
    ),
    "job.retry": (
        "Retry exactly one known failed or interrupted Job only when persisted state says retry is safe. Use it only for explicit user intent; paid retries require new approval. Do not retry a running Job or an unknown paid submission.",
        ("retry job", "restart task", "重试任务", "重新执行", "任务恢复"),
    ),
    "job.confirm_new_submission": (
        "Start the separately approved recovery path for exactly one interrupted paid Job whose submission state is unknown. Use it only after the user explicitly confirms a new submission. Do not call it in the same turn that first reports the interruption.",
        (
            "confirm new submission",
            "unknown submission",
            "new paid submission",
            "确认重新提交",
            "未知提交",
        ),
    ),
}


def _schema(
    facade_name: str,
    properties: tuple[str, ...],
    required: tuple[str, ...],
    *,
    one_of: tuple[tuple[str, ...], ...] = (),
    property_constraints: Mapping[str, Mapping[str, object]] | None = None,
) -> dict[str, object]:
    source = _FACADE_SPECS[facade_name].parameters
    source_properties = source.get("properties")
    assert isinstance(source_properties, Mapping)
    selected = {name: source_properties[name] for name in properties}
    for name, constraints in (property_constraints or {}).items():
        if name not in selected:
            raise ValueError(f"Cannot constrain unselected property {name!r}.")
        selected[name] = {
            **dict(selected[name]),
            **dict(constraints),
        }
    if "plan_step_id" in source_properties:
        selected["plan_step_id"] = source_properties["plan_step_id"]
    result: dict[str, object] = {
        "type": "object",
        "properties": selected,
        "additionalProperties": False,
    }
    if required:
        result["required"] = list(required)
    if one_of:
        result["oneOf"] = [{"required": list(names)} for names in one_of]
    return result


def _spec(
    name: str,
    label: str,
    facade_name: str,
    *,
    fixed: Mapping[str, object],
    properties: tuple[str, ...],
    required: tuple[str, ...],
    one_of: tuple[tuple[str, ...], ...] = (),
    property_constraints: Mapping[str, Mapping[str, object]] | None = None,
    planner_operations: tuple[str, ...] = (),
    note: str = "",
) -> OperationToolSpec:
    try:
        operation_description, search_terms = _OPERATION_GUIDANCE[name]
    except KeyError as error:
        raise ValueError(f"Missing model-facing guidance for {name}.") from error
    description = (
        f"{operation_description} Use exact managed references returned by prior Tool Results. "
        "Do not invent paths, provider profiles, approvals, credentials, or IDs."
    )
    if note:
        description = f"{description} {note}"
    return OperationToolSpec(
        name,
        label,
        description,
        _schema(
            facade_name,
            properties,
            required,
            one_of=one_of,
            property_constraints=property_constraints,
        ),
        facade_name,
        dict(fixed),
        search_terms,
        planner_operations,
    )


_GENERATION_OPTIONS = (
    "candidate_count",
    "aspect_ratio",
    "size",
    "quality",
    "output_format",
    "structure_strength",
    "seed",
    "steps",
)
_REFERENCE_GENERATION_OPTIONS = tuple(
    name for name in _GENERATION_OPTIONS if name not in {"seed", "steps"}
)
_NORMALIZE_OPTIONS = (
    "target_width",
    "target_height",
    "max_long_edge",
    "lock_aspect_ratio",
    "rotate_degrees",
    "flip",
    "output_format",
    "quality",
    "preserve_alpha",
)
_LOCAL_BACKGROUND_OPTIONS = (
    "background_method",
    "target_color",
    "tolerance",
    "contiguous_only",
    "channel",
    "min_threshold",
    "max_threshold",
    "invert",
    "feather",
    "edge_shrink",
)
_LOCAL_SPLIT_OPTIONS = (
    "alpha_threshold",
    "min_area",
    "padding",
    "max_outputs",
)

OPERATION_TOOL_SPECS = (
    _spec("project.get_state", "Read current project state", "inspect_workspace", fixed={"view": "summary"}, properties=(), required=()),
    _spec("asset.list", "List managed assets", "inspect_workspace", fixed={"view": "assets"}, properties=("group",), required=()),
    _spec("asset.get_metadata", "Inspect one managed asset", "inspect_workspace", fixed={"view": "asset_details"}, properties=("asset_refs",), required=("asset_refs",), property_constraints={"asset_refs": {"minItems": 1, "maxItems": 1}}, planner_operations=("verify_output",)),
    _spec("asset.compare", "Compare two managed assets", "inspect_workspace", fixed={"view": "compare"}, properties=("asset_refs",), required=("asset_refs",), property_constraints={"asset_refs": {"minItems": 2, "maxItems": 2}}),
    _spec("runtime.get_capabilities", "Read current runtime capabilities", "inspect_workspace", fixed={"view": "capabilities"}, properties=(), required=()),
    _spec("asset.set_current", "Set the current managed asset", "select_asset", fixed={}, properties=("asset_ref", "reason"), required=("asset_ref", "reason")),
    _spec("image.analyze_content", "Persist image content analysis", "analyze_image", fixed={"analysis_type": "content"}, properties=("source_asset_ref", "refresh"), required=("source_asset_ref",)),
    _spec("image.analyze_style", "Persist image style analysis", "analyze_image", fixed={"analysis_type": "style"}, properties=("source_asset_ref", "refresh"), required=("source_asset_ref",)),
    _spec("image.evaluate_3d_suitability", "Evaluate image suitability for 3D", "analyze_image", fixed={"analysis_type": "3d_suitability"}, properties=("source_asset_ref",), required=("source_asset_ref",)),
    _spec("image.understand_for_agent", "Answer one grounded image question", "understand_image", fixed={}, properties=("source_asset_ref", "question"), required=("source_asset_ref", "question"), planner_operations=("inspect_image",)),
    _spec("image.generate_from_prompt", "Generate images from prompt text", "generate_images", fixed={"mode": "from_prompt"}, properties=("prompt", *_GENERATION_OPTIONS), required=("prompt", "candidate_count"), planner_operations=("generate_image_from_prompt",)),
    _spec("image.generate_from_prompt_asset", "Generate images from a managed prompt", "generate_images", fixed={"mode": "from_prompt"}, properties=("prompt_asset_ref", *_GENERATION_OPTIONS), required=("prompt_asset_ref", "candidate_count"), planner_operations=("generate_image_from_prompt",)),
    _spec("image.transform_from_reference", "Transform one reference image", "generate_images", fixed={"mode": "from_image"}, properties=("source_asset_ref", "prompt", "prompt_asset_ref", *_REFERENCE_GENERATION_OPTIONS), required=("source_asset_ref", "candidate_count"), one_of=(("prompt",), ("prompt_asset_ref",)), planner_operations=("transform_from_reference",)),
    _spec("image.generate_variants", "Generate variants of one image", "generate_images", fixed={"mode": "variants"}, properties=("source_asset_ref", "prompt", "prompt_asset_ref", *_REFERENCE_GENERATION_OPTIONS), required=("source_asset_ref", "candidate_count"), one_of=(("prompt",), ("prompt_asset_ref",))),
    _spec("image.trim_transparent", "Trim transparent image bounds", "edit_image", fixed={"operation": "trim_transparent"}, properties=("source_asset_ref", "padding", "alpha_threshold"), required=("source_asset_ref",), planner_operations=("normalize_components_local",)),
    _spec("image.normalize", "Normalize one image", "edit_image", fixed={"operation": "normalize"}, properties=("source_asset_ref", *_NORMALIZE_OPTIONS), required=("source_asset_ref",), planner_operations=("normalize_components_local", "resize_image_local")),
    _spec("image.remove_background_local", "Remove background locally", "edit_image", fixed={"operation": "remove_background_local"}, properties=("source_asset_ref", *_LOCAL_BACKGROUND_OPTIONS), required=("source_asset_ref", "background_method"), planner_operations=("remove_background_local",), note="Omit target_color unless exact pixel evidence is available so color-key mode can derive it from corners."),
    _spec("image.upscale_local", "Upscale one image locally", "edit_image", fixed={"operation": "upscale_local"}, properties=("source_asset_ref", "scale"), required=("source_asset_ref", "scale"), planner_operations=("upscale_image_local",)),
    _spec("image.upscale_provider", "Upscale one image with the configured provider", "edit_image", fixed={"operation": "upscale"}, properties=("source_asset_ref", "scale"), required=("source_asset_ref", "scale"), planner_operations=("upscale_image_provider",)),
    _spec("image.remove_background_provider", "Remove a complex background with the configured provider", "edit_image", fixed={"operation": "remove_background"}, properties=("source_asset_ref",), required=("source_asset_ref",), planner_operations=("remove_background_provider",)),
    _spec("image.inpaint_selection", "Inpaint one confirmed selection", "edit_image", fixed={"operation": "inpaint"}, properties=("source_asset_ref", "selection_ref", "prompt_asset_ref"), required=("source_asset_ref", "selection_ref", "prompt_asset_ref")),
    _spec("element.export_transparent", "Export one extracted element with transparency", "edit_image", fixed={"operation": "export_transparent"}, properties=("source_asset_ref",), required=("source_asset_ref",), planner_operations=("export_transparent_provider",)),
    _spec("image.split_alpha_components", "Split transparent alpha components locally", "split_image", fixed={"split_mode": "alpha_components"}, properties=("source_asset_ref", *_LOCAL_SPLIT_OPTIONS), required=("source_asset_ref",), planner_operations=("split_alpha_components_local",)),
    _spec("image.split_grid", "Split a verified regular grid locally", "split_image", fixed={"split_mode": "grid"}, properties=("source_asset_ref", "columns", "rows", *_LOCAL_SPLIT_OPTIONS), required=("source_asset_ref", "columns", "rows"), planner_operations=("split_grid_local",)),
    _spec("selection.request_user", "Request a user selection for extraction", "split_image", fixed={"split_mode": "boxsplit"}, properties=("source_asset_ref",), required=("source_asset_ref",)),
    _spec("element.split_semantic", "Split image elements semantically", "split_image", fixed={"split_mode": "element"}, properties=("source_asset_ref", "prompt_asset_ref"), required=("source_asset_ref", "prompt_asset_ref")),
    _spec("element.split_selection", "Split one confirmed selection", "split_image", fixed={"split_mode": "boxsplit"}, properties=("source_asset_ref", "selection_ref", "prompt_asset_ref"), required=("source_asset_ref", "selection_ref", "prompt_asset_ref")),
    _spec("multiview.generate", "Generate a front-side-back sheet", "prepare_multiview", fixed={"operation": "create"}, properties=("source_asset_ref", "prompt_asset_ref"), required=("source_asset_ref",), planner_operations=("prepare_multiview",)),
    _spec("multiview.request_region_confirmation", "Ask the user to confirm three-view regions", "prepare_multiview", fixed={"operation": "request_region_confirmation"}, properties=("source_asset_ref",), required=("source_asset_ref",), planner_operations=("confirm_multiview",)),
    _spec("multiview.detect_regions", "Detect regions in a persisted multiview set", "prepare_multiview", fixed={"operation": "detect_regions"}, properties=("multiview_ref",), required=("multiview_ref",)),
    _spec("multiview.regenerate_view", "Regenerate one multiview direction", "prepare_multiview", fixed={"operation": "regenerate_view"}, properties=("multiview_ref", "target_view"), required=("multiview_ref", "target_view")),
    _spec("model3d.generate_from_image", "Generate a backend-dependent 3D model from one image", "generate_model3d", fixed={"mode": "image"}, properties=("image_asset_ref", "parameters"), required=("image_asset_ref", "parameters"), planner_operations=("generate_model3d",)),
    _spec("model3d.generate_from_multiview", "Generate a backend-dependent 3D model from confirmed views", "generate_model3d", fixed={"mode": "multiview"}, properties=("multiview_ref", "view_asset_refs", "parameters"), required=("multiview_ref", "view_asset_refs", "parameters"), planner_operations=("generate_model3d",)),
    _spec("model3d.inspect", "Inspect one managed 3D model", "process_model3d", fixed={"operation": "inspect"}, properties=("asset_refs",), required=("asset_refs",), property_constraints={"asset_refs": {"minItems": 1, "maxItems": 1}}, planner_operations=("inspect_model3d",)),
    _spec("model3d.render_preview", "Capture one managed 3D model preview", "process_model3d", fixed={"operation": "open_preview"}, properties=("asset_refs",), required=("asset_refs",), property_constraints={"asset_refs": {"minItems": 1, "maxItems": 1}}),
    _spec("model3d.convert", "Convert one managed GLB model to FBX", "process_model3d", fixed={"operation": "convert", "target_format": "fbx"}, properties=("asset_refs",), required=("asset_refs",), property_constraints={"asset_refs": {"minItems": 1, "maxItems": 1}}, planner_operations=("convert_model3d",)),
    _spec("model3d.optimize", "Optimize one managed 3D model", "process_model3d", fixed={"operation": "optimize"}, properties=("asset_refs", "target_triangles", "max_texture_bytes"), required=("asset_refs",), property_constraints={"asset_refs": {"minItems": 1, "maxItems": 1}}, planner_operations=("optimize_model3d",)),
    _spec("model3d.package", "Package managed 3D assets for delivery", "process_model3d", fixed={"operation": "package"}, properties=("asset_refs",), required=("asset_refs",), planner_operations=("package_model3d",)),
    _spec("job.get_status", "Read one known job status", "control_job", fixed={"action": "status"}, properties=("job_ref",), required=("job_ref",)),
    _spec("job.cancel", "Cancel one known job", "control_job", fixed={"action": "cancel"}, properties=("job_ref",), required=("job_ref",)),
    _spec("job.retry", "Retry one known retryable job", "control_job", fixed={"action": "retry"}, properties=("job_ref",), required=("job_ref",)),
    _spec("job.confirm_new_submission", "Confirm a new submission for one unknown paid job", "control_job", fixed={"action": "confirm_new_submission"}, properties=("job_ref",), required=("job_ref",)),
)

BUSINESS_TOOL_NAMES = tuple(spec.name for spec in OPERATION_TOOL_SPECS)
MODEL_TOOL_NAMES = (*_BUILTIN_TOOL_NAMES, "toolbox.status", "toolbox.load", *BUSINESS_TOOL_NAMES)


class AIPicOperationTool:
    execution_mode: Literal["sequential"] = "sequential"

    def __init__(
        self,
        dispatcher: _FacadeDispatcher,
        spec: OperationToolSpec,
    ) -> None:
        self._dispatcher = dispatcher
        self._spec = spec
        self.name = spec.name
        self.label = spec.label
        self.description = spec.description
        self.parameters = spec.parameters
        self.search_terms = spec.search_terms

    async def execute(
        self,
        tool_call_id: str,
        arguments: dict[str, object],
        context: ToolContext,
        cancellation: CancellationToken,
        on_update: ToolUpdateCallback | None = None,
    ) -> ToolResult:
        del context
        merged = {**self._spec.fixed_arguments, **arguments}
        result = await self._dispatcher.execute(
            self._spec.dispatch_name, tool_call_id, merged, cancellation
        )
        if on_update is not None:
            update = on_update(result)
            if update is not None:
                await update
        return result


class ToolboxStatusTool:
    name = "toolbox.status"
    label = "Inspect active and available Tools"
    description = (
        "Inspect the current Tool area or search the host Tool catalog by capability. "
        "Use this when the required operation is not already active. It does not activate or execute a Tool."
    )
    execution_mode: Literal["sequential"] = "sequential"
    parameters: Mapping[str, object] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "maxLength": 120,
                "description": "Optional capability, operation, or Tool-name search text.",
            },
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": 40,
                "default": 12,
                "description": "Maximum matching Tool summaries to return.",
            },
        },
        "additionalProperties": False,
    }

    def __init__(
        self,
        catalog: Callable[[], AgentToolCatalog],
        active_names: Callable[[], tuple[str, ...]] = lambda: (),
    ) -> None:
        self._catalog = catalog
        self._active_names = active_names

    async def execute(self, tool_call_id, arguments, context, cancellation, on_update=None):
        del context
        cancellation.raise_if_cancelled()
        query = str(arguments.get("query") or "").strip().casefold()
        query_tokens = tuple(item for item in query.split() if item)
        limit_value = arguments.get("max_results", 12)
        limit = int(limit_value) if isinstance(limit_value, int) and not isinstance(limit_value, bool) else 12
        active = self._active_names()
        ranked_matches: list[tuple[int, int, dict[str, object]]] = []
        for catalog_index, tool in enumerate(self._catalog().all()):
            if tool.name in {self.name, "toolbox.load"}:
                continue
            search_terms = tuple(
                str(item)
                for item in getattr(tool, "search_terms", ())
                if isinstance(item, str)
            )
            raw_properties = tool.parameters.get("properties", {})
            properties = raw_properties if isinstance(raw_properties, Mapping) else {}
            parameter_text = " ".join(
                f"{name} {value.get('description', '') if isinstance(value, Mapping) else ''}"
                for name, value in properties.items()
            )
            haystack = " ".join(
                (tool.name, tool.label, tool.description, *search_terms, parameter_text)
            ).casefold()
            if query and query not in haystack and not all(
                token in haystack for token in query_tokens
            ):
                continue
            required = tool.parameters.get("required", ())
            score = 0 if query == tool.name.casefold() else (
                1 if query and query in tool.name.casefold() else
                2 if query and query in tool.label.casefold() else
                3 if query and query in " ".join(search_terms).casefold() else
                4
            )
            ranked_matches.append(
                (
                    score,
                    catalog_index,
                    {
                        "name": tool.name,
                        "label": tool.label,
                        "description": tool.description,
                        "required_parameters": (
                            list(required) if isinstance(required, list | tuple) else []
                        ),
                        "execution_mode": tool.execution_mode,
                        "active": tool.name in active,
                        "permanent": tool.name in PERMANENT_TOOL_NAMES,
                    },
                )
            )
        ranked_matches.sort(key=lambda item: (item[0], item[1]))
        matches = [item[2] for item in ranked_matches[:limit]]
        payload = {
            "active_tool_names": list(active),
            "active_count": len(active),
            "catalog_count": len(self._catalog().names),
            "matches": matches,
            "load_instruction": "Call toolbox.load with exact unloaded tool_names; newly loaded schemas are callable on the next model turn.",
        }
        result = ToolResult(
            (TextContent(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),),
            details=payload,
        )
        if on_update is not None:
            update = on_update(result)
            if update is not None:
                await update
        return result


class ToolboxLoadTool:
    name = "toolbox.load"
    label = "Load Tools for the next model turn"
    description = (
        "Activate exact Tool names discovered with toolbox.status. Loaded Tool schemas are appended "
        "to the active Tool area and become callable on the next model turn, never inside this same response."
    )
    execution_mode: Literal["sequential"] = "sequential"
    parameters: Mapping[str, object] = {
        "type": "object",
        "properties": {
            "tool_names": {
                "type": "array",
                "minItems": 1,
                "maxItems": 12,
                "uniqueItems": True,
                "items": {"type": "string", "minLength": 1, "maxLength": 80},
                "description": "Exact Tool names returned by toolbox.status.",
            }
        },
        "required": ["tool_names"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        catalog: Callable[[], AgentToolCatalog],
        active_names: Callable[[], tuple[str, ...]] = lambda: (),
    ) -> None:
        self._catalog = catalog
        self._active_names = active_names

    async def execute(self, tool_call_id, arguments, context, cancellation, on_update=None):
        del tool_call_id, context
        cancellation.raise_if_cancelled()
        requested = arguments.get("tool_names")
        assert isinstance(requested, list)
        unknown = tuple(name for name in requested if not isinstance(name, str) or not self._catalog().contains(name))
        if unknown:
            return ToolResult(
                (TextContent("Unknown Tool names. Call toolbox.status and use exact returned names."),),
                details={"unknown_tool_names": list(unknown)},
                is_error=True,
            )
        active = set(self._active_names())
        added = tuple(name for name in requested if isinstance(name, str) and name not in active)
        result = ToolResult(
            (TextContent("Tool schemas will be available on the next model turn."),),
            details={"requested_tool_names": list(requested), "added_tool_names": list(added), "effective": "next_turn"},
            added_tool_names=added,
        )
        if on_update is not None:
            update = on_update(result)
            if update is not None:
                await update
        return result


def build_progressive_tool_catalog(
    builtin_tools: tuple[AgentTool, ...],
    registry: AIPicToolRegistry,
    invocation: Callable[[], AIPicToolInvocation],
    runtime_context: RuntimeContext | None = None,
    prompt_creator: PromptCreator | None = None,
    job_completion_broker: Any | None = None,
    *,
    active_names: Callable[[], tuple[str, ...]] = lambda: (),
) -> AgentToolCatalog:
    if tuple(tool.name for tool in builtin_tools) != _BUILTIN_TOOL_NAMES:
        raise ValueError("Progressive Tool catalog requires read/write/edit/bash in stable order.")
    dispatcher = _FacadeDispatcher(
        registry, invocation, runtime_context, prompt_creator, job_completion_broker
    )
    catalog_box: list[AgentToolCatalog] = []
    status = ToolboxStatusTool(lambda: catalog_box[0], active_names)
    load = ToolboxLoadTool(lambda: catalog_box[0], active_names)
    operations = tuple(AIPicOperationTool(dispatcher, spec) for spec in OPERATION_TOOL_SPECS)
    catalog = AgentToolCatalog((*builtin_tools, status, load, *operations))
    catalog_box.append(catalog)
    return catalog


def _planner_operation_tools() -> dict[str, tuple[str, ...]]:
    indexed: dict[str, list[str]] = {}
    for spec in OPERATION_TOOL_SPECS:
        for operation in spec.planner_operations:
            indexed.setdefault(operation, []).append(spec.name)
    return {operation: tuple(names) for operation, names in indexed.items()}


_PLANNER_OPERATION_TOOLS = _planner_operation_tools()


def planner_tool_names(plan: ExecutionPlan) -> tuple[str, ...]:
    """Resolve advisory Planner hints to catalog names without granting permission."""

    names: list[str] = []
    seen: set[str] = set()
    available = set(MODEL_TOOL_NAMES)
    for step in plan.steps:
        candidates = (
            (step.tool_name,)
            if step.tool_name in available
            else _PLANNER_OPERATION_TOOLS.get(step.operation or "", ())
        )
        for name in candidates:
            if name not in seen:
                names.append(name)
                seen.add(name)
    return tuple(names)
