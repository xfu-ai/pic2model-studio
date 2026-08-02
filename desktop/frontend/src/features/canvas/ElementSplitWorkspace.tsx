import { DownloadSimple, FolderOpen, ImageSquare, MagicWand, Selection, Trash } from "@phosphor-icons/react";
import { useEffect, useRef, useState, type ChangeEvent, type DragEvent, type PointerEvent } from "react";
import type { ApiClient, AssetDto, SelectionDto, SelectionRect } from "../../shared/api/client";
import { CHARACTER_BREAKDOWN_PROMPT, SCENE_BREAKDOWN_PROMPT } from "../../shared/prompts/productionPrompts";
import { HostClient } from "../../shared/host/client";
import "./element-split-workspace.css";

type Handle = "nw" | "ne" | "se" | "sw";
type ElementSplitContext = { source_asset_id: string | null; split_result_asset_id: string | null; target_crop_asset_id: string | null; selection_rect: SelectionRect | null; prompt: string; job_id: string | null };
const initial = { x: 0, y: 0, width: 1, height: 1 };
const scenePrompt = SCENE_BREAKDOWN_PROMPT;
const characterPrompt = CHARACTER_BREAKDOWN_PROMPT;
const requestId = () => crypto.randomUUID();
const imageAssetTypes = new Set(["source_image", "generated_image", "annotation", "crop", "multiview"]);

