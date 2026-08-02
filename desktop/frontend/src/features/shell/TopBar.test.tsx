import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TopBar } from "./TopBar";

describe("TopBar", () => {
  it("offers diagnostics as a single support-bundle action", () => {
    const onDiagnostics = vi.fn();
    render(<TopBar
      projectName="Project"
      readOnly={false}
      onTasks={vi.fn()}
      onExports={vi.fn()}
      onDiagnostics={onDiagnostics}
      onSettings={vi.fn()}
    />);

    fireEvent.click(screen.getByRole("button", { name: "导出诊断包" }));
    expect(onDiagnostics).toHaveBeenCalledOnce();
  });
});
