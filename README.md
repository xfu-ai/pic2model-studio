# FormWeaver Studio（织形工坊）

> 从图像灵感到可交付 3D 资产的本地 AI 创作工作台。

FormWeaver Studio 把提示词整理、图片方案生成、目标提取、多视图制作、
3D 生成、模型预览与格式转换组织在同一个桌面项目中。创作者可以从任意
已有图片或 GLB 模型进入，不必按固定顺序完成整条流程。

## 核心能力

- 从参考图和文字整理可复用的视觉提示词
- 生成、比较并管理多套图片候选方案
- 裁剪、抠图、离线放大、精灵图拆分与尺寸标准化
- 从主视图生成多视图素材，为 3D 生成准备一致输入
- 管理 3D 生成任务、结果预览和项目资产
- 本地预览 GLB，并通过 Blender 转换为 FBX
- 使用桌面 Agent 串联重复操作，付费操作始终等待用户确认

## 直接运行便携版

仓库使用 Git LFS 保存应用、模型和大体积运行时。首次获取代码后执行：

```powershell
git lfs install
git lfs pull
```

然后双击：

```text
portable\FormWeaver-Studio\formweaver-studio.exe
```

便携版已内嵌 Python 3.14、Python 包、ONNX Runtime 原生库、Visual C++
运行库和 Real-ESRGAN 模型。运行便携版不需要安装 Python、Node.js 或
Rust。Windows WebView2 是系统前置组件；Blender 仅在本地导出 FBX 时
需要。

## 产品工作流

```text
参考图 / 文字灵感
        ↓
提示词与视觉方向
        ↓
图片候选方案
        ↓
目标提取与素材整理
        ↓
多视图生成
        ↓
3D 生成、预览与导出
```

每个阶段都会把结果保存为项目资产，可以跳过不需要的步骤，也可以直接
导入已有图片或模型继续工作。

## 演示视频

产品演示视频录制完成后，可将封面图和视频链接放在这里。推荐展示顺序：
创建项目 → 导入参考图 → 生成图片方案 → 提取目标 → 生成多视图 → 生成并
预览 3D 模型 → 导出资产。

## 开发环境

- Windows 10/11 x64 与 WebView2
- Git LFS 3.5 或更高版本
- Python 3.14
- Node.js 20 或更高版本、pnpm 10 或更高版本
- Rust stable 与 MSVC C++ Build Tools
- Blender 4.0 或更高版本（仅 FBX 转换功能需要）

安装锁定依赖：

```powershell
git lfs install
git lfs pull
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e . --group dev
pnpm --dir desktop install --frozen-lockfile
pnpm --dir desktop/frontend install --frozen-lockfile
```

完整依赖分类、内嵌资源和许可证入口见 [DEPENDENCIES.md](DEPENDENCIES.md)。

## 构建便携版

仓库不生成安装包。运行下面的命令即可重新封装 sidecar、构建前端和 Tauri
release，并生成完整 SHA-256 清单：

```powershell
.\scripts\build_portable.ps1
```

输出目录：`portable\FormWeaver-Studio`。

## 验证

默认验证只使用离线 fixture 和模拟 Provider，不会调用付费服务：

```powershell
.\scripts\run_controlled_validation.ps1
pnpm --dir desktop/frontend test
pnpm --dir desktop/frontend build
cargo test --manifest-path desktop/src-tauri/Cargo.toml
```

真实 Provider 冒烟测试必须显式启用安全开关，具体规则见 [AGENTS.md](AGENTS.md)
和 [docs/controlled-validation.md](docs/controlled-validation.md)。

## 项目结构

```text
desktop/frontend/        React 桌面界面
desktop/src-tauri/       Tauri/Rust 原生宿主
src/aipic_to_model/      Python 本地服务与业务能力
contracts/               稳定工具与数据契约
portable/                可直接运行的 Windows 便携版
scripts/                 构建、配置与受控验证脚本
tests/                   合约、集成、安全和 E2E 测试
```

第三方软件与模型声明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
