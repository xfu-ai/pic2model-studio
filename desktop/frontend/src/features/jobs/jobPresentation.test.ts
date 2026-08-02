import { describe, expect, it } from "vitest";
import type { JobDto } from "../../shared/api/client";
import {
  jobPresentation,
  jobSummary,
  resultActionLabel,
  taskTypeOptions,
} from "./jobPresentation";

function job(patch: Partial<JobDto>): JobDto {
  return {
    schema_version: 1,
    id: "job-1",
    job_type: "image.generate",
    status: "running",
    stage: "remote_running",
    progress: 50,
    elapsed_seconds: 10,
    estimated_seconds: null,
    provider: "meshy/default",
    cancel_capability: "not_cancellable",
    can_cancel: false,
    can_stop_waiting: false,
    output_asset_ids: [],
    ...patch,
  };
}

describe("jobPresentation", () => {
  it("uses terminal-aware copy", () => {
    const completed = job({
      status: "succeeded",
      stage: "postprocessing",
      output_asset_ids: ["a", "b"],
    });
    expect(jobSummary(completed)).toBe("候选图片已经生成。");
    expect(jobSummary(completed)).not.toContain("正在");
    expect(resultActionLabel(completed)).toBe("查看 2 个候选图");
  });

  it("presents the bundled offline upscale job as an image result", () => {
    const local = job({
      job_type: "image.upscale_local",
      status: "succeeded",
      stage: "verifying",
      output_asset_ids: ["upscaled"],
      provider: "local",
    });
    expect(jobPresentation(local).title).toBe("本地放大并增强图片");
    expect(jobSummary(local)).toBe("本地增强后的图片已经生成。");
    expect(resultActionLabel(local)).toBe("查看候选图");
  });

  it("groups editing and single-view regeneration under user-facing task categories", () => {
    expect(jobPresentation(job({ job_type: "image.inpaint_selection" })).title).toBe(
      "AI 图片编辑",
    );
    expect(jobPresentation(job({ job_type: "multiview.regenerate_view" })).title).toBe(
      "三视图修正",
    );
    expect(jobSummary(job({ job_type: "image.inpaint_selection" }))).toContain("选区");
    expect(jobSummary(job({ job_type: "multiview.regenerate_view" }))).toContain("视图");
  });

  it("uses the provider error message for failed tasks", () => {
    expect(
      jobSummary(
        job({
          status: "failed",
          error: { user_message: "结果下载失败。" },
        }),
      ),
    ).toBe("结果下载失败。");
  });

  it("provides a useful fallback for unknown job types", () => {
    const unknown = job({ job_type: "future.operation" });
    expect(jobPresentation(unknown).title).toBe("后台任务 · future.operation");
    expect(jobSummary(unknown)).toContain("正在处理");
  });

  it("builds deduplicated human-readable type options", () => {
    const options = taskTypeOptions([
      job({ job_type: "model3d.generate" }),
      job({ id: "job-2", job_type: "image.generate" }),
      job({ id: "job-3", job_type: "image.generate" }),
    ]);
    expect(options).toHaveLength(2);
    expect(options.map((option) => option.label)).toEqual(
      expect.arrayContaining(["图片生成 3D 模型", "根据 Prompt 生成图片"]),
    );
  });
});
