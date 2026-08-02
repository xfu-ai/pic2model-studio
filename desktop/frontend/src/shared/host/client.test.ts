import { describe, expect, it, vi } from "vitest";
import { HostClient } from "./client";

const mocks = vi.hoisted(() => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke }));

describe("HostClient", () => {
  it("uses typed host commands and never accepts a path argument", async () => {
    mocks.invoke.mockResolvedValue("capability-1");
    const host = new HostClient();
    await expect(host.chooseProjectDirectory()).resolves.toBe("capability-1");
    await expect(host.chooseRecentProject("project-1")).resolves.toBe("capability-1");
    await expect(host.chooseImportImage("project-1")).resolves.toBe("capability-1");
    await expect(host.chooseImportGlb("project-1")).resolves.toBe("capability-1");
    await expect(host.chooseExportDirectory("project-1")).resolves.toBe("capability-1");
    await expect(host.chooseDiagnosticsExportDirectory("project-1")).resolves.toBe("capability-1");
    await expect(host.notifyJobTerminal("succeeded")).resolves.toBe("capability-1");
    await expect(host.openModelBrowser([1, 2, 3])).resolves.toBe("capability-1");
    expect(mocks.invoke).toHaveBeenNthCalledWith(1, "choose_project_directory");
    expect(mocks.invoke).toHaveBeenNthCalledWith(2, "choose_recent_project", { recentProjectId: "project-1" });
    expect(mocks.invoke).toHaveBeenNthCalledWith(3, "choose_import_image", { projectId: "project-1" });
    expect(mocks.invoke).toHaveBeenNthCalledWith(4, "choose_import_glb", { projectId: "project-1" });
    expect(mocks.invoke).toHaveBeenNthCalledWith(5, "choose_export_directory", { projectId: "project-1" });
    expect(mocks.invoke).toHaveBeenNthCalledWith(6, "choose_diagnostics_export_directory", { projectId: "project-1" });
    expect(mocks.invoke).toHaveBeenNthCalledWith(7, "notify_job_terminal", { status: "succeeded" });
    expect(mocks.invoke).toHaveBeenNthCalledWith(8, "open_model_browser_preview", { bytes: [1, 2, 3] });
  });
});
