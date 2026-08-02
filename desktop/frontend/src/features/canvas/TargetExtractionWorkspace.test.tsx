import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_WORKSPACE } from "../../shared/state/uiStore";
import { TargetExtractionWorkspace } from "./TargetExtractionWorkspace";

const source = {
  id: "source-1",
  name: "source.png",
  asset_type: "source_image",
  is_current: true,
  metadata: { width: 100, height: 80 },
};

describe("TargetExtractionWorkspace", () => {
  afterEach(() => document.body.replaceChildren());

  it("opens the native image chooser and imports the selected source", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:selected") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const imported = { ...source, id: "imported-1", name: "selected.png", is_current: false };
    const host = { chooseImportImage: vi.fn().mockResolvedValue("image-capability") };
    const api = {
      assets: vi.fn().mockResolvedValue([]),
      selections: vi.fn().mockResolvedValue([]),
      importImage: vi.fn().mockResolvedValue(imported),
      assetContent: vi.fn().mockResolvedValue(new Blob(["image"])),
    };
    render(<TargetExtractionWorkspace
      projectId="project-1"
      api={api as never}
      host={host as never}
      workflowContext={DEFAULT_WORKSPACE.workflow_contexts.target_extract}
      onWorkflowContextChange={vi.fn()}
      onContinueToMultiview={vi.fn()}
    />);

    fireEvent.click(screen.getByRole("button", { name: "选择图片" }));
    await waitFor(() => expect(host.chooseImportImage).toHaveBeenCalledWith("project-1"));
    await waitFor(() => expect(api.importImage).toHaveBeenCalledWith(
      "project-1",
      "image-capability",
      expect.stringMatching(/^target-extract-import-/),
    ));
    expect(screen.getByText(/图片已导入并作为本页来源/)).toBeVisible();
  });

  it("loads the unified direct extraction surface and explains its intent", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:source") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const api = {
      assets: vi.fn().mockResolvedValue([source]),
      assetContent: vi.fn().mockResolvedValue(new Blob(["image"])),
      selections: vi.fn().mockResolvedValue([]),
    };
    render(<TargetExtractionWorkspace
      projectId="project-1"
      api={api as never}
      workflowContext={{ ...DEFAULT_WORKSPACE.workflow_contexts.target_extract, source_asset_id: source.id, stage: "select_target" }}
      onWorkflowContextChange={vi.fn()}
      onContinueToMultiview={vi.fn()}
    />);

    expect((await screen.findAllByAltText("source.png"))[0]).toBeVisible();
    expect(screen.getByRole("heading", { name: "提取可建模主体" })).toBeVisible();
    expect(screen.getByRole("radio", { name: /直接框选目标/ })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText(/这不是普通截图/)).toBeVisible();
    expect(screen.getByRole("button", { name: "生成独立目标图" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "仅裁切选区（本地）" })).toBeDisabled();
  });

  it("binds direct extraction to a confirmed selection, approval, and controlled job", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:source") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const onWorkflowContextChange = vi.fn();
    const api = {
      assets: vi.fn().mockResolvedValue([source]),
      assetContent: vi.fn().mockResolvedValue(new Blob(["image"])),
      selections: vi.fn().mockResolvedValue([]),
      saveSelection: vi.fn().mockResolvedValue({ id: "selection-1", revision: 1, status: "draft", rects: [{ x: 2, y: 3, width: 40, height: 50 }] }),
      confirmSelection: vi.fn().mockResolvedValue({ id: "selection-1", revision: 2, status: "confirmed", rects: [{ x: 2, y: 3, width: 40, height: 50 }] }),
      savePromptVersion: vi.fn().mockResolvedValue({ asset: { id: "prompt-1" } }),
      invokeTool: vi.fn().mockResolvedValue({ status: "awaiting_ui_action", ui_action: { action_id: "approval-1" } }),
      decideApproval: vi.fn().mockResolvedValue({ status: "queued", job: { job_id: "job-1" } }),
      job: vi.fn().mockResolvedValue({ status: "running", stage: "provider", progress: 25 }),
    };
    render(<TargetExtractionWorkspace
      projectId="project-1"
      api={api as never}
      workflowContext={{ ...DEFAULT_WORKSPACE.workflow_contexts.target_extract, source_asset_id: source.id, stage: "select_target" }}
      onWorkflowContextChange={onWorkflowContextChange}
      onContinueToMultiview={vi.fn()}
    />);

    await screen.findAllByAltText("source.png");
    fireEvent.change(screen.getByLabelText("x"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("y"), { target: { value: "3" } });
    fireEvent.change(screen.getByLabelText("width"), { target: { value: "40" } });
    fireEvent.change(screen.getByLabelText("height"), { target: { value: "50" } });
    fireEvent.click(screen.getByRole("button", { name: "生成独立目标图" }));

    await waitFor(() => expect(api.invokeTool).toHaveBeenCalledWith(
      "project-1",
      "element.split",
      expect.objectContaining({
        source_asset_id: source.id,
        selection_id: "selection-1",
        prompt_asset_id: "prompt-1",
        split_mode: "boxsplit",
      }),
      expect.stringMatching(/^target-extract-propose-/),
      { providerProfile: "image-generation/auto" },
    ));
    expect(await screen.findByText("确认外部图像生成")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /批准并提交/ }));
    await waitFor(() => expect(api.decideApproval).toHaveBeenCalledWith(
      "project-1",
      "approval-1",
      true,
      expect.stringMatching(/^target-extract-approve-/),
    ));
    expect(onWorkflowContextChange).toHaveBeenCalledWith(expect.objectContaining({
      stage: "generating",
      job_id: "job-1",
    }));
  });

  it("prepares an AI breakdown without creating a fake full-image selection", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:source") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const api = {
      assets: vi.fn().mockResolvedValue([source]),
      assetContent: vi.fn().mockResolvedValue(new Blob(["image"])),
      savePromptVersion: vi.fn().mockResolvedValue({ asset: { id: "breakdown-prompt" } }),
      invokeTool: vi.fn().mockResolvedValue({ status: "awaiting_ui_action", ui_action: { action_id: "breakdown-approval" } }),
    };
    render(<TargetExtractionWorkspace
      projectId="project-1"
      api={api as never}
      workflowContext={{
        ...DEFAULT_WORKSPACE.workflow_contexts.target_extract,
        method: "breakdown",
        stage: "configure_breakdown",
        source_asset_id: source.id,
        preset: "character",
      }}
      onWorkflowContextChange={vi.fn()}
      onContinueToMultiview={vi.fn()}
    />);

    await screen.findAllByAltText("source.png");
    fireEvent.click(screen.getByRole("button", { name: "生成部件拆解图" }));
    await waitFor(() => expect(api.invokeTool).toHaveBeenCalled());
    const argumentsValue = api.invokeTool.mock.calls[0][2];
    expect(argumentsValue).toEqual(expect.objectContaining({
      source_asset_id: source.id,
      prompt_asset_id: "breakdown-prompt",
      split_mode: "element",
    }));
    expect(argumentsValue).not.toHaveProperty("selection_id");
    expect(screen.getByText("仅发送无选框的受管来源图和当前拆解要求。")).toBeVisible();
  });

  it("crops multiple parts locally from one managed breakdown board", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:image") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const board = { ...source, id: "board-1", name: "breakdown.png", asset_type: "generated_image", is_current: false };
    const part = { ...source, id: "part-2", name: "part-2.png", asset_type: "crop", is_current: false };
    const onWorkflowContextChange = vi.fn();
    const onContinueToMultiview = vi.fn();
    const api = {
      assets: vi.fn().mockResolvedValue([source, board]),
      assetContent: vi.fn().mockResolvedValue(new Blob(["image"])),
      selections: vi.fn().mockResolvedValue([]),
      saveSelection: vi.fn().mockResolvedValue({ id: "board-selection", revision: 1, status: "draft", rects: [{ x: 5, y: 5, width: 30, height: 40 }] }),
      confirmSelection: vi.fn().mockResolvedValue({ id: "board-selection", revision: 2, status: "confirmed", rects: [{ x: 5, y: 5, width: 30, height: 40 }] }),
      cropSelection: vi.fn().mockResolvedValue([part]),
    };
    render(<TargetExtractionWorkspace
      projectId="project-1"
      api={api as never}
      workflowContext={{
        ...DEFAULT_WORKSPACE.workflow_contexts.target_extract,
        method: "breakdown",
        stage: "select_breakdown_part",
        source_asset_id: source.id,
        breakdown_asset_id: board.id,
        result_asset_ids: ["part-1"],
        active_result_asset_id: "part-1",
      }}
      onWorkflowContextChange={onWorkflowContextChange}
      onContinueToMultiview={onContinueToMultiview}
    />);

    expect((await screen.findAllByAltText("breakdown.png"))[0]).toBeVisible();
    fireEvent.change(screen.getByLabelText("部件 x"), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText("部件 y"), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText("部件 width"), { target: { value: "30" } });
    fireEvent.change(screen.getByLabelText("部件 height"), { target: { value: "40" } });
    fireEvent.click(screen.getByRole("button", { name: "裁出选中部件" }));
    await waitFor(() => expect(api.cropSelection).toHaveBeenCalledWith(
      "project-1",
      "board-selection",
      expect.stringMatching(/^target-extract-crop-/),
    ));
    expect(onWorkflowContextChange).toHaveBeenCalledWith(expect.objectContaining({
      stage: "result",
      result_asset_ids: ["part-1", "part-2"],
      active_result_asset_id: "part-2",
    }));
    fireEvent.click(await screen.findByRole("button", { name: "进入三视图制作" }));
    expect(onContinueToMultiview).toHaveBeenCalledWith("part-2");
  });
});
