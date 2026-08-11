# Pic2Model Studio 当前 Tool、Description 与 Schema 分析

> 快照日期：2026-08-06  
> 分析对象：当前工作树（含尚未提交的 `progressive_tools.py` 等改动），不是仅分析 `HEAD`。  
> 范围：Agent 模型可见 Tool、渐进加载机制、底层应用原子 Tool Manifest、实际 dispatcher / service / job handler。  
> 本文只分析 Tool 调用参数的 input schema；统一结果结构另列说明。

## 0. 三阶段实施状态

本审计提出的三阶段优化已于同一工作树完成。后续章节保留了初始问题的分析过程；如与本节冲突，以本节描述的当前实现为准。

| 阶段 | 已实施内容 | 当前结果 |
|---|---|---|
| 一：正确性 | 移除 transform/variants 不被原子层接受的 `seed/steps`；收紧 metadata/compare/单模型 3D Tool 的精确数组基数；把 preview 统一为同步 `capture_model_preview` UI action；dispatcher 缺省注入 `face_limit=100000` | 模型 schema 与原子 schema 可双向校验，preview 不再伪装成 Job |
| 二：选择准确率 | 40 个业务 Tool 改为逐 Tool description；加入用途、替代项、禁止项、local/external、审批和结果语义；增加中英文 search terms；Toolbox 返回 description、required 参数和 execution mode；Planner 映射并入 Tool 声明源 | 相近 Tool 有明确选择边界，中文/英文均可发现，Planner 与 catalog 不再维护两张映射表 |
| 三：原子结果与兼容性 | `prompt.get_current` 返回结构化 Prompt；`model3d.inspect` 返回并透传 inspection；未接线的 `project.export_package` 稳定失败并引导桌面 Export；3D status/cancel 兼容别名明确引导通用 `job.*`；所有 65 个原子 Tool 都有 legacy Agent guidance；4 个内置 Tool schema 封闭并增加边界 | 不再返回虚假成功或丢失关键结构化结果；旧 1.0.0 名称保持兼容但不会诱导新模型选用 |

新增的全量翻译合同测试会为 40 个业务 Tool 分别生成最小合法模型参数，执行 Facade 翻译，并用目标原子 JSON Schema 再校验一次。它保证每个模型 Tool 的必填字段都有来源、外层允许的字段能被底层接受、固定/注入字段符合原子合同。

验证状态：定向测试 88 项通过；完整 `scripts/run_controlled_validation.ps1` 通过（Python 41 + 195 + 277，前端 194，Rust 5 + 2）。未调用真实付费 Provider。

## 1. 结论摘要

当前项目不能用一个数字简单回答“有多少 Tool”，因为同一业务能力有三层表示：

| 层级 | 当前数量 | 谁使用 | 当前状态 |
|---|---:|---|---|
| Agent 模型目录 | 46 | LLM | 4 个本地文件/命令 Tool + 2 个 Toolbox Tool + 40 个单操作业务 Tool |
| Agent 每轮默认常驻 | 10 | LLM | `read/write/edit/bash`、2 个 Toolbox、4 个高频业务 Tool；其余按需加载 |
| 应用原子 Manifest | 65 | REST、UI、审计、dispatcher | B01 18 个 + B02 47 个；是业务执行和审计的底层合同 |
| 旧聚合 Facade | 11 | 仅作为 40 个单操作 Tool 的内部 dispatcher | 不再直接暴露给模型，但实现仍在使用 |

整体方向合理：模型看到的是窄 schema，Provider、项目 ID、审批和能力 ID主要由应用注入；底层仍保留细粒度审计合同。初始审计发现的主要漂移已经按上面的三个阶段修复。

最需要先修的事项：

1. ✅ transform/variants 的 `seed/steps` 外层/原子 schema 漂移已修复。
2. ✅ metadata、compare 和单模型 3D Tool 的数组基数已收紧。
3. ✅ `model3d.render_preview` 已统一为 sync、不可取消、`capture_model_preview` UI action；无效 Job handler 注册已删除。
4. ✅ 40 个单操作 Tool 已使用独立 description 和中英文 search terms。
5. ℹ️ 最长 180 秒等待是当前 `agent-task-planning-and-job-wait-implementation-plan.md` 的明确产品设计；旧 `agent-model-tool-contract.md` 的“queued 立即结束回合”表述需要另行同步，不再视为当前实现缺陷。
6. ✅ `prompt.get_current` 已返回结构化 Prompt，与只做合法性检查的 `prompt.validate` 分离。
7. ✅ `model3d.inspect` 的结构化 inspection 已进入 Tool data 和模型可见 continuation。
8. ✅ 未连接 submitter 时 `project.export_package` 不再返回 phantom queue，而是稳定 `TOOL_NOT_AVAILABLE/use_desktop_export`。
9. ✅ 4 个内置文件/命令 Tool 的 schema 已封闭，并增加路径非空、读取范围和 timeout 边界。
10. ✅ `toolbox.status` 已支持中英文 aliases、分词搜索和排序，并返回 description、required 参数与 execution mode。

## 2. 当前架构与“实际可调用”的定义

当前 Agent Runtime 在 `agent/integrations/runtime.py::_build` 中构造 4 个内置 Tool，并调用 `build_progressive_tool_catalog`。完整目录包含 46 个 Tool，但每轮发送给模型的是 `ActiveToolSet`：

- 永久常驻：`read`、`write`、`edit`、`bash`、`toolbox.status`、`toolbox.load`、`project.get_state`、`image.understand_for_agent`、`image.remove_background_local`、`model3d.generate_from_image`。
- Planner 可预加载它判断需要的单操作 Tool。
- 模型可用 `toolbox.status` 搜索，再用 `toolbox.load` 激活；新 schema **下一模型回合**才可调用。
- 已激活 Tool 会保存在 session；正在等待 Job 的 Tool 会被 pinned。
- 旧的 `inspect_workspace`、`edit_image`、`generate_model3d` 等 11 个聚合名称不在模型目录中；40 个单操作 Tool 仍复用它们的 `_FacadeDispatcher` 做参数翻译。

因此本文把“当前所有 Tool”分为：

1. **模型 Tool**：46 个，决定 LLM 实际能看到和调用什么。
2. **原子 Tool**：65 个，决定底层实际执行、审批、Job、审计与 REST/UI 合同。
3. **旧聚合 Facade**：是实现组件，不计入当前模型 Tool 数，但必须参与实际功能分析。

## 3. Schema 记法和统一结果

后续表格使用紧凑记法：

- `*field:type`：必填。
- `field?:type`：选填。
- `enum[a|b]`：只接受列出的值。
- `ref`：不透明的受管对象 ID；模型不应填写路径、URL、Provider 凭据或项目 ID。
- 40 个业务 Tool 均额外接受 `plan_step_id?: string(1..80)`，仅更新计划展示进度，不授予权限。
- 当前 46 个模型 Tool schema 均为封闭对象（`additionalProperties: false`）。

