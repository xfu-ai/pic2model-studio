# Agent 轻量执行提纲与 Job 等待实施方案

> 状态：根据第二轮审查意见修订，待逐项审查
> 适用仓库：`AIPicToModelClean`
> 本期重点：Tool 选对输入并串行执行、Job 最多等待 180 秒、移除前端自然语言回写
> 安全原则：默认验证不得访问 Tripo、Gemini、Meshy、OpenAI 或其他真实/付费 Provider

## 1. 修订结论

本期不建设完整 TaskPlan 系统，不新增 `plan_task` Tool，不给所有 Tool 增加 `plan_step_ref`，也不使用严格计划校验阻塞模型执行。

采用更轻的方案：

1. 通过系统 Prompt 让 Agent 在执行前先明确目标、步骤、每一步输入和产物去向。
2. 规定一个简短的 `<execution_outline>` 输出结构，帮助模型维持多步任务意识。
3. 上下文压缩时原样保留最新执行提纲，不让摘要模型改写或丢失其中的资产关系。
4. Tool 默认允许串行执行；每次拿到真实 Tool Result 后再决定下一步。
5. 后一步直接使用前一步 Tool Result 返回的精确资产引用，不通过资产列表或“最新图片”重新猜测。
6. Job 创建后在后端最多等待 180 秒；终态作为原 Tool Call 的可信 Tool Result 返回。
7. 超过 180 秒时返回一次 `waiting_external` 并结束本轮 Agent 执行；Job 继续运行，但终态不自动唤醒模型。
8. 不实现严格的 TaskPlan、依赖图和 CompletionClaimGuard；错误提纲不能让整个 Agent 阻塞。
9. 原 A07“拆分后原子生成 256×256 组件”延期，本期不改拆分和 normalize 工作流。

## 2. 当前问题与本期处理范围

最新 Agent 会话暴露了以下问题：

1. Agent 没有先明确本轮包含换风格、抠图、拆分等哪些步骤。
2. 审批通过后前端立即发送 continuation，自然语言消息在 Job 终态之前唤醒 Agent。
3. Agent 调用全局资产列表，从历史同名或相似图片中选中了错误输入。
4. 用户只要求修改白底时，Agent 把参考图生成从 `from_image` 改成了 `from_prompt`。
5. Agent 把 12 个组件和 3×4 布局擅自改成 9 个和 3×3。
6. Qwen 有时把 Tool Call JSON 当普通文本输出，导致执行链中断。

本期优先修复：

- 审批和 Job 终态之间的竞态；
- 180 秒内 Job 结果如何作为可信 Tool Result 交还 Agent；
- 后续 Tool 如何使用正确的上一步资产；
- 参考图换风格时保留原图和 `from_image`；
- 压缩后仍保留执行步骤和资产关系；
- Tool Call 格式错误的安全修复。

本期不优先修复：

- 建立通用持久化任务图；
- 对模型计划做 JSON Schema 级强校验；
- 用硬规则阻止所有可能不准确的完成声明；
- 把图片生成、抠图、拆分固化为固定流水线；
- 合并拆分和 256×256 normalize；
- 对主观风格质量做自动重试。

## 3. 不可破坏的行为原则

### 3.1 不建立固定图片依赖

以下请求必须继续独立可用：

- 直接生成图片；
- 直接对用户图片抠图；
- 直接按网格拆图；
- 直接按透明连通区域拆图；
- 直接放大或规范化；
- 对两个不同资产分别执行互不依赖的 Tool。

只有用户任务本身要求“把上一步结果继续处理”时，后一步才消费上一步产物。例如：

```text
换风格 → 抠图 → 拆分
```

这是本次请求中的执行关系，不是系统级强制流水线。

### 3.2 提纲是提示，不是门禁

- 多步、异步或输入容易混淆时，Prompt 要求模型先输出执行提纲。
- 单个明确 Tool 操作可以直接执行，不增加无关前置步骤。
- 提纲缺失、格式不完整或步骤表述不标准时，不阻止 Tool 执行。
- Runtime 不解析完整依赖图，不做 `plan_step_ref` 匹配。
- 用户修订要求时，模型更新提纲即可，不需要迁移 TaskPlan 状态。

### 3.3 Tool 可以串行执行

本期默认采用：

```text
明确当前步骤
→ 调用一个 Tool
→ 等待真实 Tool Result
→ 使用结果中的精确资产引用决定下一步
→ 调用下一个 Tool
```

不要求并行。即使两个步骤互不依赖，也允许 Agent 串行完成，以减少资产选择和异步状态的复杂度。

### 3.4 不使用全局“最新资产”衔接结果

