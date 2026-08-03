import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DiagnosticsPanel } from "./DiagnosticsPanel";

describe("DiagnosticsPanel", () => {
  afterEach(() => document.body.replaceChildren());

  it("confirms the preview manifest through a native capability without exposing a filesystem path", async () => {
    const host = { chooseDiagnosticsExportDirectory: vi.fn().mockResolvedValue("diagnostic-capability") };
    const api = {
      diagnosticsPreview: vi.fn().mockResolvedValue({
        manifest: { build: { name: "图模工坊" }, files: [{ name: "app.log", size: 256 }] },
        manifest_hash: "manifest-sha256",
        estimated_size: 256,
      }),
      exportDiagnostics: vi.fn().mockResolvedValue({ path: "diagnostics.zip", manifest_hash: "manifest-sha256" }),
    };

    const onClose = vi.fn();
    render(<DiagnosticsPanel projectId="project-1" host={host as never} api={api as never} onClose={onClose} />);
    await waitFor(() => expect(api.diagnosticsPreview).toHaveBeenCalledWith(
      "project-1", expect.stringMatching(/^diagnostics-/),
    ));
    expect(screen.getByRole("dialog", { name: "导出诊断支持包" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "导出支持包" }));
    await waitFor(() => expect(api.exportDiagnostics).toHaveBeenCalledWith(
      "project-1", "diagnostic-capability", "manifest-sha256", expect.stringMatching(/^diagnostics-/),
    ));
    expect(JSON.stringify(host.chooseDiagnosticsExportDirectory.mock.calls)).not.toContain(":\\");
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
