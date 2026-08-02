# Python Pi Agent 框架迁移实施计划

> 状态：拟实施
> 目标仓库：当前仓库
> 参考实现：冻结的上游 Pi 提交中的 `packages/agent`、`packages/ai`，以及
> `packages/coding-agent/src/core/agent-session.ts` 中的自动压缩调度
> 目标运行时：Python 3.14、`asyncio`、FastAPI、SQLite
> 首个真实模型：DeepSeek OpenAI-compatible API
> Provider 范围：迁移 Pi 当前注册的 38 个聊天 Provider、1 个图片 Provider及其认证/模型能力
> 核心取舍：继承 Pi 的 Agent Core、Harness、自动上下文压缩、Skills、内置文件/命令工具和扩展思想；不迁移分支会话树

## 1. 结论与目标

本迁移不是把 TypeScript 文件机械翻译为 Python，也不是在现有 B03 重型状态机外再套一层
Agent。目标是以 Pi 当前已经实现的核心行为为参考，在 Python 中建立本项目自己的通用本地
Agent 框架，再将 AIPic 的图片、资产、Job 和 3D 能力作为业务工具接入。

迁移完成后的系统应具备：

1. 支持流式响应和原生 Tool Calling 的异步 Agent Loop。
2. 支持多轮“模型 → Tool → Tool Result → 模型”循环。
3. 支持 `steer`、`follow_up`、取消和下一轮消息队列。
4. 支持线性、可恢复的 SQLite Conversation/Session，不支持会话树和分支导航。
5. 支持 `read`、`write`、`edit`、`bash` 内置工具，并统一通过 `ExecutionEnv` 执行。
6. 支持从应用级、用户级、项目级目录发现和激活 `SKILL.md`。
7. 支持扩展注册 Tool、Skill 来源、Provider、上下文转换器和生命周期 Hook。
8. 支持把当前 B01 `ToolRegistry` 暴露为 Agent Tool，而不复制业务执行逻辑。
9. 支持 Fake Provider 全量自动化测试和 DeepSeek 真实 API 的 opt-in 验证。
10. 密钥不进入 SQLite、消息、日志、测试报告和诊断包。
11. 支持 Pi 当前全部 Provider、认证方式、模型目录和能力差异。
12. 在阈值接近或 Provider 返回 context overflow 时自动压缩上下文，并可继续原任务。

完成后，Agent 将是通用框架，AIPic 3D 资产工作流是安装在该框架上的第一组业务能力。

## 2. 迁移边界

### 2.1 迁移

- Pi 的 Message、Tool、ToolResult、AgentEvent 抽象。
- Pi 的低层 Agent Loop 和有状态 Agent 门面。
- `transform_context` 与 `convert_to_llm` 两阶段上下文处理。
- Provider 流式事件归一化。
- Tool 参数 schema、执行、进度更新和错误回传。
- 顺序/并行 Tool 执行模式；本项目初始默认顺序执行。
- `before_tool_call`、`after_tool_call` 和 turn save-point。
- steering、follow-up、next-turn 队列。
- AgentHarness 的运行配置快照、操作锁、持久化边界和资源解析思想。
- 内置 `read/write/edit/bash` 工具及 `ExecutionEnv` 隔离层。
- Skills、Prompt Template 和资源的发现、延迟加载与激活。
- 扩展注册、确定性 Hook 顺序和错误隔离。
- Fake Provider、事件序列测试和工具循环测试方法。
- 自动上下文压缩完整流程：usage/估算、threshold/overflow 触发、cut point、滚动摘要、
  retained tail、失败重试、事件、扩展覆盖和恢复。
- Pi 当前的 10 种聊天 API 协议、1 种图片 API 协议、38 个聊天 Provider 和
  OpenRouter Images Provider。
- Provider 认证：API Key、OAuth、AWS credential chain、Google ADC/service account、
  Cloudflare 组合凭据和 Radius 动态配置。
- 模型目录、上下文窗口、输出上限、输入模态、reasoning/thinking、价格和兼容选项。

### 2.2 不迁移

- 分支 Session Tree、leaf、branch summary、tree navigation。
- coding-agent CLI、TUI、主题、快捷键和终端 UI。
- Pi monorepo 的发布脚本、npm 包结构和 TypeScript 模型代码生成器；Python 侧另建等价
  Provider/Catalog/Auth 实现与同步脚本。
- Pi 的 JS/TS 动态扩展加载方式。
- Pi 的 Node 文件系统实现。
- 多 Agent、云端协作、插件市场和会话公开分享。

### 2.3 暂缓

- Tool 并行执行，先完成顺序语义和写操作正确性。
- 自动 Skill 选择模型，首版允许显式技能和基于描述的简单选择。
- 扩展热重载。
- 复杂审批版本链；业务需要时作为扩展或 AIPic Tool Policy 添加。

## 3. 与现有设计的关系

本计划保留当前 B01 的项目、资产、设置、Keyring、ToolRegistry、审计和幂等能力，但取代
`docs/execution-batches/B03-agent-orchestration.md` 中以下设计：

- 固定 `AgentDecision={final|needs_user_input|tool_calls}` 响应协议；
- 十余种强制 Run 状态及其完整迁移矩阵；
- Agent 层重复实现的 Tool 参数和风险校验；
- 首版完整审批版本链；
- 所有行为都必须投影为十种业务卡片后才可运行的前置要求。

新的原则是：

- Provider 原生 assistant message 中出现 Tool Call 就执行工具；没有 Tool Call 就结束当前 Loop。
- Agent 层只做通用 schema 校验；AIPic 业务 Tool 继续由 B01 Registry 做资产归属、幂等和风险校验。
- 通用 Agent Session 是线性的；AIPic Job 等待和业务审批在需要时作为业务状态扩展。
- 流式增量是观察事件，完整 Message/Tool Result 才是持久事实。
- 先建立可运行、可测试的核心，再逐项添加业务约束。

在开始实现阶段 1 前，应新增 ADR，明确本计划取代旧 B03 Agent Runtime 部分；旧 B03 中仍有
价值的 Job 恢复、付费幂等和资产安全要求转移到 AIPic 业务工具集，而不是全部删除。

## 4. 目标架构

```text
Tauri / React
      |
      | FastAPI / SSE
      v
Python AgentHarness
  ├── Agent Core / Agent Loop
  ├── Linear SQLite Session
  ├── Provider Registry
  │     ├── 10 Chat API Adapters + 1 Images API Adapter
  │     ├── 38 Chat Providers + OpenRouter Images
  │     ├── Auth / OAuth / Cloud Credential Resolvers
  │     └── Versioned Model Catalog
  ├── Extension Registry / Hooks
  ├── Skill Registry
  └── Agent Tool Registry
        ├── read / write / edit / bash
        └── AIPic ToolRegistry Adapter
              └── assets / image / selection / 3D / jobs
```

### 4.1 目标代码树

