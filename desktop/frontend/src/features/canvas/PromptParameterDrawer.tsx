import { CheckCircle, SpinnerGap, WarningCircle } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import type { ApiClient, AssetDto, ReferenceContextState, ToolResultDto, WorkspaceMode } from "../../shared/api/client";
import { parseManagedPrompt, promptPair } from "../../shared/prompts/promptDocument";

const visionProfile = "gemini/google/default";
const visionModel = import.meta.env.VITE_PIC2MODEL_ANALYSIS_MODEL || "gemini-flash-lite-latest";
const imageProfile = "image-generation/auto";
const imageModel = "auto";

type AnalysisKind = "content" | "style";
type RolePrompt = {
  analysisAssetId: string | null;
  promptAsset: AssetDto | null;
  zhPrompt: string;
  enPrompt: string;
  diagnostic: string | null;
};

const emptyRole = (): RolePrompt => ({
  analysisAssetId: null,
  promptAsset: null,
  zhPrompt: "",
  enPrompt: "",
  diagnostic: null,
});

function requestId(prefix: string) {
  return `${prefix}-${crypto.randomUUID()}`;
}

function analysisChineseSegment(text?: string | null) {
  if (!text) return "";
  try {
    const envelope = JSON.parse(text) as { zh_text?: unknown; raw_response?: unknown };
    if (typeof envelope.zh_text === "string") return envelope.zh_text;
    if (typeof envelope.raw_response === "string") {
      return parseManagedPrompt(envelope.raw_response).analysis.zh;
    }
  } catch {
    return "";
  }
  return "";
}

function parsePrompt(text: string, _analysisSegment?: string | null) {
  return promptPair(text);
}

function queuedJob(result: ToolResultDto) {
  if (!result.ok || result.status !== "queued" || !result.job?.job_id) {
    throw new Error(result.error?.user_message ?? "任务未能进入队列。");
  }
  return result.job.job_id;
}

function ExternalAnalysisDialog({
  kind,
  asset,
  busy,
  onCancel,
  onConfirm,
}: {
  kind: AnalysisKind;
  asset: AssetDto;
  busy: boolean;
  onCancel(): void;
  onConfirm(): void;
}) {
  const label = kind === "content" ? "内容参考图" : "风格参考图";
  return (
    <div className="dialog-backdrop external-transfer-backdrop">
      <section className="dialog external-transfer-dialog" role="alertdialog" aria-modal="true" aria-labelledby="analysis-transfer-title">
        <p className="eyebrow">外部分析确认</p>
        <h2 id="analysis-transfer-title">分析{label}</h2>
        <p>批准后会仅发送这张受管图片的标准化分析副本到 Gemini。另一角色的分析结果不会被覆盖。</p>
        <dl className="external-transfer-summary">
          <div><dt>素材</dt><dd>{asset.name} · v{asset.version_no ?? 1}</dd></div>
          <div><dt>服务</dt><dd>Google Gemini · {visionProfile}</dd></div>
          <div><dt>模型</dt><dd>{visionModel}</dd></div>
        </dl>
        <div className="dialog-actions">
          <button type="button" onClick={onCancel}>{busy ? "后台继续，查看任务" : "取消"}</button>
          <button type="button" className="primary" disabled={busy} onClick={onConfirm}>{busy ? "正在分析…" : "批准并分析"}</button>
        </div>
      </section>
    </div>
  );
}

function PaidGenerationDialog({ promptAsset, candidateCount, busy, onCancel, onConfirm }: {
  promptAsset: AssetDto;
  candidateCount: number;
  busy: boolean;
  onCancel(): void;
  onConfirm(): void;
}) {
  return (
    <div className="dialog-backdrop external-transfer-backdrop">
      <section className="dialog external-transfer-dialog" role="alertdialog" aria-modal="true" aria-labelledby="generation-approval-title">
        <p className="eyebrow">付费 Provider 审批</p>
        <h2 id="generation-approval-title">生成 {candidateCount} 张候选图</h2>
        <p>只有批准后才会发送已确认的合并 Prompt 并创建付费任务。</p>
        <dl className="external-transfer-summary">
          <div><dt>Prompt</dt><dd>{promptAsset.name} · v{promptAsset.version_no ?? 1}</dd></div>
          <div><dt>服务</dt><dd>自动选择 Tripo3D / Meshy</dd></div>
          <div><dt>模型</dt><dd>按可用服务配置</dd></div>
        </dl>
        <div className="dialog-actions">
          <button type="button" onClick={onCancel}>{busy ? "后台继续，查看任务" : "取消"}</button>
          <button type="button" className="primary" disabled={busy} onClick={onConfirm}>{busy ? "正在提交…" : "批准并生成"}</button>
        </div>
      </section>
    </div>
  );
}

