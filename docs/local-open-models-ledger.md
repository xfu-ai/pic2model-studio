# 本地开源模型执行台账

更新规则：每完成一个子项立即写入证据；当前批次门禁全部通过后，才允许把下一批次改为“执行中”。

| 批次 | 状态 | 开始条件 | 完成证据 |
| --- | --- | --- | --- |
| L00 实施文件与基线 | 已完成 | 分支已创建 | 本方案、台账、`git diff --check` 通过 |
| L01 本地 Provider 基础设施 | 已完成 | L00 完成 | Ruff、Pyright、19 个单元/安全测试通过 |
| L02 Qwen3-VL 文本 Agent | 已完成 | L01 完成 | Ruff、Pyright、66 个 Agent/provider 测试通过 |
| L03 Qwen3-VL 图片输入 | 已完成 | L02 完成 | Ruff、Pyright、80 个 Vision/泄漏测试通过 |
| L04 Z-Image-Turbo | 已完成 | L03 完成 | Ruff、Pyright、99 个本地生图与回归测试通过 |
| L05 TripoSR Worker | 已完成 | L04 完成 | Ruff、Pyright、49 个本地/远端 3D 与模型回归测试通过 |
| L06 动态风险与设置 UI | 已完成 | L05 完成 | UI/build/Rust/WebView2 证据 |
| L07 完整与真实验证 | 已完成 | L06 完成 | controlled 与真实 smoke 证据 |

## 基线记录

- 工作区：`E:\UGit\AIPicToModelClean`
- 分支：`codex/local-open-models-exploration`
- 起始工作树：存在用户未提交改动；不得重置、覆盖或将其误归入本地模型提交。
- 当前 Python Sidecar：Python 3.14 + PyInstaller onefile。
- 当前 Agent：DeepSeek profile + OpenAI-compatible transport。
- 当前图片路由：Tripo/Meshy 固定优先级。
- 当前 3D：Tripo 远端生命周期，Agent façade 强制 Tripo/PBR。

## 执行记录

### L00

- [x] 创建探索分支。
- [x] 固化实施方案。
- [x] 建立逐批次执行台账。
- [x] 复核实施文件并记录 L00 门禁结果：2026-08-03，`git diff --check` 通过，未修改运行时代码。

### L01

- [x] 定义本地 Provider profile 和能力 DTO。
- [x] 实现回环端点安全验证。
- [x] 实现本地 Provider 状态和假探针。
- [x] 实现统一本地重任务闸门。
- [x] 完成 L01 测试和证据：2026-08-03，Ruff 全绿；Pyright `0 errors`；`tests/unit/test_local_inference.py` 为 `19 passed`。

### L02

- [x] 实现 Ollama/Qwen3-VL profile，默认 `qwen3-vl:8b`，显式支持 `qwen3-vl:4b`。
- [x] 接入 Agent 会话创建和恢复，并保留旧 DeepSeek 会话的恢复兼容。
- [x] 兼容 Ollama SSE/tool call、无凭据回环请求和模型发现。
- [x] 完成 L02 测试和证据：2026-08-03，Ruff 全绿；Pyright `0 errors`；文本、多轮、工具调用、取消、错误映射和恢复集合为 `66 passed`。

### L03

- [x] 增加仅返回托管字节与 MIME、不会暴露原生路径的附件读取端口。
- [x] 实现 Qwen3-VL Provider 请求期 `ImageContent` 注入，请求原对象和持久化消息保持无像素状态。
- [x] 增加 PNG/JPEG/WebP、单图/总字节、附件数量、尺寸和像素数限制。
- [x] 通过 SQLite 和 API 事件断言证明图片二进制/Base64 不持久化、不记录。
- [x] 完成 L03 测试和证据：2026-08-03，Ruff 全绿；Pyright `0 errors`；Vision、Agent、安全与泄漏集合为 `80 passed`。

### L04

- [x] 实现 stable-diffusion.cpp 受控执行器，固定 capability slot、命令模板、受控临时目录和去敏子进程环境。
- [x] 实现 Z-Image-Turbo Provider，提供无下载探针、结构化错误和瞬态候选图载荷。
- [x] 接入冻结的本地图片 Job 路由和候选资产 provenance，既有 `image-generation/auto` 仍走受控远端 fixture。
- [x] 完成取消、超时、进程树终止、GPU 串行、重试、损坏/缺失输出和远端隔离测试。
- [x] 完成 L04 测试和证据：2026-08-03，Ruff 全绿；Pyright `0 errors`；L04 新增集合 `19 passed`，连同现有受控生图/契约/Agent facade 回归集合共 `99 passed`；`git diff --check` 无空白错误。

### L05

- [x] 建立 Python 3.10/3.11 独立 Worker 清单、依赖基线、上游 commit 和离线单图/GLB 协议。
- [x] 实现固定 capability slot、固定 CLI、离线环境、单 GPU 闸门、心跳、超时和进程树取消。
- [x] 实现冻结的 `model3d/local/triposr` 单图 Job handler；本地任务不进入付费提交或未知提交状态。
- [x] 验证、注册并检查 GLB，持久化真实本地参数且不宣称多视图、PBR 或贴图烘焙。
- [x] 完成 L05 测试和证据：2026-08-03，Ruff 全绿；Pyright `0 errors`；L05 新增集合 `13 passed`，连同远端 Tripo 生命周期、模型检查/预览和受控 Provider 回归集合共 `49 passed`；`git diff --check` 无空白错误。

### L06