```text
src/aipic_to_model/agent/
  __init__.py
  core/
    models.py
    events.py
    stream.py
    tool.py
    agent_loop.py
    agent.py
    errors.py
  harness/
    harness.py
    state.py
    queues.py
    context.py
    compaction.py
    session.py
  providers/
    base.py
    fake.py
    registry.py
    models.py
    catalog.py
    auth/
      base.py
      api_key.py
      oauth.py
      aws.py
      google_cloud.py
      cloudflare.py
    api/
      openai_completions.py
      openai_responses.py
      azure_openai_responses.py
      openai_codex_responses.py
      anthropic_messages.py
      bedrock_converse_stream.py
      google_generative_ai.py
      google_vertex.py
      mistral_conversations.py
      pi_messages.py
      openrouter_images.py
    builtin/
      all.py
      deepseek.py
      ...
    sse.py
    websocket.py
  extensions/
    api.py
    hooks.py
    registry.py
    loader.py
  skills/
    models.py
    loader.py
    registry.py
    prompt.py
  tools/
    execution_env.py
    local_env.py
    read.py
    write.py
    edit.py
    bash.py
    output.py
    mutation_queue.py
    aipic_registry.py
  persistence/
    repository.py
    sqlite_repository.py
    migrations/
      0003_agent_linear.sql
```

```text
  tests/
  unit/agent/
  integration/agent/
  live/agent/
  fixtures/agent/
  fixtures/skills/
  fixtures/extensions/
  fixtures/providers/
  fixtures/compaction/
```

## 5. 核心契约

### 5.1 Message

首版支持：

- `UserMessage`
- `AssistantMessage`
- `ToolResultMessage`
- `SystemMessage`
- 扩展自定义消息

自定义消息默认不发送给模型。`convert_to_llm()` 负责过滤或转换；`transform_context()` 负责摘要、
裁剪和注入项目上下文。

### 5.2 Tool

```python
class AgentTool(Protocol):
    name: str
    label: str
    description: str
    parameters: dict[str, object]
    execution_mode: Literal["sequential", "parallel"]

    async def execute(
        self,
        tool_call_id: str,
        arguments: dict[str, object],
        context: ToolContext,
        cancellation: CancellationToken,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult: ...
```

所有失败通过异常进入统一错误映射，Agent Loop 将其转换为 `is_error=true` 的 Tool Result；
业务 Tool 已经返回结构化错误时，Adapter 保留其错误码和可恢复信息。

### 5.3 Provider

```python
class AgentModelProvider(Protocol):
    async def stream(
        self,
        request: ModelRequest,
        cancellation: CancellationToken,
    ) -> AsyncIterator[ProviderEvent]: ...
```

Provider 必须输出统一事件：

- `message_start`
- `text_delta`
- `tool_call_start`
- `tool_call_arguments_delta`
- `tool_call_end`
- `usage`
- `message_end`
- `provider_error`

Provider 不执行 Tool，也不写 Session。

### 5.4 AgentEvent

- `agent_start`
- `agent_end`
- `turn_start`
- `turn_end`
- `message_start`
- `message_update`
- `message_end`
- `tool_execution_start`
- `tool_execution_update`
- `tool_execution_end`
- `queue_update`
- `context_compacted`
- `extension_error`

事件订阅者按注册顺序执行；持久化订阅者必须先于 UI/SSE 通知观察到完整消息。

### 5.5 线性 Session

建议新增：

```sql
CREATE TABLE conversations (...);
CREATE TABLE agent_messages (...);
CREATE TABLE agent_operations (...);
CREATE TABLE agent_config_changes (...);
```

Session 至少保存：

- conversation ID；
- 单调 message sequence；
- Message 类型与经过脱敏的完整内容；
- Tool Call ID、Tool 名和 Tool Result；
- model/provider/thinking level；
- active tools 和 active skills；
- operation 开始、结束、中断；
- compaction summary 和覆盖到的 message sequence；
- steering/follow-up 已接受消息。

不保存：

- API Key、Authorization Header；
- Provider 完整原始请求/响应；
- 任意二进制；
- Agent Workspace 之外的绝对路径；
- 模型隐藏推理。

## 6. 分阶段迁移顺序

每一阶段只有在退出门通过后才能进入下一阶段。DeepSeek 真实验证分为“早期协议验证”和“最终综合
验证”，避免把 Provider 风险留到最后。

### 阶段 0：冻结范围、来源和基线

#### 工作

1. 记录 Pi 参考版本、commit SHA、MIT License 和参考文件清单。
2. 新增 ADR：Python Pi Agent 取代旧 B03 Agent Runtime。
3. 建立 Pi → Python 行为映射表，不逐行翻译未使用代码。
4. 冻结当前 B01 测试基线和耗时。
5. 将 Agent 新测试目录加入 pytest marker 约定：
   - `agent`
   - `live_llm`
   - `slow`
6. 规定真实测试默认跳过，只有 `RUN_LIVE_LLM_TESTS=1` 才执行。

#### 交付物

- ADR；
- `THIRD_PARTY_NOTICES.md` 中的 Pi MIT 来源；
- 行为映射表；
- B01 回归基线报告。

#### 验证

```powershell
uv run pytest -q
uv run ruff check .
uv run pyright
```

#### 退出门

- 当前测试全部通过；
- 文档明确哪些旧 B03 要求被替代、保留或下放到业务 Tool；
- 没有复制 coding-agent/TUI 代码。

#### 预期结果

团队对目标边界一致，后续不会同时实现旧 B03 状态机和新 Harness。

---

### 阶段 1：核心数据模型、事件流和取消原语

#### 工作

1. 使用 dataclass/Pydantic 定义 Message、ToolCall、ToolResult、Usage 和 ProviderEvent。
2. 实现可关闭的异步 EventStream。
3. 实现 `CancellationToken`，封装 `asyncio.Event` 与任务取消。
4. 定义稳定错误类型：
   - provider error；
   - invalid tool arguments；
   - unknown tool；
   - tool execution error；
   - cancelled；
   - context overflow；
   - extension error。
5. 实现统一时间、ID 和 JSON 序列化。

#### 交付物

- `agent/core/models.py`
- `agent/core/events.py`
- `agent/core/stream.py`
- `agent/core/errors.py`
- 对应单元测试

#### 验证

```powershell
uv run pytest tests/unit/agent/test_models.py tests/unit/agent/test_event_stream.py -q
uv run pyright
```

重点断言：

- 事件有序；
- 关闭后不能继续写；
- 消费者异常不会悄然吞掉；
- cancellation 可从 Provider 传播到 Tool；
- Message 可往返 JSON，密钥字段不属于模型。

#### 退出门

- 全部公共类型可序列化；
- EventStream 在正常、异常、取消三条路径都终止；
- 不依赖 FastAPI、SQLite 或具体 Provider。

#### 预期结果

形成与业务无关的 Python Agent Core 类型基础。

---

### 阶段 2：Provider 内核、Fake Provider 和首个 API Adapter

#### 工作

1. 实现 `AgentModelProvider`。
2. 实现脚本化 Fake Provider：
   - 文本回复；
   - 单 Tool Call；
   - 多 Tool Call；
   - 参数分片；
   - Provider 错误；
   - 中途取消；
   - usage。
3. 增加 `httpx` 异步 HTTP 依赖。
4. 建立 `ProviderRegistry`、`ModelCatalog`、`CredentialResolver` 和协议 Adapter 分层。
5. 实现 OpenAI-compatible Chat Completions SSE 解析，作为第一个 API Adapter。
6. Provider Profile 包含：
   - `provider_id`
   - `base_url`
   - `model`
   - `credential_ref`
   - `timeout_seconds`
   - `max_output_tokens`
   - 可选 headers
7. 密钥优先从现有 Keyring Profile 获取；开发期允许显式读取环境变量，但不复制到设置或日志。
8. 定义所有 Provider 共用的能力字段：
   - context window；
   - max output tokens；
   - text/image input；
   - tool calling；
   - reasoning/thinking；
   - cache；
   - transport；
   - cost。
