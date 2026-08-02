# FormWeaver Studio dependencies

本文件说明仓库中依赖的来源、用途以及便携版的封装边界。精确版本以
`uv.lock`、两个 `pnpm-lock.yaml` 和 `desktop/src-tauri/Cargo.lock` 为准。

## 便携版内嵌运行时

运行 `portable\FormWeaver-Studio\formweaver-studio.exe` 不需要单独安装以下
组件：

| 组件 | 用途 | 分发方式 |
| --- | --- | --- |
| Python 3.14 | 本地 sidecar 运行时 | PyInstaller one-file 内嵌 |
| ONNX Runtime 1.28.0 | 本地图片模型推理 | DLL/PYD 内嵌于 sidecar |
| Real-ESRGAN x4 ONNX | 离线图片放大 | 模型内嵌于 sidecar，源码资源由 Git LFS 管理 |
| Visual C++ Runtime | Python 与原生扩展运行 | DLL 内嵌于 sidecar |
| Pillow、NumPy、FastAPI 等 Python 包 | 图片处理与本地 API | 内嵌于 sidecar |
| React、Tauri API、model-viewer | 桌面界面与 3D 预览 | 编译进桌面应用 |

构建脚本会检查关键模型、DLL 和 PYD 是否确实存在于 sidecar 归档中；缺失
任意一项都会中止构建。便携目录中的 `BUNDLED_COMPONENTS.txt` 和
`SHA256SUMS.txt` 分别记录内嵌组件和分发文件哈希。

## 系统与可选依赖

| 组件 | 要求 | 说明 |
| --- | --- | --- |
| Windows | Windows 10/11 x64 | 当前桌面发行目标 |
| WebView2 Runtime | 系统组件 | Windows 11 通常已预装；未安装时需使用微软官方运行时 |
| Blender | 4.0+，可选 | 仅 GLB 转 FBX 等本地模型转换需要，不影响其他功能 |
| 网络与 Provider 凭据 | 按需 | 仅在线生成能力需要；本地处理、预览和项目管理可离线运行 |

Blender 不随仓库重新分发。程序通过配置的 Blender 路径调用其命令行导出
能力，避免捆绑第三方应用及其独立更新周期。

## Python 依赖

声明文件：`pyproject.toml`；锁文件：`uv.lock`。

主要运行依赖包括 Pillow、NumPy、ONNX Runtime、trimesh、
fast-simplification、FastAPI、HTTPX、Pydantic、keyring 和 cryptography。
PyInstaller、pytest、ruff 和 pyright 只用于构建与验证。

## JavaScript 依赖

- `desktop/pnpm-lock.yaml`：Tauri CLI
- `desktop/frontend/pnpm-lock.yaml`：React、Tauri API、model-viewer、
  Phosphor Icons、TypeScript、Vite 和 Vitest

安装时使用 `--frozen-lockfile`，避免开发机自动漂移依赖版本。

## Rust 依赖

声明文件：`desktop/src-tauri/Cargo.toml`；锁文件：
`desktop/src-tauri/Cargo.lock`。主要依赖包括 Tauri 2、dialog/notification/
opener 插件、serde、image、screenshots、rand 和 Windows Job Object API。

## 大文件与 Git LFS

以下文件类型通过 Git LFS 发布：`*.exe`、`*.dll`、`*.pyd`、`*.onnx`、
`*.wasm`。克隆后必须执行 `git lfs pull`，否则便携应用和模型只会是 LFS
指针文件。

## 许可证与更新原则

- 第三方许可证和模型来源记录在 `THIRD_PARTY_NOTICES.md`。
- 依赖升级必须同时更新对应锁文件并完成受控验证。
- 默认测试不得访问真实或付费 Provider。
- 模型、DLL 与便携应用更新后必须重新生成 SHA-256 清单。
