import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProjectPackageActions } from "./ProjectPackageActions";

const project = { id: "project-1", name: "Demo", current_asset_id: null, root_state: "available" };
describe("ProjectPackageActions", () => {
  afterEach(() => document.body.replaceChildren());
  it("exports through a project-bound native capability without forwarding a path", async () => {
    const host = { chooseExportDirectory: vi.fn().mockResolvedValue("export-capability") };
    const api = { exportProject: vi.fn().mockResolvedValue({ path: "Demo-backup.formweaver" }) };
    render(<ProjectPackageActions project={project} host={host as never} api={api as never} onProject={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "导出项目备份…" }));
    await waitFor(() => expect(api.exportProject).toHaveBeenCalledWith("project-1", "export-capability", expect.stringMatching(/^package-/)));
    expect(await screen.findByRole("status")).toHaveTextContent("Demo-backup.formweaver");
    expect(JSON.stringify(api.exportProject.mock.calls)).not.toContain(":\\\\");
  });

  it("shows immediate feedback while choosing a location and creating a backup", async () => {
    let chooseExportDirectory!: (capability: string) => void;
    let finishExport!: (result: { path: string }) => void;
    const host = {
      chooseExportDirectory: vi.fn().mockImplementation(
        () => new Promise<string>((resolve) => { chooseExportDirectory = resolve; }),
      ),
    };
    const api = {
      exportProject: vi.fn().mockImplementation(
        () => new Promise<{ path: string }>((resolve) => { finishExport = resolve; }),
      ),
    };
    render(<ProjectPackageActions project={project} host={host as never} api={api as never} onProject={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "导出项目备份…" }));
    expect(screen.getByRole("status")).toHaveTextContent("正在选择备份保存位置…");

    chooseExportDirectory("export-capability");
    await waitFor(() => expect(api.exportProject).toHaveBeenCalled());
    expect(screen.getByRole("status")).toHaveTextContent("正在导出项目备份，请稍候…");

    finishExport({ path: "Demo-backup.formweaver" });
    expect(await screen.findByRole("status")).toHaveTextContent("Demo-backup.formweaver");
  });

  it("explains that a failed backup does not move the current project", async () => {
    const host = { chooseExportDirectory: vi.fn().mockResolvedValue("export-capability") };
    const api = { exportProject: vi.fn().mockRejectedValue(new Error("export failed")) };
    render(<ProjectPackageActions project={project} host={host as never} api={api as never} onProject={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "导出项目备份…" }));
    expect(await screen.findByRole("status")).toHaveTextContent("未能生成项目备份文件。导出只会复制当前项目；正在编辑的图片、模型和工作记录仍保留在原处。");
  });
});