应用原子 Tool 共用 `ToolResultV1` 结果形状：

```text
ok:boolean
status: succeeded | queued | awaiting_ui_action | failed
tool_call_id:string
output_asset_ids:string[]
summary:string
warnings:string[]
expected_action?:object|null
ui_action?:object|null
job?:object|null
error?:object|null
reused:boolean
```

Agent adapter 会再补充 `output_refs`、`retry`、`data`、`verification` 等内部字段。模型 Tool 目前没有独立声明 output JSON Schema；Provider 只接收 input schema，结果通过 Tool message 返回。

## 4. Agent 模型目录：46 个 Tool

### 4.1 内置文件与命令 Tool（4）

| Tool | 当前 description（原意） | Schema | 实际功能 | 何时使用 / 相近 Tool | 评价与优化 |
|---|---|---|---|---|---|
| `read` | 读取 workspace 内文本；禁止读 secrets、项目数据库、受管二进制和 workspace 外路径 | `*path:string; offset?:integer; limit?:integer` | `LocalExecutionEnv.read_text` 后按行编号截取；默认 offset=1、limit=2000 | 用户要求查看源码/配置/说明时；受管资产信息用 `asset.get_metadata`，图像理解用 `image.understand_for_agent` | 描述合理；schema 应封闭，并给 `offset>=1`、`limit>=1` 和上限 |
| `write` | 原子写入 workspace 文本；禁止改受管资产、数据库、审批或 Provider 状态 | `*path:string; *content:string` | 原子写完整文本文件 | 新建用户要求的源码、配置、笔记；精确替换优先 `edit` | 描述合理；schema 应封闭；可增加 content 大小上限 |
| `edit` | 对 workspace 文本执行一次精确匹配替换 | `*path:string; *old_text:string; *new_text:string` | `edit_text` 精确替换一次/按环境语义报错 | 小范围已知文本修改；新文件或整体重写用 `write` | 描述合理；schema 应封闭并约束 `old_text` 非空 |
| `bash` | 执行 workspace 范围 PowerShell 诊断/脚本；不得绕过业务 Tool、审批或 UI action | `*command:string; timeout?:number; cwd?:string` | 启动 PowerShell，返回 stdout/stderr、exit code 和可选 artifact | 构建、测试、代码搜索、诊断；任何受管业务写操作用对应业务 Tool | 名称 `bash` 与 Windows/PowerShell 实际行为不一致；建议改 label/description 为 PowerShell，schema 封闭，`timeout>0` |

### 4.2 渐进加载 Tool（2）

| Tool | 当前 description | Schema | 实际功能 | 何时使用 | 评价与优化 |
|---|---|---|---|---|---|
| `toolbox.status` | 检查当前 Tool 区或按能力搜索目录；不激活、不执行 Tool | `query?:string(max120); max_results?:integer(1..40, default12)` | 对 name + label + description 做大小写不敏感的**连续子串**匹配；返回 active/permanent 标记 | 需要的单操作 Tool 当前未激活，或不知道准确名称时 | 意图合理；应加入中英文 aliases/tags、token/fuzzy 匹配，并返回 description 摘要、风险、sync/job、schema required 字段 |
| `toolbox.load` | 激活 `toolbox.status` 找到的精确名称；下一回合生效 | `*tool_names: unique string[1..12]` | 验证目录名称，返回 `added_tool_names`，由 Harness 在下一回合追加 schema | 已知道精确 Tool 名且未激活时 | 描述与实际一致；应拒绝/提示已常驻名称，返回未加载原因和目录版本 |

### 4.3 工作区与资产（6）

40 个单操作 Tool 的公共 description 模板是：

> `<label>. This Tool performs exactly this one operation; it does not select a different workflow or dispatch another operation. Use exact managed references returned by prior Tool Results. Do not invent paths, provider profiles, approvals, credentials, or IDs.`

只有 `image.remove_background_local` 额外补了一句不要无证据猜 `target_color`。模板安全边界正确，但以下表格“当前 description”主要只有 label 所表达的差异。

| Tool | Schema（不重复列 `plan_step_id`） | 实际映射/功能 | 什么时候用 / 不用 | Description/Schema 评价 |
|---|---|---|---|---|
| `project.get_state` | 无业务参数 | 注入当前 project ID，调用原子 `project.get_state`；返回项目与持久化 workspace state | 需要当前项目状态、尤其已确认 multiview set/crop refs 时；不要查 Job 或实时 UI 临时状态 | 常驻合理；description 应恢复“multiview 持久化状态优先”的关键规则 |
| `asset.list` | `group?: enum[input_images|generated_images|split_elements|multiview_and_crops|models|exports]` | 原子 `asset.list`，结果在 Facade 层按新到旧排序 | 不知道资产 ref、要按组发现资产时；不要轮询 Job | schema 合理；没有分页，资产多时上下文可能膨胀；description 应说明排序和非分页 |
| `asset.get_metadata` | `*asset_refs: unique ref[](max2)` | 执行层强制恰好 1 个，调用 `asset.get_metadata`，返回资产与 lineage | 已知单个资产、要核对类型/来源/元数据时 | **schema 错误：应设 `minItems=maxItems=1`** |
| `asset.compare` | `*asset_refs: unique ref[](max2)` | 执行层强制恰好 2 个，调用 `asset.compare` 并打开比较工作区 | 比较两个 sibling 版本；不用于语义/风格分析 | **schema 错误：应设 `minItems=maxItems=2`**；description 应写 sibling 限制 |
| `runtime.get_capabilities` | 无业务参数 | 不走原子注册表；直接返回 runtime context 的非敏感能力/配置状态 | 调用外部或本地可选能力前确认是否可用 | 伪 Tool 设计合理；名称容易被误认为原子 Manifest，建议显式标记 virtual/read-only |
| `asset.set_current` | `*asset_ref:ref; *reason:string(1..500)` | 映射 `asset.set_current`，固定 `decision_source=agent` | 用户明确选择，或工作流只有唯一无歧义结果时；不要用于预览、页面 slot 或候选比较 | schema/行为合理；description 应带回“不能替用户在候选中选择” |

### 4.4 图像理解与持久化分析（4）