- [x] 实现 Provider 预解析和有效风险冻结；本地 Z-Image/TripoSR 持久化为 `LOCAL_REVERSIBLE`，远程生图和 3D 在 Tool Call 持久化前冻结具体 Provider、模型和 `EXTERNAL_PAID`。
- [x] 区分本地与远端审批/恢复策略；本地任务直接排队并保持 `LOCAL_RESTARTABLE`，远程任务继续参数绑定审批，多视图 3D 始终保持远程，request 重放和重试不会切换 Provider。
- [x] 完成本地模型状态 API 和设置 UI；展示 Qwen3-VL、Z-Image-Turbo、TripoSR 的可用性、原因、能力、许可证和来源，不返回原生路径，检测不下载也不生成。
- [x] 完成 L06 验证：2026-08-03，后端策略/API/回归集合 `130 passed`（52 + 78），Ruff 全绿，Pyright `0 errors`；SettingsDialog `4 passed`；前端 build 通过；Rust `6 passed`；受控 WebView2 `local_model_settings` 与 `agent_model_settings` 均通过，证据位于 `tests/evidence/controlled-webview2-current/`。

### L07

- [x] 运行完整 controlled validation：2026-08-04，`41 passed` 安全 E2E、`192 passed` 契约、`257 passed` 集成/单元、`188 passed` 前端、`6 passed` Rust，前端 build 通过；证据根目录 `tests/evidence/controlled-validation/20260804T120903/`。
- [x] 运行受控 WebView2 回归：本地模型设置、Qwen3-VL 默认 Agent、远程 Tripo 审批/模型结果打开、Agent 双图附件均通过，运行时错误和未处理 Promise 均为 0；证据位于 `tests/evidence/controlled-webview2-current/`。
- [x] 真实验证 Ollama/Qwen3-VL：便携 Ollama `0.32.5` 仅监听 `127.0.0.1:11434`，`qwen3-vl:8b`（digest `901cae732162...`）在 RTX 5080 上完成纯文本、真实截图理解、OpenAI-compatible 结构化工具调用和应用 Agent API 持久化闭环；脱敏摘要位于 `tests/evidence/real-local-models/20260804T101936/qwen3-vl-summary.json`。
- [x] 真实验证 Z-Image-Turbo：固定 `stable-diffusion.cpp` commit `db99efdd...` 的官方 CUDA 12 二进制，使用 SHA256 校验的 Q3_K 扩散权重、Q4_K_M 文本编码器和 FLUX VAE，在 RTX 5080 上完成 512×512/8 steps 真实生成；应用 Job 以 `LOCAL_RESTARTABLE` 成功注册 PNG 候选资产。脱敏摘要位于 `tests/evidence/real-local-models/20260804T105335/z-image-turbo-summary.json`。
- [x] 真实验证 TripoSR：隔离 CPython 3.11.9、PyTorch `2.7.1+cu128` 和固定 TripoSR/torchmcubes commit 在 RTX 5080 上完成真实单图 Worker 与应用 Job 闭环；Job 以 `model3d/local/triposr`、`LOCAL_RESTARTABLE` 成功注册并检查 74,597 顶点/149,092 三角面的 GLB，受控 WebView2 加载的 Blob 哈希与应用资产一致，运行时错误和失败网络请求均为 0。脱敏摘要位于 `tests/evidence/real-local-models/20260804T114436/triposr-summary.json`。
- [x] 完成逐项审计并关闭剩余缺口：补齐 TripoSR 辅助 DINO 配置的固定 revision 与 SHA256，复核三个真实本地模型闭环、最新完整 controlled validation 和脱敏证据路径。
- [x] 补充 Agent 对话图片双路径：Qwen3-VL 等受支持多模态 profile 临时接收真实图片内容并直接理解；纯文本 Agent 只接收托管资产引用，再调用 `understand_image`，任何模型都不会收到原生路径。双图桌面交互证据位于 `tests/evidence/agent-image-routing/20260804T120809/desktop-9237/`。

真实环境进度（2026-08-04）：工作区隔离的 Ollama/Qwen3-VL、stable-diffusion.cpp/Z-Image-Turbo 与 TripoSR 三个真实 smoke 和应用闭环均已通过；推理仅使用回环地址或本地进程，未访问任何付费 Provider。TripoSR 使用隔离 CPython 3.11.9、固定离线权重和 DINO 配置缓存；主网络在 CUDA 上推理，当前未安装完整 CUDA Toolkit，因此固定 torchmcubes 使用其官方 CPU marching-cubes 回退。白底 RGB 输入会形成薄背景平面，生产输入应优先使用透明或均匀中性灰背景。相关改动文件的 Ruff 和 Pyright 均为 0；全工作树仍保留任务开始前已有的 Ruff/Pyright 问题，不在本批次擅自改写。

运行修正（2026-08-04）：真实桌面带图 Agent 请求在 Ollama 默认 4096 上下文下稳定返回 HTTP 400（请求约 7238 tokens）。本地服务改为 `OLLAMA_CONTEXT_LENGTH=32768` 后，`/api/ps` 确认实际上下文为 32768。排障阶段曾以 2048 输出上限证明完整“图片像素 + 固定 Agent 工具目录”链路可成功返回；最终策略改为按 `上下文 - 输入 - 工具 schema - 4096 安全余量` 动态分配，短输入最大输出 28672，超时 600 秒。部署和本地启动不得只依据模型元数据中的理论上下文，必须验证 Ollama 实际加载上下文。
