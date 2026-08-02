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
  "Produce a modelling reference sheet for the source subject with front, left-side, and rear orthographic views.",
  "Preserve one identical design across all views, including silhouette, proportions, part placement, materials, colors, texture landmarks, and wear.",
  "Keep the subject upright, fully visible, centered, and aligned to the same baseline and scale.",
  "Use flat neutral illumination and an even unobtrusive background; exclude perspective staging, props, labels, borders, and extra views.",
].join(" ");

export const MULTIVIEW_SHEET_REQUIREMENTS = [
  "Return one horizontal image divided into three equal, borderless regions.",
  "The regions must be ordered front, left side, then rear from left to right.",
  "Do not return separate files or place any text inside the image.",
].join(" ");

export function regenerateViewPrompt(view: string) {
  return [
    `Recreate the ${view} orthographic view as a single complete image.`,
    "Keep the existing subject identity, proportions, construction, material response, colors, texture landmarks, scale, and alignment.",
    "Correct only the requested view; use neutral lighting and background, and exclude text, props, perspective staging, and new design details.",
  ].join(" ");
}
