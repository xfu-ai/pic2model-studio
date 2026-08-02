# Agent 模型侧 15 Tool 优化方案与契约

状态：已实现。本文同时作为模型侧 Tool 契约和验证基线。

## 1. 最终 Tool 清单

重构前代码向模型暴露 63 个 Tool：

- 4 个通用 Tool；
- 19 个 B01 原子 Tool；
- 40 个 B02 原子 Tool。

优化后，每次模型请求固定暴露以下 15 个 Tool：

| # | Tool | 类型 | 主要用途 |
|---:|---|---|---|
| 1 | `read` | 现有通用 Tool | 读取工作区文本文件 |
| 2 | `write` | 现有通用 Tool | 原子写入工作区文本文件 |
| 3 | `edit` | 现有通用 Tool | 唯一匹配文本替换 |
| 4 | `bash` | 现有通用 Tool | 执行工作区 PowerShell |
| 5 | `inspect_workspace` | 业务门面 | 查询当前工作区、资产、Job 和能力 |
| 6 | `select_asset` | 业务门面 | 把用户明确选择的资产设为当前资产 |
| 7 | `analyze_image` | 业务门面 | 分析图片内容、风格和 3D 适用性 |
| 8 | `prepare_prompt` | 业务门面 | 创建、修改或校验受管 Prompt |
| 9 | `generate_images` | 业务门面 | 生成图片、转换图片或创建候选变体 |
| 10 | `edit_image` | 业务门面 | 本地/Provider 放大、标准化、裁透明边、去背景、局部重绘或透明导出 |
| 11 | `split_image` | 业务门面 | 本地连通域/网格拆分或 Provider 语义拆分 |
| 12 | `prepare_multiview` | 业务门面 | 创建、拆分或修复三视图 |
| 13 | `generate_model3d` | 业务门面 | 从单图或三视图生成 3D 模型 |
| 14 | `process_model3d` | 业务门面 | 检查、预览、转换、优化或打包模型 |
| 15 | `control_job` | 业务门面 | 查询、取消或重试持久 Job |

这 15 个 Tool 的名称、描述和参数 Schema 固定，不根据资产、Provider 或 Job
状态动态增删。

`read/write/edit/bash` 保持当前实现，不合并、不改名、不改参数和 Result。
其余 11 个是模型侧门面，内部继续复用现有 65 个 Manifest、Registry、审批、
Job、资产和 UI Action。

## 2. 模型侧与内部执行侧的关系

```text
模型固定看到 15 个 Tool
        │
        ├─ read / write / edit / bash
        │
        └─ 11 个业务门面 Tool
                  │
                  ▼
          Workflow Orchestrator
                  │
                  ▼
  65 个内部原子 Tool / Service
```

“门面 Tool”不是虚假能力，而是稳定的模型调用契约。它负责：

- 接收模型容易理解且不易传错的参数；
- 从当前项目解析 Provider、模型、默认参数和受管引用；
- 校验资产类型、状态、权限和用户确认；
- 当前版本每次门面调用翻译并调用一个内部原子 Tool；多步骤流程由持久化
  Tool Result、Job 终态 continuation 和后续门面调用串联；
- 在审批、UI Action 或 Job 边界返回结构化结果；
- 保留每一个内部步骤的审计和幂等记录。

模型不能直接调用内部原子 Tool 名称。

## 3. 不提供给模型的操作

以下操作由用户或桌面宿主完成：

- 创建、打开、切换、重命名或删除项目；
- 导入旧项目、导出项目包；
- 选择本地图片、GLB、导入目录或导出目录；
- 配置 Provider、模型、API Key、本地转换器或审批策略；
- 批准或拒绝审批；
- 确认框选、三视图区域、质量检查和候选图；
- 隐藏、恢复、移入回收站、永久删除或打开系统文件夹；
- 直接操作数据库、受管资产文件或 Provider API。

模型参数中不得出现 `project_id`、绝对路径、Provider Profile、Provider 模型、
API Key、Bearer Token、`channel` 或宿主文件能力 ID。

## 4. 真实状态快照

每次模型请求前生成只读 `runtime_context`，并在 Tool、Job、审批、UI Action、
当前资产或工作区状态变化后刷新。

示例：