9. 本阶段只完成 Fake 和 OpenAI Completions Adapter；其他协议与 Provider 在阶段 11 批量迁移。

#### 交付物

- Provider 基础协议；
- Fake Provider；
- OpenAI Completions API Adapter；
- Provider/Auth/Model Registry 骨架；
- HTTP/SSE fixtures。

#### 验证

```powershell
uv run pytest tests/unit/agent/test_fake_provider.py -q
uv run pytest tests/integration/agent/test_openai_compatible_provider.py -q
```

模拟服务覆盖：

- 合法流式文本；
- Tool Call arguments 多 chunk 拼接；
- 非 JSON chunk；
- HTTP 401/429/500；
- 响应中断；
- 超时和取消；
- usage 缺失。

#### 退出门

- Fake Provider 可完全驱动后续 Loop；
- OpenAI Completions Adapter 不依赖 DeepSeek 特有字段；
- 任何错误均转换为稳定错误，不泄漏响应 Header 或 Key。

#### 预期结果

模型传输层独立可测，后续 Agent Loop 不处理 HTTP 细节。

---

### 阶段 3：Tool 契约和低层 Agent Loop

#### 工作

1. 实现 Tool Registry 和 JSON Schema 参数校验。
2. 实现单轮 Provider 调用。
3. 收集完整 AssistantMessage。
4. 发现 Tool Call 时：
   - 校验 Tool 是否存在；
   - 合并参数分片；
   - 校验 schema；
   - 依次执行 Tool；
   - 产生 ToolResultMessage；
   - 开始下一轮 Provider 调用。
5. 无 Tool Call 时结束 Loop。
6. 实现 `before_tool_call`、`after_tool_call`。
7. 默认 `sequential`；保留 `parallel` 类型但暂不启用。
8. 与 Pi 一致：Loop 不设置通用的 turn、tool-call 或重复调用次数上限；
   可选 `deadline` 仅作为宿主取消边界，业务上的异步等待由语义化的
   工具结果和桌面完成事件处理，而不是计数器中断。
9. Tool 抛错必须回传模型，允许模型在下一轮修正。

#### 交付物

- `agent/core/tool.py`
- `agent/core/agent_loop.py`
- Loop 事件轨迹 fixtures

#### 验证

```powershell
uv run pytest tests/unit/agent/test_agent_loop.py -q
```

必测场景：

1. 纯文本一轮完成。
2. 单 Tool 后总结。
3. 连续五轮 Tool Calling。
4. 同一 assistant 返回两个 Tool，按源顺序执行和回写。
5. Tool 参数非法后模型修正。
6. Tool 抛错后模型选择替代路径。
7. 虚构 Tool。
8. 超过轮数、重复数和 deadline。
9. Provider 和 Tool 阶段分别取消。
10. Hook 阻止和修改 Tool Result。

#### 退出门

- Fake Provider 连续五轮通过；
- 每次 Tool 都有 start/update/end 和 Message 事件；
- 所有退出路径都有 `agent_end`；
- Loop 不直接访问 SQLite、文件系统或 B01 Registry。

#### 预期结果

得到可独立使用的 Python 版 Pi Agent Loop。

---

### 阶段 4：DeepSeek 早期真实协议验证

这是首个真实 API 检查点。只验证 Provider、流式协议和基础 Tool Calling，不等待 Skills/Harness 完成。

#### 配置

本机已检测到环境变量名 `DEEPSEEK_API_KEY`。测试不得输出其值。

支持：

```text
DEEPSEEK_API_KEY       必需
DEEPSEEK_BASE_URL      可选，默认使用项目确认后的 DeepSeek OpenAI-compatible base URL
DEEPSEEK_MODEL         可选，未设置时使用项目确认后的 chat 模型
RUN_LIVE_LLM_TESTS=1   显式启用真实测试
```

最终应用配置应转为现有 OS Keyring，例如 profile `agent/deepseek/default`；环境变量只用于开发和 CI
私有环境。

#### 工作

1. 实现 DeepSeek Profile 工厂，复用 OpenAI-compatible Provider。
2. 增加真实测试：
   - 流式输出包含指定短语；
   - 模型调用只读 `calculator.add` Tool；
   - Tool Result 后模型给出正确最终答案。
3. 关闭 reasoning 内容持久化；如果响应存在 reasoning 字段，只用于传输兼容，不写普通 Message。
4. 单测试最多一次自动重试；401/403 不重试。
5. 设置调用预算：
   - 温度尽可能为 0；
   - 输出 token 上限 256；
   - 每次测试最多 3 个 Provider turn；
   - 不调用图片、3D 或付费业务 Tool。

#### 验证

```powershell
$env:RUN_LIVE_LLM_TESTS='1'
uv run pytest tests/live/agent/test_deepseek_smoke.py -q -s
```

测试结束后：

```powershell
Remove-Item Env:RUN_LIVE_LLM_TESTS
```

#### 证据

保存脱敏报告：

```text
tests/evidence/agent-live/<timestamp>/deepseek-smoke.json
```

只包含：

- provider/model；
- 测试用例；
- request correlation ID；
- turn/tool 数；
- duration；
- usage（Provider 返回时）；
- success/error code；
- payload schema hash。

不包含 Prompt 全文、Key、Authorization、完整响应或隐藏推理。

#### 退出门

- DeepSeek 文本流和 Tool Calling 均真实通过；
- 事件序列与 Fake Provider 一致；
- 证据和日志扫描不到 API Key；
- API 不可用时测试明确 skip/fail，不伪装通过。

#### 预期结果

在框架尚小的时候确认 DeepSeek 与 Python Agent Loop 协议兼容。

---

### 阶段 5：有状态 Agent、队列和运行快照

#### 工作

1. 实现 `Agent` 门面，维护：
   - system prompt；
   - model/profile；
   - thinking level；
   - tools；
   - messages；
   - streaming message；
   - pending tool calls；
   - error。
2. 实现 `prompt()`、`continue_run()`、`abort()`、`wait_for_idle()`。
3. 实现 steering、follow-up、next-turn 三类队列。
4. 同时只允许一个结构性运行。
5. 当前 Provider request 使用不可变快照；运行中配置变更只影响下一 turn。
6. 事件 listener 按注册顺序 await。

#### 验证

```powershell
uv run pytest tests/unit/agent/test_agent.py tests/unit/agent/test_agent_queues.py -q
```

重点覆盖：

- 活动运行期间再次 `prompt` 返回 busy；
- steer 在安全 turn 边界进入；
- follow-up 仅在本来要结束时进入；
- abort 清 steer/follow-up，但保留 next-turn；
- listener 可观察已更新状态；
- listener 错误不会把 Agent 永久卡在 busy。

#### 退出门

- Agent 所有运行路径最终回到 idle；
- 不出现两个并发 Loop；
- 队列顺序稳定且无静默丢失。

#### 预期结果

得到可由 API、CLI 或测试直接驱动的有状态 Agent。

---

### 阶段 6：线性 SQLite Session

#### 工作

1. 新增 `0003_agent_linear.sql`。
2. 实现 Repository 和 Session service。
3. 完整 Message 在 `message_end` 持久化，delta 不逐条写数据库。
4. Tool Call/Result 使用稳定 Tool Call ID 关联。
5. 保存 operation start/end/interrupted。
6. 保存 active tools、active skills、model/profile 和 compaction。
7. 重启时从最新线性消息恢复。
8. 检测上次未完成 operation，标为 interrupted，不自动重跑 Tool。

