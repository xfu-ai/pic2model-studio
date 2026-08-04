# 本地开源模型实施方案

状态：执行中（L07）
执行分支：`codex/local-open-models-exploration`
目标模型：Qwen3-VL、Z-Image-Turbo、TripoSR

## 选型核验结论

- **Qwen3-VL 不是只会看图的模型。**它同时具备纯文本理解与生成、图像/视频理解、推理和 Agent 交互能力。Ollama 的 `qwen3-vl` 模型页明确标注 `vision`、`tools`、`thinking`；本项目仍需负责工具 schema、调用循环、权限、审批和执行结果回填，模型本身不直接执行本地工具。
- **Agent 运行时采用 Ollama。**最低要求 Ollama `0.12.7`；默认 `qwen3-vl:8b`，资源不足时允许显式选择 `qwen3-vl:4b`，不得在 Job 或会话开始后静默降级。启动 Ollama 时必须设置 `OLLAMA_CONTEXT_LENGTH=32768`：默认 4096 无法容纳图片、Agent 系统提示和固定工具目录。Qwen 每次请求的输出预算按 `32768 - 输入估算 - 工具 schema - 4096 安全余量` 动态计算，短输入最多可使用 28672 tokens；本地请求超时为 600 秒。
- **Z-Image-Turbo 采用 stable-diffusion.cpp。**官方仓库已把 stable-diffusion.cpp 列为 Z-Image 的 C/C++ 本地推理方案，支持 CUDA/Vulkan 和低显存路径。首版只开放文生图；官方 Turbo 推荐 8 NFE，实际默认参数在真实硬件 smoke 后冻结。
- **TripoSR 采用隔离 Worker。**官方实现是单图快速重建，默认单图约需 6GB VRAM，并已支持 `--model-save-format glb`。因此首版直接导出并校验 GLB，不再设计 OBJ 到 GLB 的强制中转；贴图烘焙作为显式可选能力，不宣称完整 PBR。

上游依据：

- [Qwen3-VL 官方仓库](https://github.com/QwenLM/Qwen3-VL)
- [Ollama qwen3-vl 模型页](https://ollama.com/library/qwen3-vl)
- [Z-Image 官方仓库](https://github.com/Tongyi-MAI/Z-Image)
- [stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp)
- [TripoSR 官方仓库](https://github.com/VAST-AI-Research/TripoSR)

## 不可破坏的约束

1. 默认测试和受控 E2E 不得连接任何真实或付费 Provider。
2. 真实本地模型验证只能在所有受控验证通过后运行，并且只能访问回环地址或受控本地进程。
3. React 只接收不透明 capability ID；模型、可执行文件和项目文件路径由原生 Host 管理。
4. 图片二进制只可在单次模型请求中临时注入，不能写入 Agent SQLite、事件、日志或诊断。
5. 本地生成任务必须持久化为 `LOCAL_RESTARTABLE`，不得进入付费审批、远端未知提交或下载恢复状态。
6. Provider 在 Job 创建前解析并冻结；Job 创建后不得静默切换模型或 Provider。
7. 同一 GPU 上的本地重任务默认串行执行，并支持取消完整进程树。

## 批次和门禁

### L00：实施文件与基线

- 固化本方案和执行台账。
- 记录分支、已有用户改动和验证顺序。
- 门禁：实施文件通过人工审阅；不得修改运行时代码。

### L01：本地 Provider 基础设施

- 增加本地 Provider profile、能力、执行类别、健康状态和许可证元数据。
- 只允许 `127.0.0.1`/`localhost` 回环端点，禁止重定向和任意 URL。
- 增加统一的本地 GPU 任务闸门。
- 增加 Ollama、stable-diffusion.cpp、TripoSR 的受控假探针。
- 门禁：配置、状态、安全和序列化单元测试通过。

### L02：Qwen3-VL 文本 Agent 与工具调用

- 默认 Agent profile 改为 `agent/ollama/qwen3-vl`。
- 默认模型 `qwen3-vl:8b`，允许 `qwen3-vl:4b`。
- 复用 OpenAI-compatible SSE/tool-call Provider，并锁定 Ollama `>=0.12.7`。
- 增加 Ollama 模型发现和无凭据回环调用。
- 门禁：纯文本、多轮、工具调用、取消、错误映射和会话恢复测试通过。

### L03：Qwen3-VL 真实图片输入

- 由托管资产 ID 临时读取图片字节。
- 在 Provider 请求变换阶段生成 `ImageContent`，请求完成即释放。
- 持久化层只保留附件元数据。
- 限制 MIME、尺寸、单图字节数、附件数量和总请求字节数。
- 门禁：像素请求契约测试通过；SQLite、事件和诊断泄漏测试通过。

### L04：Z-Image-Turbo 本地文生图

- 增加 `image/local/z-image-turbo`，第一版只声明 `t2i`。
- 通过固定命令模板调用 stable-diffusion.cpp；模型和引擎使用 Host capability ID。
- 输出写入受控临时目录，验证后注册为候选资产。
- 支持 seed、steps、尺寸、候选数、取消、超时和安全重试。
- 门禁：假进程、候选注册、取消、重试、损坏输出和 provenance 测试通过。

### L05：TripoSR 本地 3D Worker

- 使用独立 Python 3.10/3.11 Worker，不打包进 Python 3.14 单文件 Sidecar。
- 只支持单图；不宣称多视图、PBR、四边面或部件分离能力。
- 输入为托管图片，Worker 使用 `--model-save-format glb` 直接导出，Host 验证后注册 GLB。
- 增加本地 Job handler、进度、取消、重启恢复和模型资产注册。
- 门禁：假 Worker、进程树取消、显存不足、损坏 GLB、重试和预览检查通过。

### L06：动态风险和桌面设置

- 在创建 Job 前解析并冻结 Provider、模型和有效风险等级。
- 本地生成使用 `LOCAL_REVERSIBLE`；现有远端生成继续 `EXTERNAL_PAID`。
- 增加本地模型设置、状态、许可证和模型可用性界面。
- 设置探针不得下载权重或启动生成。
- 门禁：前端组件测试、构建、Rust tests 和 HMR WebView2 交互验证通过。

### L07：完整验证与真实本地 AI

- 运行最小相关测试后运行完整 controlled validation。
- 运行受控 WebView2 Agent、生图、3D、取消、错误和结果打开流程。
- 受控验证全部通过后，使用显式本地 smoke 开关验证真实 Ollama/Qwen3-VL、Z-Image-Turbo 和 TripoSR。
- 保留硬件、引擎版本、模型版本、耗时和非敏感结果摘要。
- 门禁：三个真实本地模型都完成至少一次成功端到端流程，且无外网或付费 Provider 请求。

## 完成定义

只有以下条件全部有证据时才可宣布完成：

- Qwen3-VL 能完成文本 Agent、真实图片理解和至少一次真实工具调用。
- Z-Image-Turbo 能生成并注册真实本地图片资产。
- TripoSR 能从托管图片生成、注册、检查并打开真实 GLB。
- 本地任务可取消、可安全重试，且不会进入付费或未知提交状态。
- 所有受控测试、前端构建、Rust tests 和要求的 WebView2 E2E 均通过。
- 真实本地验证过程没有连接 Tripo、Gemini、Meshy、OpenAI 或其他付费 Provider。