```json
{
  "schema_version": 1,
  "snapshot_version": 42,
  "project": {
    "bound": true,
    "read_only": false
  },
  "workspace": {
    "mode": "image",
    "current_asset_ref": "asset_opaque",
    "current_selection_ref": null,
    "current_prompt_ref": "prompt_opaque",
    "current_multiview_ref": null,
    "current_model_ref": null
  },
  "assets": {
    "counts_by_kind": {
      "source_image": 1,
      "generated_image": 4,
      "model_glb": 0
    },
    "recent": []
  },
  "jobs": {
    "nonterminal": []
  },
  "pending": {
    "approval": null,
    "ui_action": null
  },
  "capabilities": {
    "image_analysis": {
      "configured": true,
      "available": true,
      "unavailable_reason": null
    },
    "image_generation": {
      "configured": false,
      "available": false,
      "unavailable_reason": "provider_not_configured"
    },
    "model3d_generation": {
      "configured": true,
      "available": true,
      "unavailable_reason": null
    }
  },
  "tool_conditions": {
    "generate_images": {
      "ready": false,
      "missing": ["image_generation_provider"]
    },
    "generate_model3d": {
      "ready": false,
      "missing": ["suitable_image_or_confirmed_multiview"]
    },
    "control_job": {
      "ready": false,
      "missing": ["job_ref"]
    }
  }
}
```

状态快照只帮助模型判断当前条件，不改变 15 个 Tool 的名称或 Schema。执行层
仍要根据同一事务视图重新校验。

快照不得包含密钥、完整 Prompt、绝对路径或 Provider 原始响应，也不得为探测
能力而发起真实或付费请求。

## 5. 11 个业务 Tool 的通用 Result

```json
{
  "ok": true,
  "status": "succeeded",
  "tool_call_id": "opaque",
  "summary": "Short model-readable summary.",
  "data": {},
  "output_refs": [
    {
      "kind": "asset",
      "id": "asset_opaque",
      "role": "current"
    }
  ],
  "job": null,
  "ui_action": null,
  "error": null,
  "retry": {
    "allowed": false,
    "automatic": false,
    "requires_approval": false,
    "after_seconds": null,
    "reason": null
  },
  "reused": false
}
```

### 5.1 状态

| status | ok | 含义 | Agent 行为 |
|---|---:|---|---|
| `succeeded` | true | 操作已完成 | 可以继续 |
| `queued` | true | Job 已持久化 | 结束当前模型回合，等待终态事件 |
| `awaiting_ui_action` | true | 等待用户审批或 UI 操作，具体类型见 `ui_action.type` | 结束当前 Run，由桌面切页或显示审批 |
| `failed` | false | 操作未完成 | 按 Error 和 Retry 恢复 |

### 5.2 Job

`status=queued` 时：

```json
{
  "job_id": "job_opaque",
  "job_type": "image.generate",
  "status": "queued",
  "stage": "queued",
  "progress": null,
  "provider": "configured-profile-label",
  "can_cancel": true,
  "can_stop_waiting": false
}
```

### 5.3 UI Action

`status=awaiting_ui_action` 时：

```json
{
  "action_id": "action_opaque",
  "type": "selection_confirmation_required",
  "workspace_mode": "selection",
  "asset_ref": "asset_opaque",
  "selection_ref": "selection_opaque"
}
```

### 5.4 Error

`status=failed` 时：

```json
{
  "code": "CAPABILITY_NOT_AVAILABLE",
  "category": "configuration",
  "user_message": "当前没有可用的图片生成服务。",
  "recoverable": true,
  "safe_to_retry": false,
  "submission_state": "not_sent",
  "recommended_action": "configure_image_provider",
  "retry_after_seconds": null,
  "fee_incurred": false,
  "details_ref": "diagnostic_opaque"
}
```

`submission_state` 只允许 `not_sent`、`accepted` 和 `unknown`。`unknown` 禁止
自动重试。

### 5.5 执行、审批和重试速查

| Tool | 同步等待 | 后台挂起 | 审批 | 自动重试 |
|---|---|---|---|---|
| `read` | 文件读取完成 | 否 | 否 | 否 |
| `write` | 原子写入完成 | 否 | 否 | 否 |
| `edit` | 原子替换完成 | 否 | 否 | 否 |
| `bash` | 进程退出/超时/取消 | 否 | 否 | 否 |
| `inspect_workspace` | 最多 5 秒 | 否 | 否 | 只读 busy 最多 1 次 |
| `select_asset` | 最多 5 秒 | 否 | 否 | 否 |
| `analyze_image` | 持久化 Job，最多 5 秒 | 是 | 当前不弹付费审批 | 仅 Job 层安全恢复 |
| `prepare_prompt` | 本地最多 5 秒 | 改写时是 | 当前不弹付费审批 | 仅 Job 层安全恢复 |
| `generate_images` | 持久化审批/Job，最多 5 秒 | 是 | 必须 | 否 |
| `edit_image` | 持久化 Job，最多 5 秒 | 是 | `inpaint` 必须 | 非付费步骤仅安全恢复 |
| `split_image` | 持久化审批/Job，最多 5 秒 | 是 | 必须 | 否 |
| `prepare_multiview` | 持久化审批/Job，最多 5 秒 | 是 | 创建/修复必须 | 付费步骤不自动重试 |
| `generate_model3d` | 持久化审批/Job，最多 5 秒 | 是 | 必须 | 否 |
| `process_model3d` | 持久化 Job/UI Action，最多 5 秒 | 是 | 否 | 本地安全恢复 |
| `control_job` | 最多 5 秒 | 重试后可能是 | 付费重试必须 | 否 |

