import { CheckCircle, DownloadSimple, ImageSquare, Sparkle, SpinnerGap, WarningCircle } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import type { ApiClient, AssetDto, JobDto, WorkflowContexts, WorkspaceMode } from "../../shared/api/client";
import { HostClient } from "../../shared/host/client";
import { promptPair } from "../../shared/prompts/promptDocument";
import "./prompt-image-workspace.css";

const imageProfile = "image-generation/auto";
const imageModel = "auto";
const rewriteProfile = "gemini/google/default";
const rewriteModel = "gemini-flash-lite-latest";
const rewriteInstruction = [
  "Refine the generation description without changing the depicted subject or stated restrictions.",
  "Make composition, visible construction, materials, lighting, and spatial relationships more actionable, but do not invent unrelated content.",
  "Return the complete formweaver.prompt.v1 JSON document with equivalent natural Chinese and English fields and retained preserve/avoid constraints.",
].join(" ");

type PromptLanguage = "zh" | "en";
type PromptPair = { zh: string; en: string };
type RewriteCandidate = { assetId: string; prompts: PromptPair; language: PromptLanguage };

function requestId(prefix: string) {
  return `${prefix}-${crypto.randomUUID()}`;
}

function detectPromptLanguage(text: string): PromptLanguage {
  if (!text.trim()) return "zh";
  return /[\u3400-\u9fff]/.test(text) ? "zh" : "en";
}

function promptsFromManagedText(text: string): PromptPair {
  return promptPair(text);
}

function initialPromptState(context?: WorkflowContexts["prompt_image"]): {
  prompts: PromptPair;
  language: PromptLanguage;
  dirty: boolean;
} {
  const language = context?.display_language === "en" || context?.display_language === "zh"
    ? context.display_language
    : "zh";
  const prompts = {
    zh: context?.zh_prompt ?? "",
    en: context?.en_prompt ?? "",
  };
  return {
    prompts,
    language,
    dirty: Boolean((prompts.zh.trim() || prompts.en.trim()) && !context?.source_prompt_asset_id),
  };
}

function jobFailureMessage(job: JobDto) {
  if (job.error?.code === "JOB_HANDLER_INTERRUPTED") return "后台生成服务在安全检查点中断，未产出图片；可在任务中心重试。";
  return job.error?.user_message ?? "任务已中断或取消。";
}

