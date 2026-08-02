# Python Pi Agent 迁移台账

> 权威实施计划：[python-pi-agent-migration-plan.md](python-pi-agent-migration-plan.md)
>
> Pi 冻结来源：上游 Pi 提交 `8eef62ed3ea62d646a7fad92fa583fc8d71fec17`
>（2026-07-24T19:05:08Z，MIT License，Copyright © 2025 Mario Zechner）

## 阶段状态

| 阶段 | 名称 | 状态 |
| --- | --- | --- |
| 0 | 冻结范围、来源和基线 | completed |
| 1 | 核心数据模型、事件流和取消原语 | completed |
| 2 | Provider 内核、Fake Provider 和首个 API Adapter | completed |
| 3 | Tool 契约和低层 Agent Loop | completed |
| 4 | DeepSeek 早期真实协议验证 | completed |
| 5 | 有状态 Agent、队列和运行快照 | completed |
| 6 | 线性 SQLite Session | completed |
| 7 | AgentHarness 与自动上下文压缩 | completed |
| 8 | 扩展机制 | completed |
| 9 | ExecutionEnv 与 read/write/edit/bash | completed |
| 10 | Skills 与 Prompt Templates | completed |
| 11 | 迁移 Pi 全部 Provider、认证和模型目录 | completed |
| 12 | 接入现有 AIPic ToolRegistry | completed |
| 13 | FastAPI、SSE 和应用集成 | completed |
| 14 | DeepSeek 最终综合真实验证 | completed |
| 15 | 回归、性能、文档替换和发布门 | completed |

## 阶段记录

### 阶段 0：冻结范围、来源和基线

- 状态：completed
- 冻结 Pi commit：`8eef62ed3ea62d646a7fad92fa583fc8d71fec17`
- 实现内容：新增 ADR、Pi MIT notice、来源清单和行为映射；声明 Agent pytest
  markers，并将真实 LLM 测试设为 `RUN_LIVE_LLM_TESTS=1` opt-in；修复现有依赖
  声明与维护中 Python 文件的格式基线。
- 测试命令：`uv lock`；`uv run pytest tests/integration/test_selection_outputs.py -q`；
  `uv run pytest -q`；`uv run ruff check .`；`uv run ruff format --check .`；
  `uv run pyright`。
- 测试结果：全部通过；完整 pytest 为 153 passed，Ruff 与 Pyright 无警告。
- 证据：`tests/evidence/agent/phase-0/20260725T131547Z/`。
- 遗留限制：真实 LLM 未在本阶段运行；本机 `uv` 未在 PATH，使用已验证本地二进制。

### 阶段 1：核心数据模型、事件流和取消原语

- 状态：completed
- 实现内容：实现 Message、ToolCall、ToolResult、Usage、ProviderEvent、AgentEvent、
  稳定错误类型、可关闭异步 EventStream 与 CancellationToken。
- 测试命令：`uv run pytest tests/unit/agent/test_models.py
  tests/unit/agent/test_event_stream.py -q`；`uv run pytest -q`；`uv run ruff check .`；
  `uv run ruff format --check .`；`uv run pyright`。
- 测试结果：阶段单测 8 passed；完整回归 161 passed；Ruff 与 Pyright 无警告。
- 证据：`tests/evidence/agent/phase-1/20260725T132616Z/`。
- 遗留限制：Provider、Tool Loop、持久化与 API 均尚未开始，按后续阶段实现。

### 阶段 2：Provider 内核、Fake Provider 和首个 API Adapter

- 状态：completed
- 实现内容：实现 Provider 协议、Fake Provider、Registry/Catalog/CredentialResolver
  骨架和 OpenAI-compatible Chat Completions SSE Adapter。
- 测试命令：`uv run pytest tests/unit/agent/test_fake_provider.py
  tests/unit/agent/test_provider_registry.py tests/integration/agent/test_openai_compatible_provider.py -q`；
  `uv run pytest -q`；Ruff、format、Pyright。