表中的“后台挂起”表示当前模型回合结束并持久化等待条件，不表示占用协程等待
任务完成。

### 5.6 各业务 Tool 的成功 `data`

| Tool | `data` 的稳定字段 |
|---|---|
| `inspect_workspace` | 原子只读结果；`capabilities` 视图返回完整 `runtime_context` |
| `select_asset` | `current_asset_ref`、`previous_asset_ref`、`reason` |
| `analyze_image` | `workflow_ref`、`requested_types`、`analysis_refs` |
| `prepare_prompt` | `prompt_ref`、`task`、`validation` |
| `generate_images` | `workflow_ref`、`mode`、`candidate_refs` |
| `edit_image` | `workflow_ref`、`operation`、`result_asset_ref` |
| `split_image` | `workflow_ref`、`split_mode`、`result_asset_refs` |
| `prepare_multiview` | `workflow_ref`、`multiview_ref`、`view_asset_refs`、`validation` |
| `generate_model3d` | `workflow_ref`、`mode`、`model_asset_ref`、`inspection` |
| `process_model3d` | `workflow_ref`、`operation`、`result_asset_refs` |
| `control_job` | `job_ref`、`job_type`、`status`、`stage`、`progress`、`can_cancel`、`can_stop_waiting` |

`queued` 和 `awaiting_ui_action` 阶段允许产物字段为空；终态
continuation 必须使用相同字段返回真实产物。模型不得从 `summary` 文本解析 ID。

## 6. Tool 1～4：现有通用 Tool

### 6.1 `read`

描述、Schema 和 Result 保持当前实现：

```json
{
  "type": "object",
  "required": ["path"],
  "properties": {
    "path": {"type": "string"},
    "offset": {"type": "integer"},
    "limit": {"type": "integer"}
  }
}
```

同步读取；不审批；不自动重试。

### 6.2 `write`

```json
{
  "type": "object",
  "required": ["path", "content"],
  "properties": {
    "path": {"type": "string"},
    "content": {"type": "string"}
  }
}
```

原子写入；不审批；不自动重试。

### 6.3 `edit`

```json
{
  "type": "object",
  "required": ["path", "old_text", "new_text"],
  "properties": {
    "path": {"type": "string"},
    "old_text": {"type": "string"},
    "new_text": {"type": "string"}
  }
}
```

要求 `old_text` 唯一匹配；不审批；不自动重试。

### 6.4 `bash`

```json
{
  "type": "object",
  "required": ["command"],
  "properties": {
    "command": {"type": "string"},
    "timeout": {"type": "number"},
    "cwd": {"type": "string"}
  }
}
```

等待 PowerShell 进程退出、超时或取消；不新增审批；不自动重试；保留当前
64 KiB 输出截断和 artifact 行为。

## 7. Tool 5：`inspect_workspace`

模型描述：

> Inspect the current managed workspace. Use `summary` for current state,
> `assets` for managed assets, `asset_details` for one asset, `compare` for
> two sibling assets, `jobs` for one known durable Job, and `capabilities` for configured
> execution capabilities. This tool cannot change projects or assets.

参数 Schema：

```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "view": {
      "enum": [
        "summary",
        "assets",
        "asset_details",
        "compare",
        "jobs",
        "capabilities"
      ]
    },
    "asset_refs": {
      "type": "array",
      "maxItems": 2,
      "uniqueItems": true,
      "items": {"type": "string", "minLength": 1}
    },
    "job_ref": {"type": "string", "minLength": 1},
    "group": {"type": "string", "minLength": 1, "maxLength": 64}
  },
  "required": ["view"]
}
```

参数规则：

- `asset_details`：`asset_refs` 必须恰好 1 个；
- `compare`：`asset_refs` 必须恰好 2 个同组资产；
- `group` 只用于 `assets`；
- `job_ref` 只用于 `jobs`；
- 不接受 `project_id` 或路径。

执行：只读同步查询；不审批；不自动重试。

成功 `data`：除 `capabilities` 外保留原子 Tool 的真实只读结果；
`capabilities` 返回当前请求使用的非敏感 `runtime_context`。