- 已知 Tool Result 时，不得调用资产列表重新发现它。
- 不以文件名、创建时间、同名匹配或 `_newest_assets_first` 推断步骤结果。
- Tool facade 必须执行模型明确传入的受管资产引用，不得内部回退到“当前最新图片”。
- 用户明确要求处理历史资产时，仍然允许使用该历史资产。

## 4. 轻量执行提纲

### 4.1 推荐输出结构

在多步、异步或多资产任务的第一个业务 Tool 之前，模型输出以下简短结构：

```text
<execution_outline>
goal: 基于用户参考图换风格，并输出独立透明组件
steps:
  1. generate_images <- user_attachment:reference_image
  2. edit_image <- tool_output:1
  3. split_image <- tool_output:2
current: 1
constraints: 保持原组件数量和布局；换风格使用 from_image
</execution_outline>
```

规则：

1. `goal` 描述本轮目标。
2. `steps` 只列当前已知步骤，不需要生成通用 DAG。
3. 输入写成 `user_attachment`、`user_selected_asset`、`explicit_asset` 或 `tool_output:N`。
4. `current` 标记下一步。
5. `constraints` 只保留数量、布局、背景、尺寸、参考图等关键限制。
6. 用户改变要求时输出一个完整的新提纲，替代旧提纲。
7. 该结构允许自然语言小幅偏差，不运行严格 Schema 校验。

### 4.2 单步任务

以下请求不要求完整提纲：

```text
直接抠掉这张图的背景。
把当前图片按 4×2 拆开。
把这张图放大到 1024×1024。
```

模型只需确认当前输入资产，并直接调用对应 Tool。

### 4.3 提纲的可见性

- 首版允许提纲作为简短 assistant 内容显示在 Agent 会话中。
- 不展示内部 Job ID、数据库 ID、绝对路径或 Provider 信息。
- 如果后续需要隐藏，可将相同文本放入受管的 assistant metadata，但本期不为隐藏提纲增加额外协议复杂度。

## 5. 上下文压缩保护

用户明确要求执行提纲不能在压缩时丢失。本期使用“原样保留”，而不是让摘要模型理解并重写。

### 5.1 保护内容

压缩时必须保留：

1. 最新完整的 `<execution_outline>...</execution_outline>` 块；
2. 该提纲之后用户对步骤、源图、数量、布局、背景和风格的最新修订；
3. 当前尚未结束的 Tool Call 与其 `tool_call_id`；
4. 当前等待 Job 的 `job_id`、原 Tool Call 绑定和状态；
5. 当前串行链最近一次成功 Tool Result 的精确输出资产引用。

### 5.2 实现方式

1. 系统 Prompt 中的执行纪律本来就不进入普通历史摘要，始终重新注入。
2. `AgentHarness` 在选择 compaction cut point 前查找最新成对的 execution outline 标签。
3. 最新提纲作为 protected prefix/tail 原样进入压缩后的上下文，不传给 summarizer 改写。
4. pending Tool Call、pending Job 和最近 Tool Result 继续遵守现有安全 cut point，不拆断 Tool Call/Tool Result 对。
5. 如果没有找到合法闭合标签，压缩照常进行，不因此失败。
6. 不新增 TaskPlan 表；保护信息继续来自现有会话消息、Tool 事件和 Job wait 记录。

### 5.3 验证目标

- 自动压缩和手动压缩后，提纲文本逐字一致。
- `from_image`、3×4、12 个组件等关键约束仍在模型上下文中。
- 压缩不会把旧资产描述替换成当前步骤输入。
- malformed 提纲不会导致 compaction 或下一次 Agent 请求失败。

## 6. 正确资产衔接

### 6.1 Tool Result 输出契约

对会产生资产的 facade Tool，模型可见结果至少包含：

```json
{
  "status": "succeeded",
  "source_tool": "generate_images",
  "output_asset_refs": ["opaque-ref"],
  "output_count": 1,
  "job_ref": "opaque-job-ref"
}
```

底层 Repository 可以继续使用 `output_asset_ids`；进入 Agent Tool Result 前统一转换成可继续传给 facade Tool 的 opaque asset refs。

### 6.2 串行传递规则

Prompt 和 Tool description 强调：

1. 下一步消费上一步结果时，直接复制 Tool Result 中的 `output_asset_refs`。
2. 不调用 `inspect_workspace(view=assets)` 或资产列表寻找刚产生的结果。
3. 多个输出时，根据用户要求和结果顺序选择；无法判断时询问用户。
4. 用户明确指定历史资产时使用其明确引用，不受当前串行链影响。
5. Tool Result 没有输出资产时停止后续依赖操作并说明状态。

### 6.3 Runtime 保留的必要校验

本期只保留安全和完整性所需校验：

