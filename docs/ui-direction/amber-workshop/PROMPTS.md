# Amber Workshop 方向稿生成提示集

## 通用提示

```text
Use case: ui-mockup
Asset type: production-feasible AIPicToModel desktop application visual direction
Target dimensions: 1600 × 900 landscape

Use the approved Amber Workshop image as the authoritative global style and shell
reference. Use the corresponding current-page screenshot as the authoritative
functional inventory. Redesign information hierarchy and composition; do not
merely recolor the source.

Preserve the app/project/status header, left navigation and bottom task strip.
Where the six workspace destinations appear, treat 图片主页, 风格转化提示器,
图像生成, 目标提取, 三视图 and 3D 模型生成 as completely peer-level destinations.
Never add numbering, connectors, arrows, locks, checks, completion, progress or
dependency semantics. Only the active destination uses brass text and a thin
underline or quiet selected surface.

Keep the complete ~380px AI Agent permanently visible on the right. Preserve
AI Agent, History, New, conversation, generated image, input/send and Pin
parameters. Never collapse or replace it.

Palette: #11100E background, #1A1815 shell, #211E19 panels, #2A261F controls,
#F4F0E8 primary text, #AAA297 secondary text, #D9A441 current/primary,
#B8734A generated-content auxiliary, #4BC3A5 Agent/service.

Use fine warm borders, 6–8px radii and minimal shadows. Avoid purple, glass blur,
neon and decorative gradients. Everything must be achievable with existing
React, CSS Grid/Flex, CSS variables and ordinary icon-library icons.

Strictly preserve existing functions, states and business loop. Do not add fake
metrics, ornamental widgets or new business features. Intentional whitespace is
welcome. No clipped content, browser/device frame or watermark.
```

## 页面专用提示

### 02 Reference Prompt

```text
Use a 38:62 editor composition. The left compact dual-reference rail preserves
CONTENT/内容参考 and STYLE/风格参考 previews and all load, restore, choose-image
and screenshot controls. The right segmented editor preserves 内容分析（主体与结构）,
风格分析（美术与质感） and 合并 Prompt, including editable Chinese/English
fields, reanalyze, save-content, save-style and generate-merged-prompt actions.
Keep all three editor sections discoverable in the first screen.
```

### 03 Image Generation

```text
Use a compact ~320px parameter rail plus a dominant result stage. Preserve the
provider, Prompt editor, 中文/English, clear, smart rewrite, candidate count
2/3/4, aspect ratio, generation state and primary generation action. Preserve
the large selected result, candidate count, set-current-asset, export PNG and
two candidate thumbnails with selected state and filenames.
```

### 04 Target Extraction

```text
Use a compact source/settings rail, dominant extraction canvas and compact
crop/result rail. Preserve source preview, extraction-method choices and
explanation, split presets, all load/choose/screenshot/restore/clear actions,
zoom/fit canvas controls, editable crop rectangle, x/y/width/height, undo/redo,
crop action, regenerate, generation status, task-center entry, result preview,
enter-multiview, set-current, continue-extract and view-in-assets actions.
```

### 05 Multiview

```text
Use source/generation tools at left, a dominant three-region crop canvas and a
compact output/quality rail. Preserve source filename/preview, custom prompt,
file/current/restore/drop controls, auto-split, custom-prompt generation and
clear. Preserve blue/green/orange front/left/back crop regions, three output
previews, target face count and quick values, quality checklist, confirmation
and output-for-3D action.
```

### 06 3D Model

```text
Create an intentional immersive viewport with a centered matte gray cube,
neutral ground grid and restrained studio light. Preserve model.glb, load current
asset, restore, target face count 50000, optimize, browser preview, screenshot,
export FBX and rotate/zoom/pan guidance. Do not add model metrics or extra panels.
```

### 07 Assets

```text
Create a calm mixed-asset library with a sticky compact heading and stable
three-column rhythm. Preserve image assets, current-image state, fit/contain
preview and use-current action; prompt assets with readable excerpt, language,
character count and copy action; JSON/general files with type and size; GLB
preview and view-3D action; existing loading/error/empty and continuous-scroll
behavior. Do not add filters, folders or new metadata/actions.
```

### 08 Jobs

```text
Use an intentional centered 900–1000px task workspace. Preserve title,
description, 2.5-second refresh, hide-finished action, running/needs-attention/
completed filters and counts, search, task type, interrupted jobs with retry,
hide and technical details, and completed jobs with input/output assets and
continue-result action. Use semantic red/green rails and aligned row actions.
```

### 09 Export

```text
Use one restrained 720–780px panel near the optical center. Preserve PROJECT
PACKAGE, title, host-path security contract and all four actions. Place Export
project and Choose export location in the main tier. Keep the page focused on
the current project package format and its validation feedback
compatibility. Do not add recent exports, metrics, file trees or format options.
```
