# Agent 图片工具使用规则

`image.generate_from_prompt`、`image.transform_from_reference` 和 `image.generate_variants` 可直接接收完整 Prompt。Agent 不调用 `prepare_prompt`；系统会把该 Prompt 保存为受管 Prompt，并在“创意图生成”页签和生成结果一起展示。

模型始终只看到 10 个常驻 Tool。Planner 会在 Executor 首轮请求前预加载计划所需的单操作 Tool；执行中能力不足时，模型先用 `toolbox.status` 搜索，再用 `toolbox.load` 追加。新 Tool schema 从下一轮模型请求开始可调用，不会被注入 system message 或改写历史消息。

多步任务按 Tool Result 串行衔接：下一步直接复制上一步的 `output_asset_refs`，不得通过资产列表、文件名或“最新资产”重新猜测。参考图换风格默认使用 `mode=from_image` 和原始 `source_asset_ref`；用户只改背景、颜色、材质、光照或尺寸时仍保留该绑定。

- 普通生成、变体、转图、多视图，以及图片已足够清晰的 3D 请求：模型自行理解图片；受管图片仅在确实需要具体视觉事实时调用 `image.understand_for_agent`。
- 用户需要对内容做可复用的说明或规格：调用 `image.analyze_content`。
- 用户需要分析、保留、比较或复用视觉风格：调用 `image.analyze_style`。
- 用户要求、或任务确实无法判断图片是否适合 3D：调用 `image.evaluate_3d_suitability`。
- 内容和风格分析默认不同时调用；只有用户明确提出两个独立目标时才分别调用。
- `refresh=true` 只用于用户明确要求重新分析。