- 资产引用存在；
- 资产属于当前项目；
- Tool 参数满足既有 Schema；
- Job 结果来自 Job Repository；
- Job、conversation、run 和原 `tool_call_id` 绑定一致；
- 一个 Tool Call 只产生一个模型可见 Tool Result。

不增加以下严格校验：

- 不要求 Tool Call 携带计划步骤 ID；
- 不比较模型提纲与实际 Tool 名称；
- 不因模型漏写某个步骤而拒绝 Tool；
- 不建立通用步骤依赖状态机；
- 不因主观风格不匹配自动阻塞或重新付费。

## 7. Job 最长 180 秒等待

### 7.1 目标语义

Job 创建成功后，原 Tool 执行在后端等待终态，最长 180 秒：

```text
Tool Call
→ 创建 Job
→ JobCompletionBroker.wait_for_terminal(job_id, timeout=180s)
→ 终态 Tool Result，或一次 waiting_external 后结束本轮 Run
```

模型不调用 `sleep`，不循环调用 `control_job(status)`，前端不拼接 Job 结果消息。

### 7.2 无审批 Job

对直接创建 Job 的 Tool：

1. facade 创建 Job；
2. `JobCompletionBroker` 最多等待 180 秒；
3. 180 秒内终态时，原 Tool Call 直接收到可信 Tool Result；
4. Agent 根据 Tool Result 串行执行下一步。

### 7.3 需要审批的 Job

审批会暂停原 Agent Run，但仍要保持原 Tool Call 关系：

```text
Agent Tool Call
→ awaiting_ui_action
→ 用户审批
→ 后端创建 Job
→ ApprovedToolJobWait 接管原 tool_call_id
→ 最多等待 180 秒
→ 写入原 Tool Call 的可信 Tool Result
→ 有后续工作时恢复 Agent
```

审批 HTTP 请求不保持 180 秒。审批成功后由后端 wait task 等待，前端只展示状态。

协议要求：

1. `awaiting_ui_action` 是发送给桌面 UI 的运行时控制事件，不是关闭 Tool Call 的模型可见最终 Tool Result。
2. 原 `tool_call_id` 在审批期间保持 suspended。
3. 用户批准后，同一逻辑 Tool 执行继续创建 Job 并等待。
4. 用户拒绝时，原 Tool Call 收到一次 `declined` Tool Result。
5. Job 在 180 秒内终态时，原 Tool Call 收到一次终态 Tool Result。
6. Job 超时时，原 Tool Call 收到一次 `waiting_external` Tool Result。
7. 任一路径都只能产生一个模型可见 Tool Result。

### 7.4 180 秒超时

180 秒是前台等待预算，不是 Job 失败期限：

```json
{
  "status": "waiting_external",
  "job_ref": "opaque-job-ref",
  "waited_seconds": 180,
  "output_asset_refs": []
}
```

超时后：

- Job 继续运行；
- 不重复提交、不自动取消；
- 串行链暂停，不执行需要该结果的下一步；
- UI 显示后台处理中；
- Agent 不通过资产列表猜测输出；
- Job 晚到终态后只更新 Job Repository 和工作区状态，不自动恢复 Agent。

### 7.5 最小持久化

不持久化 TaskPlan，只持久化 Job 等待所需的最小关系：

```text
job_id
project_id
conversation_id
run_id
tool_call_id
tool_name
state: waiting | terminal_returned | waiting_external
created_at / resumed_at
```

建议新增迁移：

```text
src/aipic_to_model/agent/session/migrations/0006_agent_job_waits.sql
```

该记录只用于 180 秒等待、幂等和后续向模型提供已知 pending Job 上下文，不表达业务步骤和依赖图。

### 7.6 超时后的继续方式与重启

1. Job worker 晚到终态时只更新 Job Repository、资产注册和工作区状态。
2. 不因为晚到终态创建 Agent 消息、Tool Result、模型请求或自动 continuation。
3. UI 可以读取 Job 状态并展示结果，但不得把结果拼成 Agent 消息。
4. 用户下一次发送“继续”或其他新请求时，Runtime 将已知 pending Job 摘要加入上下文。
5. Agent 使用已知 `job_ref` 调用一次 `control_job(status)`；终态结果作为这次新 Tool Call 的唯一可信 Tool Result 返回。
6. 如果 Job 仍未终态，Agent 继续报告等待，不重新提交任务。
7. 应用重启后，RecoveryService 只恢复 Job 自身；不会自动唤醒旧 Agent Run。
8. 前端不调用 Agent resume API，不发送 `Task job_id=... result_asset_ids=...`。

## 8. Prompt 和 Tool guidance 修订

### 8.1 系统 Prompt 核心内容

在现有 Agent 系统 Prompt 中加入：

