import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AssetDto } from "../../shared/api/client";
import { CanvasContextToolbar } from "./CanvasContextToolbar";

const image: AssetDto = {
  id: "asset-image",
  asset_type: "source_image",
  name: "Reference.png",
  is_current: true,
  metadata: { width: 1024, height: 1024, format: "png" },
  version_no: 2,
};

const prompt: AssetDto = {
  id: "asset-prompt",
  asset_type: "prompt",
  name: "Prompt.md",
  is_current: false,
  metadata: {},
  version_no: 1,
};

describe("CanvasContextToolbar", () => {
  afterEach(cleanup);

  it("renders all fixed entries and exposes unavailable reasons", () => {
    render(
      <CanvasContextToolbar
        projectId="project-1"
        api={{} as never}
        asset={image}
        promptAsset={null}
        referenceAvailable={false}
        onModeChange={vi.fn()}
      />,
    );

    for (const name of ["框选与裁切", "提取建模主体", "分析内容与风格", "生成创意图", "制作三视图", "发起 3D 生成", "更多"]) {
      expect(screen.getByRole("button", { name })).toBeVisible();
    }
    expect(screen.getByRole("button", { name: "分析内容与风格" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(screen.getByText("需要先导入一张受管图片作为参考。")).toBeInTheDocument();
    expect(screen.getByText("需要先在内容与风格分析页保存受管 Prompt。")).toBeInTheDocument();
  });

  it("does not invoke a Provider when the external-transfer approval is cancelled", () => {
    const api = { invokeTool: vi.fn(), decideApproval: vi.fn() };
    render(
      <CanvasContextToolbar
        projectId="project-1"
        api={api as never}
        asset={image}
        promptAsset={prompt}
        referenceAvailable
        onModeChange={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "生成创意图" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("原文件仍在本机");
    expect(screen.getByRole("alertdialog")).toHaveTextContent("Reference.png");
    expect(screen.getByRole("alertdialog")).toHaveTextContent("image-generation/auto");
    expect(screen.getByRole("alertdialog")).toHaveTextContent("Tripo3D / Meshy");
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(api.invokeTool).not.toHaveBeenCalled();
    expect(api.decideApproval).not.toHaveBeenCalled();
  });

  it("binds the visible approval to the real tool approval before queueing", async () => {
    const api = {
      invokeTool: vi.fn().mockResolvedValue({
        ok: true,
        status: "awaiting_ui_action",
        ui_action: { action_id: "approval-1", type: "approval_required" },
      }),
      decideApproval: vi.fn().mockResolvedValue({
        ok: true,
        status: "queued",
        job: { job_id: "job-1", status: "queued" },
      }),
    };
    const onModeChange = vi.fn();
    render(
      <CanvasContextToolbar
        projectId="project-1"
        api={api as never}
        asset={image}
        promptAsset={prompt}
        referenceAvailable
        onModeChange={onModeChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "生成创意图" }));
    fireEvent.click(screen.getByRole("button", { name: "批准并提交" }));

    await waitFor(() => expect(api.invokeTool).toHaveBeenCalledTimes(1));
    expect(api.invokeTool).toHaveBeenCalledWith(
      "project-1",
      "image.generate_variants",
      expect.objectContaining({
        source_asset_id: "asset-image",
        prompt_asset_id: "asset-prompt",
        provider_profile: "image-generation/auto",
        channel: "auto",
        model: "auto",
        candidate_count: 4,
      }),
      expect.any(String),
      { providerProfile: "image-generation/auto" },
    );
    await waitFor(() =>
      expect(api.decideApproval).toHaveBeenCalledWith(
        "project-1",
        "approval-1",
        true,
        expect.any(String),
      ),
    );
    expect(onModeChange).toHaveBeenCalledWith("task_waiting");
  });

  it("submits the current managed image to Tripo3D after explicit approval", async () => {
    const api = {
      invokeTool: vi.fn().mockResolvedValue({
        ok: true,
        status: "awaiting_ui_action",
        ui_action: { action_id: "approval-3d", type: "approval_required" },
      }),
      decideApproval: vi.fn().mockResolvedValue({
        ok: true,
        status: "queued",
        job: { job_id: "job-3d", status: "queued" },
      }),
    };
    const onModeChange = vi.fn();
    const onModelJobQueued = vi.fn();
    render(
      <CanvasContextToolbar
        projectId="project-1"
        api={api as never}
        asset={image}
        promptAsset={prompt}
        referenceAvailable
        onModeChange={onModeChange}
        onModelJobQueued={onModelJobQueued}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "发起 3D 生成" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("Tripo3D");
    fireEvent.click(screen.getByRole("button", { name: "批准并提交" }));

    await waitFor(() => expect(api.invokeTool).toHaveBeenCalledTimes(1));
    expect(api.invokeTool).toHaveBeenCalledWith(
      "project-1",
      "model3d.generate",
      expect.objectContaining({
        mode: "image",
        image_asset_id: "asset-image",
        provider_profile: "tripo3d/default",
      }),
      expect.any(String),
      { providerProfile: "tripo3d/default" },
    );
    await waitFor(() =>
      expect(api.decideApproval).toHaveBeenCalledWith(
        "project-1",
        "approval-3d",
        true,
        expect.any(String),
      ),
    );
    expect(onModeChange).toHaveBeenCalledWith("task_waiting");
    expect(onModelJobQueued).toHaveBeenCalledWith("job-3d");
  });

  it("creates a local resized asset and makes it current", async () => {
    const api = {
      invokeTool: vi.fn().mockResolvedValue({
        ok: true,
        status: "succeeded",
        output_asset_ids: ["asset-resized"],
      }),
      setCurrentAsset: vi.fn().mockResolvedValue({}),
    };
    const onModeChange = vi.fn();
    const onLocalImageCompleted = vi.fn();
    render(
      <CanvasContextToolbar
        projectId="project-1"
        api={api as never}
        asset={image}
        promptAsset={prompt}
        referenceAvailable
        onModeChange={onModeChange}
        onLocalImageCompleted={onLocalImageCompleted}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "更多" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /调整尺寸与超分/ }));
    expect(screen.getByRole("dialog", { name: "调整尺寸与超分" })).toHaveTextContent("原图不会被覆盖");
    fireEvent.change(screen.getByLabelText("目标宽度"), { target: { value: "768" } });
    fireEvent.change(screen.getByLabelText("目标高度"), { target: { value: "512" } });
    fireEvent.change(screen.getByLabelText("输出格式"), { target: { value: "webp" } });
    fireEvent.click(screen.getByRole("button", { name: "生成缩放结果" }));

    await waitFor(() => expect(api.invokeTool).toHaveBeenCalledWith(
      "project-1",
      "image.normalize",
      {
        source_asset_id: "asset-image",
        target_width: 768,
        target_height: 512,
        lock_aspect_ratio: true,
        output_format: "webp",
        quality: 90,
        preserve_alpha: true,
      },
      expect.any(String),
    ));
    expect(api.setCurrentAsset).toHaveBeenCalledWith("project-1", "asset-resized", expect.any(String));
    expect(onLocalImageCompleted).toHaveBeenCalledWith("asset-resized");
    expect(onModeChange).toHaveBeenCalledWith("image");
  });

  it("queues bundled local super-resolution without Provider approval", async () => {
    const api = {
      invokeTool: vi.fn().mockResolvedValue({
        ok: true,
        status: "queued",
        output_asset_ids: [],
        job: { job_id: "job-upscale", status: "queued" },
      }),
      decideApproval: vi.fn(),
    };
    const onModeChange = vi.fn();
    render(
      <CanvasContextToolbar
        projectId="project-1"
        api={api as never}
        asset={image}
        promptAsset={prompt}
        referenceAvailable
        onModeChange={onModeChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "更多" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /调整尺寸与超分/ }));
    fireEvent.click(screen.getByRole("button", { name: "本地超分" }));
    fireEvent.change(screen.getByLabelText("放大倍数"), { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: "开始本地超分" }));

    await waitFor(() => expect(api.invokeTool).toHaveBeenCalledWith(
      "project-1",
      "image.upscale_local",
      { source_asset_id: "asset-image", scale: 4 },
      expect.any(String),
    ));
    expect(api.decideApproval).not.toHaveBeenCalled();
    expect(onModeChange).toHaveBeenCalledWith("task_waiting");
  });
});