主要失败：`PROJECT_NOT_BOUND`、`ASSET_NOT_FOUND`、
`ASSET_COMPARE_INCOMPATIBLE`、`CONTEXT_UNAVAILABLE`。

## 8. Tool 6：`select_asset`

模型描述：

> Set one managed asset as the current asset only when the user explicitly
> selected it or the workflow produced exactly one unambiguous result. Do not
> use this tool to choose among candidates on the user's behalf, hide assets,
> restore assets, trash assets, or import files.

参数 Schema：

```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "asset_ref": {"type": "string", "minLength": 1},
    "reason": {"type": "string", "minLength": 1, "maxLength": 500}
  },
  "required": ["asset_ref", "reason"]
}
```

执行：同步本地事务，硬超时 5 秒；不审批；使用 Tool Call 幂等键但不自动重试。

成功 `data`：`current_asset_ref`、`previous_asset_ref`、`reason`。

主要失败：`ASSET_NOT_FOUND`、`ASSET_SCOPE_MISMATCH`、
`ASSET_STATE_INVALID`、`PROJECT_READ_ONLY`。

## 9. Tool 7：`analyze_image`

模型描述：

> Analyze one managed image for exactly one purpose: content, visual style, or
> suitability for 3D generation. Request only the analysis needed for the user's goal.
> Existing matching analysis is reused unless the user explicitly requests a
> refresh. Provider and model selection are owned by the application.

参数 Schema：

```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "source_asset_ref": {"type": "string", "minLength": 1},
    "analysis_type": {"enum": ["content", "style", "3d_suitability"]},
    "refresh": {"type": "boolean", "default": false}
  },
  "required": ["source_asset_ref", "analysis_type"]
}
```

执行：只等待校验和 Job 持久化，硬超时 5 秒；Provider 分析返回 `queued`；
匹配结果存在时可直接 `succeeded/reused=true`。

审批：按当前外部分析策略，不弹付费审批。

重试：模型不重复调用；安全恢复通过 `control_job`。

主要失败：`ASSET_NOT_FOUND`、`IMAGE_INPUT_INVALID`、
`CAPABILITY_NOT_AVAILABLE`、`PROVIDER_*`。

## 10. Tool 8：`prepare_prompt`

模型描述：

> Create or maintain managed prompts. Use `extract` with one content/style
> analysis, `merge` with separate content and style prompts, `rewrite` with one
> prompt and a concrete instruction, or `validate` with one prompt.
> This tool always stores a managed prompt result; it does not generate images.

参数 Schema：

```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "task": {"enum": ["extract", "merge", "rewrite", "validate"]},
    "analysis_asset_ref": {"type": "string", "minLength": 1},
    "analysis_kind": {"enum": ["content", "style"]},
    "content_prompt_ref": {"type": "string", "minLength": 1},
    "style_prompt_ref": {"type": "string", "minLength": 1},
    "prompt_asset_ref": {"type": "string", "minLength": 1},
    "instruction": {"type": "string", "minLength": 1, "maxLength": 4000}
  },
  "required": ["task"]
}
```

参数规则：

- `extract`：需要 `analysis_asset_ref` 和匹配的 `analysis_kind`；
- `merge`：需要 `content_prompt_ref` 和 `style_prompt_ref`；
- `rewrite`：需要 `prompt_asset_ref` 和 `instruction`；
- `validate`：只需要 `prompt_asset_ref`；
- 模型不传 Provider 或模型名。

执行：

- 提取、合并和校验可同步完成；
- 外部 Prompt 改写返回 `queued`；
- 同步阶段硬超时 5 秒。

审批：按当前 Prompt 改写策略，不弹付费审批。

主要失败：`ANALYSIS_NOT_READY`、`PROMPT_NOT_FOUND`、
`PROMPT_VALIDATION_FAILED`、`CAPABILITY_NOT_AVAILABLE`。

## 11. Tool 9：`generate_images`

模型描述：

> Generate managed image candidates from a prompt. Use `from_prompt` without a
> source image, `from_image` to transform one source while following the prompt,
> and `variants` to create alternatives of one source. This is a paid external
> operation and always uses parameter-bound user approval.

参数 Schema：

```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "mode": {"enum": ["from_prompt", "from_image", "variants"]},
    "prompt_asset_ref": {"type": "string", "minLength": 1},
    "source_asset_ref": {"type": "string", "minLength": 1},
    "candidate_count": {
      "type": "integer",
      "enum": [1, 2, 4]
    },
    "aspect_ratio": {
      "type": "string",
      "minLength": 1,
      "maxLength": 32
    },
    "size": {"type": "string", "minLength": 1, "maxLength": 32},
    "quality": {"type": "string", "minLength": 1, "maxLength": 32},
    "output_format": {"enum": ["png", "jpg", "webp"]},
    "structure_strength": {
      "type": "number",
      "minimum": 0,
      "maximum": 1
    }
  },
  "required": ["mode", "prompt_asset_ref", "candidate_count"]
}
```

