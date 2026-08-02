import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TaskBar } from "./TaskBar";

describe("TaskBar", () => {
  it("shows the newest valid active job instead of an older quality-gate interruption", async () => {
    const api = {
      jobs: vi.fn().mockResolvedValue({
        items: [
          {
            id: "old-quality-gate",
            status: "interrupted",
            stage: "postprocessing",
            error: { code: "MULTIVIEW_MANUAL_CONFIRMATION_REQUIRED" },
          },
          {
            id: "current-tripo-job",
            status: "waiting",
            stage: "remote_queued",
            error: null,
          },
        ],
      }),
    };
    render(<TaskBar projectId="project-1" api={api as never} />);
    await waitFor(() =>
      expect(screen.getByText("remote_queued · waiting")).toBeVisible(),
    );
    expect(screen.queryByText("postprocessing · interrupted")).not.toBeInTheDocument();
  });
});