#### 验证

```powershell
uv run pytest tests/integration/agent/test_linear_session.py -q
uv run pytest tests/integration/agent/test_session_recovery.py -q
uv run pytest tests/integration/test_migrations.py -q
```

故障注入：

- assistant message 持久化前退出；
- Tool start 后、结果前退出；
- Tool Result 后、下一 turn 前退出；
- SQLite busy；
- listener 持久化失败；
- final message 后、operation end 前退出。

#### 退出门

- 重开后消息顺序、Tool 关联和 active config 一致；
- 未完成 Tool 不自动重放；
- 数据库迁移可从空库和 B01 库重复执行；
- 没有 branch/leaf/tree 表。

#### 预期结果

Agent 具备线性、可解释的持久会话和保守恢复能力。

---

### 阶段 7：AgentHarness 与自动上下文压缩

#### 工作

1. 实现 Harness phase：
   - `idle`
   - `turn`
   - `compaction`
2. Harness 组合 Agent、Session、Provider、Tools、Skills 和 Extensions。
3. 每个 turn 建立运行快照：
   - 线性 Session context；
   - system prompt；
   - active skills；
   - active tools；
   - model/profile；
   - tool context；
   - stream options。
4. 实现 `transform_context()`：
   - 保留最近消息；
   - 保留未闭合 Tool Call/Result；
   - 注入滚动摘要；
   - 注入当前项目/资产轻量上下文。
5. 迁移 Pi 的 `CompactionSettings`：
   - `enabled=true`；
   - `reserve_tokens=16384`；
   - `keep_recent_tokens=20000`；
   - 根据实际模型 context window 进行合法化，不能让保留/预留预算超过窗口。
6. 实现上下文 token 计算：
   - 优先使用最近一次有效 Assistant usage；
   - 对 usage 之后的消息做增量估算；
   - usage 缺失时估算全部 Message；
   - error/aborted/全零 usage 不覆盖上一条有效 usage；
   - 图片按配置的近似 token 成本计入。
7. 实现两种自动触发：
   - `threshold`：`context_tokens > context_window - reserve_tokens`；
   - `overflow`：Provider 明确返回 context overflow，在安全条件下压缩并重试原 turn 一次。
8. 自动压缩只能发生在 Provider turn 完成后的 save-point，不能在 Tool 执行到一半时改写上下文。
9. 实现 cut point：
   - 从后向前累计并保留约 `keep_recent_tokens`；
   - 优先在 UserMessage/完整 turn 边界切分；
   - 不拆散 Tool Call 与对应 Tool Result；
   - 单个超大 turn 无法整体保留时，生成 turn-prefix summary 并保留后缀。
10. 实现滚动摘要：
    - 首次总结旧历史；
    - 再次压缩时把 previous summary 作为输入更新；
    - 固定包含 Goal、Constraints、Progress、Key Decisions、Next Steps、Critical Context；
    - 从 read/write/edit/bash 轨迹提取已读/已修改文件清单；
    - 不总结为对用户问题的直接回答。
11. Session 保存 CompactionRecord：
    - `reason=manual|threshold|overflow`；
    - `summary`；
    - `first_kept_sequence`；
    - `retained_tail`；
    - `tokens_before/tokens_after`；
    - `usage`；
    - `model/provider`；
    - `previous_compaction_id`；
    - `created_at`。
12. 原始 Message 不从 SQLite 删除；context projection 使用最新 summary + retained tail + 后续消息。
13. 实现显式 `compact()`、自动开关和可配置独立 summarization model/profile。
14. 实现：
    - `compaction_start`；
    - `compaction_end`；
    - `context_compacted`；
    - `retry_scheduled/attempt_start/finished`。
15. `session_before_compact` Hook 可以取消或提供自定义 CompactionResult。
16. threshold 压缩失败时保留原上下文并产生诊断；overflow 压缩失败时返回明确错误，禁止无限重试。
17. 重启时完整 CompactionRecord 直接生效；只有 started、没有 committed 的压缩记录标 interrupted，
    原始消息保持可用。

#### 验证

```powershell
uv run pytest tests/unit/agent/test_context.py -q
uv run pytest tests/integration/agent/test_harness.py -q
uv run pytest tests/integration/agent/test_compaction.py -q
uv run pytest tests/integration/agent/test_auto_compaction.py -q
```

#### 退出门

- threshold 和 overflow 两种自动触发均通过；
- 20+ turn 后仍可恢复并调用正确 Tool；
- 压缩不拆散 Tool Call/Result；
- 超大单 turn 能生成 prefix summary 并保留后缀；
- previous summary 可迭代更新；
- 最近有效 usage + trailing estimate 计算稳定；
- 摘要失败不删除原消息；
- overflow 最多自动重试原 turn 一次；
- Harness busy 时拒绝第二个结构性操作；
- save-point 后配置更新影响下一 turn。

#### 预期结果

形成具备 Pi 同等级自动压缩语义的 Python AgentHarness，而不是只在内存运行的 Loop。

---

### 阶段 8：扩展机制

#### 工作

1. 定义 `AgentExtension`：
   - `extension_id`
   - `version`
   - `priority`
   - `register(context)`
   - `close()`
2. 扩展可注册：
   - Tool；
   - Provider；
   - Skill root；
   - Prompt template；
   - context transform；
   - lifecycle hook；
   - custom message projector。
3. Hook 初始集合：
   - `before_agent_start`
   - `before_provider_request`
   - `after_provider_response`
   - `before_tool_call`
   - `after_tool_call`
   - `turn_end`
   - `agent_end`
   - `context_transform`
   - `session_message_append`
4. 顺序固定为 `priority, extension_id, registration_order`。
5. 扩展加载来源：
   - 应用内置 Python 模块；
   - 用户扩展目录；
   - 项目扩展目录。
6. 用户/项目 Python 扩展视为受信任本地代码；首次启用必须显式配置，不假装它是安全沙箱。
7. 首版不实现热重载；变更后重启 Harness。

#### 验证

```powershell
uv run pytest tests/unit/agent/test_extension_registry.py -q
uv run pytest tests/integration/agent/test_extension_hooks.py -q
```

必测：

- 重复 extension/tool/provider ID；
- Hook 顺序；
- Hook 修改 Provider request；
- Hook 阻止 Tool；
- 一个扩展失败时诊断和禁用；
- teardown；
- 扩展无法访问未显式提供的 SecretStore 对象。

#### 退出门

- 扩展注册确定且可审计；
- 扩展异常不会破坏 Harness phase；
- 业务组件可以通过扩展安装，而不修改 Agent Core。

#### 预期结果

框架具备与 Pi 类似的宿主扩展能力，同时使用 Python 原生接口。

---

### 阶段 9：ExecutionEnv 与 read/write/edit/bash

#### 工作

1. 定义 `ExecutionEnv`，所有文件和命令操作只通过它。
2. 实现 `LocalExecutionEnv`：
   - workspace roots；
   - cwd；
   - 文本读取；
   - 原子写入；
   - 追加；
   - stat/list；
   - 进程执行；
   - timeout/cancel；
   - stdout/stderr 流。