参数规则：

- `from_prompt` 不接受 `source_asset_ref`；
- `from_image` 和 `variants` 必须提供 `source_asset_ref`；
- `structure_strength` 只用于 `from_image`；
- Provider、模型和 channel 由应用配置解析。

执行：校验后返回 `awaiting_ui_action`（审批类型见 `ui_action.type`）；批准后创建 Job 并返回 `queued`；
不等待生成完成。

重试：付费请求不自动重提；用户明确重试时通过 `control_job`，并重新审批。

主要失败：`PROMPT_NOT_READY`、`IMAGE_INPUT_INVALID`、
`CAPABILITY_NOT_AVAILABLE`、`APPROVAL_DENIED`、`PROVIDER_*`。

## 12. Tool 10：`edit_image`

模型描述：

> Apply one managed image edit. `trim_transparent`, `normalize`,
> `remove_background_local`, and `upscale_local` are bundled offline
> operations. `upscale`, `remove_background`, `inpaint`, and
> `export_transparent` retain their Provider semantics. Never silently fall
> back from a requested local operation to a Provider.

参数 Schema：

```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "operation": {
      "enum": [
        "upscale",
        "remove_background",
        "inpaint",
        "export_transparent",
        "trim_transparent",
        "normalize",
        "remove_background_local",
        "upscale_local"
      ]
    },
    "source_asset_ref": {"type": "string", "minLength": 1},
    "selection_ref": {"type": "string", "minLength": 1},
    "prompt_asset_ref": {"type": "string", "minLength": 1},
    "scale": {"enum": [2, 4]},
    "background_method": {"enum": ["color_key", "channel"]},
    "target_color": {"type": "array", "minItems": 3, "maxItems": 3},
    "target_width": {"type": "integer"},
    "target_height": {"type": "integer"},
    "max_long_edge": {"type": "integer"},
    "output_format": {"enum": ["png", "jpeg", "webp"]}
  },
  "required": ["operation", "source_asset_ref"]
}
```

参数规则：

- `upscale`、`upscale_local` 必须提供 `scale`；
- `inpaint` 必须提供 `selection_ref` 和 `prompt_asset_ref`；
- `remove_background_local` 必须提供 `background_method`；
- 本地操作只生成新的受管派生资产，不覆盖源资产，不会调用网络。

执行：`trim_transparent`、`normalize`、`remove_background_local` 同步完成；
`upscale_local` 进入可恢复本地 Job；Provider 操作保持原有审批/Job 语义。

审批：`inpaint` 需要参数绑定审批；其他操作按当前外部非付费策略执行。

主要失败：`IMAGE_INPUT_INVALID`、`SELECTION_NOT_CONFIRMED`、
`PROMPT_NOT_READY`、`CAPABILITY_NOT_AVAILABLE`、`PROVIDER_*`。

## 13. Tool 11：`split_image`

模型描述：

> Split one managed image. Use `alpha_components` or `grid` for deterministic
> bundled offline splitting. Use `element` for semantic Provider extraction
> and `boxsplit` for the user's confirmed rectangular regions.

参数 Schema：

```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "source_asset_ref": {"type": "string", "minLength": 1},
    "selection_ref": {"type": "string", "minLength": 1},
    "prompt_asset_ref": {"type": "string", "minLength": 1},
    "split_mode": {"enum": ["element", "boxsplit", "alpha_components", "grid"]},
    "columns": {"type": "integer"},
    "rows": {"type": "integer"},
    "alpha_threshold": {"type": "integer"},
    "min_area": {"type": "integer"},
    "padding": {"type": "integer"},
    "max_outputs": {"type": "integer"}
  },
  "required": [
    "source_asset_ref",
    "split_mode"
  ]
}
```

执行：

- `alpha_components` 和 `grid` 本地同步执行，不需要 Prompt 或选区；
- `grid` 必须提供 `columns` 和 `rows`；
- `element` 需要 Prompt；`boxsplit` 需要 Prompt 和已确认选区；
- `boxsplit` 选区未确认时提示用户先在前端确认；
- Provider 模式已确认时返回 `awaiting_ui_action`（审批类型见 `ui_action.type`）；
- 批准后创建 Job，不等待 Provider 完成。

重试：不自动重试；付费重试重新审批。