function GeneratedPreview({ projectId, api, asset, featured = false, selected = false, onSelect }: {
  projectId: string;
  api: ApiClient;
  asset: AssetDto;
  featured?: boolean;
  selected?: boolean;
  onSelect?(): void;
}) {
  const [url, setUrl] = useState("");

  useEffect(() => {
    let active = true;
    let objectUrl = "";
    void api.assetContent(projectId, asset.id).then((blob) => {
      objectUrl = URL.createObjectURL(blob);
      if (active) setUrl(objectUrl);
    }).catch(() => undefined);
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [api, asset.id, projectId]);

  const image = url && <img src={url} alt={`生成图像：${asset.name}`} />;
  return <figure className={`prompt-image-preview${featured ? " featured" : ""}${selected ? " selected" : ""}`}>
    {onSelect
      ? <button type="button" className="prompt-image-candidate" onClick={onSelect} aria-pressed={selected} aria-label={`选择 ${asset.name}`}>{image}</button>
      : <div className="prompt-image-candidate">{image}</div>}
    {!featured && <figcaption>{selected ? "已选中 · " : ""}{asset.name}</figcaption>}
  </figure>;
}

export function PromptImageWorkspace({ projectId, api, host = new HostClient(), onModeChange, generationJobId, mergedPromptAssetId, workflowContext, onWorkflowContextChange, onCurrentAssetChange, onJobQueued }: {
  projectId: string;
  api: ApiClient;
  host?: Pick<HostClient, "chooseExportDirectory">;
  onModeChange(mode: WorkspaceMode): void;
  generationJobId?: string | null;
  mergedPromptAssetId?: string | null;
  workflowContext?: WorkflowContexts["prompt_image"];
  onWorkflowContextChange?(value: WorkflowContexts["prompt_image"]): void;
  onCurrentAssetChange?(): void;
  onJobQueued?(jobId: string): void;
}) {
  const initial = initialPromptState(workflowContext);
  const [prompts, setPrompts] = useState<PromptPair>(initial.prompts);
  const [displayLanguage, setDisplayLanguage] = useState<PromptLanguage>(initial.language);
  const [promptDirty, setPromptDirty] = useState(initial.dirty);
  const [sourcePromptAssetId, setSourcePromptAssetId] = useState(
    workflowContext?.source_prompt_asset_id ?? null,
  );
  const [candidateCount, setCandidateCount] = useState(
    [1, 2, 4].includes(workflowContext?.candidate_count ?? 2)
      ? workflowContext?.candidate_count ?? 2
      : 2,
  );
  const [aspectRatio, setAspectRatio] = useState(workflowContext?.aspect_ratio ?? "1:1");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [job, setJob] = useState<JobDto | null>(null);
  const [generated, setGenerated] = useState<AssetDto[]>([]);
  const [selectedCandidateId, setSelectedCandidateId] = useState<string | null>(workflowContext?.selected_candidate_id ?? null);
  const [resultAction, setResultAction] = useState<"current" | "export" | null>(null);
  const [resultNotice, setResultNotice] = useState<string | null>(null);
  const [rewritePreparing, setRewritePreparing] = useState(false);
  const [rewriteJobId, setRewriteJobId] = useState<string | null>(workflowContext?.rewrite_job_id ?? null);
  const [rewriteNotice, setRewriteNotice] = useState<string | null>(null);
  const [pendingRewrite, setPendingRewrite] = useState<RewriteCandidate | null>(null);
  const editRevision = useRef(0);
  const rewriteSnapshot = useRef<{ revision: number; language: PromptLanguage } | null>(null);
  const prompt = prompts[displayLanguage];
  const rewriteBusy = rewritePreparing || Boolean(rewriteJobId);
  const jobFailed = Boolean(job && (job.error || ["failed", "cancelled", "interrupted"].includes(job.status)));
  const requiresSubmissionConfirmation = Boolean(
    job
    && job.status === "interrupted"
    && job.stage === "unknown_submission"
    && job.error?.code === "JOB_UNKNOWN_SUBMISSION"
    && job.recovery_actions?.includes("confirm_new_submission"),
  );
  useEffect(() => {
    const next = {
      zh_prompt: prompts.zh,
      en_prompt: prompts.en,
      display_language: displayLanguage,
      source_prompt_asset_id: sourcePromptAssetId,
      candidate_count: candidateCount,
      aspect_ratio: aspectRatio,
      selected_candidate_id: selectedCandidateId,
      job_id: generationJobId ?? null,
      rewrite_job_id: rewriteJobId,
    };
    if (
      workflowContext
      && workflowContext.zh_prompt === next.zh_prompt
      && workflowContext.en_prompt === next.en_prompt
      && workflowContext.display_language === next.display_language
      && workflowContext.source_prompt_asset_id === next.source_prompt_asset_id
      && workflowContext.candidate_count === next.candidate_count
      && workflowContext.aspect_ratio === next.aspect_ratio
      && workflowContext.selected_candidate_id === next.selected_candidate_id
      && workflowContext.job_id === next.job_id
      && (workflowContext.rewrite_job_id ?? null) === next.rewrite_job_id
    ) return;
    onWorkflowContextChange?.(next);
  }, [aspectRatio, candidateCount, displayLanguage, generationJobId, onWorkflowContextChange, prompts.en, prompts.zh, rewriteJobId, selectedCandidateId, sourcePromptAssetId, workflowContext]);

  useEffect(() => {
    if (!mergedPromptAssetId || mergedPromptAssetId === sourcePromptAssetId) return;
    let active = true;
    void api.assetText(projectId, mergedPromptAssetId)
      .then((text) => {
        if (!active) return;
        const restored = promptsFromManagedText(text);
        if (restored.zh || restored.en) {
          editRevision.current += 1;
          setPrompts(restored);
          setDisplayLanguage("zh");
          setPromptDirty(false);
          setSourcePromptAssetId(mergedPromptAssetId);
          setPendingRewrite(null);
        }
      })
      .catch(() => { if (active) setError("合并 Prompt 无法读取；请返回参考图页面重新保存。"); });
    return () => { active = false; };
  }, [api, mergedPromptAssetId, projectId, sourcePromptAssetId]);

  useEffect(() => {
    if (!generationJobId) {
      setJob(null);
      return;
    }
    let active = true;
    const load = () => void api.job(projectId, generationJobId).then((next) => { if (active) setJob(next); }).catch(() => undefined);
    load();
    const timer = window.setInterval(load, 2500);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [api, generationJobId, projectId]);

  useEffect(() => {
    if (job?.status !== "succeeded" || !job.output_asset_ids.length) {
      setGenerated([]);
      return;
    }
    let active = true;
    void api.assets(projectId).then((assets) => {
      if (!active) return;
      const byId = new Map(assets.map((asset) => [asset.id, asset]));
      const next = job.output_asset_ids.map((id) => byId.get(id)).filter((asset): asset is AssetDto => Boolean(asset));
      setGenerated(next);
      setSelectedCandidateId((current) => next.some((asset) => asset.id === current) ? current : (next[0]?.id ?? null));
    }).catch(() => undefined);
    return () => { active = false; };
  }, [api, job?.id, job?.output_asset_ids, job?.status, projectId]);

  useEffect(() => {
    if (!rewriteJobId) return;
    let active = true;
    let completing = false;
    const load = async () => {
      try {
        const rewriteJob = await api.job(projectId, rewriteJobId);
        if (!active || completing) return;
        if (rewriteJob.status === "succeeded") {
          completing = true;
          const rewrittenAssetId = rewriteJob.output_asset_ids[0];
          if (!rewrittenAssetId) throw new Error("智能扩写任务没有返回新的 Prompt 版本。");
          const rewritten = promptsFromManagedText(await api.assetText(projectId, rewrittenAssetId));
          if (!rewritten.zh || !rewritten.en) throw new Error("智能扩写结果缺少中文或英文 Prompt。");
          if (!active) return;
          const candidate: RewriteCandidate = {
            assetId: rewrittenAssetId,
            prompts: rewritten,
            language: rewriteSnapshot.current?.language ?? displayLanguage,
          };
          if (rewriteSnapshot.current && editRevision.current !== rewriteSnapshot.current.revision) {
            setPendingRewrite(candidate);
            setRewriteNotice("智能扩写已完成，但你在等待期间修改了 Prompt。请确认是否应用扩写结果。");
          } else {
            setPrompts(candidate.prompts);
            setDisplayLanguage(candidate.language);
            setSourcePromptAssetId(candidate.assetId);
            setPromptDirty(false);
            setPendingRewrite(null);
            setRewriteNotice(`智能扩写已完成，已回填${candidate.language === "zh" ? "中文" : "英文"} Prompt。`);
          }
          setRewriteJobId(null);
          rewriteSnapshot.current = null;
          return;
        }
        if (["failed", "cancelled", "interrupted"].includes(rewriteJob.status)) {
          completing = true;
          setError(rewriteJob.error?.user_message ?? "智能扩写任务未完成。");
          setRewriteNotice(null);
          setRewriteJobId(null);
          rewriteSnapshot.current = null;
          return;
        }
        setRewriteNotice("正在使用 Gemini 智能扩写中英文 Prompt…");
      } catch (unknownError) {
        if (!active || !completing) return;
        setError(unknownError instanceof Error ? unknownError.message : "无法读取智能扩写结果。");
        setRewriteNotice(null);
        setRewriteJobId(null);
        rewriteSnapshot.current = null;
      }
    };
    void load();
    const timer = window.setInterval(() => void load(), 1500);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [api, displayLanguage, projectId, rewriteJobId]);

  const editPrompt = (value: string) => {
    editRevision.current += 1;
    setPromptDirty(true);
    setPendingRewrite(null);
    setRewriteNotice(null);
    if (!prompts.zh.trim() && !prompts.en.trim() && value.trim()) {
      const detected = detectPromptLanguage(value);
      setDisplayLanguage(detected);
      setPrompts({ zh: detected === "zh" ? value : "", en: detected === "en" ? value : "" });
      return;
    }
    setPrompts((current) => ({ ...current, [displayLanguage]: value }));
  };

  const clearPrompts = () => {
    editRevision.current += 1;
    setPrompts({ zh: "", en: "" });
    setPromptDirty(true);
    setPendingRewrite(null);
    setRewriteNotice(null);
    setError(null);
  };

  const applyRewrite = (candidate: RewriteCandidate) => {
    editRevision.current += 1;
    setPrompts(candidate.prompts);
    setDisplayLanguage(candidate.language);
    setSourcePromptAssetId(candidate.assetId);
    setPromptDirty(false);
    setPendingRewrite(null);
    setRewriteNotice(`已应用扩写结果并回填${candidate.language === "zh" ? "中文" : "英文"} Prompt。`);
    setError(null);
  };

  const generate = async () => {
    const text = prompt.trim();
    if (!text) return;
    setBusy(true);
    setError(null);
    try {
      const promptAssetId = sourcePromptAssetId && !promptDirty && prompts.zh.trim() && prompts.en.trim()
        ? sourcePromptAssetId
        : (await api.savePromptVersion(
          projectId,
          {
            zhPrompt: prompts.zh.trim() || text,
            enPrompt: prompts.en.trim() || text,
            kind: "image",
            parentAssetId: sourcePromptAssetId,
          },
          requestId("save-direct-prompt"),
        )).asset.id;
      const proposed = await api.invokeTool(
        projectId,
        "image.generate",
        {
          prompt_asset_id: promptAssetId,
          provider_profile: imageProfile,
          channel: "auto",
          model: imageModel,
          candidate_count: candidateCount,
          aspect_ratio: aspectRatio,
          output_format: "png",
        },
        requestId("generate-from-prompt"),
        { providerProfile: imageProfile },
      );
      const approvalId = proposed.ui_action?.action_id;
      const queued = proposed.status === "awaiting_ui_action" && approvalId
        ? await api.decideApproval(projectId, approvalId, true, requestId("approve-direct-prompt"))
        : proposed;
      if (!queued.ok || queued.status !== "queued") {
        throw new Error(queued.error?.user_message ?? "图像生成任务未能进入队列。");
      }
      const jobId = queued.job?.job_id;
      if (!jobId) throw new Error("图像生成任务没有返回任务编号。");
      setSourcePromptAssetId(promptAssetId);
      setPromptDirty(false);
      setJob(null);
      onJobQueued?.(jobId);
    } catch (unknownError) {
      setError(unknownError instanceof Error ? unknownError.message : "提示词生图失败。");
    } finally {
      setBusy(false);
    }
  };

  const confirmNewSubmission = async () => {
    if (!job || !requiresSubmissionConfirmation) return;
    setBusy(true);
    setError(null);
    try {
      const proposed = await api.confirmNewSubmission(
        projectId,
        job.id,
        requestId("confirm-new-image-submission"),
      );
      const approvalId = proposed.ui_action?.action_id;
      const queued = proposed.status === "awaiting_ui_action" && approvalId
        ? await api.decideApproval(
          projectId,
          approvalId,
          true,
          requestId("approve-confirmed-image-submission"),
        )
        : proposed;
      const replacementJobId = queued.job?.job_id;
      if (!queued.ok || queued.status !== "queued" || !replacementJobId) {
        throw new Error(queued.error?.user_message ?? "确认后的图像生成任务未能进入队列。");
      }
      if (replacementJobId === job.id) {
        throw new Error("服务未创建新的审计任务，已阻止重复使用结果不确定的旧任务。");
      }
      setJob(null);
      onJobQueued?.(replacementJobId);
    } catch (unknownError) {
      setError(unknownError instanceof Error ? unknownError.message : "无法确认新的图像生成提交。");
    } finally {
      setBusy(false);
    }
  };

  const expandPrompt = async () => {
    const current = prompt.trim();
    if (!current) return;
    const snapshot = {
      revision: editRevision.current,
      language: displayLanguage,
      prompts: { ...prompts },
    };
    setRewritePreparing(true);
    setError(null);
    setPendingRewrite(null);
    setRewriteNotice("正在保存当前 Prompt 并准备智能扩写…");
    try {
      const sourceId = sourcePromptAssetId && !promptDirty && prompts.zh.trim() && prompts.en.trim()
        ? sourcePromptAssetId
        : (await api.savePromptVersion(
          projectId,
          {
            zhPrompt: snapshot.prompts.zh.trim() || current,
            enPrompt: snapshot.prompts.en.trim() || current,
            kind: "image",
            parentAssetId: sourcePromptAssetId,
          },
          requestId("save-prompt-for-rewrite"),
        )).asset.id;
      const queued = await api.invokeTool(
        projectId,
        "prompt.rewrite",
        {
          prompt_asset_id: sourceId,
          provider_profile: rewriteProfile,
          model: rewriteModel,
          instruction: rewriteInstruction,
        },
        requestId("rewrite-image-prompt"),
        { providerProfile: rewriteProfile },
      );
      const jobId = queued.job?.job_id;
      if (!queued.ok || queued.status !== "queued" || !jobId) {
        throw new Error(queued.error?.user_message ?? "智能扩写任务未能进入队列。");
      }
      rewriteSnapshot.current = { revision: snapshot.revision, language: snapshot.language };
      setRewriteJobId(jobId);
      setRewriteNotice("正在使用 Gemini 智能扩写中英文 Prompt…");
    } catch (unknownError) {
      setError(unknownError instanceof Error ? unknownError.message : "智能扩写启动失败。");
      setRewriteNotice(null);
      rewriteSnapshot.current = null;
    } finally {
      setRewritePreparing(false);
    }
  };

  const selectedCandidate = generated.find((asset) => asset.id === selectedCandidateId) ?? generated[0];
  const setSelectedAsCurrent = async () => {
    if (!selectedCandidate || selectedCandidate.is_current) return;
    setResultAction("current");
    setResultNotice(null);
    setError(null);
    try {
      await api.setCurrentAsset(projectId, selectedCandidate.id, requestId("select-generated-image"));
      setGenerated((assets) => assets.map((asset) => ({ ...asset, is_current: asset.id === selectedCandidate.id })));
      onCurrentAssetChange?.();
      setResultNotice(`${selectedCandidate.name} 已设为当前资产。`);
    } catch (unknownError) {
      setError(unknownError instanceof Error ? unknownError.message : "无法将候选图设为当前资产。");
    } finally {
      setResultAction(null);
    }
  };
  const exportSelected = async () => {
    if (!selectedCandidate) return;
    setResultAction("export");
    setResultNotice(null);
    setError(null);
    try {
      const exportCapabilityId = await host.chooseExportDirectory(projectId);
      if (!exportCapabilityId) {
        setResultNotice("已取消导出，项目内的受管图片保持不变。");
        return;
      }
      await api.exportAsset(
        projectId,
        selectedCandidate.id,
        exportCapabilityId,
        requestId("export-generated-image"),
      );
      setResultNotice(`${selectedCandidate.name} 已导出到所选文件夹。`);
    } catch (unknownError) {
      setError(unknownError instanceof Error ? unknownError.message : "候选图片导出失败，请重新选择导出位置。");
    } finally {
      setResultAction(null);
    }
  };
  const generationState = busy
    ? "正在提交生成任务"
    : jobFailed
      ? "生成未完成"
      : job?.status === "succeeded"
        ? `已生成 ${generated.length || job.output_asset_ids.length} 张候选图`
        : job
          ? `正在生成图像：${job.stage}`
          : "等待生成";

  const results = selectedCandidate
    ? <section className="prompt-image-results" aria-label="本次生成的图像">
      <header><div><p className="eyebrow">Generated images</p><h2>本次生成的候选图</h2></div><span>{generated.length} 张</span></header>
      <div className="prompt-image-stage"><GeneratedPreview projectId={projectId} api={api} asset={selectedCandidate} featured selected /></div>
      <div className="prompt-image-result-toolbar">
        <div className="prompt-image-result-actions">
          <button type="button" className="primary" disabled={resultAction !== null || selectedCandidate.is_current} onClick={() => void setSelectedAsCurrent()}>
            {resultAction === "current" ? <SpinnerGap className="spin" size={18} /> : <CheckCircle size={18} />}
            {selectedCandidate.is_current ? "已是当前资产" : resultAction === "current" ? "正在保存…" : "设为当前资产"}
          </button>
          <button type="button" disabled={resultAction !== null} onClick={() => void exportSelected()}>
            {resultAction === "export" ? <SpinnerGap className="spin" size={18} /> : <DownloadSimple size={18} />}
            {resultAction === "export" ? "正在导出…" : "导出 PNG"}
          </button>
        </div>
        {resultNotice && <p role="status">{resultNotice}</p>}
      </div>
      <nav className="prompt-image-filmstrip" aria-label="候选图选择">{generated.map((asset) => <GeneratedPreview key={asset.id} projectId={projectId} api={api} asset={asset} selected={asset.id === selectedCandidate.id} onSelect={() => setSelectedCandidateId(asset.id)} />)}</nav>
    </section>
    : <div className="prompt-image-empty-preview"><ImageSquare size={36} /><h2>生成结果将在这里显示</h2><p>提交 Prompt 后，可直接对比本次生成的候选图。</p></div>;

  return <section className="prompt-image-workspace" aria-labelledby="prompt-image-title">
    <div className="prompt-image-controls">
      <header>
        <div><p className="eyebrow">Creative image generation</p><h1 id="prompt-image-title">生成并比较创意图</h1></div>
        <span>自动路由 · Tripo3D / Meshy</span>
      </header>
      <p>输入设计描述并比较候选方案；只有确认后才会提交外部付费生成任务。</p>
      <div className="prompt-image-language-row">
        <span>Prompt</span>
        <div className="prompt-image-language-options" aria-label="Prompt 显示语言">
          <button type="button" aria-pressed={displayLanguage === "zh"} onClick={() => setDisplayLanguage("zh")}>中文</button>
          <button type="button" aria-pressed={displayLanguage === "en"} onClick={() => setDisplayLanguage("en")}>English</button>
        </div>
      </div>
      <label className="prompt-image-editor">
        <span className="sr-only">{displayLanguage === "zh" ? "中文 Prompt" : "English Prompt"}</span>
        <textarea
          value={prompt}
          onChange={(event) => editPrompt(event.target.value)}
          aria-label={displayLanguage === "zh" ? "中文 Prompt" : "English Prompt"}
          placeholder={displayLanguage === "zh" ? "描述你想生成的图像…" : "Describe the image you want to generate…"}
          disabled={busy}
        />
      </label>
      <div className="prompt-image-prompt-actions">
        <button type="button" onClick={clearPrompts} disabled={busy || (!prompts.zh && !prompts.en)}>清空</button>
        <button type="button" className="prompt-image-expand" onClick={() => void expandPrompt()} disabled={busy || rewriteBusy || !prompt.trim()}>
          {rewriteBusy ? <SpinnerGap className="spin" size={16} /> : <Sparkle size={16} />}
          {rewriteBusy ? "智能扩写中…" : "智能扩写"}
        </button>
      </div>
      {rewriteNotice && <div className="prompt-image-rewrite-notice" role="status">
        <span>{rewriteNotice}</span>
        {pendingRewrite && <div>
          <button type="button" className="primary" onClick={() => applyRewrite(pendingRewrite)}>应用扩写结果</button>
          <button type="button" onClick={() => { setPendingRewrite(null); setRewriteNotice("已保留当前 Prompt，扩写结果未应用。"); }}>保留当前内容</button>
        </div>}
      </div>}
      <div className="prompt-image-options">
        <fieldset><legend>候选数量</legend><div className="candidate-count-options">{[1, 2, 4].map((value) => <button key={value} type="button" aria-pressed={candidateCount === value} disabled={busy} onClick={() => setCandidateCount(value)}>{value}</button>)}</div></fieldset>
        <label>宽高比<select value={aspectRatio} disabled={busy} onChange={(event) => setAspectRatio(event.target.value)}><option>1:1</option><option>16:9</option><option>9:16</option><option>4:3</option><option>3:4</option></select></label>
      </div>
      <section className={`prompt-image-generation-state${jobFailed ? " failed" : ""}`} aria-live="polite"><span>生成状态</span><strong>{generationState}</strong>{job?.status === "succeeded" && <small>可在右侧选择本次生成的候选图。</small>}</section>
      {error && <p className="prompt-image-error" role="alert"><WarningCircle size={18} />{error}</p>}
      {job && requiresSubmissionConfirmation && <section className="prompt-image-recovery" aria-label="生成任务人工恢复">
        <WarningCircle size={20} />
        <div>
          <strong>上次提交结果不确定，已停止自动重试</strong>
          <p>请先在任务实际使用的 Tripo3D 或 Meshy 账户中确认是否已产生任务或扣费。只有确认没有远端任务后，才可明确授权创建一个带审计关联的新任务。</p>
          <button type="button" disabled={busy} onClick={() => void confirmNewSubmission()}>
            {busy ? "正在准备新任务…" : "我已核对远端账户，确认重新提交"}
          </button>
        </div>
      </section>}
      {job && jobFailed && !requiresSubmissionConfirmation && <p className="prompt-image-error" role="alert"><WarningCircle size={18} />图像生成未完成：{jobFailureMessage(job)}</p>}
      <button className="primary" type="button" disabled={busy || rewriteBusy || !prompt.trim()} onClick={() => void generate()}>{busy ? <><SpinnerGap className="spin" size={18} />正在提交…</> : <><ImageSquare size={18} />确认并生成</>}</button>
    </div>
    <aside className="prompt-image-preview-pane">{results}</aside>
  </section>;
}
