import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
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
    imageGenerationJobId,
    workflowContexts,
  }: {
    mode: string;
    candidateResult?: { jobId: string; assetIds: string[] } | null;
    imageGenerationJobId?: string | null;
    workflowContexts?: typeof DEFAULT_WORKSPACE.workflow_contexts;
  }) => (
    <section>
      <h1>{mode}</h1>
      <output>
        {candidateResult
          ? `${candidateResult.jobId}:${candidateResult.assetIds.join(",")}`
          : "project-results"}
      </output>
      <output aria-label="creative image job">{imageGenerationJobId ?? "no-image-job"}</output>
      <output aria-label="prompt state">{JSON.stringify(workflowContexts?.prompt_image ?? {})}</output>
      <output aria-label="multiview state">{JSON.stringify(workflowContexts?.multiview ?? {})}</output>
      <output aria-label="model state">{JSON.stringify(workflowContexts?.model3d ?? {})}</output>
      <output aria-label="target extract state">{JSON.stringify(workflowContexts?.target_extract ?? {})}</output>
    </section>
  ),
}));

vi.mock("../agent/AgentPanel", () => ({
  AgentPanel: ({
    onJobQueued,
    onWorkspaceAction,
  }: {
    onJobQueued?(jobId: string, mode: string, jobType?: string): void;
    onWorkspaceAction?(action: Record<string, unknown>): void;
  }) => <div>
    Agent
    <button onClick={() => onWorkspaceAction?.({
      mode: "prompt_image",
      actionId: "approval-agent-image",
      prompt: "A raw English prompt from the Agent",
      promptAssetId: "prompt-agent",
      candidateCount: 4,
      aspectRatio: "16:9",
    })}>模拟 Agent 生图审批</button>
    <button onClick={() => onJobQueued?.("job-agent-image", "prompt_image", "image.generate")}>模拟 Agent 生图排队</button>
    <button onClick={() => onWorkspaceAction?.({
      mode: "prompt_image",
      jobId: "job-agent-image",
      jobType: "image.generate",
      resultAssetIds: ["result-agent-a", "result-agent-b"],
    })}>模拟 Agent 生图完成</button>
    <button onClick={() => onWorkspaceAction?.({
      mode: "compare",
      jobId: "job-agent-analysis",
      assetId: "new-content-source",
      resultAssetIds: ["new-content-analysis"],
      analysisKind: "content",
    })}>模拟 Agent 内容分析完成</button>
    <button onClick={() => onWorkspaceAction?.({
      mode: "compare",
      jobId: "job-agent-suitability",
      jobType: "image.evaluate_3d_suitability",
      assetId: "suitability-source",
      resultAssetIds: ["suitability-analysis"],
      analysisKind: "suitability",
    })}>模拟 Agent 适用性分析完成</button>
    <button onClick={() => onJobQueued?.("job-agent-rewrite", "prompt_image", "prompt.rewrite")}>模拟 Agent Prompt 重写排队</button>
    <button onClick={() => onWorkspaceAction?.({
      mode: "prompt_image",
      jobId: "job-agent-rewrite",
      jobType: "prompt.rewrite",
      resultAssetIds: ["prompt-rewritten"],
    })}>模拟 Agent Prompt 重写完成</button>
    <button onClick={() => onWorkspaceAction?.({
      mode: "candidate",
      jobId: "job-agent-candidate",
      jobType: "image.upscale",
      resultAssetIds: ["candidate-agent"],
    })}>模拟 Agent 候选图完成</button>
    <button onClick={() => onJobQueued?.("job-agent-multiview", "multiview", "multiview.generate")}>模拟 Agent 三视图排队</button>
    <button onClick={() => onWorkspaceAction?.({
      mode: "multiview",
      jobId: "job-agent-multiview",
      jobType: "multiview.generate",
      resultAssetIds: ["sheet-agent"],
    })}>模拟 Agent 三视图完成</button>
    <button onClick={() => onWorkspaceAction?.({
      mode: "multiview",
      jobId: "job-agent-detect",
      jobType: "multiview.detect_regions",
      resultAssetIds: ["selection-front", "selection-side", "selection-back"],
    })}>模拟 Agent 区域检测完成</button>
    <button onClick={() => onWorkspaceAction?.({
      mode: "multiview",
      jobId: "job-agent-regenerate",
      jobType: "multiview.regenerate_view",
      resultAssetIds: ["regenerated-side"],
    })}>模拟 Agent 单视图重生成完成</button>
    <button onClick={() => onJobQueued?.("job-agent-model", "model3d", "model3d.generate")}>模拟 Agent 3D 排队</button>
    <button onClick={() => onWorkspaceAction?.({
      mode: "model3d",
      jobId: "job-agent-model",
      jobType: "model3d.generate",
      resultAssetIds: ["model-agent"],
    })}>模拟 Agent 3D 完成</button>
    <button onClick={() => onWorkspaceAction?.({
      mode: "target_extract",
      method: "direct",
      assetId: "breakdown-sheet",
      jobType: "image.split_local",
      resultAssetIds: ["part-a", "part-b"],
    })}>模拟 Agent 本地部件拆分</button>
    <button onClick={() => onWorkspaceAction?.({
      mode: "target_extract",
      assetId: "part-a",
      jobType: "edit_image.remove_background_local",
      resultAssetIds: ["part-a-clean"],
    })}>模拟 Agent 部件去背景</button>
    <button onClick={() => onWorkspaceAction?.({
      mode: "image",
      assetId: "source",
      jobType: "image.normalize",
      resultAssetIds: ["source-resized"],
    })}>模拟 Agent 调整当前图片尺寸</button>
  </div>,
}));