export function ElementSplitWorkspace({ projectId, api, workflowContext, onWorkflowContextChange, onQueued }: { projectId: string; api: ApiClient; workflowContext?: ElementSplitContext; onWorkflowContextChange?(value: ElementSplitContext): void; onQueued(): void }) {
  const [source, setSource] = useState<AssetDto | null>(null);
  const [sourceUrl, setSourceUrl] = useState("");
  const [canvasAsset, setCanvasAsset] = useState<AssetDto | null>(null);
  const [canvasUrl, setCanvasUrl] = useState("");
  const [targetUrl, setTargetUrl] = useState("");
  const [targetCropId, setTargetCropId] = useState<string | null>(workflowContext?.target_crop_asset_id ?? null);
  const [prompt, setPrompt] = useState(workflowContext?.prompt ?? "");
  const [selection, setSelection] = useState<SelectionDto | null>(null);
  const [rect, setRect] = useState<SelectionRect>(workflowContext?.selection_rect ?? initial);
  const [message, setMessage] = useState("请拖入图片、点击选择图片，或重新加载创意图生成页的结果。");
  const [busy, setBusy] = useState(false);
  const [draggingSource, setDraggingSource] = useState(false);
  const [approvalId, setApprovalId] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(workflowContext?.job_id ?? null);
  const hydrated = useRef(false);
  const loadVersion = useRef(0);
  const [generationStatus, setGenerationStatus] = useState("");
  const [restorePoint, setRestorePoint] = useState<ElementSplitContext | null>(null);
  const image = useRef<HTMLImageElement>(null);
  const sourceInput = useRef<HTMLInputElement>(null);
  const operation = useRef<{ start: { x: number; y: number }; rect: SelectionRect; handle?: Handle; kind: "draw" | "move" | "resize" } | null>(null);

  const replaceUrl = (setter: (next: string) => void, previous: string, blob: Blob) => { if (previous) URL.revokeObjectURL(previous); setter(URL.createObjectURL(blob)); };
  const load = async () => {
    const version = ++loadVersion.current;
    try {
      const assets = await api.assets(projectId);
      if (version !== loadVersion.current) return;
      const compatible = (asset: AssetDto | undefined | null): asset is AssetDto => Boolean(asset && imageAssetTypes.has(asset.asset_type) && asset.trashed_at == null);
      const contextSource = workflowContext?.source_asset_id ? assets.find((asset) => asset.id === workflowContext.source_asset_id) : null;
      const contextSplit = workflowContext?.split_result_asset_id ? assets.find((asset) => asset.id === workflowContext.split_result_asset_id) : null;
      const contextTarget = workflowContext?.target_crop_asset_id ? assets.find((asset) => asset.id === workflowContext.target_crop_asset_id) : null;
      const hasContext = Boolean(workflowContext?.source_asset_id || workflowContext?.split_result_asset_id || workflowContext?.target_crop_asset_id);
      const splitResult = compatible(contextSplit) ? contextSplit : null;
      const targetCrop = compatible(contextTarget) ? contextTarget : null;
      const inferredSource = splitResult?.parent_asset_id
        ? assets.find((asset) => asset.id === splitResult.parent_asset_id && imageAssetTypes.has(asset.asset_type) && asset.trashed_at == null)
        : null;
      const input = compatible(contextSource)
        ? contextSource
        : compatible(inferredSource)
          ? inferredSource
          : !hasContext
            ? assets.find((asset) => asset.is_current && imageAssetTypes.has(asset.asset_type) && asset.trashed_at == null) ?? null
            : null;
      if (!input) {
        setSource(null); setCanvasAsset(null); setTargetCropId(null); setMessage("当前没有可用于元素拆分的图像。");
        return;
      }
      const nextSourceBlob = await api.assetContent(projectId, input.id);
      if (version !== loadVersion.current) return;
      const nextSplitBlob = splitResult ? await api.assetContent(projectId, splitResult.id) : null;
      if (version !== loadVersion.current) return;
      const nextTargetBlob = targetCrop ? await api.assetContent(projectId, targetCrop.id) : null;
      if (version !== loadVersion.current) return;
      replaceUrl(setSourceUrl, sourceUrl, nextSourceBlob);
      setSource(input);
      if (nextSplitBlob && splitResult) {
        replaceUrl(setCanvasUrl, canvasUrl, nextSplitBlob);
        setCanvasAsset(splitResult);
      } else {
        if (canvasUrl) URL.revokeObjectURL(canvasUrl);
        setCanvasUrl("");
        setCanvasAsset(null);
      }
      if (nextTargetBlob && targetCrop) {
        replaceUrl(setTargetUrl, targetUrl, nextTargetBlob);
        setTargetCropId(targetCrop.id);
      } else {
        if (targetUrl) URL.revokeObjectURL(targetUrl);
        setTargetUrl("");
        setTargetCropId(null);
      }
      setMessage(targetCrop ? `已恢复目标物体：${targetCrop.name}` : splitResult ? `已加载拆分结果：${splitResult.name}` : `已加载源图：${input.name}`);
    } catch {
      if (version === loadVersion.current) setMessage("无法加载当前图像。");
    }
  };
  useEffect(() => {
    hydrated.current = false;
    const expectedVersion = loadVersion.current + 1;
    void load().finally(() => {
      if (loadVersion.current === expectedVersion) hydrated.current = true;
    });
    return () => { loadVersion.current += 1; };
  }, [api, projectId, workflowContext?.source_asset_id, workflowContext?.split_result_asset_id, workflowContext?.target_crop_asset_id]);
  useEffect(() => () => { if (sourceUrl) URL.revokeObjectURL(sourceUrl); }, [sourceUrl]);
  useEffect(() => () => { if (canvasUrl) URL.revokeObjectURL(canvasUrl); }, [canvasUrl]);
  useEffect(() => () => { if (targetUrl) URL.revokeObjectURL(targetUrl); }, [targetUrl]);
  useEffect(() => {
    if (!hydrated.current) return;
    const next = { source_asset_id: source?.id ?? null, split_result_asset_id: canvasAsset?.id ?? null, target_crop_asset_id: targetCropId, selection_rect: rect, prompt, job_id: jobId };
    if (workflowContext
      && workflowContext.source_asset_id === next.source_asset_id
      && workflowContext.split_result_asset_id === next.split_result_asset_id
      && workflowContext.target_crop_asset_id === next.target_crop_asset_id
      && JSON.stringify(workflowContext.selection_rect) === JSON.stringify(next.selection_rect)
      && workflowContext.prompt === next.prompt
      && workflowContext.job_id === next.job_id) return;
    onWorkflowContextChange?.(next);
  }, [canvasAsset?.id, jobId, onWorkflowContextChange, prompt, rect, source?.id, targetCropId, workflowContext]);
  useEffect(() => {
    const selectionNode = document.querySelector<HTMLElement>(".element-rect");
    const renderedImage = image.current;
    if (!selectionNode || !renderedImage || !renderedImage.naturalWidth || !renderedImage.naturalHeight) return;
    // The image remains centered in its canvas; shift the selection by the same rendered-image offset.
    const scale = Math.min(renderedImage.clientWidth / renderedImage.naturalWidth, renderedImage.clientHeight / renderedImage.naturalHeight);
    const offsetX = (renderedImage.clientWidth - renderedImage.naturalWidth * scale) / 2;
    const offsetY = (renderedImage.clientHeight - renderedImage.naturalHeight * scale) / 2;
    selectionNode.style.translate = `${renderedImage.offsetLeft + offsetX}px ${renderedImage.offsetTop + offsetY}px`;
  }, [canvasUrl, rect]);
  useEffect(() => {
    if (!jobId) return;
    let active = true;
    const refresh = () => void api.job(projectId, jobId).then((job) => { if (!active) return; const label = job.status === "succeeded" ? "生成完成：可在候选图中选择拆分结果。" : job.status === "failed" ? `生成失败：${job.error?.user_message ?? "请到任务中心查看详情。"}` : job.status === "cancelled" ? "生成已取消。" : `正在生成 · ${job.stage}${job.progress == null ? "" : ` · ${job.progress}%`}`; setGenerationStatus(label); }).catch(() => active && setGenerationStatus("正在生成，暂时无法读取进度。"));
    refresh(); const timer = window.setInterval(refresh, 2500); return () => { active = false; window.clearInterval(timer); };
  }, [api, jobId, projectId]);
  useEffect(() => {
    if (!jobId) return;
    const panel = document.querySelector<HTMLElement>(".element-split-right"); if (!panel) return;
    const card = document.createElement("section"); card.className = "element-generation-status"; card.setAttribute("role", "status"); card.textContent = generationStatus || "任务已提交，正在获取生成进度…"; panel.prepend(card);
    return () => card.remove();
  }, [generationStatus, jobId]);

  const snapshotContext = (): ElementSplitContext => ({
    source_asset_id: source?.id ?? null,
    split_result_asset_id: canvasAsset?.id ?? null,
    target_crop_asset_id: targetCropId,
    selection_rect: rect,
    prompt,
    job_id: jobId,
  });
  const applyContext = (context: ElementSplitContext) => {
    setPrompt(context.prompt);
    setRect(context.selection_rect ?? initial);
    setTargetCropId(context.target_crop_asset_id);
    setJobId(context.job_id);
    onWorkflowContextChange?.(context);
  };
  const applySourceAsset = async (asset: AssetDto) => {
    if (!imageAssetTypes.has(asset.asset_type) || asset.trashed_at != null) throw new Error("Unsupported image asset");
    const version = ++loadVersion.current;
    const blob = await api.assetContent(projectId, asset.id);
    if (version !== loadVersion.current) return;
    replaceUrl(setSourceUrl, sourceUrl, blob);
    if (canvasUrl) URL.revokeObjectURL(canvasUrl);
    if (targetUrl) URL.revokeObjectURL(targetUrl);
    setSource(asset);
    setCanvasAsset(null);
    setCanvasUrl("");
    setTargetUrl("");
    setSelection(null);
    setApprovalId(null);
    applyContext({
      source_asset_id: asset.id,
      split_result_asset_id: null,
      target_crop_asset_id: null,
      selection_rect: initial,
      prompt,
      job_id: null,
    });
  };
  const applySavedContext = async (context: ElementSplitContext) => {
    const version = ++loadVersion.current;
    const assets = await api.assets(projectId);
    if (version !== loadVersion.current) return;
    const restoredSource = context.source_asset_id ? assets.find((asset) => asset.id === context.source_asset_id && imageAssetTypes.has(asset.asset_type) && asset.trashed_at == null) ?? null : null;
    const restoredSplit = context.split_result_asset_id ? assets.find((asset) => asset.id === context.split_result_asset_id && imageAssetTypes.has(asset.asset_type) && asset.trashed_at == null) ?? null : null;
    const restoredTarget = context.target_crop_asset_id ? assets.find((asset) => asset.id === context.target_crop_asset_id && imageAssetTypes.has(asset.asset_type) && asset.trashed_at == null) ?? null : null;
    if (restoredSource) {
      const blob = await api.assetContent(projectId, restoredSource.id);
      if (version !== loadVersion.current) return;
      replaceUrl(setSourceUrl, sourceUrl, blob);
    } else if (sourceUrl) {
      URL.revokeObjectURL(sourceUrl);
      setSourceUrl("");
    }
    if (restoredSplit) {
      const blob = await api.assetContent(projectId, restoredSplit.id);
      if (version !== loadVersion.current) return;
      replaceUrl(setCanvasUrl, canvasUrl, blob);
    } else if (canvasUrl) {
      URL.revokeObjectURL(canvasUrl);
      setCanvasUrl("");
    }
    if (restoredTarget) {
      const blob = await api.assetContent(projectId, restoredTarget.id);
      if (version !== loadVersion.current) return;
      replaceUrl(setTargetUrl, targetUrl, blob);
    } else if (targetUrl) {
      URL.revokeObjectURL(targetUrl);
      setTargetUrl("");
    }
    setSource(restoredSource);
    setCanvasAsset(restoredSplit);
    setSelection(null);
    setApprovalId(null);
    applyContext(context);
  };
  const loadCurrentAsset = async () => {
    setBusy(true);
    try {
      const assets = await api.assets(projectId);
      const current = assets.find((asset) => asset.is_current && imageAssetTypes.has(asset.asset_type) && asset.trashed_at == null);
      if (!current) {
        setMessage("项目当前资产不是可用于元素拆分的图片。");
        return;
      }
      if (current.id === source?.id) {
        setMessage(`${current.name} 已经是本页元素拆分来源。`);
        return;
      }
      setRestorePoint(snapshotContext());
      await applySourceAsset(current);
      setMessage(`${current.name} 已加载到元素拆分页；其他页签未改变。`);
    } catch {
      setMessage("无法加载项目当前资产。");
    } finally {
      setBusy(false);
    }
  };
  const restoreLoadedAsset = async () => {
    if (!restorePoint) return;
    setBusy(true);
    const current = snapshotContext();
    try {
      await applySavedContext(restorePoint);
      setRestorePoint(current);
      setMessage("已恢复元素拆分页加载当前资产之前的状态。");
    } catch {
      setMessage("无法恢复元素拆分页加载前的状态。");
    } finally {
      setBusy(false);
    }
  };

  const receiveSource = async (file: File | undefined) => {
    if (!file) return;
    if (!/^image\/(png|jpeg|webp|bmp)$/i.test(file.type)) { setMessage("请使用 PNG、JPG、WEBP 或 BMP 图片。"); return; }
    setBusy(true);
    try {
      setRestorePoint(snapshotContext());
      const capability = await new HostClient().stageDroppedFile(projectId, "source_image", file.name, Array.from(new Uint8Array(await file.arrayBuffer())));
      const asset = await api.importImage(projectId, capability, requestId());
      await api.setCurrentAsset(projectId, asset.id, requestId());
      await applySourceAsset(asset);
    } catch { setMessage("图片无法导入为元素拆分源图。"); } finally { setBusy(false); }
  };
  const importSource = async () => {
    setBusy(true);
    try { const capability = await new HostClient().chooseImportImage(projectId); if (!capability) return; setRestorePoint(snapshotContext()); const asset = await api.importImage(projectId, capability, requestId()); await api.setCurrentAsset(projectId, asset.id, requestId()); await applySourceAsset(asset); }
    catch { setMessage("导入原图失败。"); } finally { setBusy(false); }
  };
  const clearSource = () => {
    loadVersion.current += 1;
    [sourceUrl, canvasUrl, targetUrl].filter(Boolean).forEach((url) => URL.revokeObjectURL(url));
    setSource(null); setSourceUrl(""); setCanvasAsset(null); setCanvasUrl(""); setTargetUrl(""); setTargetCropId(null); setSelection(null); setRect(initial); setApprovalId(null); setJobId(null);
    setMessage("已清空本页图片；项目中的原始资产不会被删除。");
  };
  useEffect(() => {
    if (!source) return;
    const header = document.querySelector<HTMLElement>(".element-source-head");
    if (!header) return;
    const button = document.createElement("button");
    button.type = "button"; button.textContent = "清空图片"; button.setAttribute("aria-label", "清空元素拆分图片");
    button.addEventListener("click", clearSource); header.append(button);
    return () => { button.removeEventListener("click", clearSource); button.remove(); };
  }, [source, sourceUrl, canvasUrl, targetUrl]);
  const coordinate = (event: PointerEvent<HTMLElement>) => { const node = image.current; if (!node || !node.naturalWidth || !node.naturalHeight) return null; const box = node.getBoundingClientRect(); const scale = Math.min(box.width / node.naturalWidth, box.height / node.naturalHeight); const offsetX = (box.width - node.naturalWidth * scale) / 2; const offsetY = (box.height - node.naturalHeight * scale) / 2; return { x: Math.max(0, Math.min(node.naturalWidth - 1, Math.round((event.clientX - box.left - offsetX) / scale))), y: Math.max(0, Math.min(node.naturalHeight - 1, Math.round((event.clientY - box.top - offsetY) / scale))) }; };
  const constrain = (value: SelectionRect) => { const width = image.current?.naturalWidth ?? Number(canvasAsset?.metadata.width) ?? 1; const height = image.current?.naturalHeight ?? Number(canvasAsset?.metadata.height) ?? 1; const x = Math.max(0, Math.min(Math.round(value.x), width - 1)); const y = Math.max(0, Math.min(Math.round(value.y), height - 1)); return { x, y, width: Math.max(1, Math.min(Math.round(value.width), width - x)), height: Math.max(1, Math.min(Math.round(value.height), height - y)) }; };
  // Element split intentionally uses a single gesture: left mouse down is always the new selection origin.
  // Existing boxes never consume the gesture, so users can immediately replace a prior selection.
  const begin = (event: PointerEvent<HTMLDivElement>) => { if (event.button !== 0) return; const point = coordinate(event); if (!point) return; event.preventDefault(); operation.current = { start: point, rect: initial, kind: "draw" }; event.currentTarget.setPointerCapture(event.pointerId); setRect({ x: point.x, y: point.y, width: 1, height: 1 }); };
  const move = (event: PointerEvent<HTMLDivElement>) => {
    const point = coordinate(event); const active = operation.current; if (!point || !active) return;
    const dx = point.x - active.start.x; const dy = point.y - active.start.y;
    setRect(constrain({ x: Math.min(active.start.x, point.x), y: Math.min(active.start.y, point.y), width: Math.max(1, Math.abs(dx)), height: Math.max(1, Math.abs(dy)) }));
  };
  const finish = (event: PointerEvent<HTMLDivElement>) => { if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); operation.current = null; };
  const saveSelection = async (asset: AssetDto, value: SelectionRect) => { const draft = await api.saveSelection(projectId, asset.id, value, requestId(), selection?.id, selection?.revision); const confirmed = await api.confirmSelection(projectId, draft.id, draft.revision, requestId()); setSelection(confirmed); return confirmed; };
  const generate = async (text: string) => { if (!source) return; setBusy(true); try { const selected = await saveSelection(source, { x: 0, y: 0, width: Number(source.metadata.width) || 1, height: Number(source.metadata.height) || 1 }); const promptAsset = await api.savePromptVersion(projectId, { zhPrompt: text, enPrompt: text, kind: "element", parentAssetId: source.id }, requestId()); const proposed = await api.invokeTool(projectId, "element.split", { source_asset_id: source.id, selection_id: selected.id, prompt_asset_id: promptAsset.asset.id, provider_profile: "image-generation/auto", channel: "auto", model: "auto" }, requestId(), { providerProfile: "image-generation/auto" }); if (proposed.status !== "awaiting_ui_action" || !proposed.ui_action?.action_id) throw new Error(proposed.error?.user_message ?? "无法准备元素拆分任务。"); setApprovalId(proposed.ui_action.action_id); setMessage("已准备好元素拆分；批准后将在后台生成。"); } catch (error) { setMessage(error instanceof Error ? error.message : "无法准备元素拆分任务。"); } finally { setBusy(false); } };
  const approve = async () => { if (!approvalId) return; setBusy(true); try { const result = await api.decideApproval(projectId, approvalId, true, requestId()); if (result.status !== "queued" || !result.job?.job_id) throw new Error(result.error?.user_message ?? "任务未进入队列。"); setApprovalId(null); setJobId(result.job.job_id); setGenerationStatus("任务已提交，正在获取生成进度…"); setMessage("元素拆分任务已提交，生成状态显示在右侧。"); } catch (error) { setMessage(error instanceof Error ? error.message : "无法提交任务。"); } finally { setBusy(false); } };
  const exportTarget = async () => { if (!canvasAsset) return; try { const output = (await api.cropSelection(projectId, (await saveSelection(canvasAsset, rect)).id, requestId()))[0]; if (output) { await api.setCurrentAsset(projectId, output.id, requestId()); replaceUrl(setTargetUrl, targetUrl, await api.assetContent(projectId, output.id)); setTargetCropId(output.id); setMessage(`已输出目标物体：${output.name}；可在三视图制作页作为单图输入使用。`); } } catch { setMessage("输出目标物体失败，请先在画布中框选。"); } };
  const selectionScale = image.current?.clientWidth && image.current?.clientHeight && image.current.naturalWidth && image.current.naturalHeight ? Math.min(image.current.clientWidth / image.current.naturalWidth, image.current.clientHeight / image.current.naturalHeight) : 1; const scaleX = selectionScale; const scaleY = selectionScale;

  return <section className="element-split-workspace" aria-labelledby="element-split-title"><div className="element-split-left"><div className="element-source-head"><span>① 本页元素拆分来源</span><button onClick={() => void loadCurrentAsset()} disabled={busy}>加载当前资产</button><button onClick={restoreLoadedAsset} disabled={busy || !restorePoint}>恢复加载前状态</button><button onClick={() => void importSource()} disabled={busy}><ImageSquare size={16} />导入原图</button></div><div className={`element-source-drop${draggingSource ? " dragging" : ""}`} onDragEnter={(event) => { event.preventDefault(); setDraggingSource(true); }} onDragOver={(event) => event.preventDefault()} onDragLeave={() => setDraggingSource(false)} onDrop={(event: DragEvent<HTMLElement>) => { event.preventDefault(); setDraggingSource(false); void receiveSource(event.dataTransfer.files[0]); }}>{sourceUrl ? <img className="element-source-preview" src={sourceUrl} alt="元素拆分源图" /> : <button type="button" className="element-empty" disabled={busy} onClick={() => sourceInput.current?.click()}>拖入图片，或点击选择</button>}<input ref={sourceInput} type="file" accept="image/png,image/jpeg,image/webp,image/bmp" onChange={(event: ChangeEvent<HTMLInputElement>) => { void receiveSource(event.target.files?.[0]); event.target.value = ""; }} /></div><label>② 元素拆分提示词（留空则使用默认场景拆分提示词）<textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="可填写自定义元素拆分提示词" /></label><div className="element-generation-actions"><button className="secondary" disabled={busy || !source} onClick={() => void generate(scenePrompt)}><MagicWand size={17} />场景自动拆分</button><button className="secondary" disabled={busy || !source} onClick={() => void generate(characterPrompt)}><MagicWand size={17} />角色自动拆分</button></div><button className="secondary full" disabled={busy || !source || !prompt.trim()} onClick={() => void generate(prompt.trim())}>使用自定义提示词拆分</button><button className="neutral full" disabled><FolderOpen size={17} />元素拆分规格设置</button><p className="element-status" role="status">{message}</p></div><div className="element-split-center"><header><div><p className="eyebrow">Element split</p><h1 id="element-split-title">元素拆分画布</h1></div><button onClick={() => setRect(initial)} disabled={!canvasUrl}><Trash size={16} />清除选中框</button></header><div className="element-target-toolbar"><button><Selection size={17} />目标物体</button><span>在拆分结果上框选目标物体，用于三视图生成。</span></div>{canvasUrl ? <div className="element-canvas" onPointerDown={begin} onPointerMove={move} onPointerUp={finish} onPointerCancel={finish}><img ref={image} src={canvasUrl} alt={canvasAsset?.name ?? "元素拆分结果"} /><div className="element-rect" data-selection="target" style={{ left: rect.x * scaleX, top: rect.y * scaleY, width: rect.width * scaleX, height: rect.height * scaleY }}>{(["nw", "ne", "se", "sw"] as Handle[]).map((handle) => <span key={handle} className={`element-handle ${handle}`} data-handle={handle} />)}</div></div> : <div className="element-empty">生成并选择拆分结果后，会显示在这里。</div>}</div><aside className="element-split-right"><button className="primary full" disabled={!canvasUrl || busy} onClick={() => void exportTarget()}><DownloadSimple size={17} />输出目标物体</button><h2>目标物体（切片）</h2>{targetUrl ? <img className="element-target-preview" src={targetUrl} alt="目标物体切片" /> : <div className="element-target-preview">框选后点击“输出目标物体”</div>}{approvalId && <section className="element-approval"><strong>确认外部图像生成</strong><p>仅发送受管源图与提示词；原图不会被覆盖。</p><button disabled={busy} onClick={() => setApprovalId(null)}>取消</button><button className="primary" disabled={busy} onClick={() => void approve()}>批准并提交</button></section>}</aside></section>;
}
