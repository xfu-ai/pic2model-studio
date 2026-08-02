import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HostClient } from "../../shared/host/client";
import { ScreenCapturePicker } from "./ScreenCapturePicker";

describe("ScreenCapturePicker", () => {
  beforeEach(() => vi.stubGlobal("PointerEvent", MouseEvent));
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    document.body.replaceChildren();
  });

  it("keeps the default native client stable while the preview renders", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:desktop") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const preview = vi.spyOn(HostClient.prototype, "screenCapturePreview").mockResolvedValue(new Uint8Array([1, 2, 3]).buffer);

    render(<ScreenCapturePicker token="capture-token" />);

    await waitFor(() => expect(preview).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(document.querySelector(".screen-capture-stage img")).not.toBeNull());
    expect(preview).toHaveBeenCalledWith("capture-token");
  });

  it("lets the user drag a normalized region and confirms only that region", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:desktop") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const host = {
      screenCapturePreview: vi.fn().mockResolvedValue(new Uint8Array([1, 2, 3]).buffer),
      completeScreenCapture: vi.fn().mockResolvedValue(undefined),
      cancelScreenCapture: vi.fn().mockResolvedValue(undefined),
    };
    render(<ScreenCapturePicker
      token="capture-token"
      sourceSize={{ width: 7680, height: 2160 }}
      host={host}
    />);
    const stage = screen.getByLabelText("屏幕截图框选区域");
    Object.defineProperty(stage, "getBoundingClientRect", { configurable: true, value: () => ({ left: 0, top: 0, width: 1000, height: 500, right: 1000, bottom: 500 }) });
    Object.defineProperty(stage, "setPointerCapture", { configurable: true, value: vi.fn() });
    Object.defineProperty(stage, "hasPointerCapture", { configurable: true, value: vi.fn().mockReturnValue(false) });
    await waitFor(() => expect(host.screenCapturePreview).toHaveBeenCalledWith("capture-token"));

    fireEvent.pointerDown(stage, { button: 0, pointerId: 1, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(stage, { pointerId: 1, clientX: 700, clientY: 400 });
    fireEvent.pointerUp(stage, { pointerId: 1, clientX: 700, clientY: 400 });
    expect(screen.getByLabelText("截图尺寸")).toHaveTextContent("4608 × 1296 px");
    fireEvent.click(screen.getByRole("button", { name: "完成截图" }));

    expect(host.completeScreenCapture).toHaveBeenCalledTimes(1);
    const [confirmedToken, confirmedRect] = host.completeScreenCapture.mock.calls[0];
    expect(confirmedToken).toBe("capture-token");
    expect(confirmedRect.x).toBeCloseTo(0.1);
    expect(confirmedRect.y).toBeCloseTo(0.2);
    expect(confirmedRect.width).toBeCloseTo(0.6);
    expect(confirmedRect.height).toBeCloseTo(0.6);
  });

  it("supports cancelling without confirming a region", async () => {
    const host = {
      screenCapturePreview: vi.fn().mockResolvedValue(new ArrayBuffer(0)),
      completeScreenCapture: vi.fn(),
      cancelScreenCapture: vi.fn().mockResolvedValue(undefined),
    };
    render(<ScreenCapturePicker token="capture-token" host={host} />);
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    await waitFor(() => expect(host.cancelScreenCapture).toHaveBeenCalledWith("capture-token"));
    expect(host.completeScreenCapture).not.toHaveBeenCalled();
  });
});