- 测试结果：Provider 合同 10 passed；完整回归 170 passed；静态检查无警告。
- 证据：`tests/evidence/agent/phase-2/20260725T133653Z/`。
- 遗留限制：其余 API 协议和 Provider 在阶段 11 实现。

### 阶段 3：Tool 契约和低层 Agent Loop

- 状态：completed
- 实现内容：实现 AgentTool/ToolRegistry 和 Draft 2020-12 参数校验；实现顺序多轮
  Tool Loop、ToolResult 回写、hooks、限制、deadline 与 Provider/Tool 取消；补齐 Tool
  lifecycle/message 事件和 OpenAI-compatible SSE 最终 AssistantMessage 聚合与 ToolCall 回传。
- 测试命令：`uv run pytest tests/unit/agent/test_agent_loop.py -q`；Provider 合同；
  受影响 B01 回归；`uv run pytest -q`；Ruff、format、Pyright、`git diff --check`。
- 测试结果：阶段单测 8 passed；Provider 合同 11 passed；B01 受影响回归 22 passed；
  完整 pytest 180 passed；Ruff 与 Pyright 无警告。
- 证据：`tests/evidence/agent/phase-3/20260725T054754Z/`。
- 遗留限制：阶段 4 的真实 DeepSeek 协议验证尚未开始，按计划顺序执行。

### 阶段 4：DeepSeek 早期真实协议验证

- 状态：completed
- 实现内容：新增固定 DeepSeek profile（默认 `https://api.deepseek.com` / `deepseek-v4-flash`）、
  OS Keyring 与开发/CI 环境变量凭据解析、脱敏 live smoke；OpenAI-compatible adapter 支持
  DeepSeek 的可选 usage stream 禁用、Tool wire-name 规范化和 response correlation ID。
- 测试命令：`RUN_LIVE_LLM_TESTS=1 uv run pytest tests/live/agent/test_deepseek_smoke.py -q -s`；
  阶段单测、受影响 B01 回归、完整 pytest、Ruff、format、Pyright、`git diff --check`。
- 测试结果：真实 smoke 通过（2 turns / 1 Tool / 465 total tokens）；阶段单测 20 passed；
  B01 受影响回归 22 passed；完整 pytest 183 passed、1 skipped；静态检查无警告。
- 证据：`tests/evidence/agent/phase-4/20260725T062959Z/` 与
  `tests/evidence/agent-live/20260725T062959Z/deepseek-smoke.json`。
- 遗留限制：此前错误模型的脱敏失败证据保留于 `tests/evidence/agent-live/20260725T055616Z/`；
  不影响当前退出门。

### 阶段 5：有状态 Agent、队列和运行快照

- 状态：completed
- 实现内容：实现状态 Agent 门面、单运行互斥、顺序 listener 与状态投影、三类队列、abort 策略和
  不可变 Provider request runtime snapshot。
- 测试命令：`uv run pytest tests/unit/agent/test_agent.py tests/unit/agent/test_agent_queues.py -q`；
  `uv run pytest -q`；Ruff、format、Pyright、`git diff --check`。
- 测试结果：阶段测试 14 passed；完整 pytest 189 passed、1 skipped；静态检查无警告。
- 证据：`tests/evidence/agent/phase-5/20260725T063753Z/`。
- 遗留限制：无。

### 阶段 6：线性 SQLite Session

- 状态：completed
- 实现内容：实现独立线性 SQLite Session 迁移、Repository、operation/tool 生命周期、完整
  message_end 持久化与保守 interrupted 恢复。
- 测试命令：阶段集成、B01 受影响回归、完整 pytest、Ruff、format、Pyright、`git diff --check`。
- 测试结果：阶段集成 16 passed；B01 回归 22 passed；完整 pytest 194 passed、1 skipped；静态检查无警告。
- 证据：`tests/evidence/agent/phase-6/20260725T064242Z/`。
- 遗留限制：无。

### 阶段 7：AgentHarness 与自动上下文压缩

