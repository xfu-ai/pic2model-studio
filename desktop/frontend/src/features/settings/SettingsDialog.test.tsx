import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ApiClient, ServiceProviderStatusDto } from "../../shared/api/client";
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
      }),
      serviceProviders: vi.fn().mockResolvedValue(providers),
      refreshServiceProviders: vi.fn().mockResolvedValue(providers),
      probeServiceProvider: vi.fn().mockResolvedValue(providers),
      updateSettings: vi.fn().mockResolvedValue({}),
      setSecret: vi.fn().mockResolvedValue({ configured: true, mask: "••••1234" }),
    } as unknown as ApiClient;

    render(<SettingsDialog api={api} onClose={vi.fn()} />);

    expect(await screen.findByText("seedream_v5 · 可用")).toBeInTheDocument();
    expect(screen.getByText("gemini-flash-lite-latest · 凭据无效")).toBeInTheDocument();
    expect(screen.getByText("deepseek-v4-flash · 可用")).toBeInTheDocument();
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
      settings: vi.fn().mockResolvedValue({ blender_path: "C:\\Blender\\blender.exe", image_provider_priority: ["tripo3d/default", "meshy/default"], provider_probe_interval_seconds: 300 }),
      serviceProviders: vi.fn().mockResolvedValue(providers),
      refreshServiceProviders: vi.fn().mockResolvedValue(providers),
      probeServiceProvider: vi.fn().mockResolvedValue(providers),
      updateSettings: vi.fn().mockResolvedValue({}),
      setSecret: vi.fn(),
    } as unknown as ApiClient;

    render(<SettingsDialog api={api} onClose={vi.fn()} />);
    await screen.findByText("seedream_v5 · 可用");
    expect(screen.getByLabelText("Blender 可执行文件")).toHaveValue("C:\\Blender\\blender.exe");
    fireEvent.change(screen.getByLabelText("优先顺序"), { target: { value: "meshy" } });
    fireEvent.click(screen.getByRole("button", { name: "保存设置" }));

    await waitFor(() => expect(api.updateSettings).toHaveBeenCalledWith(
      expect.objectContaining({
        blender_path: "C:\\Blender\\blender.exe",
        image_provider_priority: ["meshy/default", "tripo3d/default"],
      }),
      expect.any(String),
    ));
  });
});