```text
Before starting a multi-step, asynchronous, or multi-asset task, write a short
<execution_outline> that identifies the goal, ordered steps, the input source
for each step, the current step, and critical constraints. This outline is a
working aid, not a permission gate. A malformed or omitted outline must not by
itself block a valid Tool call.

Execute Tools serially unless there is a clear reason not to. After each Tool
result, use its exact output_asset_refs for the next dependent operation. Do
not search the asset list, choose the newest asset, or match by filename when
the required output reference is already known.

A direct request to remove a background, split an image, resize an image, or
generate an image is a valid one-step task. Do not invent unrelated prerequisite
steps.

When the user changes only one constraint, preserve all unchanged inputs and
constraints. In particular, do not change a reference-image transformation
from from_image to from_prompt unless the user explicitly removes the image
reference.
```

### 8.2 Tool description 调整

- `inspect_workspace`：不得用于重新发现已经由 Tool Result 返回的 Job 输出。
- `generate_images`：参考图换风格默认使用 `from_image`。
- `edit_image`：直接抠图是合法单步任务；若消费上一 Tool 输出，使用其精确引用。
- `split_image`：直接拆分是合法单步任务；不得猜测网格数量。
- `control_job`：Runtime 正在等待的 Job 不由模型主动轮询。

### 8.3 不增加 Prompt 专用业务 hack

Prompt 描述的是通用原则：

- 先想清步骤；
- 明确当前输入；
- 串行消费真实结果；
- 保留用户未修改的约束；
- 不猜测已知资产。

不写成“遇到换风格必须先抠图”或“拆分前必须执行某个 Tool”等固定图片流水线。

## 9. 参考图换风格

以下表达默认识别为参考图编辑/变体：

- 基于这张图换风格；
- 保持内容或布局，只改变视觉风格；
- 参考这张图生成另一个风格版本；
- 把刚才生成改成白底，但其他要求不变。

预期调用：

```text
generate_images
mode: from_image
source_asset_ref: 用户原始参考图
prompt: 完整风格、保留内容、背景和布局要求
```

只有以下情况改用 `from_prompt`：

- 用户明确要求不再参考原图；
- 用户明确要求从文字重新生成；
- 参考资产不可用且用户确认改为纯文本生成。

用户只修改白底、色彩、材质、光照或尺寸时，必须保留原参考图绑定。

## 10. 完成表达采用轻量约束

本期不实现 `CompletionClaimGuard`，不因为模型总结可能不准确而阻塞整个 Agent。

采用以下方式降低误报：

1. Tool Result 返回 `status`、`output_count`、尺寸、格式和透明通道等已有结构化事实。
2. Prompt 要求最终回答只能引用 Tool Result 中实际存在的事实。
3. Job 仍在 `queued/running/waiting_external` 时，只能说明等待，不能声称已完成。
4. Tool 明确失败或输出为空时，不继续串行消费该结果。
5. 网格拆分数量仍由 Tool 既有参数和结果决定，不由模型改写。
6. 可增加非阻塞诊断日志提示“总结数量与 Tool Result 不一致”，但不拒绝 Tool 或卡住会话。

本期不新增图片内容 QA、主观风格评分或自动付费重试。

## 11. Qwen Tool Call 格式修复

当模型把 Tool Call 作为纯 JSON 文本输出时：

1. 检测文本是否高度符合已注册 Tool Call 外形。
2. 不直接执行文本中的 Tool。
3. 向同一模型发送一次格式纠正请求，要求使用原生 Tool Call 通道。
4. Provider 支持时使用 `tool_choice=required`。
5. 第二次仍失败则返回稳定格式错误，不循环修复。
6. 修复后的参数仍通过现有 Tool Schema 和审批边界。

这项修复只处理协议格式，不替模型修改资产、步骤或业务参数。

## 12. 桌面 UI 最小改动

本期不建设完整计划面板，只展示必要状态：

- 执行提纲继续显示为会话中的简短文本；
- 审批已提交；
- Job 正在 180 秒等待窗口内处理；
- Job 超时后仍在后台处理；
- Job 失败或取消；
- Job 成功后展示准确结果资产。

必须删除：

```text
The user approved the external operation...
Task job_id=... completed, result_asset_ids=...
```

这些内容不得再由前端通过 `sendAgentMessage` 注入会话。

## 13. 详细实施批次

### A00：离线回归基线

任务步骤：

1. 建立包含用户参考图、多个历史同名资产和可控 Job 的离线 fixture。
2. 固化审批后提前唤醒、错误选择旧资产、`from_image` 丢失和 Qwen 文本 Tool JSON 样本。
3. 测试只记录当前失败，不先修改生产行为。

涉及文件：

- `tests/fixtures/controlled/`
- `tests/integration/agent/`
- `desktop/frontend/src/features/agent/AgentPanel.test.tsx`

