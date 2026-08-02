# Gemini 与 NanoBanana 本地配置指南

## 默认路由

项目默认使用 Google 官方 Gemini API，不使用 Xais 网站：

- API：`https://generativelanguage.googleapis.com/v1beta`
- 协议：`google_generative_ai`
- 文本与分析：`gemini-flash-lite-latest`
- 流程生图：`gemini-flash-lite-latest` + `text_render`
- Xais fallback：默认关闭

`gemini-flash-lite-latest` 已实际完成最小文本推理，并解析到
`gemini-3.5-flash-lite`。旧的 `gemini-2.5-flash-lite` 对新用户已停用，
不得作为默认模型。

## 文件与凭据

- 本机配置：`.local/gemini.local.json`
- 可提交示例：`.local/gemini.example.json`
- Google API Key：Windows 安全凭据
- 凭据服务：`AIPicToModel`
- 凭据引用：`gemini/google/default`
- 可选 Xais 配置：`.local/xais-nanobanana.local.json`

真实配置文件已被 `.gitignore` 忽略。API Key 不得写入 JSON、文档、
测试证据或 Git 历史。

首次配置可复制示例：

```powershell
Copy-Item .\.local\gemini.example.json .\.local\gemini.local.json
```

交互式保存 API Key：

```powershell
.\.venv\Scripts\python.exe .\scripts\configure_gemini_credential.py
```

检查凭据状态：

```powershell
.\.venv\Scripts\python.exe .\scripts\configure_gemini_credential.py --status
```

验证配置、文本模型和当前图片后端模型可访问性：

```powershell
.\.venv\Scripts\python.exe .\scripts\verify_gemini_config.py
```

验证工具会进行一次 16-token 上限的最小文本推理，并通过 `countTokens`
检查图片后端使用的模型；它不会生成收费图片，也不会输出 API Key。

## 图片生成后端

当前默认使用 `text_render`：免费 Gemini 文本模型生成受限 JSON 场景计划，再由
本地 Pillow 安全渲染为 PNG。这条路径用于跑通分析→Prompt→候选图→3D 的工程流程，
不是把文本模型宣传为原生图片模型，也不用于最终画质验收。

默认配置为：

```json
{
  "image_generation_model": "gemini-flash-lite-latest",
  "image_backend": "text_render",
  "image_count": 1,
  "aspect_ratio": "1:1",
  "output_format": "png"
}
```

切换到原生 Gemini 图片模型时，将 `image_backend` 改为 `native`，并把
`image_generation_model` 改为可用图片模型。原生请求将
`generationConfig.responseModalities` 设为 `["TEXT", "IMAGE"]`，并从候选响应的
`inlineData` 保存图片。正式批量生成前必须先执行单张真实 smoke。

2026-07-26 当前 Key 对 `gemini-2.5-flash-image`、`gemini-3.1-flash-image` 和
`gemini-3.1-flash-lite-image` 的实际生成均返回免费层 429 配额限制；模型可列出和
`countTokens` 成功不代表拥有图片生成配额。因此默认使用 `text_render` 跑流程。

## 更换模型

只修改 `.local/gemini.local.json` 中对应字段：

- 文本默认模型：`default_text_model`
- 分析模型：`analysis_model`
- NanoBanana 模型：`image_generation_model`
- 图片后端：`image_backend=native|text_render`

修改后运行：

```powershell
.\.venv\Scripts\python.exe .\scripts\verify_gemini_config.py
```

2026-07-26 官方模型列表中可见的主要选择：

| 用途 | 模型 |
| --- | --- |
| 低成本文本 | `gemini-flash-lite-latest` |
| 通用文本 | `gemini-flash-latest`、`gemini-3.5-flash` |
| 强推理 | `gemini-pro-latest` |
| Nano Banana | `gemini-2.5-flash-image` |
| Nano Banana Pro | `gemini-3-pro-image` |
| Nano Banana 2 | `gemini-3.1-flash-image` |
| 低成本 Nano Banana 2 | `gemini-3.1-flash-lite-image` |

模型列表可见不等于永久可用。换模后必须运行验证工具；图片模型的真实生图
仍可能受配额、计费和内容政策影响。

## Xais 备用路径

Xais 浏览器工作流保留在 `.local/xais-nanobanana.local.json`，其中
`enabled=false`、`priority=fallback_only`。只有用户明确要求使用 Xais 时才可启用。

可选 Xais 卡密配置工具：

```powershell
.\.venv\Scripts\python.exe .\scripts\configure_xais_nanobanana_credential.py
```

不得因为 Google API 临时错误自动切换到 Xais；自动切换可能改变计费、内容政策
和结果保留方式。
