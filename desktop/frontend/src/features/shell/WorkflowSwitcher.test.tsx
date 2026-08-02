import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WorkflowSwitcher } from "./WorkflowSwitcher";

describe("WorkflowSwitcher", () => {
  afterEach(cleanup);
  it("packages six tools into four independent product workbenches", () => {
    const onSelect = vi.fn();
    render(<WorkflowSwitcher mode="image" onSelect={onSelect} />);

    const tabs = screen.getAllByRole("button");
    expect(tabs).toHaveLength(6);
    for (const stage of ["素材工作台", "创意定稿", "建模准备", "资产交付"]) {
      expect(screen.getByRole("region", { name: stage })).toBeVisible();
    }
    expect(screen.getByRole("button", { name: "当前图片" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: "当前图片" }));
    expect(onSelect).toHaveBeenCalledWith("image");
  });

  it("keeps crop selection visually anchored to the material workbench", () => {
    render(<WorkflowSwitcher mode="selection" onSelect={vi.fn()} />);
    expect(screen.getByRole("button", { name: "当前图片" })).toHaveAttribute("aria-pressed", "true");
  });
});