验证目标：

- 样本可稳定复现错误。
- fixture 不访问真实 Provider。
- 不读取或写入用户真实项目。

### A01：停止审批后即时 continuation

任务步骤：

1. 修改 `AgentPanel.decide`。
2. 审批返回 `queued` 后不再调用 `sendAgentMessage`。
3. 保留 Job 排队、工作区打开和状态展示。
4. 删除审批后英文 continuation 的测试期待。

涉及文件：

- `desktop/frontend/src/features/agent/AgentPanel.tsx`
- `desktop/frontend/src/features/agent/AgentPanel.test.tsx`

验证目标：

- 审批后 Job 终态前不会产生新的用户消息或模型调用。
- UI 仍显示审批成功和 Job 已排队。
- 拒绝、取消和刷新行为不回归。

### A02：JobCompletionBroker 与 180 秒等待

任务步骤：

1. 新增 `JobCompletionBroker`。
2. Job worker 在终态提交 Repository 后通知 Broker。
3. Tool 或 `ApprovedToolJobWait` 最多等待 180 秒。
4. 定义 `waiting_external`，超时不取消 Job。
5. 处理终态与超时同时发生的竞态。

涉及文件：

- 新增 `src/aipic_to_model/application/jobs/completion_broker.py`
- `src/aipic_to_model/application/jobs/runner.py`
- `src/aipic_to_model/application/jobs/worker.py`
- `src/aipic_to_model/application/b02_runtime.py`
- `src/aipic_to_model/api/dependencies.py`
- `src/aipic_to_model/composition.py`
- `tests/integration/jobs/`

验证目标：

- 180 秒内终态直接形成原 Tool Call 的 Tool Result。
- 超时返回 `waiting_external`，Job 保持运行。
- 等待取消不等于 Job 取消。
- 不重复提交付费操作。

测试使用短可配置时钟模拟 179/180 秒边界，不让单元测试真实等待 3 分钟。

### A03：审批后 Tool 等待与超时收口

任务步骤：

1. 新增 `ApprovedToolJobWait`。
2. 新增最小 `agent_job_waits` 持久化映射。
3. 将 `awaiting_ui_action` 定义为 sideband 控制事件，不提前关闭 Tool Call。
4. 审批创建 Job 后由后端接管原 `tool_call_id`。
5. 180 秒内终态时从 Repository 读取结果并写入唯一的可信 Tool Result。
6. 拒绝或超时时分别写入一次 `declined` 或 `waiting_external` Tool Result。
7. 晚到终态和应用重启只恢复 Job/工作区状态，不自动唤醒 Agent。

涉及文件：

- 新增 `src/aipic_to_model/agent/execution/approved_job_wait.py`
- 新增 `src/aipic_to_model/agent/session/migrations/0006_agent_job_waits.sql`
- `src/aipic_to_model/agent/session/sqlite.py`
- `src/aipic_to_model/agent/core/events.py`
- `src/aipic_to_model/agent/core/agent_loop.py`
- `src/aipic_to_model/agent/integrations/runtime.py`
- `src/aipic_to_model/api/contracts/agent.py`
- `src/aipic_to_model/api/routers/agent.py`
- `src/aipic_to_model/application/jobs/worker.py`
- `src/aipic_to_model/application/jobs/recovery_service.py`
- `tests/integration/agent/test_session_recovery.py`
- `tests/integration/jobs/`

验证目标：

- Job 结果绑定原 conversation、run 和 `tool_call_id`。
- 前端不能提供或替换结果资产。
- 一个 Tool Call 不会收到两个 Tool Result。
- 拒绝、成功和超时三条路径分别只产生一个最终 Tool Result。
- 晚到终态不会产生 Agent 消息或模型请求。
- 重启后用户发起下一轮时，可以通过已知 `job_ref` 查询结果并继续。

### A04：轻量执行提纲与压缩保护

任务步骤：

1. 在系统 Prompt 加入 `<execution_outline>` 结构和串行执行规则。
2. 多步任务要求模型先写简短提纲，单步任务可直接执行。
3. compaction 查找最新闭合提纲并原样保留。
4. 保留 pending Tool/Job 和最近输出资产引用。
5. malformed 或缺失提纲只记录诊断，不阻塞 Tool。
6. 不新增 TaskPlan、`plan_task`、`plan_step_ref` 或 PlanBindingGuard。

涉及文件：

- `desktop/frontend/src/features/agent/AgentPanel.tsx`
- `src/aipic_to_model/agent/integrations/runtime.py`
- `src/aipic_to_model/agent/harness/harness.py`
- `src/aipic_to_model/agent/harness/context.py`
- `tests/integration/agent/test_compaction.py`
- `tests/integration/agent/test_auto_compaction.py`
- `tests/integration/agent/test_prompt_templates.py`

