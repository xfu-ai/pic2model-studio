import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SelectionWorkspace } from "./SelectionWorkspace";

describe("SelectionWorkspace", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    document.body.replaceChildren();
  });
  it("keeps normal selection focused on local crop instead of mixing in AI extraction", () => {
    const api = { assets: vi.fn().mockResolvedValue([]) };
    render(<SelectionWorkspace projectId="project-1" api={api as never} onDone={vi.fn()} onQueued={vi.fn()} />);
    expect(screen.getByRole("heading", { name: "拖框选择要保留的图片范围" })).toBeVisible();
    expect(screen.getByRole("button", { name: "返回当前图片" })).toBeVisible();
    expect(screen.getByRole("button", { name: "裁切并返回主页" })).toBeDisabled();
    expect(screen.queryByLabelText("工作流")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "生成拆分图" })).not.toBeInTheDocument();
  });

  it("keeps an undo and redo history for precise selection edits", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:source") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const api = {
      assets: vi.fn().mockResolvedValue([{ id: "image-1", name: "source.png", asset_type: "source_image", is_current: true, metadata: { width: 100, height: 80 } }]),
      assetContent: vi.fn().mockResolvedValue(new Blob(["image"])),
      selections: vi.fn().mockResolvedValue([]),
    };
    render(<SelectionWorkspace projectId="project-1" api={api as never} onDone={vi.fn()} onQueued={vi.fn()} />);
    await waitFor(() => expect(screen.getByLabelText("x")).toBeVisible());
    fireEvent.click(screen.getByRole("button", { name: "放大画布" }));
    expect(screen.getByText("125%")).toBeVisible();
    fireEvent.change(screen.getByLabelText("x"), { target: { value: "25" } });
    expect(screen.getByLabelText("x")).toHaveValue(25);
    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    expect(screen.getByLabelText("x")).toHaveValue(0);
    fireEvent.click(screen.getByRole("button", { name: "重做" }));
    expect(screen.getByLabelText("x")).toHaveValue(25);
  });

  it("uses the pointer-up position for fast drags and ignores plain clicks", async () => {
    vi.stubGlobal("PointerEvent", MouseEvent);
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:source") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const api = {
      assets: vi.fn().mockResolvedValue([{ id: "image-1", name: "source.png", asset_type: "source_image", is_current: true, metadata: { width: 100, height: 80 } }]),
      assetContent: vi.fn().mockResolvedValue(new Blob(["image"])),
      selections: vi.fn().mockResolvedValue([]),
    };
    render(<SelectionWorkspace projectId="project-1" api={api as never} onDone={vi.fn()} onQueued={vi.fn()} />);
    const source = await screen.findByAltText("source.png");
    const canvas = source.parentElement as HTMLDivElement;
    Object.defineProperties(source, {
      naturalWidth: { configurable: true, value: 100 },
      naturalHeight: { configurable: true, value: 80 },
      clientWidth: { configurable: true, value: 500 },
      clientHeight: { configurable: true, value: 400 },
      getBoundingClientRect: { configurable: true, value: () => ({ left: 0, top: 0, width: 500, height: 400, right: 500, bottom: 400 }) },
    });
    Object.defineProperties(canvas, {
      setPointerCapture: { configurable: true, value: vi.fn() },
      hasPointerCapture: { configurable: true, value: vi.fn().mockReturnValue(true) },
      releasePointerCapture: { configurable: true, value: vi.fn() },
    });
    fireEvent.load(source);

    // A fast drag may contain no intermediate pointermove event. Pointer-up
    // must still create the full final rectangle rather than the 1 px seed.
    fireEvent.pointerDown(source, { button: 0, pointerId: 1, clientX: 50, clientY: 50 });
    fireEvent.pointerUp(source, { button: 0, pointerId: 1, clientX: 300, clientY: 250 });
    expect(screen.getByLabelText("x")).toHaveValue(10);
    expect(screen.getByLabelText("y")).toHaveValue(10);
    expect(screen.getByLabelText("width")).toHaveValue(50);
    expect(screen.getByLabelText("height")).toHaveValue(40);
    expect(source).toHaveAttribute("draggable", "false");

    // A click without a drag must preserve the useful selection.
    fireEvent.pointerDown(source, { button: 0, pointerId: 2, clientX: 400, clientY: 300 });
    fireEvent.pointerUp(source, { button: 0, pointerId: 2, clientX: 400, clientY: 300 });
    expect(screen.getByLabelText("width")).toHaveValue(50);
    expect(screen.getByLabelText("height")).toHaveValue(40);
    expect(screen.getByText("按住鼠标左键并拖动，才能创建新的选区。")).toBeVisible();
  });

  it("keeps the source chooser and result region visible in the box-split workspace", async () => {
    const api = { assets: vi.fn().mockResolvedValue([]) };
    render(<SelectionWorkspace projectId="project-1" api={api as never} onDone={vi.fn()} onQueued={vi.fn()} initialWorkflow="boxsplit" />);
    expect(screen.getByRole("button", { name: "选择图片（系统文件）" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "框选拆分结果" })).toBeVisible();
    expect(screen.getByText("等待生成结果")).toBeVisible();
  });

  it("clears only the box-split workspace source without deleting its managed asset", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:source") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const onWorkflowContextChange = vi.fn();
    const api = { assets: vi.fn().mockResolvedValue([{ id: "image-1", name: "source.png", asset_type: "source_image", is_current: true, metadata: { width: 100, height: 80 } }]), assetContent: vi.fn().mockResolvedValue(new Blob(["image"])), selections: vi.fn().mockResolvedValue([]) };
    render(<SelectionWorkspace projectId="project-1" api={api as never} onDone={vi.fn()} onQueued={vi.fn()} initialWorkflow="boxsplit" onWorkflowContextChange={onWorkflowContextChange} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "清空图片" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "清空图片" }));
    expect(onWorkflowContextChange).toHaveBeenCalledWith(expect.objectContaining({ source_asset_id: null, result_asset_id: null }));
    expect(api.assets).toHaveBeenCalledTimes(1);
  });

  it("restores the box-split source before loading current asset and supports one-step recovery", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:source") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const previous = { id: "previous", name: "previous.png", asset_type: "source_image", is_current: false, metadata: { width: 100, height: 80 } };
    const current = { id: "current", name: "current.png", asset_type: "generated_image", is_current: true, metadata: { width: 100, height: 80 } };
    const previousSelection = { id: "selection-previous", asset_id: previous.id, status: "confirmed", revision: 2, rects: [{ x: 5, y: 6, width: 70, height: 60 }] };
    const api = {
      assets: vi.fn().mockResolvedValue([current, previous]),
      assetContent: vi.fn().mockResolvedValue(new Blob(["image"])),
      selections: vi.fn(async (_projectId: string, assetId: string) => assetId === previous.id ? [previousSelection] : []),
    };
    render(<SelectionWorkspace
      projectId="project-1"
      api={api as never}
      onDone={vi.fn()}
      onQueued={vi.fn()}
      initialWorkflow="boxsplit"
      workflowContext={{
        source_asset_id: previous.id,
        selection_id: previousSelection.id,
        result_asset_id: null,
        workflow: "boxsplit",
        prompt: "saved prompt",
        job_id: null,
      }}
      onWorkflowContextChange={vi.fn()}
    />);

    expect(await screen.findByAltText("previous.png")).toBeVisible();
    expect(screen.getByLabelText("x")).toHaveValue(5);
    fireEvent.click(screen.getByRole("button", { name: "加载当前资产" }));
    expect(await screen.findByAltText("current.png")).toBeVisible();
    expect(screen.getByLabelText("x")).toHaveValue(0);
    fireEvent.click(screen.getByRole("button", { name: "恢复加载前状态" }));
    expect(await screen.findByAltText("previous.png")).toBeVisible();
    expect(screen.getByLabelText("x")).toHaveValue(5);
  });

  it("does not load prompt or analysis assets as box-split images", async () => {
    const api = {
      assets: vi.fn().mockResolvedValue([
        { id: "analysis", name: "analysis.json", asset_type: "analysis", is_current: true, metadata: {} },
        { id: "prompt", name: "prompt.txt", asset_type: "prompt", is_current: false, metadata: {} },
      ]),
      assetContent: vi.fn(),
      selections: vi.fn(),
    };
    render(<SelectionWorkspace
      projectId="project-1"
      api={api as never}
      onDone={vi.fn()}
      onQueued={vi.fn()}
      initialWorkflow="boxsplit"
      onWorkflowContextChange={vi.fn()}
    />);

    await waitFor(() => expect(api.assets).toHaveBeenCalled());
    expect(screen.queryByRole("img", { name: "框选拆分源图" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "加载当前资产" }));
    expect(await screen.findByText("项目当前资产不是可用于框选拆分的图片。")).toBeVisible();
    expect(api.assetContent).not.toHaveBeenCalled();
    expect(api.selections).not.toHaveBeenCalled();
  });

  it("consumes a succeeded box-split job only once", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:result") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const source = { id: "source", name: "source.png", asset_type: "source_image", is_current: true, metadata: { width: 100, height: 80 } };
    const result = { id: "result", name: "result.png", asset_type: "generated_image", is_current: false, metadata: { width: 100, height: 80 } };
    const api = {
      assets: vi.fn().mockResolvedValue([source, result]),
      assetContent: vi.fn().mockResolvedValue(new Blob(["image"])),
      selections: vi.fn().mockResolvedValue([]),
      job: vi.fn().mockResolvedValue({ status: "succeeded", output_asset_ids: [result.id] }),
      setCurrentAsset: vi.fn().mockResolvedValue({}),
    };
    render(<SelectionWorkspace
      projectId="project-1"
      api={api as never}
      onDone={vi.fn()}
      onQueued={vi.fn()}
      initialWorkflow="boxsplit"
      workflowContext={{
        source_asset_id: source.id,
        selection_id: null,
        result_asset_id: null,
        workflow: "boxsplit",
        prompt: "",
        job_id: "job-1",
      }}
      onWorkflowContextChange={vi.fn()}
    />);

    await waitFor(() => expect(api.setCurrentAsset).toHaveBeenCalledTimes(1));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(api.job).toHaveBeenCalledTimes(1);
    expect(api.setCurrentAsset).toHaveBeenCalledTimes(1);
    expect(await screen.findByAltText("result.png")).toBeVisible();
  });
});
