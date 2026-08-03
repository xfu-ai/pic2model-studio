import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AssetBrowser } from "./AssetBrowser";

const imageAsset = {
  id: "image-asset",
  name: "Character.png",
  asset_type: "source_image",
  is_current: false,
  is_hidden: false,
  thumbnail_asset_id: "image-thumbnail",
  metadata: { width: 512, height: 512 },
};

describe("AssetBrowser", () => {
  beforeEach(() => {
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn((blob: Blob) => `blob:${blob.type || "preview"}`),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
  });

  afterEach(() => {
    document.body.replaceChildren();
    vi.restoreAllMocks();
    Reflect.deleteProperty(URL, "createObjectURL");
    Reflect.deleteProperty(URL, "revokeObjectURL");
    Reflect.deleteProperty(navigator, "clipboard");
  });

  it("uses an image as the only asset type that changes project current state", async () => {
    const current = { ...imageAsset, is_current: true };
    const api = {
      assets: vi.fn()
        .mockResolvedValueOnce([imageAsset])
        .mockResolvedValueOnce([current]),
      assetThumbnail: vi.fn().mockResolvedValue(new Blob(["thumb"], { type: "image/jpeg" })),
      assetText: vi.fn(),
      assetContent: vi.fn(),
      setCurrentAsset: vi.fn().mockResolvedValue({ decision: { asset_id: imageAsset.id } }),
    };
    const onCurrent = vi.fn();
    render(<AssetBrowser projectId="project-image" api={api as never} readOnly={false} onCurrent={onCurrent} />);

    expect(await screen.findByText("Character.png")).toBeVisible();
    expect(await screen.findByAltText("Character.png")).toHaveAttribute("src", "blob:image/jpeg");
    fireEvent.click(screen.getByRole("button", { name: "使用此图片" }));

    await waitFor(() => expect(api.setCurrentAsset).toHaveBeenCalledWith(
      "project-image",
      "image-asset",
      expect.stringMatching(/^asset-/),
    ));
    expect(onCurrent).toHaveBeenCalled();
    expect(await screen.findAllByText("当前图片")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Set current" })).not.toBeInTheDocument();
  });

  it("loads a prompt near the viewport and copies its full managed text without changing current", async () => {
    const promptAsset = {
      id: "prompt-asset",
      name: "character-prompt.json",
      asset_type: "prompt",
      is_current: false,
      metadata: {},
    };
    const clipboard = { writeText: vi.fn().mockResolvedValue(undefined) };
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: clipboard });
    const promptContent = "A stylized 3D character.\nNeutral pose, full body.";
    const prompt = JSON.stringify({
      schema: "pic2model.prompt.v1",
      analysis: { zh: "角色分析", en: "character analysis" },
      generation: { zh: promptContent, en: promptContent },
      constraints: { preserve: ["full body"], avoid: ["text"] },
    });
    const api = {
      assets: vi.fn().mockResolvedValue([promptAsset]),
      assetText: vi.fn().mockResolvedValue(prompt),
      assetThumbnail: vi.fn(),
      assetContent: vi.fn(),
      setCurrentAsset: vi.fn(),
    };
    render(<AssetBrowser projectId="project-prompt" api={api as never} readOnly={false} onCurrent={vi.fn()} />);

    expect(await screen.findByText(/A stylized 3D character/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "复制 Prompt" }));

    await waitFor(() => expect(clipboard.writeText).toHaveBeenCalledWith(promptContent));
    expect(screen.getByRole("button", { name: "已复制" })).toBeVisible();
    expect(api.setCurrentAsset).not.toHaveBeenCalled();
  });

  it("uses a cached static preview for GLB cards and opens one explicit model", async () => {
    const model = {
      id: "model-asset",
      name: "character.glb",
      asset_type: "glb",
      is_current: false,
      size_bytes: 24.8 * 1024 * 1024,
      metadata: {},
    };
    const preview = {
      id: "model-preview",
      name: "front-preview.png",
      asset_type: "preview",
      parent_asset_id: model.id,
      is_current: false,
      metadata: { width: 640, height: 480 },
    };
    const api = {
      assets: vi.fn().mockResolvedValue([model, preview]),
      assetContent: vi.fn().mockResolvedValue(new Blob(["snapshot"], { type: "image/png" })),
      assetThumbnail: vi.fn(),
      assetText: vi.fn(),
      setCurrentAsset: vi.fn(),
    };
    const onOpenModel = vi.fn();
    render(<AssetBrowser projectId="project-model" api={api as never} readOnly={false} onCurrent={vi.fn()} onOpenModel={onOpenModel} />);

    expect(await screen.findByAltText("character.glb 静态预览")).toBeVisible();
    expect(api.assetContent).toHaveBeenCalledWith("project-model", "model-preview", expect.any(AbortSignal));
    fireEvent.click(screen.getByRole("button", { name: "查看 3D" }));

    expect(onOpenModel).toHaveBeenCalledWith("model-asset");
    expect(api.setCurrentAsset).not.toHaveBeenCalled();
    expect(api.assetContent).not.toHaveBeenCalledWith("project-model", "model-asset", expect.anything());
  });

  it("keeps the simplified library free of version, compare, trash, and hide controls", async () => {
    const api = {
      assets: vi.fn().mockResolvedValue([imageAsset]),
      assetThumbnail: vi.fn().mockResolvedValue(new Blob(["thumb"], { type: "image/jpeg" })),
      assetText: vi.fn(),
      assetContent: vi.fn(),
    };
    render(<AssetBrowser projectId="project-simple" api={api as never} readOnly={false} onCurrent={vi.fn()} />);

    expect(await screen.findByRole("heading", { name: "Assets" })).toBeVisible();
    expect(screen.queryByRole("button", { name: /version/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /compare/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /trash/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /hide|restore visibility/i })).not.toBeInTheDocument();
  });

  it("shows only prompts, image assets, and 3D assets in the project library", async () => {
    const api = {
      assets: vi.fn().mockResolvedValue([
        imageAsset,
        { id: "prompt", name: "generated-prompt.txt", asset_type: "prompt", is_current: false, metadata: {} },
        { id: "glb", name: "character.glb", asset_type: "glb", is_current: false, metadata: {} },
        { id: "analysis", name: "content-analysis.json", asset_type: "analysis", is_current: false, metadata: {} },
        { id: "package", name: "character-package.zip", asset_type: "export", is_current: false, metadata: {} },
        { id: "preview", name: "model-preview.png", asset_type: "preview", is_current: false, metadata: {} },
      ]),
      assetThumbnail: vi.fn().mockResolvedValue(new Blob(["thumb"], { type: "image/png" })),
      assetText: vi.fn().mockResolvedValue("A generated character prompt."),
      assetContent: vi.fn(),
      setCurrentAsset: vi.fn(),
    };
    render(<AssetBrowser projectId="project-library" api={api as never} readOnly={false} onCurrent={vi.fn()} />);

    expect(await screen.findByText("Character.png")).toBeVisible();
    expect(screen.getByText("generated-prompt.txt")).toBeVisible();
    expect(screen.getByText("character.glb")).toBeVisible();
    expect(screen.queryByText("content-analysis.json")).not.toBeInTheDocument();
    expect(screen.queryByText("character-package.zip")).not.toBeInTheDocument();
    expect(screen.queryByText("model-preview.png")).not.toBeInTheDocument();
  });

  it("shows newest assets first and filters the library by asset type", async () => {
    const api = {
      assets: vi.fn().mockResolvedValue([
        { ...imageAsset, id: "old-image", name: "old-image.png", is_current: true, created_at: "2026-07-01T00:00:00Z" },
        { id: "new-prompt", name: "new-prompt.txt", asset_type: "prompt", is_current: false, metadata: {}, created_at: "2026-07-03T00:00:00Z" },
        { id: "model", name: "model.glb", asset_type: "glb", is_current: false, metadata: {}, created_at: "2026-07-02T00:00:00Z" },
        { ...imageAsset, id: "new-image", name: "new-image.png", created_at: "2026-07-04T00:00:00Z" },
      ]),
      assetThumbnail: vi.fn().mockResolvedValue(new Blob(["thumb"], { type: "image/png" })),
      assetText: vi.fn().mockResolvedValue("A generated character prompt."),
      assetContent: vi.fn(),
      setCurrentAsset: vi.fn(),
    };
    const { container } = render(<AssetBrowser projectId="project-filters" api={api as never} readOnly={false} onCurrent={vi.fn()} />);

    await screen.findByText("new-image.png");
    expect(Array.from(container.querySelectorAll(".asset-card"), (card) => card.getAttribute("data-asset-id"))).toEqual([
      "new-image", "new-prompt", "model", "old-image",
    ]);

    fireEvent.click(screen.getByRole("button", { name: "图片" }));
    expect(screen.getByText("new-image.png")).toBeVisible();
    expect(screen.getByText("old-image.png")).toBeVisible();
    expect(screen.queryByText("new-prompt.txt")).not.toBeInTheDocument();
    expect(screen.queryByText("model.glb")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Prompt" }));
    expect(screen.getByText("new-prompt.txt")).toBeVisible();
    expect(screen.queryByText("new-image.png")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "3D 资产" }));
    expect(screen.getByText("model.glb")).toBeVisible();
    expect(screen.queryByText("new-prompt.txt")).not.toBeInTheDocument();
  });
});