验证目标：

- 多步任务在第一个业务 Tool 前出现执行提纲。
- 直接抠图和直接拆分不被添加依赖。
- 提纲格式轻微错误不阻塞 Tool。
- 压缩前后提纲、关键限制和当前资产引用逐字保持。

### A05：Tool guidance 与正确资产串行传递

任务步骤：

1. 统一会产生资产的 facade Tool Result 结构。
2. Prompt 和 Tool description 要求复制精确 `output_asset_refs`。
3. 移除任何 Tool 内部“最新资产”回退。
4. 禁止用资产列表重新发现已知 Job 结果。
5. 强调参考图换风格使用 `from_image`。
6. 用户局部修订时保留未变化的输入和约束。

涉及文件：

- `src/aipic_to_model/agent/integrations/facade_tools.py`
- `src/aipic_to_model/agent/integrations/tool_guidance.py`
- `src/aipic_to_model/agent/integrations/runtime.py`
- `desktop/frontend/src/features/agent/AgentPanel.tsx`
- `docs/agent-model-tool-contract.md`
- `docs/agent-image-tool-usage.md`
- `tests/integration/agent/test_facade_tools.py`
- `tests/contract/test_agent_api.py`

验证目标：

- 历史中存在多个同名资产时，后续 Tool 仍使用上一步精确输出。
- 单步 Tool 继续使用用户当前明确资产。
- 用户只修改白底时仍然使用原参考图和 `from_image`。
- Tool 串行执行可以完成多步任务。

### A06：轻量结果事实与完成表达

任务步骤：

1. Tool Result 返回已有的状态、数量、格式、尺寸等结构化事实。
2. Prompt 要求最终回答依据这些事实。
3. 等待、失败和空输出时禁止在 Prompt 中声称成功。
4. 可增加非阻塞诊断日志，不增加 CompletionClaimGuard。

涉及文件：

- `src/aipic_to_model/agent/integrations/facade_tools.py`
- `src/aipic_to_model/agent/integrations/runtime.py`
- `src/aipic_to_model/agent/core/agent_loop.py`
- `tests/unit/agent/test_agent_loop.py`
- `tests/integration/agent/test_facade_tools.py`

验证目标：

- Job 等待时回答为等待状态。
- Tool 失败时不会被 Prompt 引导成成功。
- 结果事实不足时可以说明不确定，而不是阻塞会话。
- 不引入新的硬完成门禁。

### A07：拆分后原子 256×256 输出——本期延期

本期不实施，不修改以下内容：

- `split_image` 参数；
- `local_image_processing.py` 的拆分/normalize 实现；
- 批量 contain、透明画布、边距和放大规则；
- 拆分与规范化的原子事务。

延期原因：先验证 Agent 能稳定选对输入、等待正确 Job、拿到正确 Tool Result 并串行执行现有 Tool。现有逐张 normalize 虽然效率较低，但可以继续使用。

进入后续阶段的前提：A01～A06、A08 和核心受控 E2E 已稳定通过，且性能数据证明逐图调用确实是主要瓶颈。

### A08：Qwen Tool Call 格式修复

任务步骤：

1. 检测疑似纯文本 Tool JSON。
2. 不直接执行文本内容。
3. 最多发起一次原生 Tool Call 格式纠正。
4. 第二次失败返回稳定错误。
5. 修复后继续使用既有 Schema 和审批校验。

涉及文件：

- `src/aipic_to_model/agent/providers/api/openai_completions.py`
- `src/aipic_to_model/agent/harness/harness.py`
- `src/aipic_to_model/agent/core/agent_loop.py`
- `tests/integration/agent/test_qwen3_vl_runtime.py`
- `tests/integration/agent/test_openai_compatible_provider.py`

验证目标：

- 纯文本 JSON 永不直接执行。
- 一次修复成功后产生原生 Tool Call。
- 非法参数仍由 Tool Schema 拒绝。
- 付费审批边界不能被绕过。

### A09：桌面等待状态与准确结果展示

任务步骤：

1. 删除审批和 Job 终态的自然语言 continuation。
2. 展示 180 秒内处理和超时后台处理状态。
3. Job 成功时打开准确结果资产。
4. 刷新后从后端 Job 状态恢复显示。
5. 不建设完整计划面板。

涉及文件：

- `desktop/frontend/src/features/agent/AgentPanel.tsx`
- `desktop/frontend/src/features/agent/AgentPanel.test.tsx`
- `desktop/frontend/src/features/shell/shell.css`
- `desktop/frontend/src/shared/api/client.ts`

验证目标：

