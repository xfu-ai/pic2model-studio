import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { WorkspaceMode } from "../../shared/api/client";
import { WorkspaceRouter } from "./WorkspaceRouter";

vi.mock("../canvas/PromptImageWorkspace", () => ({
  PromptImageWorkspace: ({ onWorkflowContextChange }: { onWorkflowContextChange?(context: Record<string, unknown>): void }) => (
    <button onClick={() => onWorkflowContextChange?.({
      zh_prompt: "rewritten Chinese prompt",
      en_prompt: "rewritten English prompt",
      display_language: "zh",
      source_prompt_asset_id: "rewritten-prompt",
      candidate_count: 2,
      aspect_ratio: "1:1",
      selected_candidate_id: null,
      job_id: null,
      rewrite_job_id: null,
    })}>persist-prompt</button>
  ),
}));

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

  it("makes an accepted Prompt version the reference used by later refreshes", () => {
    const onWorkflowContextChange = vi.fn();
    const onReferenceContextChange = vi.fn();
    render(<WorkspaceRouter
      mode="prompt_image"
      onRecover={vi.fn()}
      projectId="project-1"
      api={{} as never}
      onWorkflowContextChange={onWorkflowContextChange}
      onReferenceContextChange={onReferenceContextChange}
    />);

    fireEvent.click(screen.getByRole("button", { name: "persist-prompt" }));

    expect(onWorkflowContextChange).toHaveBeenCalledWith(expect.objectContaining({
      prompt_image: expect.objectContaining({ source_prompt_asset_id: "rewritten-prompt" }),
    }));
    expect(onReferenceContextChange).toHaveBeenCalledWith({ merged_prompt_asset_id: "rewritten-prompt" });
  });
});