| Tool | Schema | 实际映射/功能 | 什么时候用 / 不用 | 评价 |
|---|---|---|---|---|
| `image.understand_for_agent` | `*source_asset_ref:ref; *question:string(1..4000)` | 注入 Gemini profile/model，调用同步外部 `image.understand_for_agent`；返回纯文本，不创建分析资产 | 日常看图问答、模型需要理解画面时 | 常驻且边界合理；应在 Tool description 强调“非持久化”以及会访问外部视觉 Provider |
| `image.analyze_content` | `*source_asset_ref:ref; refresh?:boolean(false)` | 调 `image.analyze_content` Job；refresh=true 转为固定 revision 标识 | 用户明确要持久化主体/构图/场景分析，或 prompt workflow 明确需要时 | 与 understand 接近；当前通用 description 未解释差异，搜索和选用易错 |
| `image.analyze_style` | `*source_asset_ref:ref; refresh?:boolean(false)` | 调 `image.analyze_style` Job，生成持久化风格分析资产 | 要持久化色板、光照、材质、渲染语言时 | 应说明不是内容识别；当前 description 太泛 |
| `image.evaluate_3d_suitability` | `*source_asset_ref:ref` | 调外部视觉 Job，评估几何可见性和重建适合度 | 单图 3D 前不确定遮挡/结构时；已有已确认 multiview 时无需调用 | 与通用质量评分不同，description 应明确；当前不够充分 |

选择准则：普通问题优先 `image.understand_for_agent`；只有结果要成为项目资产或下游 prompt 工作流输入时，才用三个 `analyze/evaluate` Tool。不要为了同一个问题先 understand 再 analyze。

### 4.5 图像生成（4）

| Tool | Schema | 实际映射/功能 | 什么时候用 / 不用 | 评价 |
|---|---|---|---|---|
| `image.generate_from_prompt` | `*prompt:string(1..20000); *candidate_count:enum[1|2|4]; aspect_ratio?/size?/quality?:string; output_format?:enum[png|jpg|webp]; structure_strength?:0..1; seed?:0..2147483647; steps?:1..20` | 先把直接文本物化为受管 Prompt，再调用 `image.generate`；应用选择 Provider；需审批 | 新的纯文本生图，且没有要复用的 prompt 资产 | 直传 prompt 降低调用步数合理；`structure_strength` 对无 source 的 t2i 无意义，应从 schema 移除 |
| `image.generate_from_prompt_asset` | `*prompt_asset_ref:ref; *candidate_count...`，其余同上 | 复用已有 Prompt，调用 `image.generate`；需审批 | 明确复用用户或已有受管 Prompt | 与上一 Tool 区分合理；description 应说明 prompt 资产必须已经存在且有效 |
| `image.transform_from_reference` | `*source_asset_ref; prompt XOR prompt_asset_ref; *candidate_count...; seed?/steps?` | 直接 prompt 会先物化；映射 `image.transform`；需审批 | 保留参考图结构做图生图/风格变换；不用于纯文本生成、小区域修补或 variants | **P0：原子 `image.transform` schema 不接受 `seed/steps`，当前这两个参数会导致合法外层调用失败** |
| `image.generate_variants` | 与 transform 相同 | 映射 `image.generate_variants`；需审批 | 用户要同一来源的多个替代方案；不用于相同参数重试或 upscale | **同样存在 `seed/steps` schema 漂移**；与 transform 的业务差异只在 label，description 不足 |

四个 Tool 最终都经 `_FacadeDispatcher`。如果底层返回 `queued`，当前实现会等待 terminal 最长 180 秒；超时才返回等待外部结果，这一点与 Tool description 和 durable-job 文档不一致。

### 4.6 图像编辑（8）

| Tool | Schema | 实际映射/功能 | 什么时候用 / 相近 Tool | 评价 |
|---|---|---|---|---|
| `image.trim_transparent` | `*source_asset_ref; padding?:0..256; alpha_threshold?:0..255` | 本地同步裁掉透明边缘，生成新受管资产和 verification | 已有 alpha、只去透明空边 | 合理；与 crop 不同，crop 基于用户选区 |
| `image.normalize` | `*source_asset_ref; target_width?/target_height?/max_long_edge?:1..16384; lock_aspect_ratio?:bool; rotate_degrees?:enum[0|90|180|270]; flip?:enum[none|horizontal|vertical]; output_format?:enum[png|jpeg|webp]; quality?:1..100; preserve_alpha?:bool` | 本地同步调整尺寸、方向、格式和 alpha，生成新资产 | 统一尺寸/方向/编码；不做语义增强 | 功能清楚；schema 未表达 target width/height/max edge 的组合约束，需记录优先级或用 `oneOf` |
| `image.remove_background_local` | `*source_asset_ref; *background_method:enum[color_key|channel]; target_color?:RGB[3]; tolerance?:0..255; contiguous_only?:bool; channel?:enum[red|green|blue|luminance|saturation]; min_threshold?/max_threshold?:0..255; invert?:bool; feather?/edge_shrink?:0..20` | 本地同步色键或通道阈值抠图；target_color 缺省时从角点推断；生成 verification | 平坦纯色/绿幕等可确定背景；复杂自然背景改用 Provider | 常驻有利于离线优先；额外 note 合理；应以条件 schema 区分 color_key/channel 参数，并约束 min<=max |
| `image.upscale_local` | `*source_asset_ref; *scale:enum[2|4]` | 本地可恢复 Job，使用 bundled Real-ESRGAN | 离线放大、可接受本地模型效果时 | 与 provider 版区分合理；description 应说明是 Job 而非同步 |
| `image.upscale_provider` | `*source_asset_ref; *scale:enum[2|4]` | 映射原子 `image.upscale`，应用注入 Provider | 本地能力不可用或用户明确要 Provider 时 | 名称清晰；当前 description 未说明外部网络/成本策略 |
| `image.remove_background_provider` | `*source_asset_ref` | 映射原子 `image.remove_background` | 复杂背景、头发/半透明边缘，本地阈值不合适时 | 与 local 版区分清楚，但选择规则只存在旧聚合 description 中 |
| `image.inpaint_selection` | `*source_asset_ref; *selection_ref; *prompt_asset_ref` | 映射 `image.inpaint_selection`；需要已确认选区和参数绑定审批 | 只修改选中区域 | schema 充分；description 应明确 selection 必须 confirmed、会审批 |
| `element.export_transparent` | `*source_asset_ref` | 让 Provider 把已经提取的元素输出为透明图 | 元素已经被抽取，只需透明交付图 | 容易与两种 remove background 混淆；description 应说明输入必须是 extracted element |

### 4.7 图像拆分与用户选区（5）