3. 实现 `read`：
   - 文本行号与分页；
   - 编码错误；
   - 图片元数据/受控预览；
   - 大文件截断。
4. 实现 `write`：
   - 原子 replace；
   - 父目录创建策略；
   - 写入大小限制。
5. 实现 `edit`：
   - 精确旧文本匹配；
   - 零匹配/多匹配报错；
   - 原子替换。
6. 实现 `bash`：
   - 工具名保持 `bash` 以兼容 Agent Prompt；
   - Windows 后端使用 PowerShell；
   - cwd 限制；
   - timeout；
   - 输出流和截断；
   - 不自动继承未允许的敏感环境变量。
7. 实现 per-path mutation queue，避免并发写同一文件。
8. 大输出保存到受管临时 artifact，Tool Result 只返回摘要和 artifact ref。

#### 验证

```powershell
uv run pytest tests/unit/agent/test_execution_env.py -q
uv run pytest tests/integration/agent/test_builtin_tools.py -q
uv run pytest tests/security/test_agent_workspace.py -q
```

必测：

- read 分页；
- write 原子性；
- edit 唯一匹配；
- bash stdout/stderr/exit code；
- timeout/cancel；
- 超大输出；
- 并发写队列；
- `..`、符号链接/junction 和跨 root；
- 环境变量过滤；
- Key 不进入命令事件和结果。

#### 退出门

- 四个工具只依赖 `ExecutionEnv`；
- 所有测试使用临时 workspace；
- 没有工具直接访问全局文件系统或无边界 `subprocess`；
- Windows PowerShell 行为明确。

#### 预期结果

Agent 获得通用本地操作能力，可供 Skills 和扩展复用。

---

### 阶段 10：Skills 与 Prompt Templates

#### 工作

1. 定义 Skill 元数据：
   - name；
   - description；
   - source；
   - root；
   - version/hash；
   - required tools；
   - instructions；
   - resources。
2. 搜索顺序：
   - 应用内置；
   - 用户；
   - 项目。
3. 同名优先级采用项目 > 用户 > 应用，并产生覆盖诊断。
4. 首次发现只读取元数据；激活时完整读取 `SKILL.md` 和直接引用资源。
5. 支持：
   - `harness.skill(name, user_input)`；
   - 将激活 Skill 注入 system/context；
   - Skill 所需工具检查；
   - Skill 加载诊断。
6. 实现 Prompt Template 变量替换，变量缺失时明确失败。
7. Skill 内容属于受信任 Prompt 资源，但仍不能直接获得 SecretStore。

#### 验证

```powershell
uv run pytest tests/unit/agent/test_skill_loader.py -q
uv run pytest tests/integration/agent/test_skills.py -q
uv run pytest tests/integration/agent/test_prompt_templates.py -q
```

场景：

- 三层发现和覆盖；
- 无效 front matter；
- 缺失引用；
- 循环引用；
- required tool 缺失；
- Skill 修改后重启生效；
- Skill 驱动 Fake Provider 使用 read/write；
- 恶意 Skill 尝试读取 workspace 外路径时被 ExecutionEnv 拒绝。

#### 退出门

- Skill 可独立加载、激活和测试；
- 不需要修改 Agent Core 就能添加工作流知识；
- Session 保存 active skill 名称/hash，不复制秘密资源。

#### 预期结果

Agent 可以通过本地 `SKILL.md` 获得专业工作流，而不是把全部 Prompt 写死在代码里。

---

### 阶段 11：迁移 Pi 全部 Provider、认证和模型目录

本阶段的“全部”以阶段 0 冻结的 Pi commit 为准：38 个聊天 Provider、1 个图片 Provider。
之后 Pi 新增 Provider 不自动进入本次退出门，应通过版本化同步流程单独升级。

#### 11.1 API Adapter

完成以下聊天协议：

1. `openai-completions`
2. `openai-responses`
3. `azure-openai-responses`
4. `openai-codex-responses`
5. `anthropic-messages`
6. `bedrock-converse-stream`
7. `google-generative-ai`
8. `google-vertex`
9. `mistral-conversations`
10. `pi-messages`

完成图片协议：

11. `openrouter-images`

每个 Adapter 必须统一支持其协议具备的：

- text/image content；
- Tool schemas、Tool Calls 和 Tool Results；
- 流式文本；
- reasoning/thinking block；
- usage/cost；
- cache read/write；
- finish/stop reason；
- cancel/timeout；
- SSE/WebSocket；
- Provider error 和 context overflow 识别；
- Unicode、空 content、partial JSON 和未知事件容错。

#### 11.2 Provider 清单

聊天 Provider：

```text
amazon-bedrock
ant-ling
anthropic
azure-openai-responses
cerebras
cloudflare-ai-gateway
cloudflare-workers-ai
deepseek
fireworks
github-copilot
google
google-vertex
groq
huggingface
kimi-coding
minimax
minimax-cn
mistral
moonshotai
moonshotai-cn
nvidia
openai
openai-codex
opencode
opencode-go
openrouter
qwen-token-plan
qwen-token-plan-cn
radius
together
vercel-ai-gateway
xai
xiaomi
xiaomi-token-plan-ams
xiaomi-token-plan-cn
xiaomi-token-plan-sgp
zai
zai-coding-cn
```

图片 Provider：

```text
openrouter-images
```

每个 Provider 只声明：

- Provider ID、名称、默认 base URL；
- 使用的 API Adapter；
- 认证解析器；
- 默认/动态 headers；
- 模型过滤和发现策略；
- Provider 特有兼容选项。

不得为同协议 Provider 复制整个 HTTP/stream 实现。

#### 11.3 认证

迁移：

- 普通 API Key + 环境变量 + OS Keyring；
- Anthropic OAuth；
- GitHub Copilot OAuth；
- Kimi Coding OAuth；
- OpenAI Codex OAuth；
- OpenRouter OAuth；
- Radius OAuth/动态 Gateway；
- xAI OAuth；
- Amazon Bedrock bearer token、AWS profile 和 credential chain；
- Google Vertex API Key、ADC 和 service account；
- Cloudflare API Key + account ID + gateway ID；
- Provider 自定义 Header。

OAuth 必须包含：

- device/browser flow；
- access/refresh token；
- 到期刷新；
- refresh 并发锁；
- token 原子更新；
- revoke/logout；
- 用户取消；
- Keyring 存储；
- 日志脱敏。

#### 11.4 模型目录

1. 将 Pi 冻结 commit 的模型目录导出为版本化 JSON，不手抄模型表。
2. 保存：
   - provider/model ID；
   - API；
   - context window；
   - max output tokens；
   - text/image input；
   - reasoning；
   - thinking level 映射；
   - cost；
   - cache；
   - compatibility options。
3. 记录：
   - source Pi commit；
   - generated timestamp；
   - schema version；
   - content hash。
4. 实现 `scripts/sync_pi_provider_catalog.py`：
   - 接受显式 Pi source path 或导出的 catalog 文件；
   - 生成 Python Provider descriptors/catalog；
   - 输出 diff；
   - 不在应用运行时依赖本地 Pi 检出目录。
5. 动态 Provider（例如 Radius）在运行时发现模型，但仍使用统一 Model DTO。
6. Model Catalog 更新必须通过 schema/duplicate/capability 验证。

#### 11.5 实施顺序

