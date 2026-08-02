import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ImportImageAction } from "./ImportImageAction";

describe("ImportImageAction", () => {
  afterEach(() => document.body.replaceChildren());
  it("uses only a project-bound native capability then sets the backend asset current", async () => {
    const host = { chooseImportImage: vi.fn().mockResolvedValue("image-capability") };
    const api = { importImage: vi.fn().mockResolvedValue({ id: "asset-1" }), setCurrentAsset: vi.fn().mockResolvedValue({ id: "asset-1" }) };
    const onImported = vi.fn();
    render(<ImportImageAction projectId="project-1" host={host as never} api={api as never} onImported={onImported} />);
    fireEvent.click(screen.getByRole("button", { name: "Choose image" }));
    await waitFor(() => expect(api.importImage).toHaveBeenCalledWith("project-1", "image-capability", expect.stringMatching(/^asset-import-/)));
    expect(api.setCurrentAsset).toHaveBeenCalledWith("project-1", "asset-1", expect.stringMatching(/^asset-import-/));
    expect(JSON.stringify(api.importImage.mock.calls)).not.toContain(":\\\\");
    expect(onImported).toHaveBeenCalledOnce();
  });

  it("keeps a caller-provided image-selection label while invoking the native chooser", async () => {
    const host = { chooseImportImage: vi.fn().mockResolvedValue(null) };
    render(<ImportImageAction projectId="project-1" host={host as never} api={{} as never} onImported={vi.fn()} label="选择图片" />);
    fireEvent.click(screen.getByRole("button", { name: "选择图片" }));
    await waitFor(() => expect(host.chooseImportImage).toHaveBeenCalledWith("project-1"));
  });

  it("shows a recoverable error when the native chooser cannot open", async () => {
    const host = { chooseImportImage: vi.fn().mockRejectedValue(new Error("dialog failed")) };
    render(<ImportImageAction projectId="project-1" host={host as never} api={{} as never} onImported={vi.fn()} label="选择图片" />);
    fireEvent.click(screen.getByRole("button", { name: "选择图片" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("无法打开或导入图片，请重试。");
    expect(screen.getByRole("button", { name: "选择图片" })).toBeEnabled();
  });
});
