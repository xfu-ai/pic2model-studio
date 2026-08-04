export const DIRECT_TARGET_PROMPT = [
  "Use the green selection only as the subject boundary.",
  "Reconstruct the selected object as a single isolated studio reference for 3D production.",
  "Retain its silhouette, proportions, visible construction, colors, surface response, and distinguishing details.",
  "Exclude every object outside the selection, the original environment, text, labels, and decorative additions.",
  "Center the complete subject against an even neutral charcoal background with clear separation and no cropping.",
].join(" ");

export const SCENE_BREAKDOWN_PROMPT = [
  "Create a modular parts reference for the principal object in the source image.",
  "Separate only physically meaningful components and arrange them with generous spacing in a balanced grid.",
  "Keep each part's original scale relationship, shape, material, color, texture, wear, and attachment details.",
  "Show complete, non-overlapping parts on an even neutral charcoal background.",
  "Exclude captions, numbering, diagrams, callout lines, invented internals, and unrelated scene objects.",
].join(" ");

export const CHARACTER_BREAKDOWN_PROMPT = [
  "Create a production parts reference for the character shown in the source image.",
  "Separate the visible body, hair, garments, footwear, equipment, props, and accessories into complete non-overlapping groups.",
  "Preserve the character's proportions, shapes, palette, materials, surface detail, and recognizable construction.",
  "Arrange the groups clearly on an even neutral charcoal background with consistent scale.",
  "Exclude captions, numbering, guide lines, duplicated parts, hidden anatomy guesses, and unrelated objects.",
].join(" ");

export const MULTIVIEW_BASE_PROMPT = [
  "Produce a clean, wide modelling reference sheet containing exactly three orthographic views of the source subject: front, left-side, and rear.",
  "Preserve one identical design across all views, including silhouette, proportions, part placement, materials, colors, texture landmarks, and wear.",
  "Keep every view upright, fully visible from its outermost top to bottom and left to right, centered in its own column, and aligned to the same baseline and scale.",
  "Leave generous neutral-background clearance around every complete silhouette so each view can be cropped independently.",
  "Use flat neutral illumination and an even unobtrusive background; exclude perspective staging, props, labels, borders, dividing lines, and extra views.",
].join(" ");

export const MULTIVIEW_SHEET_REQUIREMENTS = [
  "Return one wide horizontal image laid out as three equal conceptual columns, ordered front, left side, then rear from left to right.",
  "Place exactly one complete view in each column and keep each subject at roughly 75 to 85 percent of the canvas height, with visible background above and below.",
  "Leave a clearly empty vertical gutter of neutral background between adjacent silhouette bounding boxes, at least 5 percent of the full canvas width; the views must not touch, overlap, or visually merge.",
  "Hair, limbs, loose parts, weapons, accessories, floor shadows, and effects must remain inside their own column and must not enter either gutter.",
  "Each view must be independently crop-ready. Do not return separate files or add text, captions, frames, borders, panel lines, or extra views.",
].join(" ");

export function regenerateViewPrompt(view: string) {
  return [
    `Recreate the ${view} orthographic view as a single complete image.`,
    "Keep the existing subject identity, proportions, construction, material response, colors, texture landmarks, scale, and alignment.",
    "Correct only the requested view; use neutral lighting and background, and exclude text, props, perspective staging, and new design details.",
  ].join(" ");
}