| Tool | Schema | 实际映射/功能 | 什么时候用 / 不用 | 评价 |
|---|---|---|---|---|
| `image.split_alpha_components` | `*source_asset_ref; alpha_threshold?:0..255; min_area?:1..100000000; padding?:0..256; max_outputs?:1..256` | 本地连通 alpha component 拆分，同步生成多个资产和 verification | 透明图中元素已经由 alpha 隔开 | 合理、确定性强，优先于 Provider semantic split |
| `image.split_grid` | `*source_asset_ref; *columns:1..64; *rows:1..64;` 加同上可选项 | 本地规则网格拆分 | 已验证的规则 sprite/grid 图 | 应说明不会自动判断网格；columns×rows 与 max_outputs 的关系未进 schema |
| `selection.request_user` | `*source_asset_ref` | 固定 boxsplit 上下文，调用 B01 `selection.request_user`，打开矩形选区 UI | 需要用户画/调整区域但尚无 confirmed selection 时 | 名称看似通用，实际仅用于 extraction flow；建议改为 `selection.request_user_for_split` 或在 description 明说 |
| `element.split_semantic` | `*source_asset_ref; *prompt_asset_ref` | 映射 `element.split(split_mode=element)`；Provider 语义拆分、需审批 | 元素不能由 alpha/grid 确定拆分时 | 与 `element.split_selection` 差异应写入 description |
| `element.split_selection` | `*source_asset_ref; *selection_ref; *prompt_asset_ref` | 映射 `element.split(split_mode=boxsplit)`；已确认矩形 + Provider 生成、需审批 | 用户已确认一个框，要按框抽取/重绘元素 | 不是简单 crop；description 应明确它会调用 Provider，简单矩形裁切应走 UI 原子 `image.crop` |

### 4.8 Multiview（3）

| Tool | Schema | 实际映射/功能 | 什么时候用 / 不用 | 评价 |
|---|---|---|---|---|
| `multiview.generate` | `*source_asset_ref; prompt_asset_ref?:ref` | 生成 front-side-back sheet，需审批；输出首先是 sheet，不是 persisted set ID | 从单图新建三视图 sheet | description 应强调输出类型以及后续需用户确认区域 |
| `multiview.detect_regions` | `*multiview_ref` | 外部视觉 Job，在已有 persisted set 上实验性检测三个区域 | 用户明确要自动检测，且还没有已保存的三个 crop | 已有 confirmed crops 时绝不能调用；当前通用 description 丢失这一关键规则 |
| `multiview.regenerate_view` | `*multiview_ref; *target_view:enum[front|side|back]` | 重生成恰好一个方向，需审批 | 只修复现有 set 的一个坏视图 | 与重新生成整张 sheet 区分合理；description 应提示会产生新版本/需再次确认的状态变化 |

注意：用户确认 regions、crop views、可选 quality checks 等原子 Tool 不暴露给模型，当前由桌面 UI/内部流程负责。模型应以 `project.get_state` 中已持久化的 confirmed crop refs 为准。

### 4.9 3D 模型（7）

`parameters` 当前允许：`model_version?:ref`、`texture_quality?:standard|detailed|extreme`、`geometry_quality?:standard|detailed`、`texture_alignment?:original_image|geometry`、`texture?:const true`、`pbr?:const true`、`quad?:bool`、`face_limit?:integer>=0(default 100000)`、`auto_size?:bool`、`orientation?:default|align_image`、`smart_low_poly?:bool`、`generate_parts?:bool`、`compress?:''|geometry`、`enable_image_autofix?:bool`、`model_seed?/texture_seed?:integer>=0`。`parameters` 本身必填，但允许空对象；dispatcher 会强制写入 `texture=true,pbr=true`。

| Tool | Schema | 实际映射/功能 | 什么时候用 / 不用 | 评价 |
|---|---|---|---|---|
| `model3d.generate_from_image` | `*image_asset_ref; *parameters:object` | 映射 `model3d.generate(mode=image)`，固定 Tripo profile/model，需审批；当前常驻 | 只有一个适合重建的受管图像时 | 常驻但付费操作仍有审批，风险可控；通用 description 丢失 face budget 选择规则；空 parameters 使模型可不选预算 |
| `model3d.generate_from_multiview` | `*multiview_ref; *view_asset_refs:{front,side,back}; *parameters` | 映射 `model3d.generate(mode=multiview)`；set ID 与三个 distinct confirmed crop refs 都必须正确 | 已有确认的 front/side/back crop 时，优先于单图 | description 必须强调 `multiview_ref` 是 persisted set ID，不是 sheet/crop ID；当前模板没有 |
| `model3d.inspect` | `*asset_refs: unique ref[](1..32)` | 执行层只接受 1 个 GLB；本地检查并持久化报告，但 Tool Result 不返回报告 | 生成/导入模型后检查真实性、几何、材质 | **schema 应 maxItems=1；Result 应包含 inspection DTO** |
| `model3d.render_preview` | `*asset_refs: unique ref[](1..32)` | 执行层只接受 1 个；原子 runtime 直接返回 `capture_model_preview` UI action | 用户要在桌面打开模型并捕获预览图时 | **schema 应 maxItems=1；名称/label/Manifest execution 均与实际需统一** |
| `model3d.convert` | `*asset_refs: unique ref[](1..32)` | 固定转换 GLB→FBX，本地 Job | 用户明确要 FBX；不用于优化 | **schema 应 maxItems=1**；外层无需再暴露固定 `target_format` 是合理收窄 |
| `model3d.optimize` | `*asset_refs: unique ref[](1..32); target_triangles?:1..10000000; max_texture_bytes?:1..209715200` | 本地 Job；能力不可用时稳定失败 | 需要减面/压缩纹理预算时 | **schema 应 maxItems=1**；description 应提示先查 capability |
| `model3d.package` | `*asset_refs: unique ref[](1..32)` | 创建受管交付包的本地 Job | 完成模型资产后打交付包；不等于整个项目 `.pic2model` 导出 | schema 与底层一致；description 应强调与 `project.export_package` 区别 |

### 4.10 Job 控制（3）

| Tool | Schema | 实际映射/功能 | 什么时候用 / 不用 | 评价 |
|---|---|---|---|---|
| `job.get_status` | `*job_ref:ref` | 读取真实持久化 Job 状态 | 用户明确问进度且当前上下文没有新 terminal event 时，只查一次 | description 应写“禁止轮询”；旧 guidance 有、当前模板没有 |
| `job.cancel` | `*job_ref:ref` | 根据 Job 状态执行本地取消、远端取消请求或停止等待 | 仅用户明确取消；terminal Job 不调用 | label “Cancel” 没表达 stop-waiting 分支；应在 description/Result 明示实际动作 |
| `job.retry` | `*job_ref:ref` | 仅 safe-to-retry 或安全 interrupted Job；付费重试重新审批，unknown submission 禁止普通 retry | 用户明确要求，且持久化错误允许重试 | 实际安全策略合理；description 过短，未说明审批与 unknown submission |

## 5. 应用原子 Manifest：65 个 Tool

### 5.1 B01 原子 Tool（18）

B01 的实际 description 全部是 `B01 canonical tool: <name>`。它作为冻结合同标识尚可，但作为人或模型的选择说明不合理。B01 文件由 SHA-256 冻结；改 description 需要显式升级合同/fixture，而不是直接改旧文件。

