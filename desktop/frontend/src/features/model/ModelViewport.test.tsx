import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ModelViewport } from "./ModelViewport";

function renderableGlb() {
  const json = new TextEncoder().encode('{"asset":{"version":"2.0"},"meshes":[{"primitives":[{}]}]}');
  const paddedLength = Math.ceil(json.length / 4) * 4;
  const bytes = new Uint8Array(20 + paddedLength);
  bytes.fill(0x20, 20);
  bytes.set([0x67, 0x6c, 0x54, 0x46], 0);
  const view = new DataView(bytes.buffer);
  view.setUint32(4, 2, true);
  view.setUint32(8, bytes.length, true);
  view.setUint32(12, paddedLength, true);
  bytes.set([0x4a, 0x53, 0x4f, 0x4e], 16);
  bytes.set(json, 20);
  return new Blob([bytes], { type: "model/gltf-binary" });
}

describe("ModelViewport", () => {
  afterEach(() => document.body.replaceChildren());

  it("uses the project-owned asset beacon only as a non-exportable preview fallback", async () => {
    const createObjectUrl = vi.fn().mockReturnValue("blob:asset-beacon");
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectUrl });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const api = { assets: vi.fn().mockResolvedValue([]) };
    render(<ModelViewport projectId="project-1" api={api as never} />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "资产信标预览" })).toBeVisible());
    expect(document.querySelector("model-viewer")?.getAttribute("src")).toBe("blob:asset-beacon");
    expect(screen.getByText(/不会上传、导出或写入项目/)).toBeVisible();
    expect(screen.getByRole("button", { name: "保存截图" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "导出 FBX" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Choose GLB" })).toBeVisible();

    const previewBlob = createObjectUrl.mock.calls[0]?.[0] as Blob;
    const bytes = await new Promise<Uint8Array>((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(reader.error);
      reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
      reader.readAsArrayBuffer(previewBlob);
    });
    const view = new DataView(bytes.buffer);
    expect(previewBlob.type).toBe("model/gltf-binary");
    expect(view.getUint32(0, true)).toBe(0x46546c67);
    expect(view.getUint32(4, true)).toBe(2);
    const jsonLength = view.getUint32(12, true);
    const json = JSON.parse(new TextDecoder().decode(bytes.slice(20, 20 + jsonLength)).trim());
    expect(json.asset.generator).toBe("FormWeaver Studio procedural asset beacon");
    expect(json.nodes).toHaveLength(10);
    expect(json.materials.map((item: { name: string }) => item.name)).toEqual([
      "Amber Alloy",
      "Graphite Structure",
      "Mint Signal",
    ]);
  });

  it("queues a managed GLB geometry optimization with the selected triangle budget", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:model") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const api = {
      assets: vi.fn().mockResolvedValue([{ id: "model-1", name: "chair.glb", asset_type: "glb", is_current: true }]),
      assetContent: vi.fn().mockResolvedValue(renderableGlb()),
      invokeTool: vi.fn().mockResolvedValue({ status: "queued", summary: "Optimization queued." }),
    };
    const onQueued = vi.fn();
    render(<ModelViewport projectId="project-1" api={api as never} onQueued={onQueued} />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "chair.glb" })).toBeVisible());
    fireEvent.change(screen.getByLabelText("目标面数"), { target: { value: "120" } });
    fireEvent.click(screen.getByRole("button", { name: "优化" }));
    await waitFor(() => expect(api.invokeTool).toHaveBeenCalledWith(
      "project-1", "model3d.optimize", { asset_id: "model-1", target_triangles: 120 }, expect.any(String),
    ));
    expect(onQueued).toHaveBeenCalledOnce();
  });

  it("shows an actionable error when the embedded model viewer cannot load the GLB", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:model") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const api = {
      assets: vi.fn().mockResolvedValue([{ id: "model-1", name: "chair.glb", asset_type: "glb", is_current: true }]),
      assetContent: vi.fn().mockResolvedValue(renderableGlb()),
      jobs: vi.fn().mockResolvedValue({ items: [] }),
    };
    render(<ModelViewport projectId="project-1" api={api as never} />);
    const modelViewer = await waitFor(() => {
      const element = document.querySelector("model-viewer");
      expect(element).toBeInTheDocument();
      return element as HTMLElement;
    });

    fireEvent.error(modelViewer);

    expect(screen.getByRole("alert")).toHaveTextContent(
      "模型预览加载失败。请重新加载当前资产，或使用“浏览器预览”确认模型文件。",
    );
  });

  it("keeps an FBX conversion on the model page instead of opening the task center", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:model") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const api = {
      assets: vi.fn().mockResolvedValue([{ id: "model-1", name: "chair.glb", asset_type: "glb", is_current: true }]),
      assetContent: vi.fn().mockResolvedValue(renderableGlb()),
      jobs: vi.fn().mockResolvedValue({ items: [] }),
      invokeTool: vi.fn().mockResolvedValue({
        status: "queued",
        summary: "Conversion queued.",
        job: { job_id: "convert-job", status: "queued", job_type: "model3d.convert", stage: "queued", provider: "local" },
      }),
    };
    const onQueued = vi.fn();
    render(<ModelViewport projectId="project-1" api={api as never} onQueued={onQueued} />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "chair.glb" })).toBeVisible());

    fireEvent.click(screen.getByRole("button", { name: "导出 FBX" }));

    await waitFor(() => expect(api.invokeTool).toHaveBeenCalledWith(
      "project-1", "model3d.convert", { asset_id: "model-1", target_format: "fbx" }, expect.any(String),
    ));
    expect(onQueued).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "正在转换 FBX" })).toBeDisabled();
    expect(screen.getByText("FBX 正在转换，完成后会保存在项目资产中。")).toBeVisible();
  });

  it("saves a completed managed FBX to a user-selected folder from the model page", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:model") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const glb = { id: "model-1", name: "chair.glb", asset_type: "glb", is_current: true };
    const fbx = { id: "fbx-1", name: "chair.fbx", asset_type: "fbx", is_current: false };
    const api = {
      assets: vi.fn().mockResolvedValue([glb, fbx]),
      assetContent: vi.fn().mockResolvedValue(renderableGlb()),
      jobs: vi.fn().mockResolvedValue({ items: [{
        id: "convert-job",
        job_type: "model3d.convert",
        status: "succeeded",
        stage: "verifying",
        progress: 100,
        output_asset_ids: ["fbx-1"],
        input_asset_ids: ["model-1"],
      }] }),
      exportAsset: vi.fn().mockResolvedValue({ asset_id: "fbx-1", name: "chair.fbx", bytes: 42 }),
    };
    const host = { chooseExportDirectory: vi.fn().mockResolvedValue("export-capability") };
    render(<ModelViewport projectId="project-1" api={api as never} host={host as never} />);

    const save = await screen.findByRole("button", { name: "保存到文件夹" });
    fireEvent.click(save);

    await waitFor(() => expect(api.exportAsset).toHaveBeenCalledWith(
      "project-1", "fbx-1", "export-capability", expect.any(String),
    ));
    expect(host.chooseExportDirectory).toHaveBeenCalledWith("project-1");
    expect(screen.getByText(/拖拽旋转/)).toHaveTextContent("FBX 已保存到所选文件夹：chair.fbx。");
  });

  it("shows only the completed FBX that belongs to the loaded GLB", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:model") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const modelA = { id: "model-a", name: "a.glb", asset_type: "glb", is_current: true };
    const modelB = { id: "model-b", name: "b.glb", asset_type: "glb", is_current: false };
    const fbxA = { id: "fbx-a", name: "a.fbx", asset_type: "fbx", is_current: false };
    const fbxB = { id: "fbx-b", name: "b.fbx", asset_type: "fbx", is_current: false };
    const api = {
      assets: vi.fn().mockResolvedValue([modelA, modelB, fbxA, fbxB]),
      assetContent: vi.fn().mockResolvedValue(renderableGlb()),
      jobs: vi.fn().mockImplementation(() => Promise.resolve({ items: [
        { id: "convert-b", job_type: "model3d.convert", status: "succeeded", stage: "verifying", progress: 100, output_asset_ids: ["fbx-b"], input_asset_ids: ["model-b"] },
        { id: "convert-a", job_type: "model3d.convert", status: "succeeded", stage: "verifying", progress: 100, output_asset_ids: ["fbx-a"], input_asset_ids: ["model-a"] },
      ] })),
      exportAsset: vi.fn().mockResolvedValue({ asset_id: "fbx-a", name: "a.fbx", bytes: 42 }),
    };
    const host = { chooseExportDirectory: vi.fn().mockResolvedValue("export-capability") };
    render(<ModelViewport projectId="project-1" api={api as never} host={host as never} />);

    expect(await screen.findByText(/a\.fbx/)).toBeVisible();
    expect(screen.queryByText(/b\.fbx/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存到文件夹" }));
    await waitFor(() => expect(api.exportAsset).toHaveBeenCalledWith(
      "project-1", "fbx-a", "export-capability", expect.any(String),
    ));
  });

  it("does not let an old conversion replace a new request while invoke is pending", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:model") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    let resolveInvoke!: (value: unknown) => void;
    const invoke = new Promise((resolve) => { resolveInvoke = resolve; });
    const glb = { id: "model-1", name: "chair.glb", asset_type: "glb", is_current: true };
    const oldFbx = { id: "old-fbx", name: "old.fbx", asset_type: "fbx", is_current: false };
    const api = {
      assets: vi.fn().mockResolvedValue([glb, oldFbx]),
      assetContent: vi.fn().mockResolvedValue(renderableGlb()),
      jobs: vi.fn().mockImplementation(() => Promise.resolve({ items: [{
        id: "old-convert",
        job_type: "model3d.convert",
        status: "succeeded",
        stage: "verifying",
        progress: 100,
        output_asset_ids: ["old-fbx"],
        input_asset_ids: ["model-1"],
      }] })),
      invokeTool: vi.fn().mockReturnValue(invoke),
    };
    render(<ModelViewport projectId="project-1" api={api as never} />);
    expect(await screen.findByRole("button", { name: "保存到文件夹" })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "导出 FBX" }));
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 2100)); });

    expect(screen.getByRole("button", { name: "正在转换 FBX" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "保存到文件夹" })).not.toBeInTheDocument();
    await act(async () => {
      resolveInvoke({
        status: "queued",
        summary: "queued",
        job: { job_id: "new-convert", status: "queued", job_type: "model3d.convert", stage: "queued", provider: "local" },
      });
      await Promise.resolve();
    });
  });

  it("shows a business failure returned by the FBX tool without opening tasks", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:model") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const api = {
      assets: vi.fn().mockResolvedValue([{ id: "model-1", name: "chair.glb", asset_type: "glb", is_current: true }]),
      assetContent: vi.fn().mockResolvedValue(renderableGlb()),
      jobs: vi.fn().mockResolvedValue({ items: [] }),
      invokeTool: vi.fn().mockResolvedValue({
        status: "failed",
        summary: "Conversion unavailable.",
        error: { user_message: "未找到可用的 FBX 转换工具。" },
      }),
    };
    const onQueued = vi.fn();
    render(<ModelViewport projectId="project-1" api={api as never} onQueued={onQueued} />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "chair.glb" })).toBeVisible());
    fireEvent.click(screen.getByRole("button", { name: "导出 FBX" }));

    await waitFor(() => expect(screen.getByText(/拖拽旋转/)).toHaveTextContent("未找到可用的 FBX 转换工具。"));
    expect(onQueued).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "导出 FBX" })).toBeEnabled();
  });

  it("retries resolving a completed FBX asset after it becomes visible", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:model") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const glb = { id: "model-1", name: "chair.glb", asset_type: "glb", is_current: true };
    const fbx = { id: "fbx-1", name: "chair.fbx", asset_type: "fbx", is_current: false };
    const assets = vi.fn()
      .mockResolvedValueOnce([glb])
      .mockResolvedValueOnce([glb])
      .mockResolvedValue([glb, fbx]);
    const api = {
      assets,
      assetContent: vi.fn().mockResolvedValue(renderableGlb()),
      jobs: vi.fn().mockImplementation(() => Promise.resolve({ items: [{
        id: "convert-job",
        job_type: "model3d.convert",
        status: "succeeded",
        stage: "verifying",
        progress: 100,
        output_asset_ids: ["fbx-1"],
        input_asset_ids: ["model-1"],
      }] })),
    };
    render(<ModelViewport projectId="project-1" api={api as never} />);
    expect(await screen.findByText(/正在读取文件信息/)).toBeVisible();

    await waitFor(() => expect(screen.getByText(/chair\.fbx/)).toBeVisible(), { timeout: 3500 });
    expect(screen.getByRole("button", { name: "保存到文件夹" })).toBeEnabled();
    expect(assets).toHaveBeenCalledTimes(3);
  });

  it("shows the current 3D generation stage and progress while no GLB is available yet", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:default-cube") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const api = {
      assets: vi.fn().mockResolvedValue([]),
      jobs: vi.fn().mockResolvedValue({ items: [{ id: "job-1", job_type: "model3d.generate", status: "running", stage: "building_mesh", progress: 62 }] }),
    };
    render(<ModelViewport projectId="project-1" api={api as never} workflowContext={{ asset_id: null, target_triangles: 50000, generation_job_id: "job-1" }} />);
    expect(await screen.findByRole("status")).toHaveTextContent("3D 正在生成 · building_mesh · 62%");
    expect(document.querySelector(".model-workspace")).toHaveClass("has-generation-status");
  });

  it("shows an actionable interrupted quality-gate error without collapsing the preview", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:default-cube") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const api = {
      assets: vi.fn().mockResolvedValue([]),
      jobs: vi.fn().mockResolvedValue({ items: [{
        id: "job-1",
        job_type: "model3d.generate",
        status: "interrupted",
        stage: "postprocessing",
        progress: 0,
        error: {
          code: "MULTIVIEW_MANUAL_CONFIRMATION_REQUIRED",
          user_message: "Confirm all six checks.",
        },
      }] }),
    };
    render(<ModelViewport projectId="project-1" api={api as never} workflowContext={{ asset_id: null, target_triangles: 50000, generation_job_id: "job-1" }} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "3D 生成未开始：最终裁切的三视图尚未完成质量确认。请返回三视图制作页重新确认后提交。",
    );
    expect(document.querySelector(".model-workspace")).toHaveClass("has-generation-status");
    expect(document.querySelector("model-viewer")).toBeInTheDocument();
  });

  it("shows the current remote job instead of an older looping quality-gate failure", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:default-cube") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const api = {
      assets: vi.fn().mockResolvedValue([]),
      jobs: vi.fn().mockResolvedValue({ items: [
        {
          id: "old-quality-gate",
          job_type: "model3d.generate",
          status: "interrupted",
          stage: "postprocessing",
          progress: null,
          error: { code: "MULTIVIEW_MANUAL_CONFIRMATION_REQUIRED" },
        },
        {
          id: "current-tripo-job",
          job_type: "model3d.generate",
          status: "waiting",
          stage: "remote_queued",
          progress: null,
          error: null,
        },
      ] }),
    };
    render(<ModelViewport projectId="project-1" api={api as never} workflowContext={{ asset_id: null, target_triangles: 50000, generation_job_id: "current-tripo-job" }} />);
    expect(await screen.findByRole("status")).toHaveTextContent(
      "3D 正在生成 · remote_queued",
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a cancelled generation as a visible terminal status", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:default-cube") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const api = {
      assets: vi.fn().mockResolvedValue([]),
      jobs: vi.fn().mockResolvedValue({ items: [{
        id: "job-cancelled",
        job_type: "model3d.generate",
        status: "cancelled",
        stage: "cancel_requested",
        progress: null,
      }] }),
    };
    render(<ModelViewport projectId="project-1" api={api as never} workflowContext={{ asset_id: null, target_triangles: 50000, generation_job_id: "job-cancelled" }} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("3D 生成已取消。");
  });

  it("ignores an older generation poll that resolves after a newer terminal result", async () => {
    vi.useFakeTimers();
    try {
      Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:default-cube") });
      Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
      let resolveFirst!: (value: unknown) => void;
      let resolveSecond!: (value: unknown) => void;
      const first = new Promise((resolve) => { resolveFirst = resolve; });
      const second = new Promise((resolve) => { resolveSecond = resolve; });
      const api = {
        assets: vi.fn().mockResolvedValue([]),
        jobs: vi.fn().mockReturnValueOnce(first).mockReturnValueOnce(second),
      };
      render(<ModelViewport projectId="project-1" api={api as never} workflowContext={{ asset_id: null, target_triangles: 50000, generation_job_id: "job-new" }} />);
      expect(api.jobs).toHaveBeenCalledTimes(1);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
      expect(api.jobs).toHaveBeenCalledTimes(2);
      await act(async () => {
        resolveSecond({ items: [{
          id: "job-new",
          job_type: "model3d.generate",
          status: "failed",
          stage: "postprocessing",
          progress: null,
          error: { user_message: "new terminal result" },
        }] });
        await Promise.resolve();
      });
      expect(screen.getByRole("alert")).toHaveTextContent("new terminal result");
      await act(async () => {
        resolveFirst({ items: [{
          id: "job-old",
          job_type: "model3d.generate",
          status: "running",
          stage: "uploading",
          progress: 20,
        }] });
        await Promise.resolve();
      });
      expect(screen.getByRole("alert")).toHaveTextContent("new terminal result");
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps the recovered model until current asset is explicitly loaded and can restore it", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:model") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const previous = { id: "previous-model", name: "previous.glb", asset_type: "glb", is_current: false };
    const current = { id: "current-model", name: "current.glb", asset_type: "glb", is_current: true };
    const api = {
      assets: vi.fn().mockResolvedValue([current, previous]),
      assetContent: vi.fn().mockResolvedValue(renderableGlb()),
      jobs: vi.fn().mockResolvedValue({ items: [] }),
    };
    render(<ModelViewport
      projectId="project-1"
      api={api as never}
      workflowContext={{ asset_id: previous.id, target_triangles: 50000, generation_job_id: null }}
      onWorkflowContextChange={vi.fn()}
    />);

    expect(await screen.findByRole("heading", { name: "previous.glb" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "加载当前资产" }));
    expect(await screen.findByRole("heading", { name: "current.glb" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "恢复加载前状态" }));
    expect(await screen.findByRole("heading", { name: "previous.glb" })).toBeVisible();
  });

  it("automatically loads the GLB produced by the tracked completed generation", async () => {
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn().mockImplementation((blob: Blob) =>
        blob.size === 1 ? "blob:old-model" : "blob:new-model",
      ),
    });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const oldModel = { id: "old-model", name: "old.glb", asset_type: "glb", is_current: true };
    const newModel = { id: "new-model", name: "generated.glb", asset_type: "glb", is_current: false };
    const onWorkflowContextChange = vi.fn();
    const api = {
      assets: vi.fn().mockResolvedValue([newModel, oldModel]),
      assetContent: vi.fn().mockImplementation((_projectId: string, assetId: string) =>
        Promise.resolve(assetId === "old-model" ? new Blob(["x"]) : renderableGlb()),
      ),
      jobs: vi.fn().mockResolvedValue({ items: [{
        id: "generation-job",
        job_type: "model3d.generate",
        status: "succeeded",
        stage: "verifying",
        progress: 100,
        output_asset_ids: ["new-model"],
      }] }),
    };
    render(
      <ModelViewport
        projectId="project-1"
        api={api as never}
        workflowContext={{ asset_id: oldModel.id, target_triangles: 50000, generation_job_id: "generation-job" }}
        onWorkflowContextChange={onWorkflowContextChange}
      />,
    );
    expect(await screen.findByRole("heading", { name: "generated.glb" })).toBeVisible();
    expect(api.assetContent).toHaveBeenCalledWith("project-1", "new-model");
    expect(document.querySelector(".model-workspace")).toHaveAttribute("data-asset-id", "new-model");
    expect(onWorkflowContextChange).toHaveBeenCalledWith({
      asset_id: "new-model",
      target_triangles: 50000,
      generation_job_id: null,
    });
  });

  it("does not replace an asset explicitly selected from the asset library with a historical generation", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:model") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const selected = { id: "selected-model", name: "selected.glb", asset_type: "glb", is_current: false };
    const historical = { id: "historical-model", name: "historical.glb", asset_type: "glb", is_current: true };
    const api = {
      assets: vi.fn().mockResolvedValue([historical, selected]),
      assetContent: vi.fn().mockResolvedValue(renderableGlb()),
      jobs: vi.fn().mockResolvedValue({ items: [{
        id: "historical-generation",
        job_type: "model3d.generate",
        status: "succeeded",
        stage: "verifying",
        progress: 100,
        output_asset_ids: [historical.id],
      }] }),
    };
    render(
      <ModelViewport
        projectId="project-1"
        api={api as never}
        workflowContext={{ asset_id: selected.id, target_triangles: 50000, generation_job_id: null }}
        onWorkflowContextChange={vi.fn()}
      />,
    );

    expect(await screen.findByRole("heading", { name: "selected.glb" })).toBeVisible();
    await waitFor(() => expect(api.jobs).toHaveBeenCalled());
    expect(screen.getByRole("heading", { name: "selected.glb" })).toBeVisible();
    expect(api.assetContent).not.toHaveBeenCalledWith("project-1", historical.id);
  });
});