1. API Adapter 合同。
2. 统一 auth/credential store。
3. Model DTO 和 catalog generator。
4. 先迁移每种 API 的一个代表 Provider。
5. 批量迁移共享 API 的 Provider descriptor。
6. 迁移 OAuth/云凭据 Provider。
7. 迁移动态模型 Provider。
8. 迁移 OpenRouter Images。
9. 全量 catalog/descriptor 快照验收。

#### 11.6 Python 依赖与打包

1. 通用 HTTP/SSE 优先复用 `httpx` 和自有事件解析，不为每个 OpenAI-compatible Provider
   引入独立 SDK。
2. WebSocket 使用单一异步实现。
3. Bedrock 使用 AWS 官方 credential/signing 能力，支持默认 credential chain。
4. Google Vertex 使用官方 Google Auth/ADC；Gemini REST 与 Vertex 认证分离。
5. OAuth 使用统一 PKCE/device flow、回环 callback 和 refresh 实现，Provider 只提供端点和 scope。
6. 只有协议确实依赖官方 SDK 的部分才引入 SDK，并记录包体影响。
7. Provider 模块延迟 import；未使用 Provider 不应显著增加启动时间。
8. PyInstaller sidecar 必须执行全 Provider import smoke，避免动态模块未打包。
9. 依赖版本固定并进行许可证、漏洞和安装脚本检查。

#### 验证

```powershell
uv run pytest tests/unit/agent/providers -q
uv run pytest tests/contract/agent/providers -q
uv run pytest tests/integration/agent/providers -q
uv run python scripts/sync_pi_provider_catalog.py --check
```

合同测试：

- 11 种 API Adapter 每种至少包含文本、Tool、错误、取消和 usage fixture；
- reasoning、image、cache、WebSocket 等按 Adapter 能力增加 fixture；
- 所有 39 个 Provider descriptor 均可注册；
- Provider ID 唯一；
- 每个 Provider 至少关联一个 Adapter 或动态模型来源；
- 所有静态 Model 可解析且 context/max token 合法；
- thinking level 不支持时明确降级或拒绝；
- Provider headers 和 Tool Call ID 转换符合冻结 Pi 行为。

认证测试：

- Keyring/env 优先级；
- OAuth login/refresh/logout；
- refresh 并发；
- AWS profile/chain；
- Google ADC/service account；
- Cloudflare 多字段；
- Radius 动态配置；
- secret scan。

真实测试：

- DeepSeek 是必跑真实 Provider。
- 其他 Provider 只有检测到对应 credential 且设置 `RUN_LIVE_PROVIDER_TESTS=1` 时运行 smoke。
- 缺少其他 Provider 凭据不阻塞本阶段，但其 mock contract、catalog 和 auth resolver 必须全部通过。
- 每个真实 smoke 限制为一次短文本和一次只读 Tool Call。

#### 退出门

- 38 个聊天 Provider和 1 个图片 Provider全部出现在 Registry；
- 10 个聊天 API Adapter和 1 个图片 Adapter合同测试全部通过；
- Provider/Auth/Catalog 快照与冻结 Pi commit 一致；
- DeepSeek live suite 通过；
- 其他 Provider 无凭据时明确标为 `not_configured`，不能伪装已验证；
- OAuth/Keyring/日志中无 secret 泄漏；
- Core/Harness 不包含 Provider 名称分支。

#### 预期结果

Python 框架具备与冻结 Pi 版本相同范围的 Provider 和模型能力，新增同协议 Provider只需增加
descriptor/catalog，不需要修改 Agent Loop。

---

### 阶段 12：接入现有 AIPic ToolRegistry

#### 工作

1. 实现 `AIPicRegistryToolAdapter`。
2. 将 B01 可见 Manifest 转换为 `AgentTool`：
   - canonical name；
   - description；
   - input schema；
   - execution mode；
   - provider profile。
3. Agent 调用时继续经过 B01：
   - schema；
   - asset/project ownership；
   - 幂等；
   - audit；
   - executor。
4. 将 B01 `ToolResultV1` 转换为 `AgentToolResult`。
5. queued Job 转成可识别的 Tool Result；Harness 停止本次自动循环并记录
   `waiting_external`，Job 完成后以新的 resume event 继续线性 Session。
6. B01 工具与通用文件工具使用不同 ToolContext，不把项目绝对路径暴露给模型。
7. 把 AIPic 工具集做成内置 Extension。

#### 验证

```powershell
uv run pytest tests/integration/agent/test_aipic_tool_adapter.py -q
uv run pytest tests/integration/agent/test_agent_job_resume.py -q
uv run pytest tests/integration/test_tool_idempotency.py -q
```

#### 退出门

- Agent 可发现并调用 B01 Tool；
- 所有 B01 原有合同测试继续通过；
- queued Job 不占用 Provider HTTP stream；
- Job 恢复不重复提交；
- 模型不能借 Adapter 传入任意路径。

#### 预期结果

通用 Python Agent 框架正式获得 AIPic 图片/资产/3D 业务能力。

---

### 阶段 13：FastAPI、SSE 和应用集成

#### 工作

1. 新增最小 API：
   - 创建/获取 Conversation；
   - 发送 Message；
   - 获取 Message；
   - 获取 Agent 状态；
   - cancel；
   - steer/follow-up；
   - Skills 列表/激活；
   - Extensions 状态；
   - SSE events。
2. SSE 将 AgentEvent 投影为稳定 API DTO。
3. 增量文本可以实时推送，完整消息由 Session 回查。
4. Sidecar 重启时恢复线性 Session，未完成 operation 标记 interrupted。
5. Provider/Skill/Extension/Tool 健康状态加入现有 health。
6. 不在这一阶段实现完整 React Agent 面板，只完成 API 合同和测试客户端。

#### 验证

```powershell
uv run pytest tests/contract/test_agent_api.py -q
uv run pytest tests/integration/agent/test_agent_sse.py -q
uv run pytest tests/e2e/test_agent_sidecar_lifecycle.py -q
```

#### 退出门

- HTTP 发送消息可获得流式回复；
- SSE 重连后可从持久消息恢复，不要求重放每个 token；
- cancel 能终止 Provider/Tool；
- sidecar 重启不丢完整消息；
- API 不返回 Key、Authorization 或 workspace 外绝对路径。

#### 预期结果

框架可以被后续 Tauri/React 工作台使用。

---

### 阶段 14：DeepSeek 最终综合真实验证

#### 工作

使用真实 DeepSeek 运行五组受控场景。每组使用独立临时 Conversation 和临时 Workspace。

#### 场景 A：基础多轮工具

Prompt 要求模型：

1. 调用 `calculator.add`；
2. 将结果传给 `calculator.multiply`；
3. 返回最终值。

断言：

- 至少两个 Tool Call；
- 参数和最终结果正确；
- Tool Result 顺序正确；
- 最终 AssistantMessage 无 Tool Call。

#### 场景 B：内置文件工具

Prompt 明确要求：

1. 使用 `write` 创建临时 workspace 中的文件；
2. 使用 `edit` 修改唯一文本；
3. 使用 `read` 读取；
4. 使用 `bash` 计算文件 hash 或统计行数；
5. 总结结果。

断言：

- 四种工具都被调用；
- 文件最终内容正确；
- 所有路径位于临时 workspace；
- bash 超时和输出限制配置生效；
- Session 中没有 API Key。

#### 场景 C：Skill

加载测试 Skill，要求按固定格式读取两个输入文件并生成汇总文件。

断言：