- 状态：completed
- 实现内容：实现线性 AgentHarness、turn/compaction/idle phase、持久化运行快照、有效 usage 加 trailing estimate 的 token 估算、图片 fallback、合法化的压缩预算、Tool Call/Result 成对的 cut point、rolling structured summary、threshold/manual/overflow 触发、一次 overflow retry、hook 取消/自定义摘要、事件和 `CompactionRecord` SQLite migration；原始消息保持不删除，重启会中断未提交压缩。
- 测试命令：阶段单元/集成测试、受影响 B01 回归、完整 pytest、Ruff、格式检查、Pyright、`git diff --check`。
- 测试结果：Phase 7 13 passed；受影响 B01 23 passed；完整 pytest 207 passed、1 skipped；静态和格式检查无错误。
- 证据：`tests/evidence/agent/phase-7/20260725T145539Z/`。
- 遗留限制：无；Phase 8 的 Extension 具体接口尚未实现，按顺序进入下一阶段。

### 阶段 8：扩展机制

- 状态：completed
- 实现内容：实现确定性 ExtensionRegistry、显式可信本地模块/目录加载、资源注册、context transform、生命周期 Hook、provider request patch、tool block、失败禁用诊断和 reverse-order teardown；ExtensionContext 不暴露 SecretStore。
- 测试命令：Phase 8 单元/集成测试、受影响 B01 回归、完整 pytest、Ruff、format、Pyright、`git diff --check`。
- 测试结果：阶段测试 5 passed；受影响 B01 23 passed；完整 pytest 212 passed、1 skipped；静态检查无错误。
- 证据：`tests/evidence/agent/phase-8/20260725T150510Z/`。
- 遗留限制：无；Phase 9 按计划实现 ExecutionEnv 和内置文件/命令工具。

### 阶段 9：ExecutionEnv 与 read/write/edit/bash

- 状态：completed
- 实现内容：实现受 workspace roots 约束的 LocalExecutionEnv、原子 write、唯一匹配 edit、mutation queue、PowerShell bash、timeout/cancel、环境变量 allowlist 和大输出 artifact；四个内置工具只依赖该环境。
- 测试命令：Phase 9 单元/集成/security、受影响 B01 回归、完整 pytest、Ruff、format、Pyright、`git diff --check`。
- 测试结果：阶段测试 4 passed；受影响 B01 23 passed；完整 pytest 216 passed、1 skipped；静态检查无错误。
- 证据：`tests/evidence/agent/phase-9/20260725T150937Z/`。
- 遗留限制：无；Phase 10 按计划实现 Skills 和 Prompt Templates。

### 阶段 10：Skills 与 Prompt Templates

- 状态：completed
- 实现内容：实现三层 Skill discovery/override、metadata/hash、lazy activation、required tool/resource 检查、harness skill 注入与 active name/hash 持久化，以及严格 Prompt Template 变量替换。
- 测试命令：Phase 10 单元/集成测试、受影响 B01 回归、完整 pytest、Ruff、format、Pyright、`git diff --check`。
- 测试结果：阶段测试 5 passed；受影响 B01 23 passed；完整 pytest 221 passed、1 skipped；静态检查无错误。
- 证据：`tests/evidence/agent/phase-10/20260725T151318Z/`。
- 遗留限制：无；Phase 11 按冻结 Pi provider/auth/catalog 范围继续。

### 阶段 11：迁移 Pi 全部 Provider、认证和模型目录

- 状态：completed
- 实现内容：冻结 Pi 的 38 个聊天 Provider、1 个图片 Provider、11 个协议 Adapter、版本化模型目录、动态 Radius catalog、认证与 OAuth/keyring、AWS credential chain、Google ADC/service account、Cloudflare 组合认证和 PyInstaller import smoke。
- 测试命令：provider/auth/catalog/security 合同、DeepSeek live smoke、Ruff、format、Pyright、catalog check、Core/Harness provider-branch scan 和 diff check。
- 测试结果：76 个离线 provider/security 测试和 1 个 DeepSeek live 测试通过；Ruff、format、Pyright、catalog 和架构检查通过。
- 证据：`tests/evidence/agent/phase-11/20260725T173000Z/`。
- 遗留限制：无阻塞限制；未配置 Provider 明确报告 `not_configured`，未伪装为 live 通过。

