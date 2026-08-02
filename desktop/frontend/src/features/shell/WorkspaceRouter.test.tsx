import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { WorkspaceMode } from "../../shared/api/client";
import { WorkspaceRouter } from "./WorkspaceRouter";

describe("WorkspaceRouter", () => {
  const modes: WorkspaceMode[] = ["empty", "prompt_image", "image", "compare", "selection", "target_extract", "candidate", "multiview", "model3d", "task_waiting", "error_diagnostics"];
  it("renders every registered workspace and supplies a recovery action", () => {
    for (const mode of modes) {
      const view = render(<WorkspaceRouter mode={mode} onRecover={vi.fn()} />);
      expect(screen.getByRole("heading")).toBeVisible();
      if (mode === "error_diagnostics") expect(screen.getByRole("button", { name: "恢复默认工作台" })).toBeVisible();
      view.unmount();
    }
  });
});