function RoleEditor({ kind, value, busy, canAnalyze, hasAnalysis, onChange, onAnalyze, onSave }: {
  kind: AnalysisKind;
  value: RolePrompt;
  busy: boolean;
  canAnalyze: boolean;
  hasAnalysis: boolean;
  onChange(next: RolePrompt): void;
  onAnalyze(): void;
  onSave(): void;
}) {
  const title = kind === "content" ? "内容分析（主体与结构）" : "风格分析（美术与质感）";
  const ready = Boolean(value.zhPrompt.trim() && value.enPrompt.trim());
  return (
    <section className="prompt-role-editor" aria-label={title}>
      <header>
        <div><h3>{title}</h3></div>
        <button type="button" className="primary" disabled={busy || !canAnalyze} onClick={onAnalyze}>{hasAnalysis ? "重新分析" : "开始分析"}</button>
      </header>
      {!canAnalyze ? <p>先在左侧选择对应的参考图，才能开始分析。</p> : !hasAnalysis && <p>尚未分析。此步骤独立于另一张参考图，可单独重试。</p>}
      {value.analysisAssetId && !ready && <p className="prompt-drawer-error"><WarningCircle size={18} />模型结果未满足双语 Prompt 契约。请依据诊断手动修正后保存。</p>}
      {value.diagnostic && <details><summary>查看模型原始诊断</summary><pre>{value.diagnostic}</pre></details>}
      <label>中文 {kind === "content" ? "内容" : "风格"} Prompt<textarea value={value.zhPrompt} onChange={(event) => onChange({ ...value, zhPrompt: event.target.value })} /></label>
      <label>English {kind === "content" ? "content" : "style"} Prompt<textarea value={value.enPrompt} onChange={(event) => onChange({ ...value, enPrompt: event.target.value })} /></label>
      <button type="button" className="primary" disabled={busy || !ready} onClick={onSave}>保存{kind === "content" ? "内容" : "风格"} Prompt</button>
    </section>
  );
}

