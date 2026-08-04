import { ArrowLeft, Camera, ImageSquare, Sparkle, WarningCircle } from "@phosphor-icons/react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ApiClient, AssetDto, ReferenceContextState, WorkspaceMode } from "../../shared/api/client";
import { HostClient } from "../../shared/host/client";
import { PromptParameterDrawer } from "./PromptParameterDrawer";
import "./compare-workspace.css";

type SourceMap = Record<string, string>;
type ReferenceRole = "content" | "style";
type ReferenceRestorePoint = {
  assetId: string | null;
  analysisAssetId: string | null;
  promptAssetId: string | null;
  mergedPromptAssetId: string | null;
  counterpartAssetId: string | null;
  counterpartAnalysisAssetId: string | null;
  counterpartPromptAssetId: string | null;
};
const imageAssetTypes = new Set(["source_image", "generated_image", "annotation", "crop", "multiview"]);

function requestId(prefix: string) {
  return `${prefix}-${crypto.randomUUID()}`;
}

function ReferenceCard({
  role,
  asset,
  source,
  busy,
  onFile,
  onCapture,
  onLoadCurrent,
  onRestore,
  canLoadCurrent,
  canRestore,
}: {
  role: ReferenceRole;
  asset: AssetDto | null;
  source?: string;
  busy: boolean;
  onFile(file: File): void;
  onCapture(): void;
  onLoadCurrent(): void;
  onRestore(): void;
  canLoadCurrent: boolean;
  canRestore: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const isContent = role === "content";
  const title = isContent ? "内容参考图" : "风格参考图";
  const hint = isContent ? "决定主体、构图与结构" : "决定美术、光影与材质";
  const receiveFile = (file?: File) => { if (file) onFile(file); };
  return <section className="reference-card" aria-label={`${title}输入区`}>
    <header>
      <div><p className="eyebrow">{isContent ? "01 Content" : "02 Style"}</p><h2>{title}</h2><span>{hint}</span></div>
      <div className="reference-card-actions">
        <button type="button" disabled={busy || !canLoadCurrent} onClick={onLoadCurrent}>
          加载当前资产到{isContent ? "内容参考" : "风格参考"}
        </button>
        <button type="button" disabled={busy || !canRestore} onClick={onRestore}>
          恢复{isContent ? "内容参考" : "风格参考"}
        </button>
        <button type="button" disabled={busy} onClick={() => input.current?.click()}><ImageSquare size={16} />选择图片</button>
        <button type="button" disabled={busy} onClick={onCapture} title="截取屏幕作为参考图"><Camera size={16} />截图</button>
      </div>
    </header>
    <input ref={input} aria-label={`${title}文件选择`} className="reference-file-input" type="file" accept="image/png,image/jpeg,image/bmp,image/webp" onChange={(event) => { receiveFile(event.currentTarget.files?.[0]); event.currentTarget.value = ""; }} />
    <div className={`reference-preview${dragging ? " dragging" : ""}`} onDragEnter={(event) => { event.preventDefault(); setDragging(true); }} onDragOver={(event) => event.preventDefault()} onDragLeave={(event) => { if (event.currentTarget === event.target) setDragging(false); }} onDrop={(event) => { event.preventDefault(); setDragging(false); receiveFile(event.dataTransfer.files[0]); }}>
      {asset && source ? <img src={source} alt={`${title}: ${asset.name}`} draggable={false} /> : asset ? <p role="status">正在读取受管图片…</p> : <p>选择图片后将在这里显示大图预览。</p>}
      {!asset && <button type="button" className="reference-drop-action" disabled={busy} onClick={() => input.current?.click()}>拖入图片，或点击选择</button>}
    </div>
  </section>;
}

export function CompareWorkspace({ projectId, api, onModeChange, referenceContext, onReferenceContextChange, onImageJobQueued }: {
  projectId: string;
  api: ApiClient;
  onModeChange(mode: WorkspaceMode): void;
  onCurrentAssetChange?(): void;
  referenceContext?: ReferenceContextState;
  onReferenceContextChange?(patch: Partial<ReferenceContextState>): void;
  onImageJobQueued?(jobId: string): void;
}) {
  const [assets, setAssets] = useState<AssetDto[]>([]);
  const [contentId, setContentId] = useState<string | null>(referenceContext?.content_asset_id ?? null);
  const [styleId, setStyleId] = useState<string | null>(referenceContext?.style_asset_id ?? null);
  const [sources, setSources] = useState<SourceMap>({});
  const [busyRole, setBusyRole] = useState<ReferenceRole | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [suitabilityAnalysis, setSuitabilityAnalysis] = useState("");
  const [restorePoints, setRestorePoints] = useState<Partial<Record<ReferenceRole, ReferenceRestorePoint>>>({});
  const restoredReferences = useRef(new Set<ReferenceRole>());
  const host = useMemo(() => new HostClient(), []);
  const contentAsset = useMemo(() => assets.find((asset) => asset.id === contentId) ?? null, [assets, contentId]);
  const styleAsset = useMemo(() => assets.find((asset) => asset.id === styleId) ?? null, [assets, styleId]);
  const currentAsset = useMemo(
    () => assets.find((asset) => asset.is_current && imageAssetTypes.has(asset.asset_type) && asset.trashed_at == null) ?? null,
    [assets],
  );

  useEffect(() => {
    let active = true;
    void api.assets(projectId).then((items) => {
      if (!active) return;
      const images = items.filter((asset) => imageAssetTypes.has(asset.asset_type) && asset.trashed_at == null);
      const latestAnalysisParent = (kind: ReferenceRole) => [...items]
        .filter((asset) => asset.asset_type === "analysis" && asset.name === `${kind}-analysis.json` && asset.parent_asset_id)
        .sort((left, right) => (right.created_at ?? "").localeCompare(left.created_at ?? ""))[0]?.parent_asset_id ?? null;
      const current = images.find((asset) => asset.is_current) ?? images[0] ?? null;
      setAssets(items);
      const restoredContent = referenceContext?.content_asset_id ?? latestAnalysisParent("content");
      const restoredStyle = referenceContext?.style_asset_id ?? latestAnalysisParent("style");
      setContentId((previous) => previous && images.some((asset) => asset.id === previous) ? previous : restoredContent && images.some((asset) => asset.id === restoredContent) ? restoredContent : current?.id ?? null);
      setStyleId((previous) => previous && images.some((asset) => asset.id === previous) ? previous : restoredStyle && images.some((asset) => asset.id === restoredStyle) ? restoredStyle : null);
      if (!referenceContext?.content_asset_id && restoredContent && !restoredReferences.current.has("content")) {
        restoredReferences.current.add("content");
        onReferenceContextChange?.({ content_asset_id: restoredContent });
      }
      if (!referenceContext?.style_asset_id && restoredStyle && !restoredReferences.current.has("style")) {
        restoredReferences.current.add("style");
        onReferenceContextChange?.({ style_asset_id: restoredStyle });
      }
    }).catch(() => setError("无法读取项目图片。请重试，或从左侧资产页检查项目状态。"));
    return () => { active = false; };
  }, [api, projectId, referenceContext?.content_asset_id, referenceContext?.style_asset_id, onReferenceContextChange]);

  useEffect(() => {
    const references = [contentAsset, styleAsset].filter((asset): asset is AssetDto => asset !== null);
    if (!references.length) { setSources({}); return; }
    let active = true;
    const controller = new AbortController();
    const objectUrls: string[] = [];
    void Promise.all(references.map((asset) => api.assetContent(projectId, asset.id, controller.signal))).then((blobs) => {
      if (!active) return;
      const next: SourceMap = {};
      blobs.forEach((blob, index) => { const url = URL.createObjectURL(blob); objectUrls.push(url); next[references[index].id] = url; });
      setSources(next);
    }).catch((unknownError: unknown) => {
      if (active && !controller.signal.aborted) setError(unknownError instanceof Error ? unknownError.message : "参考图内容无法载入。");
    });
    return () => { active = false; controller.abort(); objectUrls.forEach((url) => URL.revokeObjectURL(url)); };
  }, [api, contentAsset, projectId, styleAsset]);

  useEffect(() => {
    const analysisAssetId = referenceContext?.suitability_analysis_asset_id;
    if (!analysisAssetId) {
      setSuitabilityAnalysis("");
      return;
    }
    let active = true;
    void api.assetText(projectId, analysisAssetId)
      .then((value) => { if (active) setSuitabilityAnalysis(value); })
      .catch(() => { if (active) setSuitabilityAnalysis("3D 建模适用性分析结果暂时无法读取。"); });
    return () => { active = false; };
  }, [api, projectId, referenceContext?.suitability_analysis_asset_id]);

  const snapshotRole = (role: ReferenceRole): ReferenceRestorePoint => role === "content"
    ? {
      assetId: contentId,
      analysisAssetId: referenceContext?.content_analysis_asset_id ?? null,
      promptAssetId: referenceContext?.content_prompt_asset_id ?? null,
      mergedPromptAssetId: referenceContext?.merged_prompt_asset_id ?? null,
      counterpartAssetId: styleId,
      counterpartAnalysisAssetId: referenceContext?.style_analysis_asset_id ?? null,
      counterpartPromptAssetId: referenceContext?.style_prompt_asset_id ?? null,
    }
    : {
      assetId: styleId,
      analysisAssetId: referenceContext?.style_analysis_asset_id ?? null,
      promptAssetId: referenceContext?.style_prompt_asset_id ?? null,
      mergedPromptAssetId: referenceContext?.merged_prompt_asset_id ?? null,
      counterpartAssetId: contentId,
      counterpartAnalysisAssetId: referenceContext?.content_analysis_asset_id ?? null,
      counterpartPromptAssetId: referenceContext?.content_prompt_asset_id ?? null,
    };
  const applyRole = (role: ReferenceRole, snapshot: ReferenceRestorePoint) => {
    if (role === "content") {
      setContentId(snapshot.assetId);
      onReferenceContextChange?.({
        content_asset_id: snapshot.assetId,
        content_analysis_asset_id: snapshot.analysisAssetId,
        content_prompt_asset_id: snapshot.promptAssetId,
        merged_prompt_asset_id: snapshot.mergedPromptAssetId,
      });
    } else {
      setStyleId(snapshot.assetId);
      onReferenceContextChange?.({
        style_asset_id: snapshot.assetId,
        style_analysis_asset_id: snapshot.analysisAssetId,
        style_prompt_asset_id: snapshot.promptAssetId,
        merged_prompt_asset_id: snapshot.mergedPromptAssetId,
      });
    }
  };
  const replaceReference = (role: ReferenceRole, asset: AssetDto) => {
    setRestorePoints((points) => ({ ...points, [role]: snapshotRole(role) }));
    applyRole(role, {
      assetId: asset.id,
      analysisAssetId: null,
      promptAssetId: null,
      mergedPromptAssetId: null,
      counterpartAssetId: role === "content" ? styleId : contentId,
      counterpartAnalysisAssetId: null,
      counterpartPromptAssetId: null,
    });
  };
  const loadCurrentReference = (role: ReferenceRole) => {
    if (!currentAsset) {
      setError("当前资产不是可用图片，无法加载到参考槽位。");
      return;
    }
    replaceReference(role, currentAsset);
  };
  const restoreReference = (role: ReferenceRole) => {
    const restorePoint = restorePoints[role];
    if (!restorePoint) return;
    const current = snapshotRole(role);
    const counterpartUnchanged = role === "content"
      ? styleId === restorePoint.counterpartAssetId
        && (referenceContext?.style_analysis_asset_id ?? null) === restorePoint.counterpartAnalysisAssetId
        && (referenceContext?.style_prompt_asset_id ?? null) === restorePoint.counterpartPromptAssetId
      : contentId === restorePoint.counterpartAssetId
        && (referenceContext?.content_analysis_asset_id ?? null) === restorePoint.counterpartAnalysisAssetId
        && (referenceContext?.content_prompt_asset_id ?? null) === restorePoint.counterpartPromptAssetId;
    applyRole(role, {
      ...restorePoint,
      mergedPromptAssetId: counterpartUnchanged ? restorePoint.mergedPromptAssetId : null,
    });
    setRestorePoints((points) => ({ ...points, [role]: current }));
  };
  const assignImportedAsset = (role: ReferenceRole, asset: AssetDto) => {
    setAssets((items) => [...items.filter((item) => item.id !== asset.id), asset]);
    replaceReference(role, asset);
  };
  const importReference = async (role: ReferenceRole, source: "screen" | File) => {
    setBusyRole(role); setError(null);
    try {
      const capabilityId = source === "screen" ? await host.captureScreen(projectId) : await host.stageDroppedFile(projectId, "source_image", source.name, Array.from(new Uint8Array(await source.arrayBuffer())));
      if (!capabilityId) return;
      const asset = await api.importImage(projectId, capabilityId, requestId(`${role}-${source}`));
      assignImportedAsset(role, asset);
    } catch {
      setError(`${role === "content" ? "内容" : "风格"}参考图未能导入；项目中没有创建无效资产。`);
    } finally { setBusyRole(null); }
  };

  return <section className="compare-workspace reference-workspace" aria-labelledby="workspace-title">
    <header className="compare-header">
      <button type="button" onClick={() => onModeChange("image")}><ArrowLeft size={18} />返回当前图片</button>
      <div><p className="eyebrow">Content & style analysis</p><h1 id="workspace-title">分析内容与风格参考</h1></div>
      <div className="compare-header-note"><Sparkle size={18} /><span>独立分析参考，形成可复用设计说明</span></div>
    </header>
    <div className="reference-workbench">
      <aside className="reference-inputs">
        <ReferenceCard role="content" asset={contentAsset} source={contentAsset ? sources[contentAsset.id] : undefined} busy={busyRole !== null} onFile={(file) => void importReference("content", file)} onCapture={() => void importReference("content", "screen")} onLoadCurrent={() => loadCurrentReference("content")} onRestore={() => restoreReference("content")} canLoadCurrent={Boolean(currentAsset && currentAsset.id !== contentId)} canRestore={Boolean(restorePoints.content)} />
        <ReferenceCard role="style" asset={styleAsset} source={styleAsset ? sources[styleAsset.id] : undefined} busy={busyRole !== null} onFile={(file) => void importReference("style", file)} onCapture={() => void importReference("style", "screen")} onLoadCurrent={() => loadCurrentReference("style")} onRestore={() => restoreReference("style")} canLoadCurrent={Boolean(currentAsset && currentAsset.id !== styleId)} canRestore={Boolean(restorePoints.style)} />
      </aside>
      <PromptParameterDrawer persistent projectId={projectId} api={api} contentAsset={contentAsset} styleAsset={styleAsset} onClose={() => undefined} onModeChange={onModeChange} referenceContext={referenceContext} onReferenceContextChange={onReferenceContextChange} onImageJobQueued={onImageJobQueued} />
    </div>
    {referenceContext?.suitability_analysis_asset_id && (
      <section className="reference-analysis-result" aria-label="3D 建模适用性分析">
        <h2>3D 建模适用性分析</h2>
        <p>Agent 的分析结果已写回当前页。</p>
        <pre>{suitabilityAnalysis || "正在读取分析结果…"}</pre>
      </section>
    )}
    {error && <p className="compare-error compare-reference-error" role="alert"><WarningCircle size={18} />{error}</p>}
  </section>;
}
