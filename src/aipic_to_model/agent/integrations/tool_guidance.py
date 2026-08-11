"""Model-facing guidance for AIPic tools.

The canonical manifests are frozen application contracts shared by REST, tests,
and the desktop.  Their terse descriptions are intentionally preserved there.
This module adds Agent-only selection guidance without changing those contracts.
"""

from __future__ import annotations

from ...domain.tools import ToolManifestV1

_AGENT_TOOL_GUIDANCE: dict[str, tuple[str, str]] = {
    "project.get_state": (
        "Use when you need the managed project's identity or persisted workflow state, including a manually confirmed multiview set and its front, side, and back crop assets.",
        "Do not use for active jobs, provider availability, or unsaved pointer movement in the live UI. Before multiview region detection or 3D generation, prefer confirmed multiview crops from this state and never run detection when a confirmed set already exists.",
    ),
    "project.save_checkpoint": (
        "Use after meaningful local changes when the user asks to save or checkpoint the project.",
        "Do not call after every read or while no project state has changed.",
    ),
    "project.export_package": (
        "Use only after this Tool is connected to the host-backed exporter and the desktop has supplied an export capability ID.",
        "Do not call it from the current Agent runtime: its Tool executor is not yet connected to the real package export flow; ask the user to use desktop Export.",
    ),
    "asset.list": (
        "Use to discover managed project assets, optionally narrowed to one group.",
        "Do not repeatedly poll it for a running job or treat returned assets as selected; inspect the chosen item with asset.get_metadata.",
    ),
    "asset.get_metadata": (
        "Use for one known asset to inspect its metadata and lineage, or before presenting that existing image in the final answer.",
        "Do not use as a project-wide search or when you do not yet have an asset ID.",
    ),
    "asset.set_current": (
        "Use when the user or workflow explicitly chooses one managed asset as the project's current asset.",
        "Do not use merely to preview, compare, or fill a page-specific input slot.",
    ),
    "asset.compare": (
        "Use to compare two known sibling versions and open the comparison workspace.",
        "Do not use for unrelated assets or as a substitute for image content/style analysis.",
    ),
    "asset.hide": (
        "Use when the user wants a managed asset removed from normal browsing without deleting it.",
        "Do not use for permanent removal or to change the current asset.",
    ),
    "asset.restore_hidden": (
        "Use to make a previously hidden managed asset visible again.",
        "Do not use for assets in the trash; use asset.restore_from_trash instead.",
    ),
    "asset.move_to_trash": (
        "Use only after the user has explicitly chosen deletion and a fresh impact token covers descendants and references.",
        "Do not guess an impact token, bypass impact review, or use this merely to hide an asset.",
    ),
    "asset.restore_from_trash": (
        "Use when the user asks to restore a known trashed asset.",
        "Do not use for hidden assets or assets that are already active.",
    ),
    "asset.open_output_folder": (
        "Use when the user asks to reveal a known managed asset in its desktop output location.",
        "Do not use to read arbitrary filesystem paths or to export/copy the asset.",
    ),
    "selection.get_current": (
        "Use to read the current saved selection for a known image asset.",
        "Do not use to create, modify, confirm, or auto-detect a selection.",
    ),
    "selection.request_user": (
        "Use when a workflow needs the user to draw or adjust a rectangle in the Selection workspace.",
        "Do not call when an already confirmed selection is sufficient.",
    ),
    "selection.set_suggestion": (
        "Use to save one or more model-proposed rectangles for a known image before user confirmation.",
        "Do not claim the rectangles are user-confirmed or use coordinates outside the source image.",
    ),
    "selection.confirm": (
        "Use after the user has reviewed the current selection and explicitly confirmed it.",
        "Do not confirm a model suggestion on the user's behalf.",
    ),
    "image.crop": (
        "Use to create a managed crop from an existing confirmed selection.",
        "Do not use to resize, inpaint, remove a background, or crop from guessed coordinates.",
    ),
    "image.render_annotation": (
        "Use to create a managed image that visibly renders an existing selection as an annotation.",
        "Do not use when the user needs the clean cropped pixels instead.",
    ),
    "image.analyze_content": (
        "Use only when the user explicitly requests a persisted semantic subject, composition, or scene analysis, or when that persisted analysis is explicitly required for prompt extraction.",
        "Do not use for routine Agent image comprehension, style-only analysis, an already sufficient analysis, or immediately after image.understand_for_agent for the same question.",
    ),
    "image.analyze_style": (
        "Use when visual style, palette, lighting, texture, or rendering-language analysis is needed.",
        "Do not use for subject identity or composition analysis.",
    ),
    "image.evaluate_3d_suitability": (
        "Use before 3D generation when the source image's geometry visibility and reconstruction suitability are uncertain.",
        "Do not use as a general image-quality score or after a validated multiview set is already available.",
    ),
    "image.understand_for_agent": (
        "Use for one grounded visual question when the Agent needs image understanding without creating a persisted analysis asset.",
        "Do not use when the user explicitly needs a saved content, style, or 3D-suitability workflow analysis.",
    ),
    "prompt.extract_bilingual": (
        "Use to create managed Chinese and English prompt text from existing analysis assets.",
        "Do not use to call a provider, rewrite an arbitrary user message, or merge content and style.",
    ),
    "prompt.merge": (
        "Use to combine known content and style prompt assets into one managed generation prompt.",
        "Do not use before both inputs exist or when the user wants either prompt kept independent.",
    ),
    "prompt.get_current": (
        "Use to read and parse one known managed prompt asset.",
        "Do not use to search assets, validate provider support, or modify prompt text.",
    ),
    "prompt.rewrite": (
        "Use when the user requests a provider-assisted rewrite of a known prompt with a specific instruction.",
        "Do not use for deterministic content/style merging or when no rewrite was requested.",
    ),
    "prompt.validate": (
        "Use to verify that one known managed prompt can be parsed and used by the workflow.",
        "Do not use as an image-quality check or a provider health probe.",
    ),
    "image.generate": (
        "Use to create new image candidates from a managed prompt after the user has requested generation.",
        "Do not call without the required provider profile or approval, and do not use it to edit an existing image.",
    ),
    "image.transform": (
        "Use for a requested provider transformation of a known source image guided by a managed prompt.",
        "Do not use for text-only generation, local compression, or a small selected-area edit.",
    ),
    "image.generate_variants": (
        "Use when the user wants multiple alternatives derived from a known source image and prompt.",
        "Do not use for identical retries, upscaling, or single-view repair.",
    ),
    "image.upscale": (
        "Use when the user requests higher resolution for a known image while preserving its content.",
        "Do not use to change composition, style, background, or selected regions.",
    ),
    "image.remove_background": (
        "Use when the user requests a transparent-background result from a known image.",
        "Do not use for object removal, cropping, or general inpainting.",
    ),
    "image.inpaint_selection": (
        "Use when the user requests a provider edit restricted to a confirmed selection and supplies a managed prompt.",
        "Do not use without a confirmed selection or for whole-image transformation.",
    ),
    "image.compress_for_provider": (
        "Use when a known image must be converted locally into provider-compatible size or encoding.",
        "Do not use as a quality enhancement, user export, or substitute for image.upscale.",
    ),
    "image.trim_transparent": (
        "Use to remove transparent outer bounds locally while creating a new managed image.",
        "Do not use for a user-selected crop, background removal, or an opaque image without transparent bounds.",
    ),
    "image.normalize": (
        "Use to normalize dimensions, orientation, encoding, or alpha locally while creating a new managed image.",
        "Do not use for semantic enhancement, super-resolution, or background removal.",
    ),
    "image.remove_background_local": (
        "Use color-key or channel-threshold removal locally for a flat or channel-separable background.",
        "Do not use for gradients, shadows, hair, or textured backgrounds; use image.remove_background for those cases.",
    ),
    "image.split_local": (
        "Use alpha-components or an explicit regular grid for deterministic local image splitting.",
        "Do not use it to guess semantic elements or an irregular layout.",
    ),
    "image.upscale_local": (
        "Use the bundled offline model to upscale one managed image by 2x or 4x through a local Job.",
        "Do not silently fall back to image.upscale when the local capability is unavailable.",
    ),
    "element.split": (
        "Use when the user wants a scene or character element extracted into a generated result using a known source and prompt.",
        "Do not use for a deterministic rectangular crop or without external approval.",
    ),
    "element.export_transparent": (
        "Use when an extracted element needs a transparent managed image result.",
        "Do not use before a source element exists or merely to reveal its folder.",
    ),
    "selection.auto_suggest_boxes": (
        "Use when provider vision should propose target rectangles for a known image.",
        "Do not treat suggestions as confirmed or call it when the user already supplied the required rectangle.",
    ),
    "multiview.generate": (
        "Use when the user requests one generated sheet containing front, side, and back views from a known source.",
        "Do not use when a complete multiview sheet already exists or without external approval.",
    ),
    "multiview.detect_regions": (
        "Use only when the user explicitly requests experimental automatic detection of front, side, and back rectangles on an existing multiview sheet that has no saved crops.",
        "Do not use as the normal three-view workflow, after three manually confirmed crops exist, or to replace the user's crop decision. Those persisted crops are authoritative. Do not crop or confirm detected regions automatically.",
    ),
    "multiview.request_box_confirmation": (
        "Use after regions exist to send the user to the Multiview workspace for manual boundary review.",
        "Do not use before a multiview set and proposed regions exist.",
    ),
    "multiview.set_regions": (
        "Use to persist front, side, and back rectangles that the user or workflow has supplied.",
        "Do not claim they are quality-confirmed or crop them before their geometry is valid.",
    ),
    "multiview.crop_views": (
        "Use after multiview regions are confirmed to create managed front, side, and back crops.",
        "Do not use on unconfirmed, missing, or overlapping regions.",
    ),
    "multiview.request_quality_confirmation": (
        "Use only when the user explicitly asks to record an optional quality review of existing crops.",
        "Do not call it as a prerequisite for 3D generation; confirmed crop assets are already authoritative.",
    ),
    "multiview.set_quality_checks": (
        "Use only to persist an optional quality review explicitly supplied by the user.",
        "Do not manufacture positive checks or override a failed check.",
    ),
    "multiview.validate": (
        "Use when provider vision should evaluate an existing multiview set for 3D consistency.",
        "Do not use it as a prerequisite for 3D generation or as a substitute for crop confirmation.",
    ),
    "multiview.regenerate_view": (
        "Use when the user requests repair of one named front, side, or back view in an existing multiview set.",
        "Do not regenerate the whole sheet or call without external approval.",
    ),
    "model3d.generate": (
        "Use to submit an approved image-to-3D or multiview-to-3D request from managed inputs.",
        (
            "For multiview mode, copy multiview_set_id only from the persisted workspace set_id; "
            "never substitute the source sheet asset ID or a crop asset ID. "
            "Choose a geometry budget before approval: use 50,000 faces for real-time/game use, "
            "100,000 for a general-purpose default, and raise it only for an explicit close-up or "
            "high-detail requirement. A 50,000-face request must use smart_low_poly=false. If "
            "smart_low_poly=true, use 500-20,000 faces for triangles or 500-10,000 with quad=true. "
            "Ask the user when the target use is unclear and the budget would "
            "materially affect the result. Do not use an unlimited Provider face budget. Do not call "
            "without suitable inputs, confirmed crop assets, provider profile, and user approval."
        ),
    ),
    "model3d.get_status": (
        "Compatibility alias for reading a known 3D Job status; new Agent workflows should use job.get_status.",
        "Do not select it when job.get_status is available and do not poll repeatedly.",
    ),
    "model3d.cancel": (
        "Compatibility alias for cancelling a known 3D Job; new Agent workflows should use job.cancel.",
        "Do not select it when job.cancel is available, without explicit user intent, or after the Job is terminal.",
    ),
    "model3d.download": (
        "Use when a successful remote 3D job needs its managed GLB result downloaded.",
        "Do not use before remote completion or for a local model asset.",
    ),
    "model3d.import_local": (
        "Use to register a host-authorized local GLB as a managed project model.",
        "Do not invent a host capability ID or use it for images, FBX files, or remote downloads.",
    ),
    "model3d.inspect": (
        "Use to run local authenticity, geometry, material, and capability inspection on a known managed model.",
        "Do not use as a visual approval or provider-side validation.",
    ),
    "model3d.render_preview": (
        "Use when the user wants a managed screenshot from the interactive 3D preview workspace.",
        "Do not claim a screenshot was captured until the desktop UI action is completed.",
    ),
    "model3d.convert": (
        "Use to convert a known managed 3D model with an available approved local converter.",
        "Do not use for optimization, packaging, remote generation, or when no conversion backend is available.",
    ),
    "model3d.optimize": (
        "Use when the user requests local polygon reduction or model optimization for a known managed model.",
        "Do not use as format conversion or when the local optimization capability is unavailable.",
    ),
    "model3d.package": (
        "Use when the user requests a managed delivery package from completed model assets.",
        "Do not use before required model outputs exist or as a project-package export.",
    ),
    "job.get_status": (
        "Use once in an Agent run to inspect a known background job's real persisted status.",
        "Do not poll repeatedly, sleep, or use asset.list to guess whether the job completed.",
    ),
    "job.cancel": (
        "Use when the user explicitly asks to cancel or stop waiting for a known job and its capability allows it.",
        "Do not use on terminal jobs or assume every provider supports remote cancellation.",
    ),
    "job.retry": (
        "Use when the user requests retry of a known failed or interrupted job and the stored error says retry is safe.",
        "Do not retry unknown submissions, paid work with uncertain submission state, or a still-running job.",
    ),
    "job.confirm_new_submission": (
        "Use only from the recovery flow after an interrupted paid Job is durably classified as an unknown submission and the user explicitly chooses a new submission.",
        "Do not use it as an ordinary retry or when the original submission may still be running.",
    ),
}


def agent_tool_description(manifest: ToolManifestV1) -> str:
    """Return operational guidance that helps the model select tools correctly."""

    guidance = _AGENT_TOOL_GUIDANCE.get(manifest.name)
    if guidance is None:
        return (
            f"{manifest.description} Use only for the managed operation described by this tool's "
            "schema. Do not invent IDs, paths, capabilities, approvals, or provider state."
        )
    return " ".join(guidance)
