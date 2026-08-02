import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PromptImageWorkspace } from "./PromptImageWorkspace";

function managedPromptDocument(zh: string, en: string) {
  return JSON.stringify({
    schema: "formweaver.prompt.v1",
    analysis: { zh: `${zh}的分析`, en: `analysis of ${en}` },
    generation: { zh, en },
    constraints: { preserve: [], avoid: [] },
  });
}

const managedPrompt = managedPromptDocument("银色骑士站在雨中的城堡前", "a silver knight before a castle in rain");
const rewrittenPrompt = managedPromptDocument("白色背景中的产品概念图，主体突出，光影自然", "product concept on a white background, prominent subject, natural lighting");

afterEach(cleanup);

describe("PromptImageWorkspace", () => {
  it("offers exactly 1, 2, or 4 prompt-image candidates", () => {
    render(<PromptImageWorkspace projectId="project-1" api={{ job: vi.fn() } as never} onModeChange={vi.fn()} />);

    const group = screen.getByRole("group", { name: "候选数量" });
    expect(within(group).getAllByRole("button").map((button) => button.textContent)).toEqual(["1", "2", "4"]);
    fireEvent.click(within(group).getByRole("button", { name: "1" }));
    expect(within(group).getByRole("button", { name: "1" })).toHaveAttribute("aria-pressed", "true");
  });

  it("clears the editable prompt without creating a job", () => {
    const api = { job: vi.fn() };
    render(<PromptImageWorkspace projectId="project-1" api={api as never} onModeChange={vi.fn()} />);

    const input = screen.getByRole("textbox", { name: "中文 Prompt" });
    fireEvent.change(input, { target: { value: "temporary prompt" } });
    fireEvent.click(screen.getByRole("button", { name: "清空" }));

    expect(input).toHaveValue("");
    expect(api.job).not.toHaveBeenCalled();
  });

  it("rewrites a local prompt through Gemini, refills the original language, and generates from the rewritten asset", async () => {
    const api = {
      savePromptVersion: vi.fn().mockResolvedValue({ asset: { id: "rewrite-source" } }),
      invokeTool: vi.fn().mockImplementation((_projectId, toolName) => Promise.resolve(
        toolName === "prompt.rewrite"
          ? { ok: true, status: "queued", job: { job_id: "rewrite-job" } }
          : { ok: true, status: "queued", job: { job_id: "image-job" } },
      )),
      job: vi.fn().mockResolvedValue({
        id: "rewrite-job",
        status: "succeeded",
        stage: "completed",
        output_asset_ids: ["rewritten-prompt"],
      }),
      assetText: vi.fn().mockResolvedValue(rewrittenPrompt),
      decideApproval: vi.fn(),
    };
    const onJobQueued = vi.fn();
    render(<PromptImageWorkspace projectId="project-1" api={api as never} onModeChange={vi.fn()} onJobQueued={onJobQueued} />);

    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "product concept" } });
    fireEvent.click(screen.getByRole("button", { name: "智能扩写" }));

    expect(await screen.findByDisplayValue("product concept on a white background, prominent subject, natural lighting")).toBeVisible();
    expect(api.savePromptVersion).toHaveBeenCalledWith(
      "project-1",
      expect.objectContaining({ zhPrompt: "product concept", enPrompt: "product concept", kind: "image" }),
      expect.stringMatching(/^save-prompt-for-rewrite-/),
    );
    expect(api.invokeTool).toHaveBeenCalledWith(
      "project-1",
      "prompt.rewrite",
      expect.objectContaining({
        prompt_asset_id: "rewrite-source",
        provider_profile: "gemini/google/default",
        model: "gemini-flash-lite-latest",
      }),
      expect.stringMatching(/^rewrite-image-prompt-/),
      { providerProfile: "gemini/google/default" },
    );
    fireEvent.click(screen.getByRole("button", { name: "中文" }));
    expect(screen.getByRole("textbox", { name: "中文 Prompt" })).toHaveValue("白色背景中的产品概念图，主体突出，光影自然");
    fireEvent.click(screen.getByRole("button", { name: "English" }));
    fireEvent.click(screen.getByRole("button", { name: "确认并生成" }));
    await waitFor(() => expect(onJobQueued).toHaveBeenCalledWith("image-job"));
    expect(api.invokeTool).toHaveBeenLastCalledWith(
      "project-1",
      "image.generate",
      expect.objectContaining({ prompt_asset_id: "rewritten-prompt" }),
      expect.stringMatching(/^generate-from-prompt-/),
      { providerProfile: "image-generation/auto" },
    );
    expect(api.savePromptVersion).toHaveBeenCalledTimes(1);
  });

  it("does not overwrite edits made while a rewrite job is running", async () => {
    let finishJob: ((job: object) => void) | undefined;
    const jobResult = new Promise<object>((resolve) => { finishJob = resolve; });
    const api = {
      savePromptVersion: vi.fn().mockResolvedValue({ asset: { id: "rewrite-source" } }),
      invokeTool: vi.fn().mockResolvedValue({ ok: true, status: "queued", job: { job_id: "rewrite-job" } }),
      job: vi.fn().mockReturnValue(jobResult),
      assetText: vi.fn().mockResolvedValue(rewrittenPrompt),
    };
    render(<PromptImageWorkspace projectId="project-1" api={api as never} onModeChange={vi.fn()} />);

    fireEvent.change(screen.getByRole("textbox"), { target: { value: "product concept" } });
    fireEvent.click(screen.getByRole("button", { name: "智能扩写" }));
    await waitFor(() => expect(api.job).toHaveBeenCalledWith("project-1", "rewrite-job"));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "user changed this while waiting" } });
    finishJob?.({ id: "rewrite-job", status: "succeeded", stage: "completed", output_asset_ids: ["rewritten-prompt"] });

    expect(await screen.findByText(/你在等待期间修改了 Prompt/)).toBeVisible();
    expect(screen.getByRole("textbox")).toHaveValue("user changed this while waiting");
    fireEvent.click(screen.getByRole("button", { name: "应用扩写结果" }));
    expect(screen.getByRole("textbox")).toHaveValue("product concept on a white background, prominent subject, natural lighting");
  });

  it("prefills the input from the merged managed prompt and submits a directly queued generation", async () => {
    const api = {
      assetText: vi.fn().mockResolvedValue(managedPrompt),
      savePromptVersion: vi.fn().mockResolvedValue({ asset: { id: "saved-prompt" } }),
      invokeTool: vi.fn().mockResolvedValue({ ok: true, status: "queued", job: { job_id: "image-job" } }),
      decideApproval: vi.fn(),
      job: vi.fn(),
    };
    const onJobQueued = vi.fn();
    render(<PromptImageWorkspace projectId="project-1" api={api as never} onModeChange={vi.fn()} mergedPromptAssetId="merged-prompt" onJobQueued={onJobQueued} />);

    expect(await screen.findByDisplayValue("银色骑士站在雨中的城堡前")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "确认并生成" }));
    await waitFor(() => expect(onJobQueued).toHaveBeenCalledWith("image-job"));
    expect(api.decideApproval).not.toHaveBeenCalled();
  });

  it("treats each explicit generate click as a distinct submission request", async () => {
    const api = {
      savePromptVersion: vi.fn().mockResolvedValue({ asset: { id: "saved-prompt" } }),
      invokeTool: vi.fn()
        .mockResolvedValueOnce({
          ok: true,
          status: "awaiting_ui_action",
          ui_action: { action_id: "approval-1" },
        })
        .mockResolvedValueOnce({
          ok: true,
          status: "awaiting_ui_action",
          ui_action: { action_id: "approval-2" },
        }),
      decideApproval: vi.fn()
        .mockResolvedValueOnce({ ok: true, status: "queued", job: { job_id: "job-1" } })
        .mockResolvedValueOnce({ ok: true, status: "queued", job: { job_id: "job-2" } }),
      job: vi.fn(),
    };
    const onJobQueued = vi.fn();
    render(<PromptImageWorkspace
      projectId="project-1"
      api={api as never}
      onModeChange={vi.fn()}
      onJobQueued={onJobQueued}
    />);

    fireEvent.change(screen.getByRole("textbox"), { target: { value: "new image" } });
    fireEvent.click(screen.getByRole("button", { name: "确认并生成" }));
    await waitFor(() => expect(onJobQueued).toHaveBeenCalledWith("job-1"));
    fireEvent.click(screen.getByRole("button", { name: "确认并生成" }));
    await waitFor(() => expect(onJobQueued).toHaveBeenCalledWith("job-2"));

    const firstRequestId = api.invokeTool.mock.calls[0][3];
    const secondRequestId = api.invokeTool.mock.calls[1][3];
    expect(firstRequestId).not.toBe(secondRequestId);
    expect(api.invokeTool).toHaveBeenCalledTimes(2);
  });

  it("replaces a cached prompt when a newly merged managed Prompt is handed off", async () => {
    const api = {
      assetText: vi.fn().mockResolvedValue(managedPrompt),
      job: vi.fn(),
    };
    const onWorkflowContextChange = vi.fn();
    render(<PromptImageWorkspace
      projectId="project-1"
      api={api as never}
      onModeChange={vi.fn()}
      mergedPromptAssetId="new-merged-prompt"
      workflowContext={{
        zh_prompt: "stale cached robot prompt",
        en_prompt: "",
        display_language: "zh",
        source_prompt_asset_id: "old-merged-prompt",
        candidate_count: 2,
        aspect_ratio: "1:1",
        selected_candidate_id: null,
        job_id: null,
        rewrite_job_id: null,
      }}
      onWorkflowContextChange={onWorkflowContextChange}
    />);

    expect(await screen.findByDisplayValue("银色骑士站在雨中的城堡前")).toBeVisible();
    expect(screen.queryByDisplayValue("stale cached robot prompt")).not.toBeInTheDocument();
    await waitFor(() => expect(onWorkflowContextChange).toHaveBeenLastCalledWith(
      expect.objectContaining({
        zh_prompt: "银色骑士站在雨中的城堡前",
        en_prompt: "a silver knight before a castle in rain",
        source_prompt_asset_id: "new-merged-prompt",
      }),
    ));
  });

  it("preserves edits when reopening the same merged Prompt asset", () => {
    const api = {
      assetText: vi.fn(),
      job: vi.fn(),
    };
    render(<PromptImageWorkspace
      projectId="project-1"
      api={api as never}
      onModeChange={vi.fn()}
      mergedPromptAssetId="same-merged-prompt"
      workflowContext={{
        zh_prompt: "",
        en_prompt: "user edited generation prompt",
        display_language: "en",
        source_prompt_asset_id: "same-merged-prompt",
        candidate_count: 4,
        aspect_ratio: "16:9",
        selected_candidate_id: null,
        job_id: null,
        rewrite_job_id: null,
      }}
    />);

    expect(screen.getByRole("textbox")).toHaveValue("user edited generation prompt");
    expect(api.assetText).not.toHaveBeenCalled();
  });

  it("does not persist an unchanged workflow context again when callback identity changes", () => {
    const api = {
      assetText: vi.fn(),
      job: vi.fn(),
    };
    const context = {
      zh_prompt: "",
      en_prompt: "user edited generation prompt",
      display_language: "en" as const,
      source_prompt_asset_id: "same-merged-prompt",
      candidate_count: 4,
      aspect_ratio: "16:9",
      selected_candidate_id: null,
      job_id: null,
      rewrite_job_id: null,
    };
    const firstChange = vi.fn();
    const secondChange = vi.fn();
    const { rerender } = render(<PromptImageWorkspace
      projectId="project-1"
      api={api as never}
      onModeChange={vi.fn()}
      mergedPromptAssetId="same-merged-prompt"
      workflowContext={context}
      onWorkflowContextChange={firstChange}
    />);

    rerender(<PromptImageWorkspace
      projectId="project-1"
      api={api as never}
      onModeChange={vi.fn()}
      mergedPromptAssetId="same-merged-prompt"
      workflowContext={context}
      onWorkflowContextChange={secondChange}
    />);

    expect(firstChange).not.toHaveBeenCalled();
    expect(secondChange).not.toHaveBeenCalled();
  });

  it("sets the selected candidate as current and exports it through a native capability", async () => {
    const generatedAssets = [
      { id: "generated-1", name: "generated-1.png", asset_type: "generated_image", is_current: true, metadata: {} },
      { id: "generated-2", name: "generated-2.png", asset_type: "generated_image", is_current: false, metadata: {} },
    ];
    const api = {
      job: vi.fn().mockResolvedValue({
        id: "image-job",
        status: "succeeded",
        stage: "completed",
        output_asset_ids: generatedAssets.map((asset) => asset.id),
      }),
      assets: vi.fn().mockResolvedValue(generatedAssets),
      assetContent: vi.fn().mockResolvedValue(new Blob(["png"], { type: "image/png" })),
      setCurrentAsset: vi.fn().mockResolvedValue({}),
      exportAsset: vi.fn().mockResolvedValue({ asset_id: "generated-2", name: "generated-2.png", bytes: 3 }),
    };
    const host = { chooseExportDirectory: vi.fn().mockResolvedValue("export-capability") };
    const onCurrentAssetChange = vi.fn();
    render(<PromptImageWorkspace
      projectId="project-1"
      api={api as never}
      host={host as never}
      onModeChange={vi.fn()}
      generationJobId="image-job"
      onCurrentAssetChange={onCurrentAssetChange}
    />);

    fireEvent.click(await screen.findByRole("button", { name: "选择 generated-2.png" }));
    fireEvent.click(screen.getByRole("button", { name: "设为当前资产" }));
    await waitFor(() => expect(api.setCurrentAsset).toHaveBeenCalledWith(
      "project-1",
      "generated-2",
      expect.stringMatching(/^select-generated-image-/),
    ));
    expect(onCurrentAssetChange).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "已是当前资产" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "导出 PNG" }));
    await waitFor(() => expect(api.exportAsset).toHaveBeenCalledWith(
      "project-1",
      "generated-2",
      "export-capability",
      expect.stringMatching(/^export-generated-image-/),
    ));
    expect(host.chooseExportDirectory).toHaveBeenCalledWith("project-1");
    expect(await screen.findByRole("status")).toHaveTextContent("generated-2.png 已导出到所选文件夹。");
  });

  it("shows a non-spinning failure message when the tracked job has an execution error", async () => {
    const api = {
      job: vi.fn().mockResolvedValue({ status: "running", stage: "postprocessing", error: { user_message: "任务在安全边界被中断。" } }),
    };
    render(<PromptImageWorkspace projectId="project-1" api={api as never} onModeChange={vi.fn()} generationJobId="failed-job" />);

    expect(await screen.findByRole("alert")).toHaveTextContent("任务在安全边界被中断。");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("does not expose an in-flight paid submission checkpoint as manual recovery", async () => {
    const api = {
      job: vi.fn().mockResolvedValue({
        id: "running-job",
        status: "running",
        stage: "creating",
        resume_class: "unknown_submission",
        recovery_actions: ["confirm_new_submission"],
        output_asset_ids: [],
        error: null,
      }),
    };
    render(<PromptImageWorkspace
      projectId="project-1"
      api={api as never}
      onModeChange={vi.fn()}
      generationJobId="running-job"
    />);

    await waitFor(() => expect(api.job).toHaveBeenCalled());
    expect(screen.queryByText("上次提交结果不确定，已停止自动重试")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "我已核对远端账户，确认重新提交" })).not.toBeInTheDocument();
  });

  it("requires explicit account confirmation before replacing an unknown submission job", async () => {
    const api = {
      job: vi.fn().mockResolvedValue({
        id: "unknown-job",
        status: "interrupted",
        stage: "unknown_submission",
        resume_class: "unknown_submission",
        recovery_actions: ["confirm_new_submission"],
        output_asset_ids: [],
        error: {
          code: "JOB_UNKNOWN_SUBMISSION",
          user_message: "The submission result is unknown.",
          safe_to_retry: false,
          recommended_action: "query_remote",
        },
      }),
      confirmNewSubmission: vi.fn().mockResolvedValue({
        ok: true,
        status: "awaiting_ui_action",
        ui_action: { action_id: "confirmation-approval" },
      }),
      decideApproval: vi.fn().mockResolvedValue({
        ok: true,
        status: "queued",
        job: { job_id: "replacement-job", stage: "queued" },
      }),
    };
    const onJobQueued = vi.fn();
    render(<PromptImageWorkspace
      projectId="project-1"
      api={api as never}
      onModeChange={vi.fn()}
      generationJobId="unknown-job"
      onJobQueued={onJobQueued}
    />);

    expect(await screen.findByText("上次提交结果不确定，已停止自动重试")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "我已核对远端账户，确认重新提交" }));

    await waitFor(() => expect(api.confirmNewSubmission).toHaveBeenCalledWith(
      "project-1",
      "unknown-job",
      expect.stringMatching(/^confirm-new-image-submission-/),
    ));
    expect(api.decideApproval).toHaveBeenCalledWith(
      "project-1",
      "confirmation-approval",
      true,
      expect.stringMatching(/^approve-confirmed-image-submission-/),
    );
    expect(onJobQueued).toHaveBeenCalledWith("replacement-job");
  });
});