| Tool | Input schema | 执行/风险 | 实际功能与使用时机 | 接近 Tool / 评价 |
|---|---|---|---|---|
| `project.get_state` | `*project_id:string` | sync/read_only | 只读打开项目，返回项目 DTO + workspace_state | 模型外层同名但会注入 project ID；合理 |
| `project.save_checkpoint` | `*project_id; *request_id` | sync/local_reversible | 保存 checkpoint 事件 | 只应由 runtime/用户保存动作触发；不应每次读取后调用 |
| `project.export_package` | `format?:const project_v1; archive_capability_id?/create_root_capability_id?/host_capability_id?:string` | job/local_reversible | 当前注册器使用默认 `InMemoryJobSubmitter` 返回 queued | 与真实 `ProjectPackageService`/桌面导出接线断开；应标 unavailable 或接真实 durable handler |
| `asset.list` | `*project_id; group?:string` | sync/read_only | 按可选 group 返回全部受管资产 | group 在原子 schema 是任意字符串，模型层收窄为 enum；建议原子层也共享 enum |
| `asset.get_metadata` | `*asset_id` | sync/read_only | 返回资产 DTO 与 lineage | 与 `asset.list`：先 list 找 ID，再 metadata 深查 |
| `asset.set_current` | `*asset_id; *decision_source:user|agent|import|system; reason?:string` | sync/local_reversible | 写 current decision 和事件 | 不等于 UI 预览/slot 选择 |
| `asset.compare` | `*left_id; *right_id` | sync/read_only | 比较 sibling 并返回 `compare_assets` UI action | 不做语义分析 |
| `asset.hide` | `*asset_id` | sync/local_reversible | 从正常浏览隐藏资产 | 轻量可恢复；不要用 trash |
| `asset.restore_hidden` | `*asset_id` | sync/local_reversible | 恢复隐藏资产 | 与 trash restore 不同 |
| `asset.move_to_trash` | `*asset_id; impact_token?:string` | sync/**destructive** | 校验影响 token 后移入垃圾箱 | 高风险；必须来自用户明确删除和 impact review；schema 却未要求 token，需由 service 动态决定 |
| `asset.restore_from_trash` | `*asset_id` | sync/local_reversible | 恢复垃圾箱资产 | 不用于 hidden 资产 |
| `asset.open_output_folder` | `*asset_id` | sync/local_reversible | 返回 `open_output_folder` UI action，并不直接操作 shell | risk 更接近 UI_action/local_reversible；功能合理 |
| `selection.get_current` | `*asset_id` | sync/read_only | 返回当前 selection 或 null | 只读，不创建/确认 |
| `selection.request_user` | `*asset_id; run_id?:string` | sync/local_reversible | 返回 `select_rectangle` UI action | 与模型层同名；原子能力更通用 |
| `selection.set_suggestion` | `*asset_id; *rects:object[](min1); label?:string` | sync/local_reversible | 保存 Agent 建议框，状态仍未确认 | rect item 仅 `object`，坐标/label/confidence 未被 JSON Schema 严格约束，建议复用 `_RECT` 合同 |
| `selection.confirm` | `*selection_id; revision?:integer` | sync/local_reversible | 用户确认 selection；缺 revision 时读取当前 revision | 只能基于用户明确确认；不应由模型自行调用 |
| `image.crop` | `*selection_id; revision?:integer` | sync/local_reversible | 从 selection 生成一个或多个干净 crop | 与 annotation 相近；实际代码未使用传入 revision，schema 字段可能是遗留 |
| `image.render_annotation` | `*selection_id; revision?:integer` | sync/local_reversible | 生成画有选区的 annotation 图 | 不用于干净裁图；同样需核对 revision 是否应保留 |

### 5.2 B02 原子 Tool（47）

B02 注册时的实际 description 全部是 `B02 canonical tool: <name>`；contract artifact 没有 `human_name/description` 字段。因此这些描述只能表明稳定名称，不能指导选用。Agent 旧 adapter 曾通过 `tool_guidance.py` 补充丰富说明，但当前渐进式 40 Tool 不再使用该函数。

以下 schema 还要求底层 `provider_profile/model/channel`，但模型外层通常不暴露这些字段，由 Facade/application policy 注入并再次 canonicalize。

#### 分析与 Prompt（9）

| Tool | Schema | 执行/风险/审批 | 实际功能 | 评价 |
|---|---|---|---|---|
| `image.analyze_content` | `*asset_id; *provider_profile; *model; analysis_revision?:string(1..128)` | job/external/否 | Vision Provider 生成持久化 content analysis | 合理；`analysis_revision` 是 refresh 幂等键，不转发 Provider |
| `image.analyze_style` | 同上 | job/external/否 | 持久化 style analysis | 与 content 需靠 description 清楚区分，目前原子描述不合格 |
| `image.evaluate_3d_suitability` | `*asset_id; *provider_profile; *model` | job/external/否 | Vision Provider 评估单图 3D 适配度 | 与一般质量分析不同 |
| `image.understand_for_agent` | `*asset_id; *provider_profile; *model; *question:string(1..4000)` | sync/external/否 | 请求线程内调用视觉 Provider，返回文本，不落分析资产 | 与前三者功能接近但生命周期不同；名称和 guidance 合理，原子描述不合理 |
| `prompt.extract_bilingual` | `*analysis_asset_id; *kind:content|style` | sync/local_reversible | 从完整双语 analysis JSON 创建 Prompt 资产 | 当前不暴露给模型；只适合旧持久化 prompt workflow |
| `prompt.merge` | `*content_prompt_asset_id; *style_prompt_asset_id` | sync/local_reversible | 合并 content/style Prompt，创建 merged Prompt | 与直接生成 prompt 文本不是一回事 |
| `prompt.get_current` | `*prompt_asset_id` | sync/read_only | **实际仅 parse/validate 并返回 “Prompt is valid.”，不返回内容** | 与 `prompt.validate` 实质重复；要么返回 parsed DTO，要么删除/重命名 |
| `prompt.rewrite` | `*prompt_asset_id; *provider_profile; *model; *instruction(1..4000)` | job/external/否 | Vision/LLM Provider 改写已有 Prompt，产生新资产 | 当前不暴露给模型；若恢复需说明不是 deterministic merge |
| `prompt.validate` | `*prompt_asset_id` | sync/read_only | parse Prompt，成功只返回原 ID | 与 `get_current` 重复；建议只保留 validate，或让 get 返回内容 |

#### 图像生成、编辑与拆分（15）

| Tool | Schema | 执行/风险/审批 | 实际功能 | 评价 |
|---|---|---|---|---|
| `image.generate` | `*prompt_asset_id; *provider_profile; *channel:auto|tripo|meshy|z_image; *model; *candidate_count:1|2|4; aspect_ratio?/size?/quality?; output_format?:png|jpg|webp; structure_strength?:0..1; seed?:0..2147483647; steps?:1..20` | job/external_paid/是 | Router 在本地 Z-Image 与外部图像 Provider 间选择；创建候选资产 | 功能完整；t2i 的 structure_strength 无语义；seed/steps 对外部路径可能被忽略，应在 capability/schema 中表达 |
| `image.transform` | 上述 generation 公共字段 + `*source_asset_id`；channel 仅 auto/meshy；**无 seed/steps** | job/external_paid/是 | i2i transform | 与外层 schema 漂移，见 P0 |
| `image.generate_variants` | 与 transform 相同 | job/external_paid/是 | i2i variants | 后端与 transform 都走 generation handler，区别主要是 job type/provenance；description 应解释结果语义 |
| `image.upscale` | `*source_asset_id; *provider_profile; *scale:2|4` | job/external/否 | Provider upscale | 与 `upscale_local` 二选一 |
| `image.remove_background` | `*source_asset_id; *provider_profile` | job/external/否 | Provider 背景移除 | 与 local/color-key/channel、export transparent 三者边界需明确 |
| `image.inpaint_selection` | `*source_asset_id; *selection_id; *prompt_asset_id; *provider_profile` | job/external/**是** | confirmed selection 范围内 Provider edit | 风险枚举是 external 但需审批；策略允许，但应说明为何非 paid 却审批 |
| `image.compress_for_provider` | `*asset_id; minimum?:boolean` | sync/local_reversible | 本地转换为 Provider 可接受的大小/编码 | 仅内部预处理；不等于用户导出或 upscale |
| `image.trim_transparent` | `*source_asset_id; padding?:0..256; alpha_threshold?:0..255` | sync/local_reversible | 本地裁透明边 | 合理 |
| `image.normalize` | 尺寸/旋转/翻转/格式/质量/alpha 可选参数 | sync/local_reversible | 本地规范化 | 建议补组合约束 |
| `image.remove_background_local` | `*source_asset_id; *method:color_key|channel` + 对应参数 | sync/local_reversible | 本地阈值抠图 | 建议用条件 schema 隔离两种 method 参数 |
| `image.split_local` | `*source_asset_id; *mode:alpha_components|grid; columns?/rows?...`；grid 条件必填 columns/rows | sync/local_reversible | 本地连通域或规则网格拆分 | 条件 schema 合理；可增加 columns×rows 上限关系 |
| `image.upscale_local` | `*source_asset_id; *scale:2|4` | job/local_reversible/否 | bundled Real-ESRGAN Job | 合理；description 应说明本地模型和 Job |
| `element.split` | `*source_asset_id; selection_id?; *prompt_asset_id; *provider_profile; *channel:auto|meshy; *model; *split_mode:element|boxsplit`；boxsplit 条件要求 selection | job/external_paid/是 | Provider 语义拆分或按 confirmed box 抽取 | 一个原子 Tool 两种模式合理，但模型层拆成两个更好 |
| `element.export_transparent` | `*source_asset_id; *provider_profile` | job/external/否 | Provider 把 extracted element 输出透明图 | 与背景移除接近，应校验输入 asset type |
| `selection.auto_suggest_boxes` | `*asset_id; *provider_profile; *model` | job/external/否 | Vision 建议框，不自动确认 | 当前不暴露模型，符合用户确认边界 |

#### Multiview（9）

| Tool | Schema | 执行/风险/审批 | 实际功能 | 评价 |
|---|---|---|---|---|
| `multiview.generate` | `*source_asset_id; prompt_asset_id?; *provider_profile; *channel:auto|meshy; *model` | job/external_paid/是 | 生成三视图 sheet | 合理 |
| `multiview.detect_regions` | `*multiview_set_id; *provider_profile; *model` | job/external/否 | Vision 自动检测三个区域 | 只应实验性/显式调用；已有 crops 时不要用 |
| `multiview.request_box_confirmation` | `*multiview_set_id` | sync/local_reversible | 返回确认区域 UI action | 与 set_regions 分工合理 |
| `multiview.set_regions` | `*multiview_set_id; *regions:{front,side,back}`，每个 rect 为 `x/y>=0,width/height>=1` | sync/local_reversible | 持久化三块区域 | schema 完整；仍需 service 校验越界/重叠 |
| `multiview.crop_views` | `*multiview_set_id` | sync/local_reversible | 从已确认 regions 生成三个受管 crop | 合理 |
| `multiview.request_quality_confirmation` | `*multiview_set_id` | sync/local_reversible | 返回六项 quality UI action | 当前产品规则称 quality 非 3D 前置条件；description/流程需保持一致，避免旧逻辑阻塞 |
| `multiview.set_quality_checks` | `*multiview_set_id; *checks:{subject_scale,direction,key_accessory,truncation,background,resolution}`，每项 passed/warning/blocking | sync/local_reversible | 保存可选质量检查并判断可继续 | schema 很好；只能接受用户实际提供的结果 |
| `multiview.validate` | `*multiview_set_id; *provider_profile; *model` | job/external/否 | Provider 验证三视图一致性 | 不是 3D 生成强制前置，也不能替代 crop 确认 |
| `multiview.regenerate_view` | `*multiview_set_id; *view:front|side|back; *provider_profile; *channel:auto|meshy; *model` | job/external_paid/是 | 只修一个方向 | 合理 |

#### 3D 与 Job（14）

| Tool | Schema | 执行/风险/审批 | 实际功能 | 评价 |
|---|---|---|---|---|
| `model3d.generate` | `*mode:image|multiview; image_asset_id?; multiview_set_id?; view_asset_ids?; *provider_profile; *model; *parameters`；按 mode 条件必填 | job/external_paid/是 | Router 选择本地 TripoSR 或远端 Tripo；远端生成/下载/检查/注册由 Job 完成 | 合理；schema 只要求相应字段存在，未禁止另一模式多余字段，Facade 在运行时补防御校验 |
| `model3d.get_status` | `*job_id` | sync/external/否 | 直接复用通用 `_status` | 与 `job.get_status` 完全重复，建议废弃专用别名 |
| `model3d.cancel` | `*job_id` | sync/external/否 | 直接复用通用 `_cancel` | 与 `job.cancel` 完全重复，建议废弃专用别名 |
| `model3d.download` | `*job_id` | job/external/否 | 对已完成远端 3D Job 创建 download 请求/Job | 这是内部流水线阶段，不应与用户“导出模型”混淆 |
| `model3d.import_local` | `*staged_file_id` | job/local_reversible/否 | 注册 host 已授权 staged GLB | UI/host 专用；模型不能发明 capability/staged ID |
| `model3d.inspect` | `*asset_id` | **sync**/local_reversible/否 | 本地检查并保存 inspection | Manifest 合理，但结果丢失 inspection DTO |
| `model3d.render_preview` | `*asset_id` | **声明 job**/local_reversible/否 | **实际立即返回 capture preview UI action** | 执行元数据、supports_cancel、名称和行为不一致；应统一 |
| `model3d.convert` | `*asset_id; *target_format:const fbx` | job/local_reversible/否 | 使用批准的本地 converter 转 FBX | 合理；能力不可用需稳定失败 |
| `model3d.optimize` | `*asset_id; target_triangles?; max_texture_bytes?` | job/local_reversible/否 | 本地减面/纹理预算优化 | 能力检查合理 |
| `model3d.package` | `*asset_ids: unique ref[](1..32)` | job/local_reversible/否 | 生成模型交付包 | 与项目导出不同 |
| `job.get_status` | `*job_id` | sync/read_only/否 | 返回 persisted Job view | 通用版本应作为唯一状态入口 |
| `job.cancel` | `*job_id` | sync/local_reversible/否 | cancel local/remote 或按能力停止等待 | 与 model3d.cancel 重复 |
| `job.retry` | `*job_id` | sync/local_reversible/否 | 安全重试；付费重新审批；unknown submission 拒绝 | 策略合理 |
| `job.confirm_new_submission` | `*job_id` | sync/local_reversible/否 | 对 unknown-submission 的原 Job保留审计记录，创建新的参数绑定审批 | 功能重要但仅 UI/恢复流程可见；命名清楚 |

