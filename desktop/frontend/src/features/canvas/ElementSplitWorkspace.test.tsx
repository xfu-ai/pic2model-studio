import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ElementSplitWorkspace } from "./ElementSplitWorkspace";

describe("ElementSplitWorkspace", () => {
  afterEach(() => document.body.replaceChildren());
  it("restores the original source, generation, target-selection and export station", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:source") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const api = { assets: vi.fn().mockResolvedValue([{ id: "source", name: "source.png", asset_type: "source_image", is_current: true, metadata: { width: 100, height: 80 } }]), assetContent: vi.fn().mockResolvedValue(new Blob(["image"])) };
    render(<ElementSplitWorkspace projectId="project-1" api={api as never} onQueued={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("img", { name: "元素拆分源图" })).toBeVisible());
    expect(screen.getByRole("button", { name: "场景自动拆分" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "角色自动拆分" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "输出目标物体" })).toBeDisabled();
    expect(screen.getByText("目标物体（切片）")).toBeVisible();
  });

  it("keeps its recovered source until current asset is explicitly loaded and can restore it", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:source") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const previous = { id: "previous", name: "previous.png", asset_type: "source_image", is_current: false, metadata: { width: 100, height: 80 } };
    const current = { id: "current", name: "current.png", asset_type: "generated_image", is_current: true, metadata: { width: 100, height: 80 } };
    const api = {
      assets: vi.fn().mockResolvedValue([current, previous]),
      assetContent: vi.fn().mockResolvedValue(new Blob(["image"])),
    };
    render(<ElementSplitWorkspace
      projectId="project-1"
      api={api as never}
      onQueued={vi.fn()}
      workflowContext={{
        source_asset_id: previous.id,
        split_result_asset_id: null,
        target_crop_asset_id: null,
        selection_rect: { x: 1, y: 2, width: 3, height: 4 },
        prompt: "saved prompt",
        job_id: null,
      }}
      onWorkflowContextChange={vi.fn()}
    />);

    expect(await screen.findByAltText("元素拆分源图")).toBeVisible();
    expect(api.assetContent).toHaveBeenLastCalledWith("project-1", previous.id);
    fireEvent.click(screen.getByRole("button", { name: "加载当前资产" }));
    await waitFor(() => expect(api.assetContent).toHaveBeenLastCalledWith("project-1", current.id));
    fireEvent.click(screen.getByRole("button", { name: "恢复加载前状态" }));
    await waitFor(() => expect(api.assetContent).toHaveBeenLastCalledWith("project-1", previous.id));
    expect(screen.getByDisplayValue("saved prompt")).toBeVisible();
  });

  it("treats a current generated image as the source instead of following its prompt parent", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:generated") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const prompt = { id: "prompt-parent", name: "prompt.txt", asset_type: "prompt", is_current: false, metadata: {} };
    const generated = { id: "generated", name: "generated.png", asset_type: "generated_image", parent_asset_id: prompt.id, is_current: true, metadata: { width: 100, height: 80 } };
    const api = {
      assets: vi.fn().mockResolvedValue([generated, prompt]),
      assetContent: vi.fn().mockResolvedValue(new Blob(["image"])),
    };
    render(<ElementSplitWorkspace projectId="project-1" api={api as never} onQueued={vi.fn()} />);

    expect(await screen.findByAltText("元素拆分源图")).toBeVisible();
    expect(api.assetContent).toHaveBeenCalledWith("project-1", generated.id);
    expect(api.assetContent).not.toHaveBeenCalledWith("project-1", prompt.id);
  });
});
