"""Frozen B02 canonical Tool manifests and dependency-injected execution.

The catalog deliberately contains no Provider, SQLite, or approval policy.
Those concerns are composed once and supplied through ``B02ToolRuntime``.
Keeping this module declarative prevents a manifest from silently becoming a
successful no-op when an integration is missing.
"""

from __future__ import annotations

from typing import Any, Protocol

from ..domain.common import RiskLevel, new_id
from ..domain.tools import ToolManifestV1, ToolResultV1


class B02ToolRuntime(Protocol):
    def invoke(
        self,
        name: str,
        risk_level: RiskLevel,
        execution: str,
        root: Any,
        project_id: str,
        arguments: dict[str, Any],
        call_id: str,
    ) -> ToolResultV1: ...


class UnavailableB02ToolRuntime:
    """Safe bootstrap runtime used only when composition is incomplete.

    It is intentionally *not* an in-memory queue: callers receive a stable,
    structured result instead of a phantom Job or an empty successful Tool.
    """

    def invoke(
        self,
        name: str,
        risk_level: RiskLevel,
        execution: str,
        root: Any,
        project_id: str,
        arguments: dict[str, Any],
        call_id: str,
    ) -> ToolResultV1:
        del root, project_id, arguments, execution
        if risk_level in {RiskLevel.EXTERNAL, RiskLevel.EXTERNAL_PAID}:
            return ToolResultV1(
                True,
                "awaiting_ui_action",
                call_id,
                [],
                "Approval is required before external work.",
                [],
                {"type": "approval_required"},
                {
                    "action_id": new_id(),
                    "type": "approval_required",
                    "workspace_mode": "working",
                },
            )
        return ToolResultV1(
            False,
            "failed",
            call_id,
            [],
            "The production Tool runtime is not available.",
            [],
            error={
                "code": "TOOL_NOT_ALLOWED",
                "category": "api_not_configured",
                "user_message": "当前环境未配置此生产能力。",
                "recoverable": True,
                "failed_object": "tool_call",
                "failed_step": "dispatch",
                "safe_to_retry": True,
                "recommended_action": "configure_provider",
            },
        )


