import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AssetDto } from "../../shared/api/client";
import { ImageWorkspace } from "./ImageWorkspace";

const assets: AssetDto[] = [
  {
    id: "asset-v1",
    asset_type: "source_image",
    name: "Reference.png",
    is_current: false,
    metadata: { width: 1024, height: 1024, format: "png" },
    version_no: 1,
  },
  {
    id: "asset-v2",
    asset_type: "source_image",
    name: "Reference.png",
    is_current: true,
    metadata: {
      width: 1024,
      height: 1024,
      format: "png",
      has_alpha: true,
    },
    version_no: 2,
  },
];

describe("ImageWorkspace", () => {
  beforeEach(() => {
    vi.stubGlobal("PointerEvent", MouseEvent);
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => "blob:managed-preview"),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    Reflect.deleteProperty(URL, "createObjectURL");
    Reflect.deleteProperty(URL, "revokeObjectURL");
  });

  it("loads managed content, switches versions, and zooms around the canvas", async () => {
    const api = {
      assets: vi.fn().mockResolvedValue(assets),
      assetLineage: vi.fn().mockResolvedValue({
        asset_id: "asset-v2",
        parent_asset_id: "asset-v1",
        children: [],
        siblings: ["asset-v1"],
        usage: {},
      }),
      assetContent: vi.fn().mockResolvedValue(new Blob(["image"])),
    };

    render(<ImageWorkspace projectId="project-1" api={api as never} onModeChange={vi.fn()} />);

    const image = await screen.findByRole("img", { name: "Reference.png" });
    expect(image).toHaveAttribute("src", "blob:managed-preview");
    expect(screen.getByRole("button", { name: "框选截屏" }).closest(".image-workspace-tools")).not.toBeNull();
    expect(screen.getByRole("button", { name: "导入图片" }).closest(".image-workspace-tools")).not.toBeNull();
    expect(api.assetContent).toHaveBeenCalledWith(
      "project-1",
      "asset-v2",
      expect.any(AbortSignal),
    );
    expect(screen.getByLabelText("Zoom level")).toHaveTextContent("Fit");
    await waitFor(() =>
      expect(screen.getByLabelText("Preview asset version")).toBeEnabled(),
    );

    fireEvent.change(screen.getByLabelText("Preview asset version"), {
      target: { value: "asset-v1" },
    });
    await waitFor(() =>
      expect(api.assetContent).toHaveBeenCalledWith(
        "project-1",
        "asset-v1",
        expect.any(AbortSignal),
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: "100%" }));
    expect(screen.getByLabelText("Zoom level")).toHaveTextContent("100%");
    fireEvent.wheel(screen.getByLabelText("Image preview canvas"), {
      deltaY: -100,
      clientX: 200,
      clientY: 200,
    });
    expect(screen.getByLabelText("Zoom level")).toHaveTextContent("110%");
    const currentImage = screen.getByRole("img", { name: "Reference.png" });

    const canvas = screen.getByLabelText("Image preview canvas");
    Object.defineProperty(canvas, "setPointerCapture", {
      configurable: true,
      value: vi.fn(),
    });
    fireEvent.keyDown(window, { code: "Space" });
    fireEvent.keyUp(window, { code: "Space" });
    fireEvent.pointerDown(canvas, {
      button: 0,
      pointerId: 1,
      clientX: 100,
      clientY: 100,
    });
    fireEvent.pointerMove(canvas, {
      pointerId: 1,
      clientX: 140,
      clientY: 120,
    });
    expect(currentImage).toHaveStyle({
      transform: "translate(20px, 0px) scale(1.1)",
    });
  });

  it("shows a recoverable error and retries the managed content request", async () => {
    const api = {
      assets: vi.fn().mockResolvedValue([assets[1]]),
      assetLineage: vi.fn().mockResolvedValue({
        asset_id: "asset-v2",
        parent_asset_id: null,
        children: [],
        siblings: [],
        usage: {},
      }),
      assetContent: vi
        .fn()
        .mockRejectedValueOnce(new Error("Managed content is temporarily unavailable."))
        .mockResolvedValue(new Blob(["image"])),
    };

    render(<ImageWorkspace projectId="project-1" api={api as never} onModeChange={vi.fn()} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Managed content is temporarily unavailable.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Retry preview" }));

    await screen.findByRole("img", { name: "Reference.png" });
    expect(api.assetContent).toHaveBeenCalledTimes(2);
  });

  it("opens and exports the selected current image from the workbench toolbar", async () => {
    const api = {
      assets: vi.fn().mockResolvedValue([assets[1]]),
      assetLineage: vi.fn().mockResolvedValue({
        asset_id: "asset-v2",
        parent_asset_id: null,
        children: [],
        siblings: [],
        usage: {},
      }),
      assetContent: vi.fn().mockResolvedValue(new Blob(["image"])),
      revealAsset: vi.fn().mockResolvedValue({ asset_id: "asset-v2", opened: true }),
      exportAsset: vi.fn().mockResolvedValue({ asset_id: "asset-v2", name: "Reference.png", bytes: 256 }),
    };
    const host = { chooseExportDirectory: vi.fn().mockResolvedValue("export-capability") };
    const { container } = render(
      <ImageWorkspace
        projectId="project-1"
        api={api as never}
        host={host}
        onModeChange={vi.fn()}
      />,
    );

    const workbench = within(container);
    await workbench.findByRole("img", { name: "Reference.png" });
    fireEvent.click(workbench.getByRole("button", { name: "打开目录" }));
    await waitFor(() => expect(api.revealAsset).toHaveBeenCalledWith(
      "project-1",
      "asset-v2",
      expect.stringMatching(/^asset-reveal-/),
    ));

    fireEvent.click(workbench.getByRole("button", { name: "导出资源" }));
    await waitFor(() => expect(host.chooseExportDirectory).toHaveBeenCalledWith("project-1"));
    expect(api.exportAsset).toHaveBeenCalledWith(
      "project-1",
      "asset-v2",
      "export-capability",
      expect.stringMatching(/^asset-export-/),
    );
    expect(await workbench.findByText("已导出 Reference.png。")).toBeVisible();
  });
});
