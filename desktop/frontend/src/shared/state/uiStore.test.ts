import { afterEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_WORKSPACE, parseWorkspaceState, WorkspaceStore } from "./uiStore";

describe("WorkspaceStore", () => {
  afterEach(() => vi.useRealTimers());
  it("repairs corrupt state and clamps the persisted panel width", () => {
    expect(parseWorkspaceState("not-json").workspace_mode).toBe("error_diagnostics");
    expect(parseWorkspaceState('{"workspace_mode":"image","agent_panel_width":900}').agent_panel_width).toBe(520);
    expect(parseWorkspaceState(JSON.stringify({
      workflow_contexts: {
        prompt_image: {
          zh_prompt: "new Chinese prompt",
          en_prompt: "new English prompt",
          display_language: "en",
          source_prompt_asset_id: "merged-prompt-2",
        },
      },
    })).workflow_contexts.prompt_image.source_prompt_asset_id).toBe("merged-prompt-2");
  });

  it("restores bilingual prompt-image state", () => {
    const bilingual = parseWorkspaceState(JSON.stringify({
      workflow_contexts: {
        prompt_image: {
          zh_prompt: "中文扩写结果",
          en_prompt: "English rewrite",
          display_language: "zh",
          rewrite_job_id: "rewrite-job",
        },
      },
    })).workflow_contexts.prompt_image;
    expect(bilingual).toEqual(expect.objectContaining({
      zh_prompt: "中文扩写结果",
      en_prompt: "English rewrite",
      display_language: "zh",
      rewrite_job_id: "rewrite-job",
    }));
  });

  it("keeps supported prompt-image counts and repairs unsupported stored counts", () => {
    const parseCount = (candidateCount: number) => parseWorkspaceState(JSON.stringify({
      workflow_contexts: { prompt_image: { candidate_count: candidateCount } },
    })).workflow_contexts.prompt_image.candidate_count;

    expect(parseCount(1)).toBe(1);
    expect(parseCount(2)).toBe(2);
    expect(parseCount(4)).toBe(4);
    expect(parseCount(3)).toBe(2);
  });

  it("restores the exact 3D generation job selected for automatic preview replacement", () => {
    const model3d = parseWorkspaceState(JSON.stringify({
      workflow_contexts: {
        model3d: {
          asset_id: "previous-model",
          target_triangles: 75000,
          generation_job_id: "generation-job-1",
        },
      },
    })).workflow_contexts.model3d;

    expect(model3d).toEqual({
      asset_id: "previous-model",
      target_triangles: 75000,
      generation_job_id: "generation-job-1",
    });
  });

  it("restores a scoped candidate result and suitability analysis references", () => {
    const restored = parseWorkspaceState(JSON.stringify({
      candidate_result: { job_id: "candidate-job", asset_ids: ["candidate-a", "candidate-b"] },
      reference_context: {
        suitability_asset_id: "source-image",
        suitability_analysis_asset_id: "suitability-report",
      },
    }));
    expect(restored.candidate_result).toEqual({
      job_id: "candidate-job",
      asset_ids: ["candidate-a", "candidate-b"],
    });
    expect(restored.reference_context).toEqual(expect.objectContaining({
      suitability_asset_id: "source-image",
      suitability_analysis_asset_id: "suitability-report",
    }));
  });

  it("persists a debounced state PATCH and ignores duplicate workspace actions", async () => {
    const updateWorkspaceState = vi.fn().mockResolvedValue({ ...DEFAULT_WORKSPACE, workspace_mode: "image" });
    const store = new WorkspaceStore(DEFAULT_WORKSPACE, "project-1", { updateWorkspaceState } as never, 0);
    expect(store.applyWorkspaceAction("action-1", { workspace_mode: "image" })).toBe(true);
    expect(store.applyWorkspaceAction("action-1", { workspace_mode: "compare" })).toBe(false);
    await store.flush();
    expect(updateWorkspaceState).toHaveBeenCalledWith("project-1", expect.objectContaining({ workspace_mode: "image" }), expect.stringMatching(/^workspace-/));
  });

 it("does not let an older save response overwrite newer local workspace state", async () => {
    vi.useFakeTimers();
 let resolveFirst!: (value: typeof DEFAULT_WORKSPACE) => void;
 const staleUpdateWorkspaceState = vi.fn()
 .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
 .mockResolvedValueOnce({ ...DEFAULT_WORKSPACE, workspace_mode: "multiview" });
 const staleStore = new WorkspaceStore(DEFAULT_WORKSPACE, "project-1", { updateWorkspaceState: staleUpdateWorkspaceState } as never, 0);
 staleStore.update({ workspace_mode: "target_extract" });
 const firstSave = staleStore.flush();
 staleStore.update({ workspace_mode: "multiview" });
 resolveFirst({ ...DEFAULT_WORKSPACE, workspace_mode: "target_extract" });
 await firstSave;
 expect(staleStore.snapshot().workspace_mode).toBe("multiview");
 await staleStore.flush();
 expect(staleUpdateWorkspaceState).toHaveBeenLastCalledWith("project-1", expect.objectContaining({ workspace_mode: "multiview" }), expect.stringMatching(/^workspace-/));
 });

 it("retries a recoverable database-busy workspace save without changing the requested state", async () => {
 vi.useFakeTimers();
 const updateWorkspaceState = vi.fn()
 .mockRejectedValueOnce({ code: "DATABASE_BUSY" })
      .mockResolvedValueOnce({ ...DEFAULT_WORKSPACE, workspace_mode: "candidate" });
    const store = new WorkspaceStore(DEFAULT_WORKSPACE, "project-1", { updateWorkspaceState } as never, 0);
    store.update({ workspace_mode: "candidate" });
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(1_000);
    expect(updateWorkspaceState).toHaveBeenCalledTimes(2);
    expect(updateWorkspaceState).toHaveBeenLastCalledWith("project-1", expect.objectContaining({ workspace_mode: "candidate" }), expect.stringMatching(/^workspace-/));
  });
});
