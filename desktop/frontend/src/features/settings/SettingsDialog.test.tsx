import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ApiClient, LocalProviderStatusDto, ServiceProviderStatusDto } from "../../shared/api/client";
import appShellCss from "../../app/app-shell.css?raw";
import { SettingsDialog } from "./SettingsDialog";

afterEach(cleanup);

const providers: ServiceProviderStatusDto = {
  probe_interval_seconds: 300,
  probes_consume_generation_credits: false,
  providers: [
    {
      profile: "tripo3d/default", label: "Tripo3D", channel: "tripo", configured: true,
      available: true, reason: null, last_checked_at: "2026-07-31T00:00:00Z", display_order: 1,
      model: "seedream_v5", models: { t2i: "seedream_v5" }, modes: ["i2i", "t2i"], capabilities: ["text_to_image", "image_editing"],
    },
    {
      profile: "meshy/default", label: "Meshy", channel: "meshy", configured: false,
      available: false, reason: "PROVIDER_NOT_CONFIGURED", last_checked_at: "2026-07-31T00:00:00Z", display_order: 2,
      model: "nano-banana", models: {}, modes: ["i2i", "t2i"], capabilities: ["text_to_image", "image_editing"],
    },
    {
      profile: "gemini/google/default", label: "Gemini", channel: "google", configured: true,
      available: false, reason: "PROVIDER_AUTH_FAILED", last_checked_at: "2026-07-31T00:00:00Z", display_order: 3,
      model: "gemini-flash-lite-latest", models: {}, modes: [], capabilities: ["image_analysis", "prompt_rewrite"],
    },
    {
      profile: "agent/deepseek/default", label: "DeepSeek Agent", channel: "deepseek", configured: true,
      available: true, reason: null, last_checked_at: "2026-07-31T00:00:00Z", display_order: 4,
      model: "deepseek-v4-flash", models: {}, modes: [], capabilities: ["agent_chat"],
    },
  ],
};

const localProviders: LocalProviderStatusDto = {
  probes_download_models: false,
  probes_create_generation_jobs: false,
  providers: [
    {
      profile: "agent/ollama/qwen3-vl", label: "Qwen3-VL (Ollama)", engine: "ollama",
      transport: "openai_compatible", model: "qwen3-vl:8b",
      capabilities: ["agent_chat", "image_analysis", "tool_calling"], configured: true,
      available: true, reason: null, engine_version: "0.12.7",
      license: { identifier: "Apache-2.0", source_url: "https://github.com/QwenLM/Qwen3-VL", notice: "Qwen3-VL model license." },
    },
    {
      profile: "image/local/z-image-turbo", label: "Z-Image-Turbo", engine: "stable_diffusion_cpp",
      transport: "controlled_process", model: "Z-Image-Turbo", capabilities: ["text_to_image"],
      configured: true, available: false, reason: "model_not_installed", engine_version: null,
      license: { identifier: "Apache-2.0", source_url: "https://github.com/Tongyi-MAI/Z-Image", notice: "Local text-to-image." },
    },
    {
      profile: "model3d/local/triposr", label: "TripoSR", engine: "triposr",
      transport: "controlled_process", model: "stabilityai/TripoSR", capabilities: ["image_to_3d"],
      configured: true, available: true, reason: null, engine_version: null,
      license: { identifier: "MIT", source_url: "https://github.com/VAST-AI-Research/TripoSR", notice: "Single-image reconstruction." },
    },
  ],
};

