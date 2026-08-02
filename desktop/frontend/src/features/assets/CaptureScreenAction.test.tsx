import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CaptureScreenAction } from "./CaptureScreenAction";

describe("CaptureScreenAction", () => {
  it("imports the native desktop snapshot and hands off to the crop workspace", async () => {
    const host = { captureScreen: vi.fn().mockResolvedValue("capture-capability") };
    const api = {
      importImage: vi.fn().mockResolvedValue({ id: "capture-asset" }),
      setCurrentAsset: vi.fn().mockResolvedValue({}),
    };
    const onImported = vi.fn();

    render(<CaptureScreenAction projectId="project-1" api={api as never} host={host as never} onImported={onImported} />);
    fireEvent.click(screen.getByRole("button", { name: "框选截屏" }));

    expect(await screen.findByRole("status")).toHaveTextContent("请在屏幕遮罩上拖框");
    await waitFor(() => expect(host.captureScreen).toHaveBeenCalledWith("project-1"));
    expect(api.importImage).toHaveBeenCalledWith("project-1", "capture-capability", expect.stringContaining("screen-capture-"));
    expect(api.setCurrentAsset).toHaveBeenCalledWith("project-1", "capture-asset", expect.stringContaining("screen-capture-"));
    expect(onImported).toHaveBeenCalledTimes(1);
  });
});
