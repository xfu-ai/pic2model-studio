import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MULTIVIEW_BASE_PROMPT } from "../../shared/prompts/productionPrompts";
import { MultiviewWorkspace } from "./MultiviewWorkspace";

const sheet = { id: "sheet", name: "front-side-back.png", asset_type: "multiview", is_current: true, metadata: { width: 768, height: 256 } };
describe("MultiviewWorkspace", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("uses CSP-compatible blob URLs for the three crop previews", async () => {
    let objectUrlIndex = 0;
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn().mockImplementation(() => (
        objectUrlIndex++ === 0
          ? "blob:source"
          : `blob:crop-preview-${objectUrlIndex}`
      )),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
      drawImage: vi.fn(),
    } as never);
    vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation(
      (callback) => callback(new Blob(["preview"], { type: "image/jpeg" })),
    );
    vi.stubGlobal("Image", class {
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;

      set src(_value: string) {
        queueMicrotask(() => this.onload?.());
      }
    });
    const api = {
      assets: vi.fn().mockResolvedValue([sheet]),
      assetContent: vi.fn().mockResolvedValue(new Blob(["sheet"])),
    };

    render(<MultiviewWorkspace
      projectId="project-1"
      api={api as never}
      onQueued={vi.fn()}
    />);

    const previews = await screen.findAllByRole("img", { name: /预览$/ });
    expect(previews).toHaveLength(3);
    expect(previews.every((preview) => (
      preview.getAttribute("src")?.startsWith("blob:crop-preview-")
    ))).toBe(true);
  });

  it("opens the native chooser from the prominent source action and selects the import", async () => {
    const imported = {
      id: "imported",
      name: "selected.png",
      asset_type: "source_image",
      is_current: true,
      metadata: { width: 1024, height: 1024 },
    };
    const host = { chooseImportImage: vi.fn().mockResolvedValue("image-capability") };
    const api = {
      assets: vi.fn().mockResolvedValue([imported]),
      importImage: vi.fn().mockResolvedValue(imported),
      setCurrentAsset: vi.fn().mockResolvedValue(imported),
      assetContent: vi.fn().mockResolvedValue(new Blob(["image"])),
    };
    render(<MultiviewWorkspace
      projectId="project-1"
      api={api as never}
      host={host as never}
      onQueued={vi.fn()}
    />);

    const chooseButton = await screen.findByRole("button", { name: "选择图片（系统文件）" });
    expect(chooseButton).toBeVisible();
    fireEvent.click(chooseButton);
    await waitFor(() => expect(host.chooseImportImage).toHaveBeenCalledWith("project-1"));
    await waitFor(() => expect(api.importImage).toHaveBeenCalledWith(
      "project-1",
      "image-capability",
      expect.stringMatching(/^asset-import-/),
    ));
    expect(await screen.findByRole("status")).toHaveTextContent("已选择新的三视图来源图");
  });

  it("loads the authoritative project current image and can restore the previous source", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:new-candidate") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const candidate = { id: "candidate", name: "new-candidate.png", asset_type: "generated_image", is_current: false, metadata: { width: 1024, height: 1024 } };
    const robot = { id: "robot", name: "robot.png", asset_type: "generated_image", is_current: true, metadata: { width: 1024, height: 1024 } };
    const api = {
      project: vi.fn().mockResolvedValue({ id: "project-1", current_asset_id: "candidate" }),
      assets: vi.fn().mockResolvedValue([candidate, robot]),
      assetContent: vi.fn().mockResolvedValue(new Blob(["new-candidate"])),
      job: vi.fn().mockReturnValue(new Promise(() => {})),
    };
    const onWorkflowContextChange = vi.fn();
    render(<MultiviewWorkspace
      projectId="project-1"
      api={api as never}
      onQueued={vi.fn()}
      workflowContext={{
        selected: { source: "robot", front: "old-front", side: "old-side", back: "old-back" },
        regions: {
          front: { x: 0, y: 0, width: 100, height: 100 },
          side: { x: 100, y: 0, width: 100, height: 100 },
          back: { x: 200, y: 0, width: 100, height: 100 },
        },
        checks: {},
        quality_confirmed: true,
        set_id: "old-set",
        job_id: "old-job",
      }}
      onWorkflowContextChange={onWorkflowContextChange}
    />);

    await waitFor(() => expect(screen.getByRole("combobox")).toHaveValue("robot"));
    fireEvent.click(screen.getByRole("button", { name: "加载项目当前图片" }));
    await waitFor(() => expect(screen.getByRole("combobox")).toHaveValue("candidate"));
    expect(screen.getByRole("option", { name: "new-candidate.png（项目当前图片）" })).toBeVisible();
    expect(api.assetContent).toHaveBeenCalledWith("project-1", "candidate");
    await waitFor(() => expect(onWorkflowContextChange).toHaveBeenLastCalledWith(
      expect.objectContaining({
        selected: { source: "candidate", front: "candidate", side: "candidate", back: "candidate" },
        quality_confirmed: false,
        job_id: null,
      }),
    ));
    fireEvent.click(screen.getByRole("button", { name: "恢复加载前状态" }));
    await waitFor(() => expect(screen.getByRole("combobox")).toHaveValue("robot"));
  });

  it("falls back to the asset current marker when project details omit current_asset_id", async () => {
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn().mockReturnValue("blob:current"),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
    const current = {
      id: "current",
      name: "current.png",
      asset_type: "generated_image",
      is_current: true,
      metadata: { width: 100, height: 100 },
    };
    const previous = {
      id: "previous",
      name: "previous.png",
      asset_type: "source_image",
      is_current: false,
      metadata: { width: 100, height: 100 },
    };
    const api = {
      project: vi.fn().mockResolvedValue({ id: "project-1" }),
      assets: vi.fn().mockResolvedValue([current, previous]),
      assetContent: vi.fn().mockResolvedValue(new Blob(["image"])),
    };
    render(
      <MultiviewWorkspace
        projectId="project-1"
        api={api as never}
        onQueued={vi.fn()}
        workflowContext={{
          selected: { source: "previous" },
          regions: {},
          checks: {},
          quality_confirmed: false,
          set_id: null,
          job_id: null,
        }}
      />,
    );

    await waitFor(() => expect(screen.getByRole("combobox")).toHaveValue("previous"));
    fireEvent.click(screen.getByRole("button", { name: "加载项目当前图片" }));
    await waitFor(() => expect(screen.getByRole("combobox")).toHaveValue("current"));
    expect(screen.getByRole("status")).toHaveTextContent(
      "current.png 已按项目当前资产加载为本页三视图来源",
    );
  });

  it("restores a persisted confirmed crop set without rerunning the generation job", async () => {
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn().mockReturnValue("blob:sheet"),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
    const api = {
      assets: vi.fn().mockResolvedValue([sheet]),
      assetContent: vi.fn().mockResolvedValue(new Blob(["image"])),
      job: vi.fn().mockResolvedValue({
        status: "succeeded",
        output_asset_ids: ["old-front", "old-side", "old-back"],
      }),
    };
    render(
      <MultiviewWorkspace
        projectId="project-1"
        api={api as never}
        onQueued={vi.fn()}
        workflowContext={{
          selected: {
            source: "sheet",
            front: "old-front",
            side: "old-side",
            back: "old-back",
          },
          regions: {
            front: { x: 0, y: 0, width: 100, height: 100 },
            side: { x: 100, y: 0, width: 100, height: 100 },
            back: { x: 200, y: 0, width: 100, height: 100 },
          },
          checks: {},
          quality_confirmed: true,
          set_id: "old-set",
          job_id: "old-job",
        }}
      />,
    );

    expect(await screen.findByLabelText("AI 生成的独立三视图")).toBeVisible();
    expect(api.job).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("我已确认三张视图与质量")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认裁切并进入 3D 模型处理" })).toBeEnabled();
  });

  it("preserves a restored generated sheet and its crop state without reapplying its completed job", async () => {
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn().mockReturnValue("blob:sheet"),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
    const api = {
      assets: vi.fn().mockResolvedValue([sheet]),
      assetContent: vi.fn().mockResolvedValue(new Blob(["image"])),
      job: vi.fn(),
    };
    const { container } = render(
      <MultiviewWorkspace
        projectId="project-1"
        api={api as never}
        onQueued={vi.fn()}
        workflowContext={{
          selected: {
            source: "sheet",
            front: "sheet",
            side: "sheet",
            back: "sheet",
          },
          regions: {
            front: { x: 11, y: 12, width: 200, height: 220 },
            side: { x: 250, y: 13, width: 201, height: 221 },
            back: { x: 500, y: 14, width: 202, height: 222 },
          },
          checks: {},
          quality_confirmed: true,
          set_id: null,
          job_id: "sheet-job",
        }}
      />,
    );

    await screen.findByAltText("三视图拼图");
    expect(api.job).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("我已确认三张视图与质量")).not.toBeInTheDocument();
    expect(
      (container.querySelector('[data-view="front"]') as HTMLElement).style
        .left,
    ).toBe("11px");
  });

  it("shows an immediate job refresh only while a generated sheet is still pending", async () => {
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn().mockReturnValue("blob:sheet"),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
    const api = {
      assets: vi.fn().mockResolvedValue([sheet]),
      assetContent: vi.fn().mockResolvedValue(new Blob(["image"])),
      job: vi.fn().mockResolvedValue({
        status: "running",
        stage: "generating",
        progress: 42,
        output_asset_ids: [],
      }),
    };
    render(
      <MultiviewWorkspace
        projectId="project-1"
        api={api as never}
        onQueued={vi.fn()}
        workflowContext={{
          selected: { source: "sheet" },
          regions: {},
          checks: {},
          quality_confirmed: false,
          set_id: null,
          job_id: "pending-sheet-job",
        }}
      />,
    );

    const refresh = await screen.findByRole("button", {
      name: "立即刷新生成进度",
    });
    expect(refresh).toBeEnabled();
    expect(
      screen.getByText("系统会自动加载完成结果，此按钮只用于立即查询生成任务。"),
    ).toBeVisible();
    await waitFor(() => expect(api.job).toHaveBeenCalledTimes(1));
    fireEvent.click(refresh);
    await waitFor(() => expect(api.job).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("status")).toHaveTextContent("三视图仍在生成：generating");
  });

  it("enables 3D submission as soon as the recovered crop regions are confirmed", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:sheet") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const api = {
      assets: vi.fn().mockResolvedValue([sheet]),
      assetContent: vi.fn().mockResolvedValue(new Blob(["image"])),
      createMultiviewSet: vi.fn().mockResolvedValue({ id: "set-1" }),
      invokeTool: vi.fn().mockImplementation((_projectId: string, tool: string) =>
        Promise.resolve(tool === "multiview.crop_views"
          ? { ok: true, output_asset_ids: ["front", "side", "back"] }
          : { ok: true, output_asset_ids: [] })),
    };
    render(<MultiviewWorkspace projectId="project-1" api={api as never} onQueued={vi.fn()} />);
    await screen.findByAltText("三视图拼图");
    expect(screen.getByRole("heading", { name: "① 来源与生成" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "③ 输出与建模" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认裁切并进入 3D 模型处理" })).toBeDisabled();
    expect(screen.queryByLabelText("我已确认三张视图与质量")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认裁切框并生成三张视图" }));
    await screen.findByRole("button", { name: "重新调整裁切框" });
    expect(screen.getByRole("button", { name: "确认裁切并进入 3D 模型处理" })).toBeEnabled();
  });

  it("moves and resizes a view region with the same direct manipulation used by other selection canvases", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:sheet") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const api = { assets: vi.fn().mockResolvedValue([sheet]), assetContent: vi.fn().mockResolvedValue(new Blob(["image"])) };
    const { container } = render(<MultiviewWorkspace projectId="project-1" api={api as never} onQueued={vi.fn()} />);
    const img = await screen.findByAltText("三视图拼图");
    Object.defineProperties(img, {
      naturalWidth: { configurable: true, value: 768 },
      naturalHeight: { configurable: true, value: 256 },
      clientWidth: { configurable: true, value: 768 },
      clientHeight: { configurable: true, value: 256 },
    });
    vi.spyOn(img, "getBoundingClientRect").mockReturnValue({
      x: 0, y: 0, left: 0, top: 0, right: 768, bottom: 256,
      width: 768, height: 256, toJSON: () => ({}),
    });
    const canvas = container.querySelector(".multiview-sheet-canvas") as HTMLDivElement;
    Object.defineProperties(canvas, {
      setPointerCapture: { configurable: true, value: vi.fn() },
      hasPointerCapture: { configurable: true, value: vi.fn().mockReturnValue(false) },
      releasePointerCapture: { configurable: true, value: vi.fn() },
    });
    const dispatchPointer = (
      target: Element,
      type: "pointerdown" | "pointermove" | "pointerup",
      init: Record<string, string | number>,
    ) => {
      const event = new Event(type, { bubbles: true, cancelable: true });
      Object.defineProperties(
        event,
        Object.fromEntries(
          Object.entries(init).map(([key, value]) => [key, { value }]),
        ),
      );
      fireEvent(target, event);
    };
    fireEvent.load(img);

    let front = container.querySelector(".multiview-region.selected") as HTMLDivElement;
    dispatchPointer(front, "pointerdown", { pointerId: 7, pointerType: "mouse", button: 0, clientX: 60, clientY: 60 });
    dispatchPointer(canvas, "pointermove", { pointerId: 7, pointerType: "mouse", clientX: 100, clientY: 60 });
    dispatchPointer(canvas, "pointerup", { pointerId: 7, pointerType: "mouse", clientX: 100, clientY: 60 });
    await waitFor(() => {
      front = container.querySelector(".multiview-region.selected") as HTMLDivElement;
      expect(front.style.left).toBe("78px");
    });

    const southeast = front.querySelector(".multiview-handle.se") as HTMLElement;
    dispatchPointer(southeast, "pointerdown", { pointerId: 8, pointerType: "mouse", button: 0, clientX: 298, clientY: 240 });
    dispatchPointer(canvas, "pointermove", { pointerId: 8, pointerType: "mouse", clientX: 318, clientY: 250 });
    dispatchPointer(canvas, "pointerup", { pointerId: 8, pointerType: "mouse", clientX: 318, clientY: 250 });
    await waitFor(() => {
      front = container.querySelector(".multiview-region.selected") as HTMLDivElement;
      expect(front.style.width).toBe("240px");
      expect(front.style.height).toBe("235px");
    });
  });

  it("persists confirmed crop assets and invalidates them before region editing resumes", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:sheet") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const api = {
      assets: vi.fn().mockResolvedValue([sheet]),
      assetContent: vi.fn().mockResolvedValue(new Blob(["image"])),
      createMultiviewSet: vi.fn().mockResolvedValue({ id: "confirmed-set" }),
      invokeTool: vi.fn().mockImplementation((_projectId: string, tool: string) =>
        Promise.resolve(tool === "multiview.crop_views"
          ? { ok: true, output_asset_ids: ["crop-front", "crop-side", "crop-back"] }
          : { ok: true, output_asset_ids: [] })),
    };
    const onWorkflowContextChange = vi.fn();
    render(
      <MultiviewWorkspace
        projectId="project-1"
        api={api as never}
        onQueued={vi.fn()}
        onWorkflowContextChange={onWorkflowContextChange}
      />,
    );

    await screen.findByAltText("三视图拼图");
    fireEvent.click(screen.getByRole("button", { name: "确认裁切框并生成三张视图" }));
    await screen.findByRole("button", { name: "重新调整裁切框" });
    await waitFor(() => expect(onWorkflowContextChange).toHaveBeenLastCalledWith(
      expect.objectContaining({
        selected: {
          source: "sheet",
          front: "crop-front",
          side: "crop-side",
          back: "crop-back",
        },
        set_id: "confirmed-set",
        quality_confirmed: false,
      }),
    ));

    fireEvent.click(screen.getByRole("button", { name: "重新调整裁切框" }));
    expect(await screen.findByRole("button", { name: "确认裁切框并生成三张视图" })).toBeEnabled();
    await waitFor(() => expect(onWorkflowContextChange).toHaveBeenLastCalledWith(
      expect.objectContaining({
        selected: { source: "sheet", front: "sheet", side: "sheet", back: "sheet" },
        set_id: null,
        quality_confirmed: false,
      }),
    ));
  });

  it("saves a managed prompt then requires approval before generating the sheet", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:sheet") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const api = { assets: vi.fn().mockResolvedValue([sheet]), assetContent: vi.fn().mockResolvedValue(new Blob(["image"])), savePromptVersion: vi.fn().mockResolvedValue({ asset: { id: "prompt-1" } }), invokeTool: vi.fn().mockResolvedValue({ status: "awaiting_ui_action", ui_action: { action_id: "approve-mv" } }) };
    render(<MultiviewWorkspace projectId="project-1" api={api as never} onQueued={vi.fn()} />);
    await screen.findByAltText("三视图拼图");
    fireEvent.click(screen.getByRole("button", { name: "自动拆分三视图" }));
    await waitFor(() => expect(api.savePromptVersion).toHaveBeenCalledWith(
      "project-1",
      expect.objectContaining({
        kind: "multiview",
        enPrompt: MULTIVIEW_BASE_PROMPT,
      }),
      expect.any(String),
    ));
    await waitFor(() => expect(api.invokeTool).toHaveBeenCalledWith("project-1", "multiview.generate", expect.objectContaining({ source_asset_id: "sheet", prompt_asset_id: "prompt-1", provider_profile: "image-generation/auto", channel: "auto", model: "auto" }), expect.any(String), { providerProfile: "image-generation/auto" }));
    expect(screen.getByText("确认外部图像生成")).toBeInTheDocument();
  });

  it("crops the final views before asking for 3D approval without a second quality gate", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:sheet") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const api = {
      assets: vi.fn().mockResolvedValue([sheet]),
      assetContent: vi.fn().mockResolvedValue(new Blob(["image"])),
      createMultiviewSet: vi.fn().mockResolvedValue({ id: "set-1" }),
      completeAgentMultiviewAction: vi.fn().mockResolvedValue({ status: "succeeded" }),
      decideApproval: vi.fn().mockResolvedValue({
        status: "queued",
        job: { job_id: "job-3d" },
      }),
      invokeTool: vi.fn().mockImplementation((_projectId: string, tool: string) => {
        if (tool === "multiview.crop_views") return Promise.resolve({ ok: true, output_asset_ids: ["front", "side", "back"] });
        if (tool === "model3d.generate") return Promise.resolve({ status: "awaiting_ui_action", ui_action: { action_id: "approve-3d" } });
        return Promise.resolve({ ok: true, output_asset_ids: [] });
      }),
    };
    const onModelWorkflowContextChange = vi.fn();
    render(
      <MultiviewWorkspace
        projectId="project-1"
        api={api as never}
        onQueued={vi.fn()}
        workflowContext={{
          selected: { source: "sheet", front: "sheet", side: "sheet", back: "sheet" },
          regions: {},
          checks: {},
          quality_confirmed: false,
          set_id: null,
          job_id: null,
          pending_action_id: "agent-confirm-regions",
        }}
        modelWorkflowContext={{ asset_id: null, target_triangles: 50000, generation_job_id: null }}
        onModelWorkflowContextChange={onModelWorkflowContextChange}
      />,
    );
    await screen.findByAltText("三视图拼图");
    expect(screen.getByLabelText("Tripo 生成目标面数")).toHaveValue(50000);
    fireEvent.click(screen.getByRole("button", { name: "5,000" }));
    await waitFor(() =>
      expect(onModelWorkflowContextChange).toHaveBeenLastCalledWith(
        { asset_id: null, target_triangles: 5000, generation_job_id: null },
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "确认裁切框并生成三张视图" }));
    await screen.findByRole("button", { name: "重新调整裁切框" });
    fireEvent.click(screen.getByRole("button", { name: "确认裁切并进入 3D 模型处理" }));
    await waitFor(() => expect(api.invokeTool).toHaveBeenCalledWith(
      "project-1",
      "model3d.generate",
      expect.objectContaining({
        parameters: expect.objectContaining({
          model_version: "v3.1-20260211",
          face_limit: 5000,
        }),
      }),
      expect.any(String),
      { providerProfile: "tripo3d/default" },
    ));
    expect(screen.getByText(/目标面数：\s*5,000。/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "批准并提交" }));
    await waitFor(() => expect(onModelWorkflowContextChange).toHaveBeenLastCalledWith({
      asset_id: null,
      target_triangles: 5000,
      generation_job_id: "job-3d",
    }));
    expect(api.completeAgentMultiviewAction).toHaveBeenCalledWith(
      "project-1",
      "agent-confirm-regions",
      "set-1",
      { front: "front", side: "side", back: "back" },
      expect.any(String),
    );
    const toolCalls = api.invokeTool.mock.calls.map((call) => call[1]);
    expect(toolCalls.indexOf("multiview.set_regions")).toBeLessThan(
      toolCalls.indexOf("multiview.crop_views"),
    );
    expect(toolCalls).not.toContain("multiview.set_quality_checks");
    expect(toolCalls.indexOf("multiview.crop_views")).toBeLessThan(
      toolCalls.indexOf("model3d.generate"),
    );
  });

  it("automatically loads one generated sheet and crops its three regions before 3D", async () => {
    let blobIndex = 0;
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn().mockImplementation(() => `blob:view-${++blobIndex}`),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
    const generatedSheet = {
      id: "sheet-ai",
      name: "multiview-sheet.png",
      asset_type: "multiview",
      is_current: false,
      metadata: { width: 1536, height: 512 },
    };
    let assetLoads = 0;
    const api = {
      assets: vi.fn().mockImplementation(() =>
        Promise.resolve(++assetLoads === 1 ? [sheet] : [sheet, generatedSheet]),
      ),
      assetContent: vi.fn().mockResolvedValue(new Blob(["image"])),
      savePromptVersion: vi.fn().mockResolvedValue({ asset: { id: "prompt-1" } }),
      decideApproval: vi.fn().mockResolvedValue({
        status: "queued",
        job: { job_id: "multiview-job" },
      }),
      job: vi.fn().mockResolvedValue({
        id: "multiview-job",
        status: "succeeded",
        stage: "postprocessing",
        output_asset_ids: ["sheet-ai"],
      }),
      createMultiviewSet: vi.fn().mockResolvedValue({ id: "set-ai" }),
      invokeTool: vi.fn().mockImplementation((_projectId: string, tool: string) => {
        if (tool === "multiview.generate") {
          return Promise.resolve({
            status: "awaiting_ui_action",
            ui_action: { action_id: "approve-mv" },
          });
        }
        if (tool === "model3d.generate") {
          return Promise.resolve({
            status: "awaiting_ui_action",
            ui_action: { action_id: "approve-3d" },
          });
        }
        if (tool === "multiview.crop_views") {
          return Promise.resolve({
            ok: true,
            output_asset_ids: ["front-crop", "side-crop", "back-crop"],
          });
        }
        return Promise.resolve({ ok: true, output_asset_ids: [] });
      }),
    };
    const { container } = render(
      <MultiviewWorkspace
        projectId="project-1"
        api={api as never}
        onQueued={vi.fn()}
      />,
    );
    await screen.findByAltText("三视图拼图");
    fireEvent.click(screen.getByRole("button", { name: "自动拆分三视图" }));
    fireEvent.click(await screen.findByRole("button", { name: "批准并生成" }));
    await waitFor(() =>
      expect(api.decideApproval).toHaveBeenCalledWith(
        "project-1",
        "approve-mv",
        true,
        expect.any(String),
      ),
    );
    await waitFor(() =>
      expect(screen.getByRole("combobox")).toHaveValue("sheet-ai"),
    );
    expect(
      screen.getByText("三视图拼图生成完成，已自动加载；请调整三个框后裁切。"),
    ).toBeInTheDocument();
    expect(api.job).toHaveBeenCalledWith("project-1", "multiview-job");
    expect(screen.getByAltText("三视图拼图")).toBeInTheDocument();
    expect(
      screen.queryByLabelText("AI 生成的独立三视图"),
    ).not.toBeInTheDocument();
    expect(container.querySelectorAll(".multiview-region")).toHaveLength(3);
    expect(container.querySelectorAll(".multiview-handle")).toHaveLength(4);
    expect(
      screen.queryByRole("button", { name: "立即刷新生成进度" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认裁切框并生成三张视图" }));
    await screen.findByRole("button", { name: "重新调整裁切框" });
    fireEvent.click(
      screen.getByRole("button", { name: "确认裁切并进入 3D 模型处理" }),
    );

    await waitFor(() =>
      expect(api.createMultiviewSet).toHaveBeenCalledWith(
        "project-1",
        "sheet-ai",
        { front: "sheet-ai", side: "sheet-ai", back: "sheet-ai" },
        expect.any(String),
      ),
    );
    expect(api.invokeTool).toHaveBeenCalledWith(
      "project-1",
      "multiview.crop_views",
      { multiview_set_id: "set-ai" },
      expect.any(String),
    );
    expect(api.invokeTool).toHaveBeenCalledWith(
      "project-1",
      "model3d.generate",
      expect.objectContaining({
        view_asset_ids: {
          front: "front-crop",
          side: "side-crop",
          back: "back-crop",
        },
      }),
      expect.any(String),
      { providerProfile: "tripo3d/default" },
    );
  });
});