## 6. 功能接近的 Tool 与选择矩阵

| 目标 | 首选 | 何时改用另一个 Tool |
|---|---|---|
| 看懂一张图并回答问题 | `image.understand_for_agent` | 需要把分析保存为下游资产时，改用 `image.analyze_content/style/evaluate_3d_suitability` |
| 纯文本生成图片 | `image.generate_from_prompt` | 已有受管 Prompt 时用 `image.generate_from_prompt_asset` |
| 参考图变换 | `image.transform_from_reference` | 明确要同一来源多个替代方案时用 `image.generate_variants`；局部修改用 inpaint |
| 去背景 | 平坦背景用 `image.remove_background_local` | 复杂自然背景用 `image.remove_background_provider`；输入已是 extracted element 且要透明交付时用 `element.export_transparent` |
| 放大图片 | 默认离线优先 `image.upscale_local` | 本地模型不可用或用户明确要求 Provider 时用 `image.upscale_provider` |
| 拆图 | alpha 分隔用 `image.split_alpha_components`；规则网格用 `image.split_grid` | 语义拆分用 `element.split_semantic`；用户框选的生成式抽取用 `selection.request_user` → 用户确认 → `element.split_selection` |
| 简单矩形裁切 | UI/内部 `selection.*` → 原子 `image.crop` | 不要用 `element.split_selection`，后者是 Provider 生成式抽取 |
| 新建三视图 | `multiview.generate` | 已有 persisted set 但无 crop 且用户明确要求自动检测时用 `multiview.detect_regions`；只坏一个方向用 `regenerate_view` |
| 生成 3D | 有 confirmed 三视图时 `model3d.generate_from_multiview` | 只有一张适合重建的图时 `model3d.generate_from_image` |
| 3D 后处理 | 检查 `inspect`、预览截图 `render_preview`、格式转换 `convert`、减面 `optimize`、交付打包 `package` | 五者不互相替代；都不能生成新模型 |
| 查 Job | `job.get_status` 一次 | 取消必须有用户明确意图用 `job.cancel`；失败且 safe_to_retry 才用 `job.retry` |
| 隐藏/删除资产 | `asset.hide` | 用户明确删除且完成影响审查后才 `asset.move_to_trash`；恢复要按 hidden/trash 状态选对应 restore |
| 文件/源码操作 | `read/write/edit/bash` | 项目受管资产、审批、Job、Provider 状态必须走业务 Tool，不能用 shell 绕过 |