- 不出现 stale spinner 或重复成功消息。
- UI 不通过消息文本解析结果资产。
- 刷新后等待状态和最终预览正确。
- 不展示内部 ID、绝对路径或 Provider 载荷。

### A10：受控 E2E 与回归

任务步骤：

1. 增加完整受控 Agent 串行 Tool 场景。
2. 预置多张旧同名/相似资产。
3. 覆盖直接抠图、直接拆分、参考图换风格和换风格后继续处理。
4. 覆盖审批、180 秒内完成、超时模拟、晚到终态不唤醒 Agent，以及用户下一轮继续。
5. 覆盖 compaction 后继续使用正确资产。
6. 保留 redacted DOM、runtime/network、workspace 和截图证据。

涉及文件：

- `scripts/run_controlled_webview2_e2e.py`
- `tests/fixtures/controlled/`
- `tests/evidence/<feature>/<timestamp>/`
- `docs/controlled-validation.md`（仅当运行方式需要更新）

验证目标：

- 审批后没有提前 Agent 调用。
- Job 终态前没有资产列表猜测。
- Tool 串行执行并使用准确的上一步输出。
- 超时后的 Job 终态不会触发新的 Agent 消息或模型请求。
- 用户下一轮继续时通过已知 `job_ref` 获得准确结果。
- 压缩后执行提纲和资产关系保持。
- 全程不访问真实 Provider，不泄露敏感信息。

## 14. 关键测试矩阵

| 场景 | 是否需要提纲 | 预期行为 |
| --- | --- | --- |
| 直接抠图 | 否 | 直接处理用户明确资产 |
| 直接透明图拆分 | 否 | 直接调用 `split_image` |
| 规则 4×2 拆分 | 否 | 输出 8 张，不强制抠图 |
| 参考图换风格 | 建议简短提纲 | `from_image`，等待 Job |
| 换风格后抠图 | 是 | 抠图使用生成 Tool Result 的精确资产 |
| 生成后直接拆分 | 是 | 合法跳过抠图 |
| A 图抠图、B 图放大 | 是 | 串行处理，各用明确资产 |
| 用户只改白底 | 更新提纲 | 保留原参考图和 `from_image` |
| 有多个旧同名资产 | 是 | 不通过列表或最新资产猜测 |
| 提纲格式不完整 | 可识别即可 | 不阻塞合法 Tool |
| 上下文自动压缩 | 已有提纲 | 最新提纲原样保留 |
| Job 180 秒内完成 | 异步 | 原 Tool Call 收到终态 Tool Result |
| Job 超过 180 秒 | 异步 | `waiting_external`，后台继续 |
| Job 晚到终态 | 异步 | 只更新 Job/工作区，不唤醒 Agent |
| 用户在晚到终态后继续 | 新用户轮次 | 用已知 `job_ref` 查询并继续 |
| Qwen 输出文本 Tool JSON | 任意 | 一次格式纠正，不直接执行 |

## 15. 验证命令与顺序

按仓库规则先运行最小测试，再扩展。

### 15.1 Python 最小验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/agent/test_compaction.py -q
.\.venv\Scripts\python.exe -m pytest tests/integration/agent/test_auto_compaction.py -q
.\.venv\Scripts\python.exe -m pytest tests/integration/agent/test_facade_tools.py -q
.\.venv\Scripts\python.exe -m pytest tests/integration/agent/test_session_recovery.py -q
.\.venv\Scripts\python.exe -m pytest tests/integration/agent/test_qwen3_vl_runtime.py -q
.\.venv\Scripts\python.exe -m pytest tests/integration/jobs -q
```

验证重点：

- execution outline 压缩保护；
- Tool Result 资产引用；
- 180 秒等待的虚拟时钟边界；
- Tool wait 幂等、超时收口和下一轮继续；
- Qwen 格式修复。

### 15.2 前端组件与构建

```powershell
pnpm --dir desktop/frontend exec vitest run src/features/agent/AgentPanel.test.tsx
pnpm --dir desktop/frontend build
```

### 15.3 受控全量验证

```powershell
.\scripts\run_controlled_validation.ps1
```

### 15.4 WebView2 热更新验证

这是用户可见状态和交互变化，实施时必须遵循 `docs/controlled-validation.md` 的 Hot-update desktop UI verification。

```powershell
.\scripts\run_controlled_webview2.ps1 -DebugPort 9230 -DevPort 14200 -CreateProject -KeepApp
.\.venv\Scripts\python.exe scripts\run_controlled_webview2_e2e.py `
  --debug-port 9230 --output tests\evidence\controlled-webview2-current\agent-job-wait
```

验证前必须按仓库规则确认并停止本工作区拥有的旧 Tauri、sidecar、Vite 进程。失败时保留 redacted DOM、runtime/network、workspace 和 screenshot 证据，修复后沿相同路径重跑成功。

