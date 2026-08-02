import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { invoke } from "@tauri-apps/api/core";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "./AppShell";

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn().mockRejectedValue(new Error("desktop unavailable")),
}));

describe("AppShell", () => {
  afterEach(cleanup);

  it("keeps a recoverable offline state accessible", async () => {
    render(<AppShell />);
    expect(await screen.findByRole("heading", { name: "本地服务暂时不可用" })).toBeVisible();
    expect(screen.getByRole("button", { name: "重新连接" })).toBeVisible();
  });

  it("requests a fresh native session when reconnecting", async () => {
    render(<AppShell />);
    fireEvent.click(await screen.findByRole("button", { name: "重新连接" }));
    await waitFor(() => expect(invoke).toHaveBeenCalledTimes(2));
  });

  it("closes the focus-trapped confirmation dialog with Escape", () => {
    render(<AppShell />);
    fireEvent.click(screen.getByRole("button", { name: "测试确认对话框" }));
    expect(screen.getByRole("alertdialog")).toBeVisible();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("announces and dismisses toasts", () => {
    render(<AppShell />);
    fireEvent.click(screen.getByRole("button", { name: "测试确认对话框" }));
    fireEvent.click(screen.getByRole("button", { name: "确认离开" }));
    expect(screen.getByText("离开操作已确认。")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "关闭提示" }));
    expect(screen.queryByText("离开操作已确认。")).not.toBeInTheDocument();
  });
});