export function PromptParameterDrawer({ projectId, api, contentAsset, styleAsset, onClose, onModeChange, persistent = false, referenceContext, onReferenceContextChange, onImageJobQueued }: {
  projectId: string;
  api: ApiClient;
  contentAsset: AssetDto | null;
  styleAsset: AssetDto | null;
  onClose(): void;
  onModeChange(mode: WorkspaceMode): void;
  persistent?: boolean;
  referenceContext?: ReferenceContextState;
  onReferenceContextChange?(patch: Partial<ReferenceContextState>): void;
  onImageJobQueued?(jobId: string): void;
}) {
  const [approvalKind, setApprovalKind] = useState<AnalysisKind | null>(null);
  const [generationApproval, setGenerationApproval] = useState(false);
  const [busy, setBusy] = useState(false);
  const [content, setContent] = useState<RolePrompt>(emptyRole);
  const [style, setStyle] = useState<RolePrompt>(emptyRole);
  const [managedPrompt, setManagedPrompt] = useState<AssetDto | null>(null);
  const [zhPrompt, setZhPrompt] = useState("");
  const [enPrompt, setEnPrompt] = useState("");
  const [candidateCount, setCandidateCount] = useState(2);
  const [aspectRatio, setAspectRatio] = useState("1:1");
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pendingAnalysisRequests = useRef<Record<AnalysisKind, {
    revision: string | null;
    attempted: boolean;
  } | null>>({
    content: null,
    style: null,
  });
  const recoveredAnalysisJobs = useRef(new Set<string>());
  const recoveredAnalysisAssets = useRef(new Set<string>());
  const previousSourceIds = useRef<{ content: string | null; style: string | null }>({
    content: contentAsset?.id ?? null,
    style: styleAsset?.id ?? null,
  });

  const role = (kind: AnalysisKind) => kind === "content" ? content : style;
  const setRole = (kind: AnalysisKind, next: RolePrompt) => kind === "content" ? setContent(next) : setStyle(next);
  const assetFor = (kind: AnalysisKind) => kind === "content" ? contentAsset : styleAsset;
  const persistedAnalysisIdFor = (kind: AnalysisKind) => kind === "content"
    ? referenceContext?.content_analysis_asset_id
    : referenceContext?.style_analysis_asset_id;
  const hasAnalysisFor = (kind: AnalysisKind) => Boolean(
    role(kind).analysisAssetId || persistedAnalysisIdFor(kind),
  );
  const openAnalysisApproval = (kind: AnalysisKind) => {
    if (!pendingAnalysisRequests.current[kind]) {
      pendingAnalysisRequests.current[kind] = {
        revision: hasAnalysisFor(kind) ? crypto.randomUUID() : null,
        attempted: false,
      };
    }
    setApprovalKind(kind);
  };
  const closeAnalysisApproval = (kind: AnalysisKind) => {
    pendingAnalysisRequests.current[kind] = null;
    setApprovalKind(null);
  };
  const dismissAnalysisApproval = (kind: AnalysisKind) => {
    if (busy || pendingAnalysisRequests.current[kind]?.attempted) {
      setApprovalKind(null);
    } else {
      closeAnalysisApproval(kind);
    }
  };

  useEffect(() => {
    const contentId = contentAsset?.id ?? null;
    const styleId = styleAsset?.id ?? null;
    if (previousSourceIds.current.content !== contentId) {
      previousSourceIds.current.content = contentId;
      setContent(emptyRole());
    }
    if (previousSourceIds.current.style !== styleId) {
      previousSourceIds.current.style = styleId;
      setStyle(emptyRole());
    }
  }, [contentAsset?.id, styleAsset?.id]);

  useEffect(() => {
    let active = true;
    const restorePrompt = async (kind: AnalysisKind, promptId: string | null | undefined, analysisId: string | null | undefined) => {
      if (!promptId) return;
      try {
        const [text, assets, analysisText] = await Promise.all([
          api.assetText(projectId, promptId),
          api.assets(projectId),
          analysisId ? api.assetText(projectId, analysisId).catch(() => null) : Promise.resolve(null),
        ]);
        const promptAsset = assets.find((asset) => asset.id === promptId) ?? null;
        const analysisAsset = analysisId ? assets.find((asset) => asset.id === analysisId) ?? null : null;
        const source = assetFor(kind);
        if (
          !promptAsset
          || !source
          || !active
          || (analysisId && (!analysisAsset || analysisAsset.parent_asset_id !== source.id))
        ) return;
        const parsed = parsePrompt(text, analysisChineseSegment(analysisText));
        const next = { ...emptyRole(), analysisAssetId: analysisId ?? null, promptAsset, zhPrompt: parsed.zh, enPrompt: parsed.en };
        const keepNewerAnalysis = (previous: RolePrompt) => (
          previous.analysisAssetId
          && previous.analysisAssetId !== analysisId
          && recoveredAnalysisAssets.current.has(previous.analysisAssetId)
            ? previous
            : next
        );
        if (kind === "content") setContent(keepNewerAnalysis); else setStyle(keepNewerAnalysis);
      } catch { /* A removed asset is simply treated as an empty role. */ }
    };
    void Promise.all([
      restorePrompt("content", referenceContext?.content_prompt_asset_id, referenceContext?.content_analysis_asset_id),
      restorePrompt("style", referenceContext?.style_prompt_asset_id, referenceContext?.style_analysis_asset_id),
      referenceContext?.merged_prompt_asset_id ? api.assetText(projectId, referenceContext.merged_prompt_asset_id).then((text) => { if (!active) return; const parsed = parsePrompt(text); setZhPrompt(parsed.zh); setEnPrompt(parsed.en); return api.assets(projectId).then((assets) => { if (active) setManagedPrompt(assets.find((asset) => asset.id === referenceContext.merged_prompt_asset_id) ?? null); }); }).catch(() => undefined) : Promise.resolve(),
    ]);
    return () => { active = false; };
  }, [api, projectId, contentAsset?.id, styleAsset?.id, referenceContext?.content_analysis_asset_id, referenceContext?.content_prompt_asset_id, referenceContext?.style_analysis_asset_id, referenceContext?.style_prompt_asset_id, referenceContext?.merged_prompt_asset_id]);

  useEffect(() => {
    let active = true;
    const recoverCompletedAnalysis = async (
      kind: AnalysisKind,
      source: AssetDto | null,
      savedPromptId: string | null | undefined,
      savedAnalysisId: string | null | undefined,
    ) => {
      if (!source) return;
      try {
        const [jobs, allAssets] = await Promise.all([api.jobs(projectId, true), api.assets(projectId)]);
        const expectedType = kind === "content" ? "image.analyze_content" : "image.analyze_style";
        const job = jobs.items.find((item) => {
          const analysisAssetId = item.output_asset_ids[0];
          const analysisAsset = analysisAssetId ? allAssets.find((asset) => asset.id === analysisAssetId) : null;
          return item.status === "succeeded"
            && (!item.job_type || item.job_type === expectedType)
            && Boolean(analysisAssetId)
            && (!savedPromptId || analysisAssetId !== savedAnalysisId)
            && (item.input_asset_ids?.includes(source.id) || analysisAsset?.parent_asset_id === source.id);
        });
        const analysisAssetId = job?.output_asset_ids[0];
        if (!analysisAssetId || !job || !active || recoveredAnalysisJobs.current.has(job.id)) return;
        const analysisText = await api.assetText(projectId, analysisAssetId);
      let diagnostic: {
        parse_error?: string | null;
        raw_response?: string | null;
        zh_text?: string | null;
      } = {};
        try {
          diagnostic = JSON.parse(analysisText) as typeof diagnostic;
        } catch {
          // Older sidecars may expose extraction-ready text rather than the
          // structured analysis envelope. Continue through the extractor.
        }
        if (diagnostic.parse_error) {
          const next = {
            ...emptyRole(),
            analysisAssetId,
            enPrompt: diagnostic.raw_response?.trim() ?? "",
            diagnostic: [
              `parse_error: ${diagnostic.parse_error}`,
              diagnostic.raw_response
                ? `raw_response:\n${diagnostic.raw_response}`
                : null,
            ]
              .filter(Boolean)
              .join("\n\n"),
          };
          if (kind === "content") setContent(next); else setStyle(next);
          if (!savedAnalysisId || analysisAssetId !== savedAnalysisId) {
            pendingAnalysisRequests.current[kind] = null;
            setApprovalKind((current) => current === kind ? null : current);
          }
          setError(
            `${kind === "content" ? "内容" : "风格"}分析已返回，但格式不符合双语 Prompt 契约：${diagnostic.parse_error}。原始响应已放入英文编辑框，请手工修正后保存或重新分析。`,
          );
          setStatus(null);
          onReferenceContextChange?.(
            kind === "content"
              ? { content_analysis_asset_id: analysisAssetId }
              : { style_analysis_asset_id: analysisAssetId },
          );
          recoveredAnalysisAssets.current.add(analysisAssetId);
          recoveredAnalysisJobs.current.add(job.id);
          return;
        }
        const extracted = await api.invokeTool(projectId, "prompt.extract_bilingual", { analysis_asset_id: analysisAssetId, kind }, requestId(`restore-${kind}`));
        const promptAssetId = extracted.output_asset_ids[0];
        if (!promptAssetId || !active) return;
        const [text, assets] = await Promise.all([api.assetText(projectId, promptAssetId), api.assets(projectId)]);
        const promptAsset = assets.find((asset) => asset.id === promptAssetId);
        if (!promptAsset || !active) return;
        const parsed = parsePrompt(text, diagnostic.zh_text);
        const next = { ...emptyRole(), analysisAssetId, promptAsset, zhPrompt: parsed.zh, enPrompt: parsed.en };
        if (kind === "content") setContent(next); else setStyle(next);
        if (!savedAnalysisId || analysisAssetId !== savedAnalysisId) {
          pendingAnalysisRequests.current[kind] = null;
          setApprovalKind((current) => current === kind ? null : current);
        }
        setStatus(`${kind === "content" ? "内容" : "风格"}分析已完成，Prompt 已自动填入。`);
        onReferenceContextChange?.(kind === "content" ? { content_analysis_asset_id: analysisAssetId, content_prompt_asset_id: promptAssetId } : { style_analysis_asset_id: analysisAssetId, style_prompt_asset_id: promptAssetId });
        recoveredAnalysisAssets.current.add(analysisAssetId);
        recoveredAnalysisJobs.current.add(job.id);
      } catch (caught) {
        if (active) {
          setError(
            caught instanceof Error
              ? `无法恢复分析结果：${caught.message}`
              : "无法恢复分析结果，请到任务中心查看详情。",
          );
        }
      }
    };
    void Promise.all([
      recoverCompletedAnalysis("content", contentAsset, referenceContext?.content_prompt_asset_id, referenceContext?.content_analysis_asset_id),
      recoverCompletedAnalysis("style", styleAsset, referenceContext?.style_prompt_asset_id, referenceContext?.style_analysis_asset_id),
    ]);
    const timer = window.setInterval(() => void Promise.all([
      recoverCompletedAnalysis("content", contentAsset, referenceContext?.content_prompt_asset_id, referenceContext?.content_analysis_asset_id),
      recoverCompletedAnalysis("style", styleAsset, referenceContext?.style_prompt_asset_id, referenceContext?.style_analysis_asset_id),
    ]), 2500);
    return () => { active = false; window.clearInterval(timer); };
  }, [api, projectId, contentAsset, styleAsset, referenceContext?.content_analysis_asset_id, referenceContext?.content_prompt_asset_id, referenceContext?.style_analysis_asset_id, referenceContext?.style_prompt_asset_id, onReferenceContextChange]);

  const analyze = async (kind: AnalysisKind) => {
    const asset = assetFor(kind);
    if (!asset) return;
    const pendingRequest = pendingAnalysisRequests.current[kind];
    if (pendingRequest) {
      pendingRequest.attempted = true;
    }
    const analysisArguments: Record<string, unknown> = {
      asset_id: asset.id,
      provider_profile: visionProfile,
      model: visionModel,
      ...(pendingRequest?.revision
        ? { analysis_revision: pendingRequest.revision }
        : {}),
    };
    setBusy(true); setError(null); setStatus(`正在分析${kind === "content" ? "内容" : "风格"}参考图…`);
    try {
      const tool = kind === "content" ? "image.analyze_content" : "image.analyze_style";
      const proposed = await api.invokeTool(projectId, tool, analysisArguments, requestId(`reference-${kind}`), { providerProfile: visionProfile });
      const approvalId = proposed.ui_action?.action_id;
      const queued = proposed.status === "awaiting_ui_action" && approvalId
        ? await api.decideApproval(projectId, approvalId, true, requestId(`approve-reference-${kind}`))
        : proposed;
      if (queued.status !== "queued") {
        throw new Error(queued.error?.user_message ?? "分析任务未能进入队列。");
      }
      queuedJob(queued);
      setStatus(`${kind === "content" ? "内容" : "风格"}参考图正在后台分析，可继续编辑其他内容。`);
      closeAnalysisApproval(kind);
    } catch (unknownError) {
      setError(unknownError instanceof Error ? unknownError.message : "分析流程失败。");
      setStatus(null);
    } finally { setBusy(false); }
  };

  const saveRole = async (kind: AnalysisKind) => {
    const current = role(kind);
    if (!current.zhPrompt.trim() || !current.enPrompt.trim()) throw new Error("中英文 Prompt 都不能为空。");
    const saved = await api.savePromptVersion(projectId, { zhPrompt: current.zhPrompt.trim(), enPrompt: current.enPrompt.trim(), kind, parentAssetId: current.promptAsset?.id ?? current.analysisAssetId }, requestId(`save-${kind}`));
    setRole(kind, { ...current, promptAsset: saved.asset });
    onReferenceContextChange?.(kind === "content" ? { content_prompt_asset_id: saved.asset.id, content_analysis_asset_id: current.analysisAssetId } : { style_prompt_asset_id: saved.asset.id, style_analysis_asset_id: current.analysisAssetId });
    return saved.asset;
  };

  const saveRoleOnly = async (kind: AnalysisKind) => {
    setBusy(true); setError(null);
    try { const saved = await saveRole(kind); setStatus(`${kind === "content" ? "内容" : "风格"} Prompt v${saved.version_no ?? 1} 已保存。`); }
    catch (unknownError) { setError(unknownError instanceof Error ? unknownError.message : "Prompt 保存失败。"); }
    finally { setBusy(false); }
  };

  const merge = async () => {
    setBusy(true); setError(null); setStatus("正在保存两个独立 Prompt 并按原始模板合并…");
    try {
      const [contentPrompt, stylePrompt] = await Promise.all([saveRole("content"), saveRole("style")]);
      const merged = await api.invokeTool(projectId, "prompt.merge", { content_prompt_asset_id: contentPrompt.id, style_prompt_asset_id: stylePrompt.id }, requestId("merge-references"));
      const promptId = merged.output_asset_ids[0];
      if (!promptId) throw new Error("本地 Prompt 合成没有返回受管版本。");
      const [text, assets] = await Promise.all([api.assetText(projectId, promptId), api.assets(projectId)]);
      const parsed = parsePrompt(text);
      const promptAsset = assets.find((asset) => asset.id === promptId) ?? null;
      if (!promptAsset) throw new Error("合并 Prompt 已创建，但资产列表尚未同步。");
      setZhPrompt(parsed.zh); setEnPrompt(parsed.en); setManagedPrompt(promptAsset);
      onReferenceContextChange?.({ merged_prompt_asset_id: promptAsset.id });
      setStatus(`合并 Prompt v${promptAsset.version_no ?? 1} 已创建；确认后即可生成。`);
    } catch (unknownError) {
      setError(unknownError instanceof Error ? unknownError.message : "Prompt 合并失败。"); setStatus(null);
    } finally { setBusy(false); }
  };

  const saveMerged = async () => {
    if (!zhPrompt.trim() || !enPrompt.trim()) throw new Error("中英文 Prompt 都不能为空。");
    const saved = await api.savePromptVersion(projectId, { zhPrompt: zhPrompt.trim(), enPrompt: enPrompt.trim(), kind: "merged", parentAssetId: managedPrompt?.id }, requestId("save-merged"));
    setManagedPrompt(saved.asset);
    onReferenceContextChange?.({ merged_prompt_asset_id: saved.asset.id });
    return saved.asset;
  };

  const copyPrompt = async (language: "中文" | "English", value: string) => {
    if (!value.trim()) return;
    try {
      await navigator.clipboard.writeText(value);
      setStatus(`${language} Prompt 已复制。`);
    } catch {
      setError("无法访问系统剪贴板；请手动复制 Prompt。");
    }
  };

  const generate = async () => {
    setBusy(true); setError(null);
    try {
      const promptAsset = await saveMerged();
      const proposed = await api.invokeTool(projectId, "image.generate", { prompt_asset_id: promptAsset.id, provider_profile: imageProfile, channel: "auto", model: imageModel, candidate_count: candidateCount, aspect_ratio: aspectRatio, output_format: "png" }, requestId("generate-candidates"), { providerProfile: imageProfile });
      const approvalId = proposed.ui_action?.action_id;
      if (proposed.status !== "awaiting_ui_action" || !approvalId) throw new Error(proposed.error?.user_message ?? "生成服务没有返回可确认的审批。");
      const approved = await api.decideApproval(projectId, approvalId, true, requestId("approve-candidates"));
      const jobId = queuedJob(approved);
      onImageJobQueued?.(jobId);
      setGenerationApproval(false);
      onModeChange("prompt_image");
    } catch (unknownError) { setError(unknownError instanceof Error ? unknownError.message : "候选生成失败。"); }
    finally { setBusy(false); }
  };

  const canMerge = Boolean(content.zhPrompt.trim() && content.enPrompt.trim() && style.zhPrompt.trim() && style.enPrompt.trim());
  const approvalAsset = approvalKind ? assetFor(approvalKind) : null;
  return (
    <aside className="prompt-parameter-drawer" aria-label="Prompt and model parameters">
      <header><div><h2>Prompt 编辑与合并</h2></div>{!persistent && <button type="button" onClick={onClose}>关闭</button>}</header>
      <RoleEditor kind="content" value={content} busy={busy} canAnalyze={contentAsset !== null} hasAnalysis={hasAnalysisFor("content")} onChange={(next) => setContent(next)} onAnalyze={() => openAnalysisApproval("content")} onSave={() => void saveRoleOnly("content")} />
      <RoleEditor kind="style" value={style} busy={busy} canAnalyze={styleAsset !== null} hasAnalysis={hasAnalysisFor("style")} onChange={(next) => setStyle(next)} onAnalyze={() => openAnalysisApproval("style")} onSave={() => void saveRoleOnly("style")} />
      <section className="prompt-role-editor" aria-label="合并 Prompt">
        <header><div><h3>合并 Prompt</h3></div><button type="button" className="primary" disabled={busy || !canMerge} onClick={() => void merge()}>生成合并 Prompt</button></header>
        {!canMerge && <p>先完成并确认内容与风格两个 Prompt，才能合并。</p>}
        {managedPrompt && <div className="managed-prompt-badge"><CheckCircle size={18} /><span>{managedPrompt.name} · v{managedPrompt.version_no ?? 1}</span></div>}
        <label>中文合并 Prompt<textarea value={zhPrompt} placeholder="完成前两项分析后，点击“生成合并 Prompt”即可填入；也可以直接手动编辑。" onChange={(event) => setZhPrompt(event.target.value)} /></label>
        <label>English merged Prompt<textarea value={enPrompt} placeholder="The merged English prompt appears here and remains editable." onChange={(event) => setEnPrompt(event.target.value)} /></label>
        <div className="model-parameter-grid"><label>模型<input value={imageModel} readOnly /></label><label>候选数量<select value={candidateCount} onChange={(event) => setCandidateCount(Number(event.target.value))}>{[1, 2, 4].map((value) => <option key={value}>{value}</option>)}</select></label><label>宽高比<select value={aspectRatio} onChange={(event) => setAspectRatio(event.target.value)}><option>1:1</option><option>16:9</option><option>9:16</option><option>4:3</option><option>3:4</option></select></label></div><div className="prompt-drawer-actions"><button type="button" disabled={busy || !zhPrompt.trim()} onClick={() => void copyPrompt("中文", zhPrompt)}>复制中文 Prompt</button><button type="button" disabled={busy || !enPrompt.trim()} onClick={() => void copyPrompt("English", enPrompt)}>Copy English Prompt</button><button type="button" disabled={busy || !zhPrompt.trim() || !enPrompt.trim()} onClick={() => void saveMerged()}>保存合并 Prompt</button><button type="button" className="primary" disabled={busy || !managedPrompt} onClick={() => setGenerationApproval(true)}>保存并生成候选</button></div>
      </section>
      {busy && <p className="prompt-drawer-status" role="status"><SpinnerGap className="spin" size={18} />{status ?? "正在处理…"}</p>}
      {!busy && status && <p className="prompt-drawer-status" role="status"><CheckCircle size={18} />{status}</p>}
      {error && <p className="prompt-drawer-error" role="alert"><WarningCircle size={18} />{error}</p>}
      {approvalKind && approvalAsset && <ExternalAnalysisDialog kind={approvalKind} asset={approvalAsset} busy={busy} onCancel={() => dismissAnalysisApproval(approvalKind)} onConfirm={() => void analyze(approvalKind)} />}
      {generationApproval && managedPrompt && <PaidGenerationDialog promptAsset={managedPrompt} candidateCount={candidateCount} busy={busy} onCancel={() => setGenerationApproval(false)} onConfirm={() => void generate()} />}
    </aside>
  );
}