### 15.5 Rust

本方案预期不修改 Rust host。只有实际实施触及 `desktop/src-tauri` 时才运行：

```powershell
cargo test --manifest-path desktop/src-tauri/Cargo.toml
```

## 16. 修复后的预期表现

### 16.1 多步参考图任务

用户：

```text
参考这张图换成赛博朋克风格，去掉背景，再把组件拆开。
```

Agent 先输出简短提纲，明确：

1. 从用户参考图执行 `from_image`；
2. 抠图使用生成结果；
3. 拆分使用抠图结果。

每一步串行执行。下一步直接消费上一步 Tool Result，不搜索全局资产。

### 16.2 直接抠图

用户：

```text
直接抠掉这张图的背景。
```

Agent 直接调用抠图 Tool，不要求先生成，也不要求先创建计划。

### 16.3 直接拆分

用户：

```text
按 4×2 直接拆开这张图。
```

Agent 直接调用 `split_image/grid`，不强制抠图，输出 8 张。

### 16.4 用户局部修订

用户：

```text
刚才的生成改成白底，其他不变。
```

Agent 更新执行提纲中的背景约束，继续使用原参考图和 `from_image`。如需重新调用付费 Provider，仍然重新审批。

### 16.5 Job 超过三分钟

UI 显示：

```text
任务仍在后台处理，本轮 Agent 已停止等待。任务不会重复提交；结果完成后，你可以让我继续后续处理。
```

Agent 不猜测结果、不调用资产列表。Job 终态后只更新工作区，不自动唤醒 Agent。用户下一次要求继续时，Agent 使用已知 `job_ref` 调用 `control_job(status)`，再根据新的可信 Tool Result 串行执行后续步骤。

### 16.6 提纲输出失败

如果模型漏掉标签或输出格式不完整，但随后给出了合法且明确的 Tool Call，系统继续执行，不因计划格式把会话卡死。后续 Prompt 仍提醒模型在需要时补充或更新提纲。

## 17. 兼容、迁移与回滚

### 17.1 兼容

- 不修改现有 facade Tool 的业务依赖关系。
- 不增加所有 Tool 都必须携带的新字段。
- 旧会话没有 execution outline 时仍可继续使用。
- 单步请求保持现有直接执行体验。
- `_newest_assets_first` 可以继续用于资产浏览，但不用于已知结果衔接。
- 新增 Job wait 表不修改已有项目资产和 Job 表。

### 17.2 回滚

- Prompt 和 compaction 保护可以独立回滚。
- Job wait 表为附加控制数据，不删除或重写业务资产。
- 前端 continuation 删除必须和后端 180 秒 Tool 等待同批启用，避免审批后无人等待原 Tool。
- 不允许回滚到由前端传入 `result_asset_ids` 的自然语言续跑方式。

## 18. 本期完成定义

只有以下条件全部满足，本期才可标记完成：

1. 审批返回 queued 后，前端不会立即唤醒 Agent。
2. Job 创建后由后端最多等待 180 秒。
3. 180 秒内终态作为原 Tool Call 的可信 Tool Result 返回。
4. 超时不失败、不取消、不重复提交。
5. 晚到终态和重启不会自动唤醒 Agent，也不依赖前端自然语言回写。
6. 用户下一轮继续时能通过已知 `job_ref` 获取可信终态并继续处理。
7. 多步任务 Prompt 会先形成轻量执行提纲。
8. 单步直接抠图、拆分、生成不被添加无关依赖。
9. Tool 可以串行执行并直接使用上一步精确输出资产。
10. 项目存在历史同名资产时不会通过“最新资产”回退误选。
11. 用户只修改白底等局部要求时不会丢失原参考图。
12. 自动或手动压缩后，最新执行提纲和关键资产关系原样保留。
13. 提纲缺失或格式错误不会阻塞合法 Tool。
14. Qwen 普通文本 Tool JSON 不会被直接执行。
15. 原 A07 没有混入本期实施范围。
16. 最小测试、前端构建、受控验证和 WebView2 交互验证通过。
17. 验证过程没有访问真实/付费 Provider，也没有泄露敏感信息。

## 19. 建议审查和实施顺序

1. A01：删除审批后的前端即时 continuation。
2. A02：JobCompletionBroker 和 180 秒等待。
3. A03：审批后 Tool 等待、超时收口和下一轮继续。
4. A04：轻量执行提纲与压缩保护。
5. A05：Tool Result 结构和正确资产串行传递。
6. A06：轻量结果事实与完成表达。
7. A08：Qwen Tool Call 格式修复。
8. A09：桌面等待状态和准确结果展示。
9. A10：受控 E2E 与完整回归。

A07 明确延期，不进入本期开发和验收。