## 7. Description 合理性总评

### 做得好的部分

- 内置 Tool 的安全边界清楚，明确禁止直接修改项目数据库、受管资产和审批状态。
- 旧聚合 Facade description 和 `tool_guidance.py` 中已有大量高质量“何时用/何时不用”规则，尤其是 understand vs analyze、multiview confirmed crops、3D face budget、禁止轮询 Job。
- 模型参数字段本身的 description 较完整，且 `plan_step_id` 明确不构成授权。
- 单操作 Tool 命名总体比旧聚合 Tool 更容易做 schema 校验和审批绑定。

### 当前不合理之处

- 40 个单操作 Tool 在 `_spec` 中统一生成 description，几乎只靠 label 区分。旧的业务规则没有迁移，导致 Tool 级语义退化。
- `toolbox.status` 正是用 description 做搜索，description 越模板化，搜索区分度越低。
- B01/B02 原子 description 是占位文本。即使它们通常不直接给模型，也会影响 API catalog、诊断、开发者理解以及未来 adapter 复用。
- “外部”“付费”“需要审批”“同步/Job/UI action”“是否持久化结果”这些最能决定 Tool 选择的信息，没有系统地出现在单操作 description 中。
- `model3d.render_preview` 的多套说法互相矛盾：Tool 名是 render preview，label 是 open preview，实际 action 是 capture preview image。

### 推荐的 description 模板

每个模型业务 Tool 应包含五个短句，且不重复无效套话：

```text
做什么（结果是否持久化、是否产生新资产）。
何时用。
最接近的 Tool 在什么情况下用。
执行边界（local/provider、sync/job/UI action、是否审批）。
关键前置条件/禁止项。
```

例如：

```text
Remove a flat or channel-separable background locally and create a new managed
transparent image with verification. Use color_key for a uniform keyed background
and channel for a separable channel range; use image.remove_background_provider for
complex natural backgrounds. This is an offline synchronous operation and does not
require approval. Do not guess target_color; omit it to derive the color from corners.
```

## 8. Schema 与执行一致性审计

