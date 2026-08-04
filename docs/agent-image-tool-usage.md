# Agent 图片工具使用规则

`generate_images` 直接接收完整 Prompt。Agent 不调用 `prepare_prompt`；系统会把该 Prompt 保存为受管 Prompt，并在“创意图生成”页签和生成结果一起展示。

- 普通生成、变体、转图、多视图，以及图片已足够清晰的 3D 请求：模型自行理解图片；受管图片仅在确实需要具体视觉事实时调用 `understand_image`。
- 用户需要对内容做可复用的说明或规格：调用 `analyze_image(content)`。
- 用户需要分析、保留、比较或复用视觉风格：调用 `analyze_image(style)`。
- 用户要求、或任务确实无法判断图片是否适合 3D：调用 `analyze_image(3d_suitability)`。
- 内容和风格分析默认不同时调用；只有用户明确提出两个独立目标时才分别调用。
- `refresh=true` 只用于用户明确要求重新分析。