主要失败：`IMAGE_INPUT_INVALID`、`SELECTION_NOT_CONFIRMED`、
`PROMPT_NOT_READY`、`CAPABILITY_NOT_AVAILABLE`。

## 14. Tool 12：`prepare_multiview`

模型描述：

> Create, inspect, or repair a managed front-side-back multiview set. Use
> `create` to generate a new multiview sheet from one source image,
> `detect_regions` for an existing managed set, and `regenerate_view` to repair
> exactly one view in an existing set. Region and quality confirmation remain
> user actions.

参数 Schema：

```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "operation": {
      "enum": ["create", "detect_regions", "regenerate_view"]
    },
    "source_asset_ref": {"type": "string", "minLength": 1},
    "prompt_asset_ref": {"type": "string", "minLength": 1},
    "multiview_ref": {"type": "string", "minLength": 1},
    "target_view": {"enum": ["front", "side", "back"]}
  },
  "required": ["operation"]
}
```

参数规则：

- `create`：需要 `source_asset_ref`，`prompt_asset_ref` 可选；
- `detect_regions`：需要 `multiview_ref`；
- `regenerate_view`：需要 `multiview_ref` 和 `target_view`。

执行：

- `create` 和 `regenerate_view` 先返回参数绑定审批；
- `detect_regions` 直接创建检测 Job；
- 区域或质量需要确认时由对应原子 Tool 返回 `awaiting_ui_action`；
- 确认后内部裁切并校验，最终返回三视图 refs。

重试：付费生成不自动重提；检测和本地裁切只在安全时由 Job 层恢复。

主要失败：`IMAGE_INPUT_INVALID`、`MULTIVIEW_NOT_FOUND`、
`MULTIVIEW_REGION_MISSING`、`MULTIVIEW_QUALITY_NOT_CONFIRMED`、
`CAPABILITY_NOT_AVAILABLE`。

## 15. Tool 13：`generate_model3d`

模型描述：

> Generate one managed 3D model from either one suitable image or one confirmed
> front-side-back multiview set. Provide exactly the inputs required by the
> selected mode. Provider and model selection are owned by the application.
> This is a paid external operation and always requires parameter-bound approval.

参数 Schema：

```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "mode": {"enum": ["image", "multiview"]},
    "image_asset_ref": {"type": "string", "minLength": 1},
    "multiview_ref": {"type": "string", "minLength": 1},
    "view_asset_refs": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "front": {"type": "string", "minLength": 1},
        "side": {"type": "string", "minLength": 1},
        "back": {"type": "string", "minLength": 1}
      },
      "required": ["front", "side", "back"]
    },
    "parameters": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "model_version": {"type": "string", "minLength": 1},
        "texture_quality": {
          "enum": ["standard", "detailed", "extreme"]
        },
        "geometry_quality": {"enum": ["standard", "detailed"]},
        "texture_alignment": {
          "enum": ["original_image", "geometry"]
        },
        "texture": {
          "type": "boolean",
          "const": true,
          "default": true
        },
        "pbr": {
          "type": "boolean",
          "const": true,
          "default": true
        },
        "quad": {"type": "boolean"},
        "face_limit": {"type": "integer", "minimum": 0},
        "auto_size": {"type": "boolean"},
        "orientation": {"enum": ["default", "align_image"]},
        "smart_low_poly": {"type": "boolean"},
        "generate_parts": {"type": "boolean"},
        "compress": {"enum": ["", "geometry"]},
        "enable_image_autofix": {"type": "boolean"},
        "model_seed": {"type": "integer", "minimum": 0},
        "texture_seed": {"type": "integer", "minimum": 0}
      }
    }
  },
  "required": ["mode", "parameters"]
}
```

参数规则：

- `image`：只需要 `image_asset_ref`；
- `multiview`：需要 `multiview_ref` 和完整 `view_asset_refs`；
- 两种输入模式不能混用。

执行：返回 `awaiting_ui_action`（审批类型见 `ui_action.type`）；批准后创建 Job；远端生成、下载、真实性检查和
资产登记均在 Job 内部完成。

重试：付费提交不自动重提；`submission_state=unknown` 禁止重试。

主要失败：`MODEL3D_INPUT_INVALID`、`MULTIVIEW_NOT_CONFIRMED`、
`CAPABILITY_NOT_AVAILABLE`、`APPROVAL_DENIED`、`PROVIDER_*`。

## 16. Tool 14：`process_model3d`

模型描述：

> Process existing managed 3D assets. Use `inspect` for local model inspection,
> `open_preview` to hand off to the desktop preview, `convert` for GLB to FBX,
> `optimize` for local geometry reduction, and `package` to create a managed
> delivery package. This tool cannot import a local file or export to an
> arbitrary path.