- Skill 被发现和激活；
- required tools 正确；
- 模型遵守关键输出结构；
- Session 保存 Skill name/hash；
- 重启后可继续对该结果提问。

#### 场景 D：Extension

测试 Extension：

- 注册一个 `project.note` Tool；
- `before_provider_request` 注入短系统约束；
- `after_tool_call` 在 details 中加入审计标记。

断言：

- Extension Hook 顺序正确；
- Tool 被真实模型选用；
- 审计标记存在；
- 禁用 Extension 后 Tool 不再可见；
- Extension 错误映射为诊断而非 Harness 卡死。

#### 场景 E：自动上下文压缩

使用测试专用低阈值，不伪造模型真实 context window：

1. 连续写入多轮包含约束、文件名、已完成结果和待办事项的对话；
2. 让 DeepSeek 正常完成一个 Tool turn；
3. 在 save-point 触发 `threshold` 自动压缩；
4. 使用 DeepSeek 生成结构化滚动摘要；
5. 继续询问压缩前的重要约束，并完成下一次 Tool Call；
6. 模拟 Provider context overflow，验证压缩后只重试原 turn 一次。

断言：

- 产生 `compaction_start/end` 和 `context_compacted`；
- summary 包含目标、约束、进展、决策、下一步和关键文件；
- retained tail 完整；
- Tool Call/Result 未拆散；
- 原始消息仍在 SQLite；
- context projection 使用 summary；
- 压缩后模型能回答压缩前关键事实；
- overflow 不会无限重试。

#### 命令

```powershell
$env:RUN_LIVE_LLM_TESTS='1'
uv run pytest tests/live/agent -q -s
Remove-Item Env:RUN_LIVE_LLM_TESTS
```

#### 稳定性策略

- Prompt 明确要求使用指定 Tool，但断言以事件和结果为准，不断言完整自然语言。
- 每个场景最多重试一次，重试必须记录。
- 真实 API 不参与默认 CI。
- 每日/发布候选运行一次，不在每次本地单测调用。
- 任何真实测试失败都保留脱敏 correlation ID 和事件摘要。
- 费用和 token 使用写入报告，但不记录 Prompt/Response 全文。

#### 退出门

- 五组真实场景连续两次全部通过；
- Fake 与 DeepSeek 的事件合同一致；
- 递归密钥扫描无命中；
- 失败时 Harness 可回到 idle 并继续新 Prompt；
- 不产生图片/3D 等额外付费业务调用。

#### 预期结果

证明框架不仅通过脚本化 Fake 测试，也能由真实 LLM 稳定选择工具、遵循 Skill 并经过 Extension。

---

### 阶段 15：回归、性能、文档替换和发布门

#### 工作

1. 执行完整 B01 + Agent 测试。
2. 增加性能基线：
   - Fake 首 token；
   - EventStream 开销；
   - SQLite message append；
   - 1000 Message context 构建；
   - Skill 扫描；
   - Extension 加载；
   - 39 Provider Registry/Catalog 加载；
   - 自动压缩前后 context 构建。
3. 执行秘密、路径和命令输出扫描。
4. 更新：
   - 功能技术设计；
   - execution batches；
   - API/OpenAPI；
   - 开发文档；
   - DeepSeek 配置说明；
   - 全量 Provider 配置、认证和能力矩阵；
   - Model Catalog 同步说明；
   - 自动上下文压缩配置与恢复说明；
   - 扩展和 Skill 开发指南。
5. 删除或明确标记已经被本计划取代的 B03 条款，避免双重规范。

#### 验证

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest -q
uv run pytest tests/security -q
uv run pytest tests/contract/agent/providers -q
uv run python scripts/sync_pi_provider_catalog.py --check
```

真实验证单独执行：

```powershell
$env:RUN_LIVE_LLM_TESTS='1'
uv run pytest tests/live/agent -q -s
Remove-Item Env:RUN_LIVE_LLM_TESTS
```

#### 最终退出门

- 静态检查和默认测试全绿；
- DeepSeek live suite 连续两次通过；
- 38 个聊天 Provider、1 个图片 Provider和全部 API Adapter合同测试通过；
- Provider Catalog 与冻结 Pi commit/hash 一致；
- threshold/overflow 自动压缩和重启恢复通过；
- B01 行为无回归；
- 强制退出/重启后线性 Session 可恢复；
- read/write/edit/bash 全部受 ExecutionEnv 管理；
- Skill 与 Extension 有开发文档和最小示例；
- 数据库、日志、报告和导出中不存在真实或哨兵 API Key；
- 新设计文档成为唯一 Agent Runtime 规范。

## 7. 验收矩阵

| 能力 | Fake 验证 | 集成验证 | DeepSeek 真实验证 | 完成标准 |
|---|---|---|---|---|
| 流式文本 | scripted deltas | mock SSE | smoke 文本流 | 事件完整、有 usage 或明确缺失 |
| Tool Calling | 1/2/5 轮 | schema + error | calculator 链 | 参数、顺序、结果正确 |
| 取消 | provider/tool cancel | FastAPI cancel | 可选短 smoke | Agent 回 idle |
| steering/follow-up | queue table tests | Session 顺序 | 非强制 | 不丢失、不抢当前 turn |
| 线性 Session | JSON roundtrip | SQLite/restart | Skill 场景重启 | 消息与 Tool 关联一致 |
| 自动压缩 | 固定摘要/usage | threshold/overflow/重启 | DeepSeek 场景 E | 不拆 Tool 对、可继续任务 |
| API Adapters | 协议事件 fixtures | mock HTTP/SSE/WS | 有凭据时 smoke | 10 chat + 1 image 全通过 |
| Provider Registry | descriptor/catalog snapshot | auth resolver | DeepSeek 必跑、其余可选 | 38 chat + 1 image 可用 |
| Extensions | hook fixtures | register/disable | Extension 场景 | 顺序稳定、失败隔离 |
| read/write/edit/bash | Fake Env | 临时 workspace | 文件工具场景 | 文件结果正确且不越界 |
| Skills | loader fixtures | required tools | Skill 场景 | 激活、遵循、可恢复 |
| AIPic Adapter | Fake Registry | B01 Registry | 后续业务 smoke | B01 合同无回归 |
| 密钥安全 | sentinel | 日志/DB 扫描 | live 证据扫描 | 零泄漏 |

## 8. DeepSeek 接入设计

### 8.1 开发期

读取 `DEEPSEEK_API_KEY`，但只在创建请求 Header 时使用。禁止：

- 写入 Pydantic model dump；
- 写入 Provider Profile JSON；
- 拼入异常消息；
- 记录请求 headers；
- 在 pytest report 中输出环境；
- 使用 `-vv` 打印完整 payload。

### 8.2 应用期

1. 设置 API 只接收 secret 并立即写入 OS Keyring。
2. SQLite 保存 `credential_ref=agent/deepseek/default` 和 `configured=true`。
3. Provider 每次请求通过 SecretStore 获取 Key。
4. UI 只获取 `configured` 和 mask。
5. 诊断只执行轻量连通性/模型调用，结果脱敏。

### 8.3 Provider 兼容策略

- DeepSeek Adapter 只负责默认 Profile 和已确认的少量兼容差异。
- SSE、Tool Call、usage 和错误解析放在 `openai-completions` API Adapter。
- Base URL 和 model 都可配置，测试不把某个未来可能变化的模型 ID写入核心代码。
- 不依赖隐藏 reasoning 内容完成 Tool 执行。
- 不向下一 Provider 重放未经确认的专有 reasoning 字段。

### 8.4 全量 Provider 策略

- Agent Core 只依赖统一 ProviderEvent，不包含 Provider 名称判断。
- 先实现 10 个聊天 API Adapter 和 1 个图片 Adapter，再用 descriptor 组合 39 个 Provider。
- 每个 Provider 的认证、Header、模型过滤和兼容差异保持在 Provider 层。
- Model Catalog 是版本化数据，不把大量模型常量散落在 Python 模块。
- OAuth/云凭据通过统一 CredentialStore 接口，敏感值进入 OS Keyring。
- 默认测试验证全部 Provider；真实 smoke 只对本机已配置的 Provider 执行。
- DeepSeek 是本项目发布前必须真实通过的 Provider，其他 Provider 必须达到合同可用和
  `not_configured` 状态准确。

## 9. 测试和证据策略

### 9.1 默认测试

- 永不访问真实模型；
- 全部使用 Fake Provider 或本地 mock HTTP server；
- 不依赖本机 Keyring 中已有密钥；
- 可在离线环境运行；
- 失败必须确定性复现。

### 9.2 Live 测试

- `RUN_LIVE_LLM_TESTS=1` 双重门；
- 缺少 `DEEPSEEK_API_KEY` 时明确 skip；
- 对认证失败返回 fail；
- 限制 turn/token/retry；
- 不调用外部业务 Tool；
- 输出脱敏证据。
- `RUN_LIVE_PROVIDER_TESTS=1` 用于其他已配置 Provider 的可选 smoke；
- 可选 Provider smoke 失败不应被标记成“未配置”，必须保留真实失败状态和脱敏证据。

### 9.3 证据目录

```text
tests/evidence/agent/
  fake/
  recovery/
  security/
  live/<timestamp>/
