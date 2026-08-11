"""Independent production instructions shared by image-generation workflows."""

MULTIVIEW_BASE_PROMPT = " ".join(  # noqa: FLY002 - sentence lists stay diffable
    (
        "Produce a clean, wide modelling reference sheet containing exactly three orthographic views of the source subject: front, left-side, and rear.",
        "Preserve one identical design across all views, including silhouette, proportions, part placement, materials, colors, texture landmarks, and wear.",
        "Keep every view upright, fully visible from its outermost top to bottom and left to right, centered in its own column, and aligned to the same baseline and scale.",
        "Leave generous neutral-background clearance around every complete silhouette so each view can be cropped independently.",
        "Use flat neutral illumination and an even unobtrusive background; exclude perspective staging, props, labels, borders, dividing lines, and extra views.",
    )
)

MULTIVIEW_SHEET_REQUIREMENTS = " ".join(  # noqa: FLY002 - sentence lists stay diffable
    (
        "Return exactly one 16:9 wide horizontal image containing a single 1-by-3 row; never use a second row, a 2-by-3 grid, stacked panels, or duplicated views.",
        "Show exactly three subject renderings in total, ordered front, left side, then rear from left to right, with one rendering centered in each equal conceptual third of the canvas.",
        "Scale all three renderings equally so every complete silhouette fits inside its own third. Keep each subject at roughly 55 to 75 percent of the canvas height, and shrink all three further when wide wings, limbs, hair, clothing, weapons, or accessories require more horizontal clearance.",
        "Reserve clearly empty neutral-background gutters between adjacent silhouette bounding boxes, each at least 7 percent of the full canvas width, plus visible empty outer margins. No pixel belonging to one view may enter another view's third; the views must not touch, overlap, or visually merge.",
        "Hair, limbs, loose parts, weapons, accessories, floor shadows, and effects must remain inside their own column and must not enter either gutter.",
        "Each view must be independently crop-ready. Do not return separate files or add text, captions, frames, borders, panel lines, alternate poses, detail insets, or extra views.",
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
