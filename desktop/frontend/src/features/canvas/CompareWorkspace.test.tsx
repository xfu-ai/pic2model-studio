import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AssetDto } from "../../shared/api/client";
import { CompareWorkspace } from "./CompareWorkspace";
import { PromptParameterDrawer } from "./PromptParameterDrawer";

const content: AssetDto = { id: "content-image", asset_type: "source_image", name: "Character.png", is_current: true, metadata: { width: 1024, height: 1024, format: "png" }, provenance: { model: "import", parameters: {} }, version_no: 1 };
const style: AssetDto = { id: "style-image", asset_type: "source_image", name: "Oil-reference.png", is_current: false, metadata: { width: 1024, height: 1024, format: "png" }, provenance: { model: "import", parameters: {} }, version_no: 1 };
const contentPrompt: AssetDto = { id: "content-prompt", asset_type: "prompt", name: "content-prompt.txt", is_current: false, metadata: {}, version_no: 1 };
const stylePrompt: AssetDto = { id: "style-prompt", asset_type: "prompt", name: "style-prompt.txt", is_current: false, metadata: {}, version_no: 1 };
const mergedPrompt: AssetDto = { id: "merged-prompt", asset_type: "prompt", name: "merged-prompt.txt", is_current: false, metadata: {}, version_no: 1 };

function managedPrompt(zh: string, en: string) {
  return JSON.stringify({
    schema: "formweaver.prompt.v1",
    analysis: { zh: `${zh}的分析`, en: `analysis of ${en}` },
    generation: { zh, en },
    constraints: { preserve: [], avoid: [] },
  });
}
const contentAnalysis: AssetDto = { id: "content-analysis", asset_type: "analysis", name: "content-analysis.json", parent_asset_id: content.id, is_current: false, metadata: {}, version_no: 1 };
const currentCandidate: AssetDto = { id: "current-candidate", asset_type: "generated_image", name: "Current-candidate.png", is_current: true, metadata: { width: 1024, height: 1024 }, version_no: 2 };

describe("Reference workspace", () => {
  beforeEach(() => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:reference") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  });
  afterEach(() => { cleanup(); vi.restoreAllMocks(); Reflect.deleteProperty(URL, "createObjectURL"); Reflect.deleteProperty(URL, "revokeObjectURL"); });

  it("uses any two managed images as independent content and style references", async () => {
    const api = { assets: vi.fn().mockResolvedValue([content, style]), assetContent: vi.fn().mockResolvedValue(new Blob(["image"])) };
    render(<CompareWorkspace projectId="project-1" api={api as never} onModeChange={vi.fn()} />);
    expect(await screen.findByRole("heading", { name: "分析内容与风格参考" })).toBeVisible();
    expect(screen.getAllByLabelText(/文件选择/)).toHaveLength(2);
    expect(screen.queryByText("项目图片")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "选择图片" })).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "截图" })).toHaveLength(2);
    expect(screen.getByRole("heading", { name: "Prompt 编辑与合并" })).toBeVisible();
    expect(screen.getByPlaceholderText(/点击“生成合并 Prompt”/)).toBeVisible();
    expect(screen.getByPlaceholderText(/merged English prompt/)).toBeVisible();
    expect("assetLineage" in api).toBe(false);
    expect("compareAssets" in api).toBe(false);
  });

  it("loads and restores the project current image in either reference slot without changing the other slot", async () => {
    const onReferenceContextChange = vi.fn();
    const api = {
      assets: vi.fn().mockResolvedValue([
        { ...content, is_current: false },
        style,
        currentCandidate,
      ]),
      assetContent: vi.fn().mockResolvedValue(new Blob(["image"])),
      assetText: vi.fn().mockResolvedValue(managedPrompt("已保存描述", "saved description")),
      jobs: vi.fn().mockResolvedValue({ items: [] }),
    };
    render(<CompareWorkspace
      projectId="project-1"
      api={api as never}
      onModeChange={vi.fn()}
      referenceContext={{
        content_asset_id: content.id,
        style_asset_id: style.id,
        content_analysis_asset_id: "content-analysis",
        style_analysis_asset_id: "style-analysis",
        content_prompt_asset_id: "content-prompt",
        style_prompt_asset_id: "style-prompt",
        merged_prompt_asset_id: "merged-prompt",
      }}
      onReferenceContextChange={onReferenceContextChange}
    />);

    expect(await screen.findByAltText("内容参考图: Character.png")).toBeVisible();
    expect(screen.getByAltText("风格参考图: Oil-reference.png")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "加载当前资产到内容参考" }));
    expect(await screen.findByAltText("内容参考图: Current-candidate.png")).toBeVisible();
    expect(screen.getByAltText("风格参考图: Oil-reference.png")).toBeVisible();
    expect(onReferenceContextChange).toHaveBeenLastCalledWith({
      content_asset_id: currentCandidate.id,
      content_analysis_asset_id: null,
      content_prompt_asset_id: null,
      merged_prompt_asset_id: null,
    });

    fireEvent.click(screen.getByRole("button", { name: "恢复内容参考" }));
    expect(await screen.findByAltText("内容参考图: Character.png")).toBeVisible();
    expect(screen.getByAltText("风格参考图: Oil-reference.png")).toBeVisible();
    expect(onReferenceContextChange).toHaveBeenLastCalledWith({
      content_asset_id: content.id,
      content_analysis_asset_id: "content-analysis",
      content_prompt_asset_id: "content-prompt",
      merged_prompt_asset_id: "merged-prompt",
    });

    fireEvent.click(screen.getByRole("button", { name: "加载当前资产到风格参考" }));
    expect(await screen.findByAltText("风格参考图: Current-candidate.png")).toBeVisible();
    expect(screen.getByAltText("内容参考图: Character.png")).toBeVisible();
    expect(onReferenceContextChange).toHaveBeenLastCalledWith({
      style_asset_id: currentCandidate.id,
      style_analysis_asset_id: null,
      style_prompt_asset_id: null,
      merged_prompt_asset_id: null,
    });
  });
});

