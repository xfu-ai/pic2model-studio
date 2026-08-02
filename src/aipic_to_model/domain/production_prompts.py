"""Independent production instructions shared by image-generation workflows."""

MULTIVIEW_BASE_PROMPT = " ".join(
    (
        "Produce a modelling reference sheet for the source subject with front, left-side, and rear orthographic views.",
        "Preserve one identical design across all views, including silhouette, proportions, part placement, materials, colors, texture landmarks, and wear.",
        "Keep the subject upright, fully visible, centered, and aligned to the same baseline and scale.",
        "Use flat neutral illumination and an even unobtrusive background; exclude perspective staging, props, labels, borders, and extra views.",
    )
)

MULTIVIEW_SHEET_REQUIREMENTS = " ".join(
    (
        "Return one horizontal image divided into three equal, borderless regions.",
        "The regions must be ordered front, left side, then rear from left to right.",
        "Do not return separate files or place any text inside the image.",
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
