import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DEFAULT_WORKSPACE } from "../../shared/state/uiStore";

vi.mock("../jobs/JobsPanel", () => ({
  JobsPanel: ({
    onOpenResult,
  }: {
    onOpenResult(job: Record<string, unknown>): void;
  }) => (
    <button
      onClick={() =>
        onOpenResult({
          schema_version: 1,
          id: "job-image",
          job_type: "image.generate",
          status: "succeeded",
          stage: "postprocessing",
          progress: 100,
          elapsed_seconds: 5,
          estimated_seconds: null,
          provider: "local",
          cancel_capability: "not_cancellable",
          can_cancel: false,
          can_stop_waiting: false,
          output_asset_ids: ["result-a", "result-b"],
        })
      }
    >
      模拟查看结果
    </button>
  ),
}));

vi.mock("./WorkspaceRouter", () => ({
  WorkspaceRouter: ({
    mode,
    candidateResult,
  }: {
    mode: string;
    candidateResult?: { jobId: string; assetIds: string[] } | null;
  }) => (
    <section>
      <h1>{mode}</h1>
      <output>
        {candidateResult
          ? `${candidateResult.jobId}:${candidateResult.assetIds.join(",")}`
          : "project-results"}
      </output>
    </section>
  ),
}));

vi.mock("../agent/AgentPanel", () => ({
  AgentPanel: () => <div>Agent</div>,
}));

import { PanelLayout } from "./PanelLayout";

describe("PanelLayout task result navigation", () => {
  it("opens a scoped candidate workspace without changing the current asset", async () => {
    const api = {
      assets: vi.fn().mockResolvedValue([
        {
          id: "source",
          name: "source.png",
          asset_type: "source_image",
          is_current: true,
        },
      ]),
      jobs: vi.fn().mockResolvedValue({ items: [] }),
      setCurrentAsset: vi.fn(),
    };

    render(
      <PanelLayout
        state={structuredClone(DEFAULT_WORKSPACE)}
        projectId="project-1"
        projectName="Project"
        readOnly={false}
        api={api as never}
        onPatch={vi.fn()}
      />,
    );

    fireEvent.click(screen.getAllByTitle("任务")[1]);
    fireEvent.click(await screen.findByRole("button", { name: "模拟查看结果" }));

    await waitFor(() =>
      expect(screen.getByText("job-image:result-a,result-b")).toBeVisible(),
    );
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "candidate" })).toHaveFocus(),
    );
    expect(api.setCurrentAsset).not.toHaveBeenCalled();
  });
});