```

每次阶段退出至少保留：

- 命令；
- exit code；
- 测试版本；
- schema/hash；
- 脱敏摘要。

## 10. 风险与处理

| 风险 | 影响 | 处理 |
|---|---|---|
| 机械翻译 Pi 造成 Python 风格差、维护困难 | 高 | 先冻结行为合同，再写 Python 原生实现 |
| 旧 B03 与新 Harness 双重实现 | 高 | 阶段 0 ADR 明确取代关系 |
| DeepSeek Tool Calling 与 Fake 行为不同 | 高 | 阶段 4 提前真实验证 |
| 39 个 Provider 范围持续变化 | 高 | 冻结 Pi commit、catalog hash、显式同步升级 |
| OAuth/云认证差异大 | 高 | 统一 CredentialResolver，分协议合同和可选 live smoke |
| Provider SDK 增大安装包 | 中 | 延迟 import、按协议复用、打包 smoke 测量 |
| 自动压缩丢失关键上下文 | 高 | 原消息不删除、结构摘要、retained tail、DeepSeek 场景 E |
| overflow 自动重试形成循环 | 高 | 每个原 turn 最多一次 overflow 压缩重试 |
| Tool 循环失控或费用增加 | 高 | max turns/tool calls/deadline/token limits |
| bash/read/write 扩大本地权限 | 高 | ExecutionEnv、workspace roots、环境过滤、超时 |
| Python 扩展可执行任意代码 | 高 | 明确“受信任代码”模型、显式启用、不宣传为沙箱 |
| SQLite 写入与事件 listener 死锁 | 中 | 持久化顺序固定、单写入锁、故障注入 |
| Session 恢复重复执行 Tool | 高 | Tool start/result 持久化，未完成 Tool 默认 interrupted |
| Skill Prompt 注入 | 中 | 来源与 hash 可见、工具权限不因 Skill 自动扩大 |
| DeepSeek 模型版本变化 | 中 | model/base URL 配置化，live tests 记录模型 |
| 过早实现所有 Pi 能力 | 中 | 严格按阶段退出门推进，分支树明确排除 |
| 近似翻译形成衍生作品而无声明 | 中 | 保留 MIT License 和来源清单 |

## 11. 预计工作量

以下是单人、熟悉当前代码后的粗略工程量，不含完整 React UI：

| 阶段 | 预计工作日 |
|---|---:|
| 0～2：基线、核心模型、Provider | 4～6 |
| 3～4：Agent Loop + DeepSeek 早期验证 | 4～6 |
| 5～7：Agent、Session、Harness、自动压缩 | 10～15 |
| 8：Extensions | 3～5 |
| 9：ExecutionEnv + 四工具 | 5～7 |
| 10：Skills/Templates | 4～6 |
| 11：10+1 API Adapters、39 Provider、Auth/Catalog | 22～35 |
| 12：AIPic Tool Adapter/Job resume | 4～7 |
| 13：FastAPI/SSE | 3～5 |
| 14～15：真实综合验证、回归、文档 | 5～8 |
| **合计** | **64～100 工作日** |

可以在阶段 4 得到第一个真实 DeepSeek Tool Calling MVP；不需要等全部 64～100 日才看到结果。

## 12. 里程碑

### M1：Agent Core MVP

包含阶段 0～4。

可观察结果：

- Python Agent 连接 DeepSeek；
- 流式回复；
- 真实调用 calculator Tool；
- Tool Result 后得到最终回答；
- Fake 五轮 Tool Calling 通过。

### M2：Durable Harness

包含阶段 5～7。

可观察结果：

- 多轮 Conversation；
- steering/follow-up；
- SQLite 重启恢复；
- threshold/overflow 自动上下文压缩；
- 中断 Tool 不自动重放。

### M3：Extensible Local Agent

包含阶段 8～10。

可观察结果：

- Extensions 可注册 Hook/Tool；
- Skills 可发现和激活；
- DeepSeek 可使用 read/write/edit/bash 在临时 workspace 完成任务。

### M4：Full Provider Agent

包含阶段 11。

可观察结果：

- 38 个聊天 Provider、1 个图片 Provider注册完成；
- 10 个聊天协议、1 个图片协议合同测试通过；
- API Key/OAuth/云凭据和模型目录可用；
- DeepSeek 真实验证通过，其他未配置 Provider 显示准确能力状态。

### M5：AIPic Agent

包含阶段 12～13。

可观察结果：

- Agent 可调用现有 AIPic ToolRegistry；
- queued Job 可退出 Provider turn 并在完成后恢复；
- FastAPI/SSE 可供桌面前端使用。

### M6：Release Candidate

包含阶段 14～15。

可观察结果：

- DeepSeek 综合 live suite 连续通过；
- 默认离线测试全绿；
- 密钥、路径、恢复和回归验证通过；
- 新 Agent 设计成为唯一有效规范。

## 13. 实施纪律

1. 每阶段只提交本阶段代码、测试和必要文档。
2. 核心层不得导入 AIPic 资产、FastAPI 或 SQLite 实现。
3. Provider 不执行 Tool；Tool 不调用 Provider；Session 不决定下一步。
4. Fake 测试先于真实 API 测试。
5. 真实 API 测试先于大规模 Skills/Extension 开发。
6. 每次真实测试前确认 token/turn 上限。
7. 不因“简化”而允许模型直接调用未注册函数。
8. 不因有 bash 工具而让 AIPic 业务资产绕过 AssetService。
9. 不把扩展机制描述为安全沙箱。
10. 不迁移会话树，除非未来另立 ADR 并提供独立价值证明。