describe("SettingsDialog service credentials", () => {
  it("keeps the modal backdrop above workspace overlays", () => {
    const backdropRule = appShellCss.match(/\.dialog-backdrop\s*\{([^}]*)\}/)?.[1] ?? "";

    expect(backdropRule).toMatch(/z-index:\s*100/);
  });

  it("lays out every API key and supports independent save and probe", async () => {
    const api = {
      settings: vi.fn().mockResolvedValue({
        image_provider_priority: ["tripo3d/default", "meshy/default"],
        provider_probe_interval_seconds: 300,
        agent_model: "deepseek-v4-flash",
      }),
      serviceProviders: vi.fn().mockResolvedValue(providers),
      localProviders: vi.fn().mockResolvedValue(localProviders),
      refreshLocalProviders: vi.fn().mockResolvedValue(localProviders),
      refreshServiceProviders: vi.fn().mockResolvedValue(providers),
      probeServiceProvider: vi.fn().mockResolvedValue(providers),
      updateSettings: vi.fn().mockResolvedValue({}),
      setSecret: vi.fn().mockResolvedValue({ configured: true, mask: "••••1234" }),
    } as unknown as ApiClient;

    render(<SettingsDialog api={api} onClose={vi.fn()} />);

    expect(await screen.findByText("seedream_v5 · 可用")).toBeInTheDocument();
    expect(screen.getByText("gemini-flash-lite-latest · 凭据无效")).toBeInTheDocument();
    expect(screen.getByText("deepseek-v4-flash · 可用")).toBeInTheDocument();
    expect(await screen.findByText("qwen3-vl:8b · 可用")).toBeInTheDocument();
    expect(screen.getByText("Z-Image-Turbo · 模型未安装")).toBeInTheDocument();
    for (const label of ["Tripo3D", "Meshy", "Gemini", "DeepSeek Agent"]) {
      expect(screen.getByLabelText(`${label} API Key`)).toBeInTheDocument();
    }

    fireEvent.change(screen.getByLabelText("Gemini API Key"), { target: { value: "new-gemini-key" } });
    fireEvent.click(screen.getAllByRole("button", { name: "保存并检测" })[2]);

    await waitFor(() => expect(api.setSecret).toHaveBeenCalledWith(
      "gemini/google/default", "new-gemini-key", expect.any(String),
    ));
    expect(api.probeServiceProvider).toHaveBeenCalledWith(
      "gemini/google/default", expect.any(String),
    );

    fireEvent.click(screen.getAllByRole("button", { name: "检测现有凭据" })[3]);
    await waitFor(() => expect(api.probeServiceProvider).toHaveBeenCalledWith(
      "agent/deepseek/default", expect.any(String),
    ));
  });

  it("keeps image routing settings independent from credential cards", async () => {
    const api = {
      settings: vi.fn().mockResolvedValue({ blender_path: "C:\\Blender\\blender.exe", image_provider_priority: ["tripo3d/default", "meshy/default"], provider_probe_interval_seconds: 300, agent_model: "deepseek-v4-flash" }),
      serviceProviders: vi.fn().mockResolvedValue(providers),
      localProviders: vi.fn().mockResolvedValue(localProviders),
      refreshLocalProviders: vi.fn().mockResolvedValue(localProviders),
      refreshServiceProviders: vi.fn().mockResolvedValue(providers),
      probeServiceProvider: vi.fn().mockResolvedValue(providers),
      updateSettings: vi.fn().mockResolvedValue({}),
      setSecret: vi.fn(),
    } as unknown as ApiClient;

    render(<SettingsDialog api={api} onClose={vi.fn()} />);
    await screen.findByText("seedream_v5 · 可用");
    expect(screen.getByLabelText("Blender 可执行文件")).toHaveValue("C:\\Blender\\blender.exe");
    expect(screen.getByLabelText("Agent 模型")).toHaveValue("deepseek-v4-flash");
    fireEvent.change(screen.getByLabelText("Agent 模型"), { target: { value: "qwen3-vl:4b" } });
    fireEvent.change(screen.getByLabelText("文生图执行后端"), { target: { value: "local" } });
    fireEvent.change(screen.getByLabelText("图生 3D 执行后端"), { target: { value: "remote" } });
    fireEvent.change(screen.getByLabelText("优先顺序"), { target: { value: "meshy" } });
    fireEvent.click(screen.getByRole("button", { name: "保存设置" }));

    await waitFor(() => expect(api.updateSettings).toHaveBeenCalledWith(
      expect.objectContaining({
        blender_path: "C:\\Blender\\blender.exe",
        image_provider_priority: ["meshy/default", "tripo3d/default"],
        agent_model: "qwen3-vl:4b",
        image_generation_backend: "local",
        model3d_generation_backend: "remote",
      }),
      expect.any(String),
    ));
  });

  it("defaults new Agent and generation policy to local-first choices and refreshes safely", async () => {
    const api = {
      settings: vi.fn().mockResolvedValue({}),
      serviceProviders: vi.fn().mockResolvedValue(providers),
      localProviders: vi.fn().mockResolvedValue(localProviders),
      refreshLocalProviders: vi.fn().mockResolvedValue(localProviders),
      refreshServiceProviders: vi.fn().mockResolvedValue(providers),
      probeServiceProvider: vi.fn().mockResolvedValue(providers),
      updateSettings: vi.fn().mockResolvedValue({}),
      setSecret: vi.fn(),
    } as unknown as ApiClient;

    render(<SettingsDialog api={api} onClose={vi.fn()} />);
    await screen.findByText("qwen3-vl:8b · 可用");
    expect(screen.getByLabelText("Agent 模型")).toHaveValue("qwen3-vl:8b");
    expect(screen.getByLabelText("文生图执行后端")).toHaveValue("auto");
    expect(screen.getByLabelText("图生 3D 执行后端")).toHaveValue("auto");

    fireEvent.click(screen.getByRole("button", { name: "检测本地模型" }));
    await waitFor(() => expect(api.refreshLocalProviders).toHaveBeenCalledWith(expect.any(String)));
    expect(await screen.findByText("本地模型状态已更新；检测不会下载模型，也不会启动生成任务。")).toBeInTheDocument();
  });
});
