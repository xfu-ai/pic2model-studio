"""Independent production instructions shared by image-generation workflows."""

MULTIVIEW_BASE_PROMPT = " ".join(
    (
        "Produce a clean, wide modelling reference sheet containing exactly three orthographic views of the source subject: front, left-side, and rear.",
        "Preserve one identical design across all views, including silhouette, proportions, part placement, materials, colors, texture landmarks, and wear.",
        "Keep every view upright, fully visible from its outermost top to bottom and left to right, centered in its own column, and aligned to the same baseline and scale.",
        "Leave generous neutral-background clearance around every complete silhouette so each view can be cropped independently.",
        "Use flat neutral illumination and an even unobtrusive background; exclude perspective staging, props, labels, borders, dividing lines, and extra views.",
    )
)

MULTIVIEW_SHEET_REQUIREMENTS = " ".join(
    (
        "Return one wide horizontal image laid out as three equal conceptual columns, ordered front, left side, then rear from left to right.",
        "Place exactly one complete view in each column and keep each subject at roughly 75 to 85 percent of the canvas height, with visible background above and below.",
        "Leave a clearly empty vertical gutter of neutral background between adjacent silhouette bounding boxes, at least 5 percent of the full canvas width; the views must not touch, overlap, or visually merge.",
        "Hair, limbs, loose parts, weapons, accessories, floor shadows, and effects must remain inside their own column and must not enter either gutter.",
        "Each view must be independently crop-ready. Do not return separate files or add text, captions, frames, borders, panel lines, or extra views.",
    )
)


def regenerate_view_prompt(view: str) -> str:
    return " ".join(
        (
            f"Recreate the {view} orthographic view as a single complete image.",
            "Keep the existing subject identity, proportions, construction, material response, colors, texture landmarks, scale, and alignment.",
            "Correct only the requested view; use neutral lighting and background, and exclude text, props, perspective staging, and new design details.",
        )
    )