| 优先级 | 问题 | 影响 | 建议修复 |
|---|---|---|---|
| P0 | transform/variants 外层暴露 `seed/steps`，原子层拒绝 | 模型按 schema 合法调用仍失败 | 从这两个外层 schema 移除，或完整支持并升级原子合同/Provider routing；优先选择前者，直到后端真正支持 |
| P0 | asset metadata/compare 数组基数过宽 | 校验延迟到运行时，错误反馈差 | 为每个窄 Tool 建独立 schema，不直接复用 `_REFS_2` |
| P0 | 单模型 3D 操作数组允许最多 32 | 同上 | `inspect/render_preview/convert/optimize` 固定 `minItems=maxItems=1` |
| P0 | render preview 声明 Job、实际 UI action | UI、取消能力、监控和文档会误判 | 若目的是捕获图，改为 sync/local_reversible + `capture_model_preview`；若确需 Job，删除 runtime special case并接 handler，二选一 |
| P1 | Facade 等待 Job 180 秒 | 占用模型回合/请求，违背 durable continuation | 业务 Tool 返回 queued 后立即结束回合；由 completion broker/event continuation 恢复；如保留短等待，应写入合同且不超过明确阈值 |
| P1 | `parameters` 可为空、face_limit default 只写在 schema | JSON Schema validator 不会自动注入 default；可能走 Provider 无限/默认预算 | dispatcher 显式 `setdefault("face_limit", 100_000)`，或 schema 要求 face_limit；按用途选择预算 |
| P1 | `prompt.get_current` 不返回 current prompt | 名实不符，与 validate 重复 | 返回 parsed bilingual prompt DTO；否则删除 get_current，统一使用 validate |
| P1 | `model3d.inspect` 丢报告 | 模型无法据此决定 optimize/package | 将 inspection DTO 写入 summary/data/verification；保留持久化 |
| P1 | `project.export_package` phantom queue | API 看似成功但没有生产执行 | 接入真实 host capability + durable Job，或 catalog 标记 unavailable 并拒绝调用 |
| P2 | 内置 Tool schema 开放 | 模型多传字段不会被早期拒绝 | 全部增加 `additionalProperties:false` 和数值/字符串上下限 |
| P2 | normalize 组合参数未建模 | 冲突参数含义依赖 service 隐式优先级 | 文档化优先级，或用 oneOf/anyOf 表达 size 方案 |
| P2 | local background method 参数混在一起 | 模型可能给 color_key 传 channel 参数 | 用 `oneOf` 分成 color_key/channel 两个 schema，或进一步拆成两个 Tool |
| P2 | selection suggestion rect item 过宽 | 非法坐标延迟到 service | B01 schema 复用严格 rect schema并定义 label/confidence |
| P2 | model3d 专用 status/cancel 与通用 Job 重复 | 维护两套名字和风险元数据 | 新版本中弃用 `model3d.get_status/cancel`，统一 `job.*` |
| P2 | manifest index 只列 B02 47 个，不列 B01 18 个 | “全量索引”认知错误 | 改名 `b02-manifest-index.json` 或生成覆盖 65 个的统一索引并标 layer |

## 9. 推荐优化顺序

### 第一阶段：修正确性漂移

1. 修复 transform/variants 的 `seed/steps`。
2. 收紧所有窄 Tool 的精确数组基数。
3. 统一 `model3d.render_preview` 的命名、execution、supports_cancel、UI action 与 handler。
4. 给 model3d generation 在 dispatcher 中实际注入安全 face budget。

这些修改应先补/改契约测试，再改实现；不需要真实 Provider。

### 第二阶段：提升模型选 Tool 的准确率

1. 把 `tool_guidance.py` 与旧 Facade 中的关键规则迁移到 40 个 `OperationToolSpec` 的专用 description，删除无区分度模板重复。
2. 给 `toolbox.status` 增加 capability tags、中文 alias、风险、execution、required 参数摘要，并使用 token/fuzzy 搜索。
3. Planner 映射与 catalog tags 使用同一个声明源，避免名称表手工漂移。
4. 给常驻 Tool 做数据验证；如果真实会话表明付费 3D 并非高频，可把 `model3d.generate_from_image` 改为 Planner/按需加载，而不是永久常驻。

### 第三阶段：清理原子合同和结果质量

1. 让 `prompt.get_current` 真正返回内容，或废弃它。
2. 让 `model3d.inspect` 返回结构化 inspection。
3. 接通或禁用 `project.export_package` 原子入口。
4. 合并 `model3d.get_status/cancel` 到通用 `job.*`。
5. 生成一个全量 catalog artifact，字段至少包含：layer、name、version、description、input_schema、output_schema、risk、execution、approval、capability、model_visibility、permanent、dispatch_target、availability。

## 10. 建议的单一声明源

当前信息分散在 `progressive_tools.py`、`facade_tools.py`、`tool_guidance.py`、B01 JSON、B02 Python schema、contract artifacts、runtime policy 和 handlers 中。建议建立一份可生成其他视图的声明源：

```text
CanonicalAtomicTool
  name/version
  human_name + operational_description
  input/output schema
  risk/execution/approval/capability
  handler availability

ModelOperationTool
  name
  maps_to atomic name or virtual executor
  fixed/injected arguments
  model input schema
  use_when / do_not_use_when / alternatives / tags / aliases
  permanent/default/planner activation policy
```

然后自动生成：

- 应用 Manifest 与 manifest index；
- Agent `OperationToolSpec`；
- `toolbox.status` 搜索索引；
- 开发者文档；
- schema/dispatcher 一致性测试；
- “模型 schema 的每个字段都能被底层接受”和“底层 required 字段都被固定、注入或暴露”的双向契约测试。

## 11. 验证建议

本文是静态分析，没有调用任何真实 Provider。后续实现优化时建议依次验证：

1. 单元测试：对 40 个模型 Tool 逐个生成最小合法 payload，断言 `_translate` 后通过目标原子 schema。
2. 反向测试：原子 schema 的每个 required 字段必须来自模型字段、fixed argument 或 runtime injection。
3. Description 测试：每个相近 Tool 至少包含一个 alternatives/do-not-use 规则；付费/审批/Job/UI action 必须显式出现。
4. Contract 测试：B01 18 + B02 47 + model 46 的集合、重名映射和索引数量固定可审查。
5. Controlled/offline 集成测试：本地 trim/normalize/background/split/upscale、UI action、审批拒绝/批准、queued/continuation、cancel/retry。
6. 只有涉及交互桌面状态时，再按 `docs/controlled-validation.md` 的 Hot-update WebView2 流程保留证据；本文档本身不需要启动桌面 UI。

## 12. 权威源码位置

- 模型目录、永久 Tool、40 个单操作 schema：`src/aipic_to_model/agent/integrations/progressive_tools.py`
- 旧聚合 schema、参数说明、dispatcher：`src/aipic_to_model/agent/integrations/facade_tools.py`
- 4 个内置 Tool：`src/aipic_to_model/agent/tools/builtin.py`
- Agent Tool 校验与 ActiveToolSet：`src/aipic_to_model/agent/core/tool.py`
- Runtime 实际组装：`src/aipic_to_model/agent/integrations/runtime.py`
- 原子 Tool Agent guidance（当前渐进目录未复用）：`src/aipic_to_model/agent/integrations/tool_guidance.py`
- B01 18 个冻结 Manifest：`src/aipic_to_model/application/tool_manifests/`
- B01 注册与 executor：`src/aipic_to_model/application/tool_catalog.py`
- B02 47 个 schema/元数据：`src/aipic_to_model/application/b02_tool_catalog.py`
- B02 审批、Job、状态、取消与重试：`src/aipic_to_model/application/b02_runtime.py`
- B02 冻结 schema artifact：`contracts/tools/`
- 本地同步实现：`src/aipic_to_model/application/local_tool_dispatch.py`
- Provider 图像/视觉 Job：`src/aipic_to_model/application/jobs/external_image_handler.py`
- 3D、本地图像与路由 handlers：`src/aipic_to_model/application/jobs/`
- 生产 composition 和 handler 注册：`src/aipic_to_model/composition.py`