import { PanelLayout } from "./PanelLayout";

describe("PanelLayout task result navigation", () => {
  afterEach(cleanup);

  it("persists Agent local part splits and replaces a processed part in target extraction", async () => {
    const initialState = structuredClone(DEFAULT_WORKSPACE);
    initialState.workflow_contexts.target_extract = {
      ...initialState.workflow_contexts.target_extract,
      method: "breakdown",
      source_asset_id: "source",
      breakdown_asset_id: "breakdown-sheet",
    };
    const api = {
      assets: vi.fn().mockResolvedValue([]),
      jobs: vi.fn().mockResolvedValue({ items: [] }),
    };
    const Harness = () => {
      const [state, setState] = useState(initialState);
      return <PanelLayout
        projectId="project-1"
        projectName="Project"
        readOnly={false}
        api={api as never}
        state={state}
        onPatch={(patch) => setState((current) => ({ ...current, ...patch }))}
      />;
    };
    render(<Harness />);

    fireEvent.click(screen.getByRole("button", { name: "模拟 Agent 本地部件拆分" }));
    await waitFor(() => expect(screen.getByLabelText("target extract state")).toHaveTextContent('"result_asset_ids":["part-a","part-b"]'));
    expect(screen.getByRole("heading", { name: "target_extract" })).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "模拟 Agent 部件去背景" }));
    await waitFor(() => expect(screen.getByLabelText("target extract state")).toHaveTextContent('"result_asset_ids":["part-a-clean","part-b"]'));
    expect(screen.getByLabelText("target extract state")).toHaveTextContent('"active_result_asset_id":"part-a-clean"');
  });

  it("recovers completed local part descendants for the current content reference", async () => {
    const initialState = structuredClone(DEFAULT_WORKSPACE);
    initialState.reference_context.content_asset_id = "parts-sheet";
    const api = {
      jobs: vi.fn().mockResolvedValue({ items: [] }),
      assets: vi.fn().mockResolvedValue([
        { id: "part-a", parent_asset_id: "parts-sheet", created_at: "2026-01-01T00:00:01Z", provenance: { parameters: { operation: "split_local", row: 0, column: 0 } } },
        { id: "part-b", parent_asset_id: "parts-sheet", created_at: "2026-01-01T00:00:02Z", provenance: { parameters: { operation: "split_local", row: 0, column: 1 } } },
        { id: "part-a-normal", parent_asset_id: "part-a", created_at: "2026-01-01T00:00:03Z", provenance: { parameters: { operation: "normalize" } } },
        { id: "part-a-clean", parent_asset_id: "part-a-normal", created_at: "2026-01-01T00:00:04Z", provenance: { parameters: { operation: "remove_background_local" } } },
      ]),
    };
    const Harness = () => {
      const [state, setState] = useState(initialState);
      return <PanelLayout
        projectId="project-1"
        projectName="Project"
        readOnly={false}
        api={api as never}
        state={state}
        onPatch={(patch) => setState((current) => ({ ...current, ...patch }))}
      />;
    };
    render(<Harness />);

    await waitFor(() => expect(screen.getByLabelText("target extract state")).toHaveTextContent('"result_asset_ids":["part-a-clean","part-b"]'));
    expect(screen.getByLabelText("target extract state")).toHaveTextContent('"source_asset_id":"parts-sheet"');
  });

  it("opens an Agent resize result as the current image", async () => {
    const api = {
      assets: vi.fn().mockResolvedValue([]),
      jobs: vi.fn().mockResolvedValue({ items: [] }),
      setCurrentAsset: vi.fn().mockResolvedValue({}),
    };
    render(
      <PanelLayout
        projectId="project-1"
        projectName="Project"
        readOnly={false}
        api={api as never}
        state={structuredClone(DEFAULT_WORKSPACE)}
        onPatch={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "模拟 Agent 调整当前图片尺寸" }));

    await waitFor(() => expect(api.setCurrentAsset).toHaveBeenCalledWith(
      "project-1",
      "source-resized",
      expect.any(String),
    ));
    expect(screen.getByRole("heading", { name: "image" })).toBeVisible();
  });

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

  it("persists an Agent image Job and reopens the creative image workspace on completion", async () => {
    const api = {
      assets: vi.fn().mockResolvedValue([]),
      jobs: vi.fn().mockResolvedValue({ items: [] }),
    };

    function StatefulPanel() {
      const [state, setState] = useState(structuredClone(DEFAULT_WORKSPACE));
      return <PanelLayout
        state={state}
        projectId="project-1"
        projectName="Project"
        readOnly={false}
        api={api as never}
        onPatch={(patch) => setState((current) => ({ ...current, ...patch }))}
      />;
    }

    render(<StatefulPanel />);

    fireEvent.click(screen.getByRole("button", { name: "模拟 Agent 生图审批" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "prompt_image" })).toBeVisible());
    fireEvent.click(screen.getByRole("button", { name: "模拟 Agent 生图排队" }));
    fireEvent.click(screen.getByRole("button", { name: "模拟 Agent 生图完成" }));

    await waitFor(() => expect(screen.getByRole("heading", { name: "prompt_image" })).toBeVisible());
    expect(screen.getByLabelText("creative image job")).toHaveTextContent("job-agent-image");
  });

  it("uses the Agent managed Prompt asset instead of persisting its raw prompt in both languages", async () => {
    const api = {
      assets: vi.fn().mockResolvedValue([]),
      jobs: vi.fn().mockResolvedValue({ items: [] }),
    };
    function StatefulPanel() {
      const [state, setState] = useState(structuredClone(DEFAULT_WORKSPACE));
      return <PanelLayout
        state={state}
        projectId="project-1"
        projectName="Project"
        readOnly={false}
        api={api as never}
        onPatch={(patch) => setState((current) => ({ ...current, ...patch }))}
      />;
    }

    render(<StatefulPanel />);
    const agentButtons = screen.getAllByRole("button").filter((button) => button.textContent?.includes("Agent"));
    fireEvent.click(agentButtons[0]);

    await waitFor(() => {
      const promptState = JSON.parse(screen.getByLabelText("prompt state").textContent ?? "{}");
      expect(promptState).toMatchObject({
        source_prompt_asset_id: "prompt-agent",
        zh_prompt: "",
        en_prompt: "",
      });
    });
  });

  it("updates the content reference when Agent content analysis completes", async () => {
    const api = {
      assets: vi.fn().mockResolvedValue([]),
      jobs: vi.fn().mockResolvedValue({ items: [] }),
    };
    const initialState = structuredClone(DEFAULT_WORKSPACE);
    initialState.reference_context = {
      ...initialState.reference_context,
      content_asset_id: "old-content-source",
      content_analysis_asset_id: "old-content-analysis",
      content_prompt_asset_id: "old-content-prompt",
      style_asset_id: "existing-style-source",
      style_analysis_asset_id: "existing-style-analysis",
      style_prompt_asset_id: "existing-style-prompt",
      merged_prompt_asset_id: "old-merged-prompt",
    };
    const onPatch = vi.fn();

    render(<PanelLayout
      state={initialState}
      projectId="project-1"
      projectName="Project"
      readOnly={false}
      api={api as never}
      onPatch={onPatch}
    />);

    fireEvent.click(screen.getByRole("button", { name: "模拟 Agent 内容分析完成" }));

    expect(onPatch).toHaveBeenCalledWith(expect.objectContaining({
      workspace_mode: "compare",
      reference_context: expect.objectContaining({
        content_asset_id: "new-content-source",
        content_analysis_asset_id: "new-content-analysis",
        content_prompt_asset_id: null,
        style_asset_id: "existing-style-source",
        style_analysis_asset_id: "existing-style-analysis",
        style_prompt_asset_id: "existing-style-prompt",
        merged_prompt_asset_id: null,
      }),
    }));
  });

  it("persists and automatically loads an Agent-generated multiview sheet", async () => {
    const api = {
      assets: vi.fn().mockResolvedValue([]),
      jobs: vi.fn().mockResolvedValue({ items: [] }),
    };

    function StatefulPanel() {
      const [state, setState] = useState(structuredClone(DEFAULT_WORKSPACE));
      return <PanelLayout
        state={state}
        projectId="project-1"
        projectName="Project"
        readOnly={false}
        api={api as never}
        onPatch={(patch) => setState((current) => ({ ...current, ...patch }))}
      />;
    }

    render(<StatefulPanel />);
    fireEvent.click(screen.getByRole("button", { name: "模拟 Agent 三视图排队" }));
    fireEvent.click(screen.getByRole("button", { name: "模拟 Agent 三视图完成" }));

    await waitFor(() => expect(screen.getByRole("heading", { name: "multiview" })).toBeVisible());
    expect(screen.getByLabelText("multiview state")).toHaveTextContent(
      JSON.stringify({
        selected: {
          source: "sheet-agent",
          front: "sheet-agent",
          side: "sheet-agent",
          back: "sheet-agent",
        },
        regions: {},
        checks: {},
        quality_confirmed: false,
        set_id: null,
        job_id: "job-agent-multiview",
        pending_action_id: null,
      }),
    );
  });

  it("keeps multiview selection outputs out of image slots and restores detected rectangles", async () => {
    const api = {
      assets: vi.fn().mockResolvedValue([]),
      jobs: vi.fn().mockResolvedValue({ items: [] }),
      selections: vi.fn().mockImplementation((_projectId: string, assetId: string) => Promise.resolve([{
        id: `selection-${assetId.replace("crop-", "")}`,
        rects: [{ x: 1, y: 2, width: 30, height: 40 }],
        revision: 1,
        status: "draft",
      }])),
    };
    const initialState = structuredClone(DEFAULT_WORKSPACE);
    initialState.workflow_contexts.multiview = {
      selected: { source: "sheet", front: "crop-front", side: "crop-side", back: "crop-back" },
      regions: {}, checks: {}, quality_confirmed: true, set_id: "set-1", job_id: null,
    };

    function StatefulPanel() {
      const [state, setState] = useState(initialState);
      return <PanelLayout state={state} projectId="project-1" projectName="Project" readOnly={false} api={api as never} onPatch={(patch) => setState((current) => ({ ...current, ...patch }))} />;
    }

    render(<StatefulPanel />);
    fireEvent.click(screen.getByRole("button", { name: "模拟 Agent 区域检测完成" }));
    await waitFor(() => expect(screen.getByLabelText("multiview state")).toHaveTextContent('"front":{"x":1,"y":2,"width":30,"height":40}'));
    expect(screen.getByLabelText("multiview state")).toHaveTextContent('"front":"crop-front"');
    expect(screen.getByLabelText("multiview state")).not.toHaveTextContent('"source":"selection-front"');
  });

  it("updates only the regenerated multiview direction from managed provenance", async () => {
    const api = {
      assets: vi.fn().mockResolvedValue([{ id: "regenerated-side", provenance: { parameters: { view: "side", multiview_set_id: "set-1" } } }]),
      jobs: vi.fn().mockResolvedValue({ items: [] }),
    };
    const initialState = structuredClone(DEFAULT_WORKSPACE);
    initialState.workflow_contexts.multiview = {
      selected: { source: "sheet", front: "front-old", side: "side-old", back: "back-old" },
      regions: {}, checks: { consistency: "passed" }, quality_confirmed: true, set_id: "set-1", job_id: null,
    };
    function StatefulPanel() {
      const [state, setState] = useState(initialState);
      return <PanelLayout state={state} projectId="project-1" projectName="Project" readOnly={false} api={api as never} onPatch={(patch) => setState((current) => ({ ...current, ...patch }))} />;
    }
    render(<StatefulPanel />);
    fireEvent.click(screen.getByRole("button", { name: "模拟 Agent 单视图重生成完成" }));
    await waitFor(() => expect(screen.getByLabelText("multiview state")).toHaveTextContent('"side":"regenerated-side"'));
    expect(screen.getByLabelText("multiview state")).toHaveTextContent('"front":"front-old"');
    expect(screen.getByLabelText("multiview state")).toHaveTextContent('"quality_confirmed":false');
  });

  it("persists Agent model generation and writes the completed GLB into the model workspace", async () => {
    const api = { assets: vi.fn().mockResolvedValue([]), jobs: vi.fn().mockResolvedValue({ items: [] }) };
    function StatefulPanel() {
      const [state, setState] = useState(structuredClone(DEFAULT_WORKSPACE));
      return <PanelLayout state={state} projectId="project-1" projectName="Project" readOnly={false} api={api as never} onPatch={(patch) => setState((current) => ({ ...current, ...patch }))} />;
    }
    render(<StatefulPanel />);
    fireEvent.click(screen.getByRole("button", { name: "模拟 Agent 3D 排队" }));
    fireEvent.click(screen.getByRole("button", { name: "模拟 Agent 3D 完成" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "model3d" })).toBeVisible());
    expect(screen.getByLabelText("model state")).toHaveTextContent('"asset_id":"model-agent"');
    expect(screen.getByLabelText("model state")).toHaveTextContent('"generation_job_id":null');
  });

  it("routes Agent Prompt rewrites to the rewrite tracker instead of the image Job slot", async () => {
    const api = { assets: vi.fn().mockResolvedValue([]), jobs: vi.fn().mockResolvedValue({ items: [] }) };
    function StatefulPanel() {
      const [state, setState] = useState(structuredClone(DEFAULT_WORKSPACE));
      return <PanelLayout state={state} projectId="project-1" projectName="Project" readOnly={false} api={api as never} onPatch={(patch) => setState((current) => ({ ...current, ...patch }))} />;
    }
    render(<StatefulPanel />);
    fireEvent.click(screen.getByRole("button", { name: "模拟 Agent Prompt 重写排队" }));
    fireEvent.click(screen.getByRole("button", { name: "模拟 Agent Prompt 重写完成" }));
    await waitFor(() => expect(screen.getByLabelText("prompt state")).toHaveTextContent('"rewrite_job_id":"job-agent-rewrite"'));
    expect(screen.getByLabelText("creative image job")).toHaveTextContent("no-image-job");
  });

  it("persists candidate scope and 3D suitability analysis results", async () => {
    const api = { assets: vi.fn().mockResolvedValue([]), jobs: vi.fn().mockResolvedValue({ items: [] }) };
    const onPatch = vi.fn();
    render(<PanelLayout state={structuredClone(DEFAULT_WORKSPACE)} projectId="project-1" projectName="Project" readOnly={false} api={api as never} onPatch={onPatch} />);
    fireEvent.click(screen.getByRole("button", { name: "模拟 Agent 候选图完成" }));
    expect(onPatch).toHaveBeenCalledWith(expect.objectContaining({ candidate_result: { job_id: "job-agent-candidate", asset_ids: ["candidate-agent"] } }));
    fireEvent.click(screen.getByRole("button", { name: "模拟 Agent 适用性分析完成" }));
    expect(onPatch).toHaveBeenCalledWith(expect.objectContaining({
      reference_context: expect.objectContaining({ suitability_asset_id: "suitability-source", suitability_analysis_asset_id: "suitability-analysis" }),
    }));
  });

  it("restores a persisted candidate result after remount", async () => {
    const api = { assets: vi.fn().mockResolvedValue([]), jobs: vi.fn().mockResolvedValue({ items: [] }) };
    const state = structuredClone(DEFAULT_WORKSPACE);
    state.workspace_mode = "candidate";
    state.candidate_result = { job_id: "restored-job", asset_ids: ["restored-a", "restored-b"] };
    render(<PanelLayout state={state} projectId="project-1" projectName="Project" readOnly={false} api={api as never} onPatch={vi.fn()} />);
    expect(screen.getByText("restored-job:restored-a,restored-b")).toBeVisible();
  });
});
