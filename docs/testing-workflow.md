# 测试工作流：命令行优先

## 原则

先用命令行在最小范围内验证业务逻辑、接口契约、状态变化和 DOM 可访问性。只有命令行无法判断的视觉布局、真实桌面宿主交互或外部服务行为，才使用截图或 `computer-use`。

这不是“少测 UI”：前端的 Vitest + JSDOM + Testing Library 测试应覆盖按钮可用性、表单输入、对话框、状态文案、API/Tool 调用以及失败恢复。失败输出中的 DOM、accessible roles 和 Testing Library 查询错误是首选调试反馈。

## 推荐顺序

1. Python 单元、契约或集成测试：验证 Provider、Tool、Job、资产和 Prompt 逻辑。
2. 前端 Vitest DOM 测试：验证组件状态、用户交互与 API 调用参数。
3. TypeScript 构建：验证类型、模块边界和生产包。
4. 截图或 `computer-use`：仅验证视觉、拖拽/指针、Tauri 宿主或真实外部服务。

## Python 命令

在仓库根目录运行，并优先指定最小测试文件：

```powershell
pytest tests/contract/providers/test_gemini_http.py -q
pytest tests/integration/test_prompt_extraction_tool.py -q
pytest tests/contract/test_prompt_version_api.py -q
```

需要覆盖一组相关修改时：

```powershell
pytest tests/contract/providers/test_gemini_http.py tests/integration/test_prompt_extraction_tool.py -q
```

## 前端 DOM 命令

在 `desktop/frontend` 目录运行：

```powershell
# 单文件、一次性执行：首选快速反馈
pnpm exec vitest run src/features/canvas/CompareWorkspace.test.tsx

# 持续监听：实现交互时使用
pnpm exec vitest --watch src/features/canvas/CompareWorkspace.test.tsx

# 全量前端测试
pnpm test

# 类型检查和生产构建
pnpm build
```

不要依赖 `pnpm test -- <文件名>` 来筛选文件；项目脚本固定为 `vitest run`，直接使用 `pnpm exec vitest` 可以确保精确筛选。

## DOM 调试方式

优先使用语义查询（`getByRole`、`getByLabelText`、`findByText`）。测试临时调试可加入：

```ts
screen.debug();
logRoles(document.body);
```

断言应覆盖用户可观察结果和边界调用，例如：按钮是否禁用、审批对话框是否出现、仅失败角色是否重试、`invokeTool` 的 Tool 名称与参数、是否创建正确的受管资产。

## 何时使用截图或 computer-use

仅在以下情况升级：

- CSS 响应式布局、遮挡、层级、颜色、图像呈现等视觉问题；
- JSDOM 不模拟的拖拽、缩放、原生文件选择或 Tauri 宿主能力；
- 需要确认真实 Provider 的配置、网络响应或桌面端到端链路。

视觉验证完成后，若行为可稳定复现，应补回对应的命令行测试，避免将截图检查变成回归测试的唯一保障。