参数 Schema：

```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "operation": {
      "enum": ["inspect", "open_preview", "convert", "optimize", "package"]
    },
    "asset_refs": {
      "type": "array",
      "minItems": 1,
      "maxItems": 32,
      "uniqueItems": true,
      "items": {"type": "string", "minLength": 1}
    },
    "target_format": {"const": "fbx"},
    "target_triangles": {
      "type": "integer",
      "minimum": 1,
      "maximum": 10000000
    },
    "max_texture_bytes": {
      "type": "integer",
      "minimum": 1,
      "maximum": 209715200
    }
  },
  "required": ["operation", "asset_refs"]
}
```

参数规则：

- `inspect/open_preview/convert/optimize`：`asset_refs` 必须恰好 1 个 GLB；
- `convert`：必须提供 `target_format=fbx`；
- `optimize`：`target_triangles` 和 `max_texture_bytes` 均可选，由本地优化器使用
  当前默认值补齐；
- `package`：允许 1～32 个相关受管资产，不接受其他参数。

执行：

- `open_preview` 先返回持久化预览 Job；完成后的 UI Action 由桌面切到 3D 工作区；
- 其他 operation 创建本地 Job；
- 不需要 Provider 审批；
- 本地可安全恢复的 Job 可由 `control_job` 重试。

主要失败：`MODEL3D_FORMAT_UNSUPPORTED`、`MODEL3D_INPUT_INVALID`、
`CONVERSION_TOOL_MISSING`、`CAPABILITY_NOT_AVAILABLE`。

## 17. Tool 15：`control_job`

模型描述：

> Read or control one known durable job. Use `status` only when the user asks
> for current progress and no fresh terminal event is already in context. Use
> `cancel` or `retry` only for explicit user intent. Never poll a job repeatedly.

参数 Schema：

```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "action": {"enum": ["status", "cancel", "retry"]},
    "job_ref": {"type": "string", "minLength": 1}
  },
  "required": ["action", "job_ref"]
}
```

执行：

- 同步读取或保存 Job 命令，硬超时 5 秒；
- `status` 不审批，同一 Agent Run 不允许轮询；
- `cancel` 需要用户明确取消意图，当前不增加第二层审批；
- `retry` 只接受 `safe_to_retry=true` 或可安全恢复的 interrupted Job；
- 非付费安全重试直接排队；
- 付费重试返回 `awaiting_ui_action`（审批类型见 `ui_action.type`）；
- `submission_state=unknown` 禁止重试。

主要失败：`JOB_NOT_FOUND`、`JOB_NOT_CANCELLABLE`、
`JOB_NOT_RETRYABLE`、`JOB_SUBMISSION_UNKNOWN`。

## 18. 固定暴露但暂时不能执行

15 个 Tool 始终存在。当前条件不足时返回明确结果：

| 情况 | Result |
|---|---|
| 缺少用户应导入的图片或模型 | `failed`，提示用户在桌面完成导入 |
| 缺少框选、候选或质量确认 | `awaiting_ui_action`，附 UI Action |
| Provider 或本地转换器未配置 | `failed/CAPABILITY_NOT_AVAILABLE` |
| 项目只读但操作需要写入 | `failed/PROJECT_READ_ONLY` |
| 资产类型或状态错误 | `failed/ASSET_STATE_INVALID` |
| Job 不存在 | `failed/JOB_NOT_FOUND` |

缺少用户输入或配置不属于自动重试。必须先完成 `recommended_action`。

## 19. Job 等待和前端表现

业务 Tool 返回 `queued` 后：

1. 保存 Tool Result；
2. 保存 `run_status=waiting_job` 和等待的 `job_ref`；
3. 当前模型回合生成“任务已开始”的可见消息；
4. 结束当前模型调用，不保持等待 Job 的协程或 HTTP 请求；
5. Job Worker 在后台执行。

前端表现：

- Tool 卡从“执行中”变成“后台执行中”；
- Agent 状态显示“等待后台任务”，不显示持续思考动画；
- 输入框恢复可用；
- 任务卡显示进度、查看任务、取消任务或停止等待；
- 进度只更新卡片和任务栏，不反复追加聊天消息。

Job 进入终态时，Worker 保存终态和事件。事件调度器创建新的 continuation
turn，Agent 基于结构化结果生成一次完成或失败消息。

明确 UI Action 时自动切换页签。若用户正在编辑其他内容，普通完成事件只显示
未读和“查看结果”，不抢夺当前编辑焦点。

## 20. 内部 Tool 映射