### 阶段 12：接入现有 AIPic ToolRegistry

- 状态：completed
- 实现内容：实现 AIPic ToolRegistry 到 AgentTool adapter、可见性/审批过滤和结构化 ToolResult/job/ui-action 映射；业务行为继续由既有 AIPic ToolRegistry、AssetService 和持久化层执行。
- 测试命令：AIPic adapter、Agent loop、B01 tool contract、security、Ruff、format、Pyright、diff check。
- 测试结果：45 tests passed；静态和差异检查通过。
- 证据：`tests/evidence/agent/phase-12/20260725T180000Z/`。
- 遗留限制：无阻塞限制。

### 阶段 13：FastAPI、SSE 和应用集成

- 状态：completed
- 实现内容：新增认证 Agent Conversation API（创建/状态/消息/取消/steer/follow-up/Skills/Extensions/health/SSE）；将 AgentEvent 投影为脱敏稳定 DTO，并在现有线性 Agent SQLite Session 中持久化单调 API 事件序列。SSE 以 `Last-Event-ID` 恢复；完整消息仍由 Session 查询。应用重启时通过既有恢复逻辑将未完成 operation 标记为 interrupted；Agent 继续仅通过受限内置工具及 AIPic ToolRegistry adapter 执行。
- 测试命令：`uv run pytest tests/contract/test_agent_api.py -q`；`uv run pytest tests/integration/agent/test_agent_sse.py -q`；`uv run pytest tests/e2e/test_agent_sidecar_lifecycle.py -q`；`uv run pytest -q`；`uv run pytest tests/security -q`；`uv run pytest tests/contract/agent/providers -q`；`uv run ruff check .`；`uv run ruff format --check .`；`uv run pyright`；`git diff --check`。
- 测试结果：Phase 13 gates 4 passed；完整 pytest 282 passed、1 skipped；security 22 passed；provider contracts 37 passed；Ruff、format、Pyright 和 diff 检查通过。
- 证据：`tests/evidence/agent/phase-13/20260725T083500Z/`。
- 遗留限制：无阻塞限制；SSE 的 token delta 是持久化的安全投影，断线恢复以完整 Session 消息为权威，不依赖 token 逐个重放。

### 阶段 14：DeepSeek 最终综合真实验证

- 状态：completed
- 实现内容：新增五组受控 DeepSeek 场景：计算器链、四个内置文件工具、Skill、Extension 和自动上下文压缩/overflow retry；补全 Extension `after_tool_call` 审计 details，以及 overflow 紧随已提交压缩时复用现有摘要的恢复路径。
- 测试命令：两次 `RUN_LIVE_LLM_TESTS=1 uv run pytest tests/live/agent -q -s`；Phase 14 离线回归、Ruff、format、Pyright、diff 和递归密钥模式扫描。
- 测试结果：两次完整 live suite 均为 11 passed；Phase 14 离线 5 passed、10 skipped；Ruff、format、Pyright、diff 和密钥扫描通过。
- 证据：`tests/evidence/agent/phase-14/20260725T090000Z/`。
- 遗留限制：无阻塞限制；live evidence 仅保存允许的 provider/model、时长、turn/tool/usage 摘要和事件类型。

### 阶段 15：回归、性能、文档替换和发布门

- 状态：completed
- 实现内容：新增 Agent 本地性能基线；发布 Python Agent Runtime 单一运行规范；将 B03 明确标记为历史设计输入；完成最终安全、目录和 live 发布门审计。
- 测试命令：Ruff、format、Pyright、完整 pytest、security、provider contracts、catalog sync、DeepSeek live suite 和 diff check。
- 测试结果：284 passed、11 skipped；security 22 passed；provider contracts 37 passed；live 11 passed；静态检查、catalog 和 diff 通过。
- 证据：`tests/evidence/agent/phase-15/20260725T091000Z/`。
- 遗留限制：无阻塞限制；未配置的非 DeepSeek Provider 仍正确报告 `not_configured`。