describe("PromptParameterDrawer", () => {
  afterEach(cleanup);

  it("approves each analysis and keeps the user in the reference workspace while its job runs", async () => {
    const invokeTool = vi.fn(async (
      _project: string,
      tool: string,
      _arguments: Record<string, unknown>,
    ) => {
      if (tool === "image.analyze_content") return { ok: true, status: "awaiting_ui_action", ui_action: { action_id: "content-approval" } };
      throw new Error(`unexpected tool ${tool}`);
    });
    const api = {
      invokeTool,
      decideApproval: vi.fn().mockResolvedValue({ ok: true, status: "queued", job: { job_id: "content-job" } }),
      jobs: vi.fn().mockResolvedValue({ items: [] }),
      assetText: vi.fn(),
      assets: vi.fn().mockResolvedValue([content, style, contentPrompt]),
      savePromptVersion: vi.fn(),
    };
    const onModeChange = vi.fn();
    render(<PromptParameterDrawer projectId="project-1" api={api as never} contentAsset={content} styleAsset={style} onClose={vi.fn()} onModeChange={onModeChange} />);

    fireEvent.click(screen.getAllByRole("button", { name: "开始分析" })[0]);
    expect(screen.getByRole("alertdialog")).toHaveTextContent("分析内容参考图");
    fireEvent.click(screen.getByRole("button", { name: "批准并分析" }));

    expect(await screen.findByText("内容参考图正在后台分析，可继续编辑其他内容。")) .toBeVisible();
    expect(api.decideApproval).toHaveBeenCalledWith("project-1", "content-approval", true, expect.any(String));
    expect(invokeTool.mock.calls.map((call) => call[1])).toEqual(["image.analyze_content"]);
    expect(onModeChange).not.toHaveBeenCalled();
    expect(screen.getAllByRole("button", { name: "开始分析" })).toHaveLength(2);
  });

  it("keeps a directly queued analysis in the reference workspace for sidecars that do not require an extra approval", async () => {
    const onModeChange = vi.fn();
    const api = {
      invokeTool: vi.fn().mockResolvedValue({ ok: true, status: "queued", job: { job_id: "content-job" } }),
      decideApproval: vi.fn(),
      jobs: vi.fn().mockResolvedValue({ items: [] }),
    };
    render(<PromptParameterDrawer projectId="project-1" api={api as never} contentAsset={content} styleAsset={style} onClose={vi.fn()} onModeChange={onModeChange} />);

    fireEvent.click(screen.getAllByRole("button", { name: "开始分析" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "批准并分析" }));
    await screen.findByText("内容参考图正在后台分析，可继续编辑其他内容。");
    expect(api.decideApproval).not.toHaveBeenCalled();
    expect(onModeChange).not.toHaveBeenCalled();
  });

  it("restores a completed analysis by matching its managed output to the selected source when an older sidecar omits job metadata", async () => {
    const api = {
      jobs: vi.fn().mockResolvedValue({ items: [{ id: "content-job", status: "succeeded", output_asset_ids: [contentAnalysis.id] }] }),
      invokeTool: vi.fn().mockResolvedValue({ ok: true, status: "succeeded", output_asset_ids: [contentPrompt.id] }),
      assetText: vi.fn().mockResolvedValue(managedPrompt("银色骑士", "silver knight")),
      assets: vi.fn().mockResolvedValue([content, style, contentAnalysis, contentPrompt]),
      savePromptVersion: vi.fn(),
    };
    render(<PromptParameterDrawer projectId="project-1" api={api as never} contentAsset={content} styleAsset={style} onClose={vi.fn()} onModeChange={vi.fn()} />);

    expect(await screen.findByDisplayValue("银色骑士")).toBeVisible();
    expect(screen.getByDisplayValue("silver knight")).toBeVisible();
    expect(api.invokeTool).toHaveBeenCalledWith("project-1", "prompt.extract_bilingual", { analysis_asset_id: contentAnalysis.id, kind: "content" }, expect.any(String));
  });

  it("uses the explicit Chinese generation field from a managed v3 style prompt", async () => {
    const styleAnalysis = { ...contentAnalysis, id: "style-analysis", name: "style-analysis.json", parent_asset_id: style.id };
    const api = {
      jobs: vi.fn().mockResolvedValue({ items: [] }),
      assets: vi.fn().mockResolvedValue([content, style, styleAnalysis, stylePrompt]),
      assetText: vi.fn(async (_projectId: string, assetId: string) => assetId === stylePrompt.id
        ? managedPrompt("暗黑奇幻数字插画", "Dark fantasy digital illustration.")
        : JSON.stringify({ raw_response: managedPrompt("暗黑奇幻数字插画", "Dark fantasy digital illustration.") })),
    };

    render(<PromptParameterDrawer
      projectId="project-1"
      api={api as never}
      contentAsset={content}
      styleAsset={style}
      onClose={vi.fn()}
      onModeChange={vi.fn()}
      referenceContext={{
        content_asset_id: null,
        content_analysis_asset_id: null,
        content_prompt_asset_id: null,
        style_asset_id: style.id,
        style_analysis_asset_id: styleAnalysis.id,
        style_prompt_asset_id: stylePrompt.id,
        merged_prompt_asset_id: null,
      }}
    />);

    expect(await screen.findByDisplayValue("暗黑奇幻数字插画")).toBeVisible();
    expect(screen.getByDisplayValue("Dark fantasy digital illustration.")).toBeVisible();
  });

  it("replaces a saved style prompt when a newer reanalysis job completes", async () => {
    const oldAnalysis = { ...contentAnalysis, id: "old-style-analysis", name: "style-analysis.json", parent_asset_id: style.id };
    const newAnalysis = { ...oldAnalysis, id: "new-style-analysis" };
    const newPrompt = { ...stylePrompt, id: "new-style-prompt" };
    const api = {
      jobs: vi.fn().mockResolvedValue({
        items: [{
          id: "new-style-job",
          job_type: "image.analyze_style",
          status: "succeeded",
          input_asset_ids: [style.id],
          output_asset_ids: [newAnalysis.id],
        }],
      }),
      assets: vi.fn().mockResolvedValue([content, style, oldAnalysis, newAnalysis, stylePrompt, newPrompt]),
      invokeTool: vi.fn().mockResolvedValue({ ok: true, status: "succeeded", output_asset_ids: [newPrompt.id] }),
      assetText: vi.fn(async (_projectId: string, assetId: string) => {
        if (assetId === stylePrompt.id) return managedPrompt("旧风格描述", "old style");
        if (assetId === newPrompt.id) return managedPrompt("新的暗黑数字插画风格", "new dark digital illustration style");
        return JSON.stringify({ zh_text: "[STYLE-1 媒介]\n新的暗黑数字插画风格。", parse_error: null });
      }),
    };
    const onReferenceContextChange = vi.fn();

    render(<PromptParameterDrawer
      projectId="project-1"
      api={api as never}
      contentAsset={content}
      styleAsset={style}
      onClose={vi.fn()}
      onModeChange={vi.fn()}
      referenceContext={{
        content_asset_id: null,
        content_analysis_asset_id: null,
        content_prompt_asset_id: null,
        style_asset_id: style.id,
        style_analysis_asset_id: oldAnalysis.id,
        style_prompt_asset_id: stylePrompt.id,
        merged_prompt_asset_id: null,
      }}
      onReferenceContextChange={onReferenceContextChange}
    />);

    expect(await screen.findByDisplayValue("新的暗黑数字插画风格")).toBeVisible();
    expect(onReferenceContextChange).toHaveBeenCalledWith({
      style_analysis_asset_id: newAnalysis.id,
      style_prompt_asset_id: newPrompt.id,
    });
  });

  it("adds a unique analysis revision when the user clears a completed prompt and reanalyzes", async () => {
    const styleAnalysis = { ...contentAnalysis, id: "style-analysis", name: "style-analysis.json", parent_asset_id: style.id };
    const invokeTool = vi.fn().mockResolvedValue({
      ok: true,
      status: "queued",
      job: { job_id: "fresh-style-job" },
    });
    const api = {
      jobs: vi.fn().mockResolvedValue({ items: [] }),
      assets: vi.fn().mockResolvedValue([content, style, styleAnalysis, stylePrompt]),
      assetText: vi.fn(async (_projectId: string, assetId: string) => assetId === stylePrompt.id
        ? managedPrompt("旧风格描述", "old style")
        : JSON.stringify({ zh_text: "[STYLE-1 媒介]\n旧风格描述。" })),
      invokeTool,
      decideApproval: vi.fn(),
    };

    render(<PromptParameterDrawer
      projectId="project-1"
      api={api as never}
      contentAsset={content}
      styleAsset={style}
      onClose={vi.fn()}
      onModeChange={vi.fn()}
      referenceContext={{
        content_asset_id: null,
        content_analysis_asset_id: null,
        content_prompt_asset_id: null,
        style_asset_id: style.id,
        style_analysis_asset_id: styleAnalysis.id,
        style_prompt_asset_id: stylePrompt.id,
        merged_prompt_asset_id: null,
      }}
    />);

    const zhField = await screen.findByDisplayValue("旧风格描述");
    const enField = screen.getByDisplayValue("old style");
    fireEvent.change(zhField, { target: { value: "" } });
    fireEvent.change(enField, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "重新分析" }));
    fireEvent.click(screen.getByRole("button", { name: "批准并分析" }));

    await screen.findByText("风格参考图正在后台分析，可继续编辑其他内容。");
    expect(invokeTool).toHaveBeenCalledWith(
      "project-1",
      "image.analyze_style",
      expect.objectContaining({
        asset_id: style.id,
        provider_profile: "gemini/google/default",
        analysis_revision: expect.any(String),
      }),
      expect.any(String),
      { providerProfile: "gemini/google/default" },
    );
    expect(invokeTool.mock.calls[0][2].analysis_revision).not.toHaveLength(0);
  });

  it("recognizes persisted analysis immediately and keeps its revision stable when submission is retried", async () => {
    const invokeTool = vi.fn()
      .mockRejectedValueOnce(new Error("response lost"))
      .mockResolvedValueOnce({
        ok: true,
        status: "queued",
        job: { job_id: "retried-style-job" },
      });
    const api = {
      jobs: vi.fn().mockResolvedValue({ items: [] }),
      assets: vi.fn().mockResolvedValue([content, style]),
      invokeTool,
      decideApproval: vi.fn(),
    };

    render(<PromptParameterDrawer
      projectId="project-1"
      api={api as never}
      contentAsset={content}
      styleAsset={style}
      onClose={vi.fn()}
      onModeChange={vi.fn()}
      referenceContext={{
        content_asset_id: null,
        content_analysis_asset_id: null,
        content_prompt_asset_id: null,
        style_asset_id: style.id,
        style_analysis_asset_id: "persisted-style-analysis",
        style_prompt_asset_id: null,
        merged_prompt_asset_id: null,
      }}
    />);

    fireEvent.click(screen.getByRole("button", { name: "重新分析" }));
    fireEvent.click(screen.getByRole("button", { name: "批准并分析" }));
    expect(await screen.findByText("response lost")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "开始分析" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("分析内容参考图");
    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    fireEvent.click(screen.getByRole("button", { name: "重新分析" }));
    fireEvent.click(screen.getByRole("button", { name: "批准并分析" }));
    await screen.findByText("风格参考图正在后台分析，可继续编辑其他内容。");

    expect(invokeTool).toHaveBeenCalledTimes(2);
    const firstRevision = invokeTool.mock.calls[0][2].analysis_revision;
    const retriedRevision = invokeTool.mock.calls[1][2].analysis_revision;
    expect(firstRevision).toEqual(expect.any(String));
    expect(retriedRevision).toBe(firstRevision);
  });

  it("keeps the same revision when the dialog is hidden during submission and the response is lost", async () => {
    let rejectFirst: ((reason: Error) => void) | undefined;
    const firstSubmission = new Promise((_resolve, reject) => {
      rejectFirst = reject;
    });
    const invokeTool = vi.fn()
      .mockImplementationOnce(() => firstSubmission)
      .mockResolvedValueOnce({
        ok: true,
        status: "queued",
        job: { job_id: "retried-background-style-job" },
      });
    const api = {
      jobs: vi.fn().mockResolvedValue({ items: [] }),
      assets: vi.fn().mockResolvedValue([content, style]),
      invokeTool,
      decideApproval: vi.fn(),
    };

    render(<PromptParameterDrawer
      projectId="project-1"
      api={api as never}
      contentAsset={content}
      styleAsset={style}
      onClose={vi.fn()}
      onModeChange={vi.fn()}
      referenceContext={{
        content_asset_id: null,
        content_analysis_asset_id: null,
        content_prompt_asset_id: null,
        style_asset_id: style.id,
        style_analysis_asset_id: "persisted-style-analysis",
        style_prompt_asset_id: null,
        merged_prompt_asset_id: null,
      }}
    />);

    fireEvent.click(screen.getByRole("button", { name: "重新分析" }));
    fireEvent.click(screen.getByRole("button", { name: "批准并分析" }));
    fireEvent.click(await screen.findByRole("button", { name: "后台继续，查看任务" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();

    rejectFirst?.(new Error("background response lost"));
    expect(await screen.findByText("background response lost")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "重新分析" }));
    fireEvent.click(screen.getByRole("button", { name: "批准并分析" }));
    await screen.findByText("风格参考图正在后台分析，可继续编辑其他内容。");

    expect(invokeTool).toHaveBeenCalledTimes(2);
    expect(invokeTool.mock.calls[1][2].analysis_revision)
      .toBe(invokeTool.mock.calls[0][2].analysis_revision);
  });

  it("starts a new revision after an unknown submission is recovered successfully in the background", async () => {
    const recoveredAnalysis = { ...contentAnalysis, id: "background-style-analysis", name: "style-analysis.json", parent_asset_id: style.id };
    const recoveredPrompt = { ...stylePrompt, id: "background-style-prompt" };
    let completedJobs: Array<Record<string, unknown>> = [];
    let analysisSubmissions = 0;
    const invokeTool = vi.fn(async (
      _project: string,
      tool: string,
      _arguments: Record<string, unknown>,
    ) => {
      if (tool === "prompt.extract_bilingual") {
        return { ok: true, status: "succeeded", output_asset_ids: [recoveredPrompt.id] };
      }
      if (tool === "image.analyze_style") {
        analysisSubmissions += 1;
        if (analysisSubmissions === 1) throw new Error("unknown style submission");
        return { ok: true, status: "queued", job: { job_id: "new-style-job" } };
      }
      throw new Error(`unexpected tool ${tool}`);
    });
    const api = {
      jobs: vi.fn(async () => ({ items: completedJobs })),
      assets: vi.fn().mockResolvedValue([content, style, recoveredAnalysis, recoveredPrompt]),
      assetText: vi.fn(async (_projectId: string, assetId: string) => assetId === recoveredAnalysis.id
        ? JSON.stringify({ zh_text: "[STYLE-1 媒介]\n后台恢复的风格描述。", parse_error: null })
        : managedPrompt("后台恢复的风格描述", "background recovered style")),
      invokeTool,
      decideApproval: vi.fn(),
    };

    render(<PromptParameterDrawer
      projectId="project-1"
      api={api as never}
      contentAsset={content}
      styleAsset={style}
      onClose={vi.fn()}
      onModeChange={vi.fn()}
      referenceContext={{
        content_asset_id: null,
        content_analysis_asset_id: null,
        content_prompt_asset_id: null,
        style_asset_id: style.id,
        style_analysis_asset_id: "previous-style-analysis",
        style_prompt_asset_id: null,
        merged_prompt_asset_id: null,
      }}
    />);

    fireEvent.click(screen.getByRole("button", { name: "重新分析" }));
    fireEvent.click(screen.getByRole("button", { name: "批准并分析" }));
    expect(await screen.findByText("unknown style submission")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    fireEvent.click(screen.getByRole("button", { name: "重新分析" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("分析风格参考图");

    completedJobs = [{
      id: "background-style-job",
      job_type: "image.analyze_style",
      status: "succeeded",
      input_asset_ids: [style.id],
      output_asset_ids: [recoveredAnalysis.id],
    }];
    expect(await screen.findByDisplayValue(
      "后台恢复的风格描述",
      {},
      { timeout: 4_000 },
    )).toBeVisible();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "重新分析" }));
    fireEvent.click(screen.getByRole("button", { name: "批准并分析" }));
    await screen.findByText("风格参考图正在后台分析，可继续编辑其他内容。");

    const analysisCalls = invokeTool.mock.calls.filter((call) => call[1] === "image.analyze_style");
    expect(analysisCalls).toHaveLength(2);
    const firstRevision = analysisCalls[0]?.[2].analysis_revision;
    const nextRevision = analysisCalls[1]?.[2].analysis_revision;
    expect(firstRevision).toEqual(expect.any(String));
    expect(nextRevision).toEqual(expect.any(String));
    expect(nextRevision).not.toBe(firstRevision);
  });

  it("clears a restored prompt when its reference image changes", async () => {
    const nextContent = { ...content, id: "next-content", name: "Next.png" };
    const api = {
      jobs: vi.fn().mockResolvedValue({ items: [] }),
      assets: vi.fn().mockResolvedValue([content, nextContent, style, contentAnalysis, contentPrompt]),
      assetText: vi.fn(async (_projectId: string, assetId: string) => assetId === contentPrompt.id
        ? managedPrompt("旧内容描述", "old content")
        : JSON.stringify({ zh_text: "[CONTENT-1 主体]\n旧内容描述。" })),
    };
    const context = {
      content_asset_id: content.id,
      content_analysis_asset_id: contentAnalysis.id,
      content_prompt_asset_id: contentPrompt.id,
      style_asset_id: null,
      style_analysis_asset_id: null,
      style_prompt_asset_id: null,
      merged_prompt_asset_id: null,
    };
    const props = {
      projectId: "project-1",
      api: api as never,
      styleAsset: style,
      onClose: vi.fn(),
      onModeChange: vi.fn(),
      referenceContext: context,
    };
    const { rerender } = render(<PromptParameterDrawer {...props} contentAsset={content} />);
    expect(await screen.findByDisplayValue("旧内容描述")).toBeVisible();

    rerender(<PromptParameterDrawer {...props} contentAsset={nextContent} />);
    await waitFor(() => expect(screen.getByLabelText("中文 内容 Prompt")).toHaveValue(""));
  });

  it("retries a completed analysis when its managed result is temporarily unreadable", async () => {
    const styleAnalysis = { ...contentAnalysis, id: "retry-style-analysis", name: "style-analysis.json", parent_asset_id: style.id };
    const recoveredPrompt = { ...stylePrompt, id: "retry-style-prompt" };
    let analysisReads = 0;
    const api = {
      jobs: vi.fn().mockResolvedValue({
        items: [{
          id: "retry-style-job",
          job_type: "image.analyze_style",
          status: "succeeded",
          input_asset_ids: [style.id],
          output_asset_ids: [styleAnalysis.id],
        }],
      }),
      assets: vi.fn().mockResolvedValue([content, style, styleAnalysis, recoveredPrompt]),
      invokeTool: vi.fn().mockResolvedValue({ ok: true, status: "succeeded", output_asset_ids: [recoveredPrompt.id] }),
      assetText: vi.fn(async (_projectId: string, assetId: string) => {
        if (assetId === styleAnalysis.id) {
          analysisReads += 1;
          if (analysisReads === 1) throw new Error("temporary read failure");
          return JSON.stringify({ zh_text: "[STYLE-1 媒介]\n恢复后的中文风格描述。", parse_error: null });
        }
        return managedPrompt("恢复后的中文风格描述", "recovered style prompt");
      }),
    };

    render(<PromptParameterDrawer
      projectId="project-1"
      api={api as never}
      contentAsset={content}
      styleAsset={style}
      onClose={vi.fn()}
      onModeChange={vi.fn()}
    />);

    expect(await screen.findByDisplayValue("恢复后的中文风格描述", {}, { timeout: 4_000 })).toBeVisible();
    expect(analysisReads).toBeGreaterThanOrEqual(2);
  });

  it("does not enable explicit merge until content and style prompts are both available", () => {
    const api = { invokeTool: vi.fn(), job: vi.fn(), assetText: vi.fn(), assets: vi.fn(), savePromptVersion: vi.fn() };
    render(<PromptParameterDrawer projectId="project-1" api={api as never} contentAsset={content} styleAsset={style} onClose={vi.fn()} onModeChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: "生成合并 Prompt" })).toBeDisabled();
  });

  it("hands an approved generation off to the task queue without waiting for candidate output", async () => {
    const onModeChange = vi.fn();
    const api = {
      invokeTool: vi.fn(async (_project: string, tool: string) => tool === "prompt.merge"
        ? { ok: true, status: "succeeded", output_asset_ids: [mergedPrompt.id] }
        : { ok: true, status: "awaiting_ui_action", output_asset_ids: [], ui_action: { action_id: "generation-approval" } }),
      assets: vi.fn().mockResolvedValue([content, style, contentPrompt, stylePrompt, mergedPrompt]),
      assetText: vi.fn().mockResolvedValue(managedPrompt("合并提示词", "merged prompt")),
      savePromptVersion: vi.fn(async (_project: string, value: { kind: string }) => ({ asset: value.kind === "content" ? contentPrompt : value.kind === "style" ? stylePrompt : mergedPrompt })),
      decideApproval: vi.fn().mockResolvedValue({ ok: true, status: "queued", job: { job_id: "queued-generation" } }),
      job: vi.fn(),
    };
    render(<PromptParameterDrawer projectId="project-1" api={api as never} contentAsset={content} styleAsset={style} onClose={vi.fn()} onModeChange={onModeChange} />);

    const fields = screen.getAllByRole("textbox");
    fireEvent.change(fields[0], { target: { value: "内容" } });
    fireEvent.change(fields[1], { target: { value: "content" } });
    fireEvent.change(fields[2], { target: { value: "风格" } });
    fireEvent.change(fields[3], { target: { value: "style" } });
    fireEvent.click(screen.getByRole("button", { name: "生成合并 Prompt" }));
    await screen.findByText(/merged-prompt.txt/);

    fireEvent.click(screen.getByRole("button", { name: "保存并生成候选" }));
    fireEvent.click(screen.getByRole("button", { name: "批准并生成" }));
    await waitFor(() => expect(onModeChange).toHaveBeenCalledWith("prompt_image"));
    expect(api.job).not.toHaveBeenCalled();
  });
});