# name, risk, execution, requires approval, capability key
B02_TOOLS: tuple[tuple[str, RiskLevel, str, bool, str | None], ...] = (
    ("image.analyze_content", RiskLevel.EXTERNAL, "job", False, "vision"),
    ("image.analyze_style", RiskLevel.EXTERNAL, "job", False, "vision"),
    ("image.evaluate_3d_suitability", RiskLevel.EXTERNAL, "job", False, "vision"),
    ("prompt.extract_bilingual", RiskLevel.LOCAL_REVERSIBLE, "sync", False, None),
    ("prompt.merge", RiskLevel.LOCAL_REVERSIBLE, "sync", False, None),
    ("prompt.get_current", RiskLevel.READ_ONLY, "sync", False, None),
    ("prompt.rewrite", RiskLevel.EXTERNAL, "job", False, "vision"),
    ("prompt.validate", RiskLevel.READ_ONLY, "sync", False, None),
    ("image.generate", RiskLevel.EXTERNAL_PAID, "job", True, "image_generation"),
    ("image.transform", RiskLevel.EXTERNAL_PAID, "job", True, "image_generation"),
    ("image.generate_variants", RiskLevel.EXTERNAL_PAID, "job", True, "image_generation"),
    ("image.upscale", RiskLevel.EXTERNAL, "job", False, "image_editing"),
    ("image.remove_background", RiskLevel.EXTERNAL, "job", False, "image_editing"),
    ("image.inpaint_selection", RiskLevel.EXTERNAL, "job", True, "image_editing"),
    ("image.compress_for_provider", RiskLevel.LOCAL_REVERSIBLE, "sync", False, None),
    ("image.trim_transparent", RiskLevel.LOCAL_REVERSIBLE, "sync", False, None),
    ("image.normalize", RiskLevel.LOCAL_REVERSIBLE, "sync", False, None),
    ("image.remove_background_local", RiskLevel.LOCAL_REVERSIBLE, "sync", False, None),
    ("image.split_local", RiskLevel.LOCAL_REVERSIBLE, "sync", False, None),
    ("image.upscale_local", RiskLevel.LOCAL_REVERSIBLE, "job", False, None),
    ("element.split", RiskLevel.EXTERNAL_PAID, "job", True, "image_generation"),
    ("element.export_transparent", RiskLevel.EXTERNAL, "job", False, "image_editing"),
    ("selection.auto_suggest_boxes", RiskLevel.EXTERNAL, "job", False, "vision"),
    ("multiview.generate", RiskLevel.EXTERNAL_PAID, "job", True, "image_generation"),
    ("multiview.detect_regions", RiskLevel.EXTERNAL, "job", False, "vision"),
    ("multiview.request_box_confirmation", RiskLevel.LOCAL_REVERSIBLE, "sync", False, None),
    ("multiview.set_regions", RiskLevel.LOCAL_REVERSIBLE, "sync", False, None),
    ("multiview.crop_views", RiskLevel.LOCAL_REVERSIBLE, "sync", False, None),
    ("multiview.request_quality_confirmation", RiskLevel.LOCAL_REVERSIBLE, "sync", False, None),
    ("multiview.set_quality_checks", RiskLevel.LOCAL_REVERSIBLE, "sync", False, None),
    ("multiview.validate", RiskLevel.EXTERNAL, "job", False, "vision"),
    ("multiview.regenerate_view", RiskLevel.EXTERNAL_PAID, "job", True, "image_generation"),
    ("model3d.generate", RiskLevel.EXTERNAL_PAID, "job", True, "tripo3d"),
    ("model3d.get_status", RiskLevel.EXTERNAL, "sync", False, "tripo3d"),
    ("model3d.cancel", RiskLevel.EXTERNAL, "sync", False, "tripo3d"),
    ("model3d.download", RiskLevel.EXTERNAL, "job", False, "tripo3d"),
    ("model3d.import_local", RiskLevel.LOCAL_REVERSIBLE, "job", False, None),
    ("model3d.inspect", RiskLevel.LOCAL_REVERSIBLE, "sync", False, None),
    ("model3d.render_preview", RiskLevel.LOCAL_REVERSIBLE, "job", False, "preview_renderer"),
    ("model3d.convert", RiskLevel.LOCAL_REVERSIBLE, "job", False, "model_conversion"),
    ("model3d.optimize", RiskLevel.LOCAL_REVERSIBLE, "job", False, "model_optimization"),
    ("model3d.package", RiskLevel.LOCAL_REVERSIBLE, "job", False, None),
    ("job.get_status", RiskLevel.READ_ONLY, "sync", False, None),
    ("job.cancel", RiskLevel.LOCAL_REVERSIBLE, "sync", False, None),
    ("job.retry", RiskLevel.LOCAL_REVERSIBLE, "sync", False, None),
    ("job.confirm_new_submission", RiskLevel.LOCAL_REVERSIBLE, "sync", False, None),
)


def _schema(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(required),
    }


_ASSET = {"type": "string", "minLength": 1}
_PROFILE = {"type": "string", "minLength": 1}
_MODEL = {"type": "string", "minLength": 1}
_RECT = _schema(
    {
        "x": {"type": "integer", "minimum": 0},
        "y": {"type": "integer", "minimum": 0},
        "width": {"type": "integer", "minimum": 1},
        "height": {"type": "integer", "minimum": 1},
    },
    ("x", "y", "width", "height"),
)


