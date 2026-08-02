import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { JobDto } from "../../shared/api/client";
import { JobsPanel } from "./JobsPanel";

function job(patch: Partial<JobDto> = {}): JobDto {
  return {
    schema_version: 1,
    id: "job-1",
    job_type: "image.generate",
    status: "succeeded",
    stage: "postprocessing",
    progress: 100,
    provider: "meshy/default",
    elapsed_seconds: 12,
    estimated_seconds: null,
    cancel_capability: "not_cancellable",
    can_cancel: false,
    can_stop_waiting: false,
    input_asset_ids: ["source"],
    output_asset_ids: ["result-a", "result-b"],
    created_at: "2026-07-30T08:00:00Z",
    completed_at: "2026-07-30T08:00:12Z",
    ...patch,
  };
}

function apiWithJobs(items: JobDto[]) {
  return {
    jobs: vi.fn().mockResolvedValue({ items }),
    assets: vi.fn().mockResolvedValue([
      {
        id: "source",
        name: "source.png",
        asset_type: "source_image",
        mime_type: "image/png",
        is_current: true,
      },
      {
        id: "result-a",
        name: "result-a.png",
        asset_type: "generated_image",
        mime_type: "image/png",
        is_current: false,
      },
      {
        id: "result-b",
        name: "result-b.png",
        asset_type: "generated_image",
        mime_type: "image/png",
        is_current: false,
      },
    ]),
    assetContent: vi.fn().mockResolvedValue(new Blob(["image"])),
    retryJob: vi.fn(),
    decideApproval: vi.fn(),
    cancelJob: vi.fn(),
  };
}

describe("JobsPanel", () => {
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
    vi.useRealTimers();
    document.body.replaceChildren();
  });

  it("notifies and announces once when an observed job reaches terminal state", async () => {
    vi.useFakeTimers();
    const api = apiWithJobs([]);
    api.jobs
      .mockResolvedValueOnce({
        items: [job({ status: "running", stage: "creating", output_asset_ids: [] })],
      })
      .mockResolvedValue({
        items: [job({ status: "succeeded", output_asset_ids: ["result-a"] })],
      });
    const host = { notifyJobTerminal: vi.fn().mockResolvedValue(undefined) };
    render(
      <JobsPanel
        projectId="project-1"
        api={api as never}
        showHistory
        host={host as never}
      />,
    );
    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_500);
    });
    expect(host.notifyJobTerminal).toHaveBeenCalledWith("succeeded");
    expect(screen.getByText("根据 Prompt 生成图片已完成")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_500);
    });
    expect(host.notifyJobTerminal).toHaveBeenCalledTimes(1);
  });

  it("shows terminal-aware copy, asset context, and precise result action", async () => {
    const api = apiWithJobs([job()]);
    const onOpenResult = vi.fn();
    render(
      <JobsPanel
        projectId="project-1"
        api={api as never}
        showHistory
        host={{ notifyJobTerminal: vi.fn() } as never}
        onOpenResult={onOpenResult}
      />,
    );

    expect(await screen.findByText("候选图片已经生成。")).toBeVisible();
    expect(screen.queryByText(/正在按照/)).not.toBeInTheDocument();
    expect(screen.getByText("source.png")).toBeVisible();
    expect(screen.getByText("result-a.png")).toBeVisible();
    const resultAction = screen.getByRole("button", { name: "查看 2 个候选图" });
    expect(resultAction).toHaveClass("job-result-action", "primary");
    fireEvent.click(resultAction);
    expect(onOpenResult).toHaveBeenCalledWith(expect.objectContaining({ id: "job-1" }));
  });

  it("filters attention tasks and searches by asset name", async () => {
    const failed = job({
      id: "failed",
      status: "failed",
      job_type: "model3d.generate",
      output_asset_ids: [],
      error: { user_message: "模型下载失败。" },
    });
    const api = apiWithJobs([job(), failed]);
    render(
      <JobsPanel
        projectId="project-1"
        api={api as never}
        showHistory
        host={{ notifyJobTerminal: vi.fn() } as never}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: /需要处理/ }));
    expect(screen.getByText("模型下载失败。")).toBeVisible();
    expect(screen.queryByText("候选图片已经生成。")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /需要处理/ }));
    fireEvent.change(screen.getByPlaceholderText("搜索任务或资产"), {
      target: { value: "result-a" },
    });
    expect(screen.getByText("候选图片已经生成。")).toBeVisible();
    expect(screen.queryByText("模型下载失败。")).not.toBeInTheDocument();
  });

  it("orders tasks by execution time regardless of status", async () => {
    const api = apiWithJobs([
      job({
        id: "old-failed",
        status: "failed",
        output_asset_ids: [],
        created_at: "2026-07-30T08:00:00Z",
      }),
      job({
        id: "new-completed",
        status: "succeeded",
        output_asset_ids: [],
        created_at: "2026-07-30T09:00:00Z",
      }),
    ]);
    render(
      <JobsPanel
        projectId="project-1"
        api={api as never}
        showHistory
        host={{ notifyJobTerminal: vi.fn() } as never}
      />,
    );
    await waitFor(() => expect(document.querySelectorAll(".job-card")).toHaveLength(2));
    expect(document.querySelectorAll(".job-card")[0]).toHaveClass("status-succeeded");
    expect(document.querySelectorAll(".job-card")[1]).toHaveClass("status-failed");
  });

  it("creates a new paid task after a second approval", async () => {
    const failed = job({
      id: "job-paid",
      status: "failed",
      output_asset_ids: [],
      error: {
        code: "PROVIDER_FAILED",
        recommended_action: "retry",
        safe_to_retry: true,
      },
    });
    const api = apiWithJobs([failed]);
    api.retryJob.mockResolvedValue({
      status: "awaiting_ui_action",
      ui_action: { action_id: "retry-approval" },
    });
    api.decideApproval.mockResolvedValue({
      status: "queued",
      job: { job_id: "job-paid-retry" },
    });
    render(
      <JobsPanel
        projectId="project-1"
        api={api as never}
        showHistory
        host={{ notifyJobTerminal: vi.fn() } as never}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "创建新任务" }));
    expect(await screen.findByText("确认创建新的外部任务")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "确认并创建新任务" }));
    await waitFor(() =>
      expect(api.decideApproval).toHaveBeenCalledWith(
        "project-1",
        "retry-approval",
        true,
        expect.any(String),
      ),
    );
  });

  it("creates a new local task without showing paid approval", async () => {
    const local = job({
      id: "job-local",
      status: "interrupted",
      provider: "local",
      output_asset_ids: [],
      error: { safe_to_retry: true, recommended_action: "retry" },
    });
    const api = apiWithJobs([local]);
    api.retryJob.mockResolvedValue({ status: "queued" });
    render(
      <JobsPanel
        projectId="project-1"
        api={api as never}
        showHistory
        host={{ notifyJobTerminal: vi.fn() } as never}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "创建新任务" }));
    await waitFor(() => expect(api.retryJob).toHaveBeenCalled());
    expect(screen.queryByText("确认创建新的外部任务")).not.toBeInTheDocument();
  });
});