| 模型 Tool | 内部能力 |
|---|---|
| `inspect_workspace` | `project.get_state`、`asset.list`、`asset.get_metadata`、`asset.compare`、`selection.get_current`、`prompt.get_current`、Job/Capability Repository |
| `select_asset` | `asset.set_current` |
| `analyze_image` | `image.analyze_content`、`image.analyze_style`、`image.evaluate_3d_suitability` |
| `prepare_prompt` | `prompt.extract_bilingual`、`prompt.merge`、`prompt.get_current`、`prompt.rewrite`、`prompt.validate` |
| `generate_images` | `image.generate`、`image.transform`、`image.generate_variants`、`image.compress_for_provider` |
| `edit_image` | `image.upscale`、`image.remove_background`、`image.inpaint_selection`、`element.export_transparent`、`image.trim_transparent`、`image.normalize`、`image.remove_background_local`、`image.upscale_local` |
| `split_image` | `selection.request_user`、`selection.set_suggestion`、`selection.confirm`、`image.crop`、`image.render_annotation`、`selection.auto_suggest_boxes`、`element.split`、`image.split_local` |
| `prepare_multiview` | 全部 `multiview.*` |
| `generate_model3d` | `model3d.generate`、`model3d.download` |
| `process_model3d` | `model3d.inspect`、`model3d.render_preview`、`model3d.convert`、`model3d.optimize`、`model3d.package` |
| `control_job` | `job.get_status`、`job.cancel`、`job.retry`、`model3d.get_status`、`model3d.cancel` |

仅内部或仅 UI：

| 内部能力 | 处理方式 |
|---|---|
| `project.save_checkpoint` | Runtime 自动执行 |
| `project.import_legacy`、`project.export_package` | 仅桌面 UI/REST |
| `asset.hide`、`asset.restore_hidden`、`asset.restore_from_trash` | 仅用户 UI |
| `asset.move_to_trash` | 仅用户 UI；破坏性策略后续统一优化 |
| `asset.open_output_folder` | 仅桌面 UI |
| `model3d.import_local` | 用户通过宿主文件能力导入 |
| 审批决定 | 仅审批卡片 API |

## 21. 实施顺序

### 21.1 模型暴露面

- 保留 `read/write/edit/bash`；
- 新建 11 个业务门面；
- Runtime 固定注册 15 个 Tool；
- 停止把 65 个原子 Manifest 注册给模型；
- REST/UI 继续使用现有原子 Manifest，不受影响。

### 21.2 状态与编排

- 建立事务一致的 `runtime_context`；
- 门面 Tool 完成引用解析和二次校验；
- Workflow Orchestrator 调用内部原子 Tool；
- 保存 workflow、内部步骤、审批、Job 和等待条件。

### 21.3 持久挂起与恢复

- `queued` 后结束当前模型回合；
- Job、审批和 UI Action 事件创建 continuation turn；
- 禁止模型轮询、sleep 或重建原提交参数；
- 相同终态事件按稳定 event ID 去重。

### 21.4 前端闭环

- 展示等待、排队、运行、完成、失败和取消状态；
- `waiting_job` 时恢复输入框；
- 完成后只生成一次 Agent 消息；
- UI Action 自动切页并聚焦资产；
- 后台时发送系统通知。

## 22. 验收标准

- 每次项目 Agent 模型请求固定包含 15 个 Tool Schema；
- `read/write/edit/bash` 名称、参数和 Result 无变化；
- 模型请求不包含任何 `project.*` 或内部原子 Tool；
- 11 个业务 Tool 的 description 包含何时用和何时不用；
- 每个 operation 的条件参数都有契约测试；
- 多传、少传、错类型、错资产状态都返回确定错误；
- Provider、模型、project ID、路径和能力 ID 不由模型填写；
- `queued` 只能在 Job 事务提交成功后返回；
- `queued` 后没有等待 Job 的长生命周期模型请求或 Agent 协程；
- Job 终态只生成一次 continuation 消息；
- 付费和未知提交状态不会自动重提；
- 审批和 UI Action 跨重启可恢复；
- 真实桌面 DOM 验证覆盖排队、完成、失败、取消、重试和自动切页。

## 23. 预期结果

- 模型固定面对 15 个含义清晰的 Tool，而不是 63 个原子 Tool；
- 图片分析、Prompt、生成、编辑、拆分、三视图和 3D 不再挤在少数超大 Schema；
- 高风险操作各自拥有准确描述、参数和审批规则；
- 模型无需知道内部 Provider、模型、路径和数据库结构；
- 应用仍保留 65 个原子能力的审计、审批、幂等和安全边界；
- 参数错误可以在门面层给出稳定、可恢复的结构化反馈。