def _input_schema(name: str) -> dict[str, Any]:
    analysis = {
        "asset_id": _ASSET,
        "provider_profile": _PROFILE,
        "model": _MODEL,
    }
    generation = {
        "prompt_asset_id": _ASSET,
        "provider_profile": _PROFILE,
        "channel": {"enum": ["auto", "meshy"]},
        "model": _MODEL,
        "candidate_count": {"type": "integer", "enum": [1, 2, 4]},
        "aspect_ratio": {"type": "string"},
        "size": {"type": "string"},
        "quality": {"type": "string"},
        "output_format": {"enum": ["png", "jpg", "webp"]},
        "structure_strength": {"type": "number", "minimum": 0, "maximum": 1},
    }
    if name in {"image.analyze_content", "image.analyze_style"}:
        # Present only when the user explicitly requests a fresh analysis of
        # an already-analyzed asset. It participates in the Tool idempotency
        # key without being forwarded to the vision provider request model.
        return _schema(
            {
                **analysis,
                "analysis_revision": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                },
            },
            ("asset_id", "provider_profile", "model"),
        )
    if name == "image.evaluate_3d_suitability":
        return _schema(analysis, ("asset_id", "provider_profile", "model"))
    if name == "prompt.extract_bilingual":
        return _schema(
            {"analysis_asset_id": _ASSET, "kind": {"enum": ["content", "style"]}},
            ("analysis_asset_id", "kind"),
        )
    if name == "prompt.merge":
        return _schema(
            {"content_prompt_asset_id": _ASSET, "style_prompt_asset_id": _ASSET},
            ("content_prompt_asset_id", "style_prompt_asset_id"),
        )
    if name in {"prompt.get_current", "prompt.validate"}:
        return _schema({"prompt_asset_id": _ASSET}, ("prompt_asset_id",))
    if name == "prompt.rewrite":
        return _schema(
            {
                "prompt_asset_id": _ASSET,
                "provider_profile": _PROFILE,
                "model": _MODEL,
                "instruction": {"type": "string", "minLength": 1, "maxLength": 4000},
            },
            ("prompt_asset_id", "provider_profile", "model", "instruction"),
        )
    if name == "image.generate":
        return _schema(
            {**generation, "channel": {"enum": ["auto", "meshy"]}},
            ("prompt_asset_id", "provider_profile", "channel", "model", "candidate_count"),
        )
    if name in {"image.transform", "image.generate_variants"}:
        return _schema(
            {**generation, "source_asset_id": _ASSET},
            (
                "prompt_asset_id",
                "source_asset_id",
                "provider_profile",
                "channel",
                "model",
                "candidate_count",
            ),
        )
    if name == "image.compress_for_provider":
        return _schema({"asset_id": _ASSET, "minimum": {"type": "boolean"}}, ("asset_id",))
    if name == "image.trim_transparent":
        return _schema(
            {
                "source_asset_id": _ASSET,
                "padding": {"type": "integer", "minimum": 0, "maximum": 256},
                "alpha_threshold": {"type": "integer", "minimum": 0, "maximum": 255},
            },
            ("source_asset_id",),
        )
    if name == "image.normalize":
        return _schema(
            {
                "source_asset_id": _ASSET,
                "target_width": {"type": "integer", "minimum": 1, "maximum": 16384},
                "target_height": {"type": "integer", "minimum": 1, "maximum": 16384},
                "max_long_edge": {"type": "integer", "minimum": 1, "maximum": 16384},
                "lock_aspect_ratio": {"type": "boolean"},
                "rotate_degrees": {"enum": [0, 90, 180, 270]},
                "flip": {"enum": ["none", "horizontal", "vertical"]},
                "output_format": {"enum": ["png", "jpeg", "webp"]},
                "quality": {"type": "integer", "minimum": 1, "maximum": 100},
                "preserve_alpha": {"type": "boolean"},
            },
            ("source_asset_id",),
        )
    if name == "image.remove_background_local":
        return _schema(
            {
                "source_asset_id": _ASSET,
                "method": {"enum": ["color_key", "channel"]},
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
            ("source_asset_id", "method"),
        )
    if name == "image.split_local":
        schema = _schema(
            {
                "source_asset_id": _ASSET,
                "mode": {"enum": ["alpha_components", "grid"]},
                "columns": {"type": "integer", "minimum": 1, "maximum": 64},
                "rows": {"type": "integer", "minimum": 1, "maximum": 64},
                "alpha_threshold": {"type": "integer", "minimum": 0, "maximum": 255},
                "min_area": {"type": "integer", "minimum": 1, "maximum": 100000000},
                "padding": {"type": "integer", "minimum": 0, "maximum": 256},
                "max_outputs": {"type": "integer", "minimum": 1, "maximum": 256},
            },
            ("source_asset_id", "mode"),
        )
        schema["allOf"] = [
            {
                "if": {"properties": {"mode": {"const": "grid"}}, "required": ["mode"]},
                "then": {"required": ["columns", "rows"]},
            }
        ]
        return schema
    if name == "image.upscale_local":
        return _schema(
            {
                "source_asset_id": _ASSET,
                "scale": {"enum": [2, 4]},
            },
            ("source_asset_id", "scale"),
        )
    if name in {"image.upscale", "image.remove_background", "element.export_transparent"}:
        properties: dict[str, Any] = {"source_asset_id": _ASSET, "provider_profile": _PROFILE}
        if name == "image.upscale":
            properties["scale"] = {"enum": [2, 4]}
            return _schema(properties, ("source_asset_id", "provider_profile", "scale"))
        return _schema(properties, ("source_asset_id", "provider_profile"))
    if name == "image.inpaint_selection":
        return _schema(
            {
                "source_asset_id": _ASSET,
                "selection_id": _ASSET,
                "prompt_asset_id": _ASSET,
                "provider_profile": _PROFILE,
            },
            ("source_asset_id", "selection_id", "prompt_asset_id", "provider_profile"),
        )
    if name == "element.split":
        schema = _schema(
            {
                "source_asset_id": _ASSET,
                "selection_id": _ASSET,
                "prompt_asset_id": _ASSET,
                "provider_profile": _PROFILE,
                "channel": {"enum": ["auto", "meshy"]},
                "model": _MODEL,
                "split_mode": {"enum": ["element", "boxsplit"]},
            },
            (
                "source_asset_id",
                "prompt_asset_id",
                "provider_profile",
                "channel",
                "model",
                "split_mode",
            ),
        )
        schema["allOf"] = [
            {
                "if": {
                    "properties": {"split_mode": {"const": "boxsplit"}},
                    "required": ["split_mode"],
                },
                "then": {"required": ["selection_id"]},
            }
        ]
        return schema
    if name == "selection.auto_suggest_boxes":
        return _schema(
            {"asset_id": _ASSET, "provider_profile": _PROFILE, "model": _MODEL},
            ("asset_id", "provider_profile", "model"),
        )
    if name == "multiview.generate":
        return _schema(
            {
                "source_asset_id": _ASSET,
                "prompt_asset_id": _ASSET,
                "provider_profile": _PROFILE,
                "channel": {"enum": ["auto", "meshy"]},
                "model": _MODEL,
            },
            ("source_asset_id", "provider_profile", "channel", "model"),
        )
    if name == "multiview.detect_regions":
        return _schema(
            {"multiview_set_id": _ASSET, "provider_profile": _PROFILE, "model": _MODEL},
            ("multiview_set_id", "provider_profile", "model"),
        )
    if name == "multiview.request_box_confirmation":
        return _schema({"multiview_set_id": _ASSET}, ("multiview_set_id",))
    if name == "multiview.set_regions":
        return _schema(
            {
                "multiview_set_id": _ASSET,
                "regions": _schema(
                    {"front": _RECT, "side": _RECT, "back": _RECT}, ("front", "side", "back")
                ),
            },
            ("multiview_set_id", "regions"),
        )
    if name == "multiview.crop_views":
        return _schema({"multiview_set_id": _ASSET}, ("multiview_set_id",))
    if name == "multiview.request_quality_confirmation":
        return _schema({"multiview_set_id": _ASSET}, ("multiview_set_id",))
    if name == "multiview.set_quality_checks":
        return _schema(
            {
                "multiview_set_id": _ASSET,
                "checks": _schema(
                    {
                        "subject_scale": {"enum": ["passed", "warning", "blocking"]},
                        "direction": {"enum": ["passed", "warning", "blocking"]},
                        "key_accessory": {"enum": ["passed", "warning", "blocking"]},
                        "truncation": {"enum": ["passed", "warning", "blocking"]},
                        "background": {"enum": ["passed", "warning", "blocking"]},
                        "resolution": {"enum": ["passed", "warning", "blocking"]},
                    },
                    (
                        "subject_scale",
                        "direction",
                        "key_accessory",
                        "truncation",
                        "background",
                        "resolution",
                    ),
                ),
            },
            ("multiview_set_id", "checks"),
        )
    if name == "multiview.validate":
        return _schema(
            {"multiview_set_id": _ASSET, "provider_profile": _PROFILE, "model": _MODEL},
            ("multiview_set_id", "provider_profile", "model"),
        )
    if name == "multiview.regenerate_view":
        return _schema(
            {
                "multiview_set_id": _ASSET,
                "view": {"enum": ["front", "side", "back"]},
                "provider_profile": _PROFILE,
                "channel": {"enum": ["auto", "meshy"]},
                "model": _MODEL,
            },
            ("multiview_set_id", "view", "provider_profile", "channel", "model"),
        )
    if name == "model3d.generate":
        parameters = _schema(
            {
                "model_version": _MODEL,
                "texture_quality": {"enum": ["standard", "detailed", "extreme"]},
                "geometry_quality": {"enum": ["standard", "detailed"]},
                "texture_alignment": {"enum": ["original_image", "geometry"]},
                "texture": {"type": "boolean"},
                "pbr": {"type": "boolean"},
                "quad": {"type": "boolean"},
                "face_limit": {"type": "integer", "minimum": 0, "default": 100_000},
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
        schema = _schema(
            {
                "mode": {"enum": ["image", "multiview"]},
                "image_asset_id": _ASSET,
                "multiview_set_id": _ASSET,
                "view_asset_ids": _schema(
                    {"front": _ASSET, "side": _ASSET, "back": _ASSET}, ("front", "side", "back")
                ),
                "provider_profile": _PROFILE,
                "model": _MODEL,
                "parameters": parameters,
            },
            ("mode", "provider_profile", "model", "parameters"),
        )
        schema["allOf"] = [
            {
                "if": {"properties": {"mode": {"const": "image"}}, "required": ["mode"]},
                "then": {"required": ["image_asset_id"]},
            },
            {
                "if": {"properties": {"mode": {"const": "multiview"}}, "required": ["mode"]},
                "then": {"required": ["multiview_set_id", "view_asset_ids"]},
            },
        ]
        return schema
    if name in {
        "model3d.get_status",
        "model3d.cancel",
        "model3d.download",
        "job.get_status",
        "job.cancel",
        "job.retry",
        "job.confirm_new_submission",
    }:
        return _schema({"job_id": _ASSET}, ("job_id",))
    if name == "model3d.import_local":
        return _schema({"staged_file_id": _ASSET}, ("staged_file_id",))
    if name in {"model3d.inspect", "model3d.render_preview", "model3d.convert", "model3d.optimize"}:
        properties = {"asset_id": _ASSET}
        if name == "model3d.convert":
            properties["target_format"] = {"const": "fbx"}
            return _schema(properties, ("asset_id", "target_format"))
        if name == "model3d.optimize":
            properties.update(
                {
                    "target_triangles": {"type": "integer", "minimum": 1, "maximum": 10_000_000},
                    "max_texture_bytes": {"type": "integer", "minimum": 1, "maximum": 209_715_200},
                }
            )
        return _schema(properties, ("asset_id",))
    if name == "model3d.package":
        return _schema(
            {
                "asset_ids": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 32,
                    "uniqueItems": True,
                    "items": _ASSET,
                }
            },
            ("asset_ids",),
        )
    raise ValueError(f"missing B02 input schema for {name}")


def _output_schema() -> dict[str, Any]:
    return _schema(
        {
            "ok": {"type": "boolean"},
            "status": {"enum": ["succeeded", "queued", "awaiting_ui_action", "failed"]},
            "tool_call_id": _ASSET,
            "output_asset_ids": {"type": "array", "items": _ASSET},
            "summary": {"type": "string"},
            "warnings": {"type": "array", "items": {"type": "string"}},
            "expected_action": {"type": ["object", "null"]},
            "ui_action": {"type": ["object", "null"]},
            "job": {"type": ["object", "null"]},
            "error": {"type": ["object", "null"]},
            "reused": {"type": "boolean"},
        },
        (
            "ok",
            "status",
            "tool_call_id",
            "output_asset_ids",
            "summary",
            "warnings",
            "expected_action",
            "ui_action",
            "job",
            "error",
            "reused",
        ),
    )


def register_b02_tools(registry: Any, runtime: B02ToolRuntime | None = None) -> None:
    active_runtime = runtime or UnavailableB02ToolRuntime()
    for name, risk, execution, requires_approval, capability in B02_TOOLS:
        if (name, "1.0.0") in registry.manifests:
            continue

        def executor(
            root: Any,
            project_id: str,
            arguments: dict[str, Any],
            call_id: str,
            *,
            _name: str = name,
            _risk: RiskLevel = risk,
            _execution: str = execution,
        ) -> ToolResultV1:
            return active_runtime.invoke(
                _name, _risk, _execution, root, project_id, arguments, call_id
            )

        registry.register(
            ToolManifestV1(
                name=name,
                version="1.0.0",
                human_name=name,
                description=f"B02 canonical tool: {name}",
                input_schema=_input_schema(name),
                output_schema=_output_schema(),
                risk_level=risk,
                execution=execution,
                idempotency=True,
                supports_cancel=execution == "job",
                allowed_asset_types=[],
                executor_key=f"b02:{name}",
                requires_approval=requires_approval,
                capability=capability,
            ),
            executor,
        )
