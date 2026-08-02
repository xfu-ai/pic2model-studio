import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CandidateWorkspace } from "./CandidateWorkspace";

describe("CandidateWorkspace", () => {
  beforeEach(() => {
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn().mockReturnValue("blob:preview"),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
  });

  afterEach(() => {
    document.body.replaceChildren();
  });

  it("shows only assets produced by the selected job without changing current asset", async () => {
    const api = {
      assets: vi.fn().mockResolvedValue([
        { id: "source", name: "source.png", asset_type: "source_image", is_current: true },
        { id: "result-a", name: "result-a.png", asset_type: "generated_image", is_current: false },
        { id: "result-b", name: "result-b.png", asset_type: "generated_image", is_current: false },
        { id: "historical", name: "historical.png", asset_type: "generated_image", is_current: false },
      ]),
      assetContent: vi.fn().mockResolvedValue(new Blob(["image"])),
      setCurrentAsset: vi.fn(),
    };

    render(
      <CandidateWorkspace
        projectId="project-1"
        api={api as never}
        assetIds={["result-b", "result-a"]}
        sourceJobId="job-12345678"
        onSelected={vi.fn()}
      />,
    );

    expect(await screen.findByText("result-b.png")).toBeVisible();
    expect(screen.getByText("result-a.png")).toBeVisible();
    expect(screen.queryByText("historical.png")).not.toBeInTheDocument();
    expect(api.setCurrentAsset).not.toHaveBeenCalled();
  });

  it("changes current asset only after explicit selection", async () => {
    const onSelected = vi.fn();
    const api = {
      assets: vi.fn().mockResolvedValue([
        { id: "result-a", name: "result-a.png", asset_type: "generated_image", is_current: false },
      ]),
      assetContent: vi.fn().mockResolvedValue(new Blob(["image"])),
      setCurrentAsset: vi.fn().mockResolvedValue(undefined),
    };
    render(
      <CandidateWorkspace
        projectId="project-1"
        api={api as never}
        assetIds={["result-a"]}
        sourceJobId="job-1"
        onSelected={onSelected}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "选择此候选" }));
    await waitFor(() =>
      expect(api.setCurrentAsset).toHaveBeenCalledWith(
        "project-1",
        "result-a",
        expect.any(String),
      ),
    );
    expect(onSelected).toHaveBeenCalled();
  });
});
