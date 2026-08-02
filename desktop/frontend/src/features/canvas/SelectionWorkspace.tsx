import { ArrowClockwise, ArrowCounterClockwise, ArrowLeft, CheckCircle, Crop, FloppyDisk, ImageSquare, Selection, Trash } from "@phosphor-icons/react";
import { useEffect, useRef, useState, type DragEvent, type PointerEvent } from "react";
import type { ApiClient, AssetDto, SelectionDto, SelectionRect } from "../../shared/api/client";
import { ImportImageAction } from "../assets/ImportImageAction";
import { CaptureScreenAction } from "../assets/CaptureScreenAction";
import { HostClient } from "../../shared/host/client";
import "./selection-workspace.css";

function requestId() { return crypto.randomUUID(); }
const initial = { x: 0, y: 0, width: 1, height: 1 };
type Workflow = "crop" | "scene" | "character" | "custom" | "boxsplit";
type Handle = "nw" | "ne" | "se" | "sw";
type SelectionContext = { source_asset_id: string | null; selection_id: string | null; result_asset_id: string | null; workflow: string; prompt: string; job_id: string | null };
type EditOperation = {
  kind: "draw" | "move" | "resize";
  start: { x: number; y: number };
  clientStart: { x: number; y: number };
  rect: SelectionRect;
  latest: SelectionRect;
  moved: boolean;
  handle?: Handle;
};
type BoxSplitRestorePoint = {
  context: SelectionContext;
  rect: SelectionRect;
  generationStatus: string;
};

function sameRect(left: SelectionRect, right: SelectionRect) { return left.x === right.x && left.y === right.y && left.width === right.width && left.height === right.height; }

const workflowCopy: Record<Workflow, { label: string; kind: "element" | "boxsplit"; prompt: string }> = {
  crop: { label: "本地裁切", kind: "element", prompt: "" },
  scene: { label: "场景元素拆分", kind: "element", prompt: "Extract only the selected scene object. Keep its complete silhouette, remove unrelated scene objects, and place it on a neutral studio background." },
  character: { label: "角色元素拆分", kind: "element", prompt: "Extract only the selected character or prop. Preserve the complete silhouette, important accessories, colors, and texture; remove every unrelated object." },
  custom: { label: "自定义元素拆分", kind: "element", prompt: "" },
  boxsplit: { label: "框选产品拆分", kind: "boxsplit", prompt: "Using the red selection rectangle as the target, generate only that object as a centered product image on a clean dark-gray studio background. Preserve its geometry, materials, and visible details." },
};
const boxSplitPrompt = "Identify only the object inside the green selection rectangle. Generate it as an isolated, high-fidelity 3D asset product photograph on a pure dark-gray studio background, with clean studio lighting, soft shadow, a three-quarter view, and no unrelated objects or original-scene background.";
const imageAssetTypes = new Set(["source_image", "generated_image", "annotation", "crop", "multiview"]);
const isCompatibleImage = (asset: AssetDto) => imageAssetTypes.has(asset.asset_type) && asset.trashed_at == null;
const dragThreshold = 3;

export function SelectionWorkspace({ projectId, api, onDone, onCancel = onDone, onQueued, initialWorkflow = "crop", workflowContext, onWorkflowContextChange }: { projectId: string; api: ApiClient; onDone(): void; onCancel?(): void; onQueued(): void; initialWorkflow?: Workflow; workflowContext?: SelectionContext; onWorkflowContextChange?(value: SelectionContext): void }) {
  const hydrated = useRef(false);
  const loadVersion = useRef(0);
  const isBoxSplit = initialWorkflow === "boxsplit";
  const [draggingSource, setDraggingSource] = useState(false);
  const [resultAsset, setResultAsset] = useState<AssetDto | null>(null);
  const [resultUrl, setResultUrl] = useState("");
  const [jobId, setJobId] = useState<string | null>(workflowContext?.job_id ?? null);
  const [generationStatus, setGenerationStatus] = useState("");
  const [restorePoint, setRestorePoint] = useState<BoxSplitRestorePoint | null>(null);
  const consumedResultJob = useRef<string | null>(workflowContext?.result_asset_id ? workflowContext.job_id : null);
  const [asset, setAsset] = useState<AssetDto | null>(null); const [url, setUrl] = useState(""); const [selection, setSelection] = useState<SelectionDto | null>(null); const [rect, setRect] = useState<SelectionRect>(initial); const [message, setMessage] = useState("在图片上拖拽创建框选区域。"); const [workflow, setWorkflow] = useState<Workflow>(initialWorkflow); const [customPrompt, setCustomPrompt] = useState(""); const [approvalId, setApprovalId] = useState<string | null>(null); const [busy, setBusy] = useState(false); const [historyVersion, setHistoryVersion] = useState(0); const [imageScale, setImageScale] = useState({ x: 1, y: 1 }); const [zoom, setZoom] = useState(1); const image = useRef<HTMLImageElement>(null); const baseImageSize = useRef<{ width: number; height: number } | null>(null); const operation = useRef<EditOperation | null>(null); const history = useRef<SelectionRect[]>([initial]); const historyIndex = useRef(0);
  useEffect(() => {
    const version = ++loadVersion.current;
    let alive = true;
    let sourceObjectUrl = "";
    let resultObjectUrl = "";
    hydrated.current = false;
    void api.assets(projectId).then(async (assets) => {
      const restoredSource = isBoxSplit && workflowContext?.source_asset_id
        ? assets.find((item) => item.id === workflowContext.source_asset_id && isCompatibleImage(item))
        : null;
      const next = restoredSource ?? assets.find((item) => item.is_current && isCompatibleImage(item)) ?? null;
      if (next) {
        const sourceBlob = await api.assetContent(projectId, next.id);
        if (!alive || version !== loadVersion.current) return;
        sourceObjectUrl = URL.createObjectURL(sourceBlob);
        const existing = await api.selections(projectId, next.id);
        if (!alive || version !== loadVersion.current) return;
        const saved = isBoxSplit && workflowContext?.selection_id
          ? existing.find((item) => item.id === workflowContext.selection_id)
          : existing[0];
        if (alive) {
          baseImageSize.current = null;
          setAsset(next);
          setUrl(sourceObjectUrl);
          setSelection(saved ?? null);
          setRect(saved?.rects[0] ?? initial);
          history.current = [saved?.rects[0] ?? initial];
          historyIndex.current = 0;
          setHistoryVersion((value) => value + 1);
        }
      }
      if (isBoxSplit && workflowContext?.result_asset_id) {
        const restoredResult = assets.find((item) => item.id === workflowContext.result_asset_id && isCompatibleImage(item));
        if (restoredResult) {
          const resultBlob = await api.assetContent(projectId, restoredResult.id);
          if (!alive || version !== loadVersion.current) return;
          resultObjectUrl = URL.createObjectURL(resultBlob);
          if (alive) {
            setResultAsset(restoredResult);
            setResultUrl(resultObjectUrl);
          }
        }
      }
      if (alive && isBoxSplit) {
        if (workflowContext?.workflow) setWorkflow(workflowContext.workflow as Workflow);
        if (workflowContext?.prompt) setCustomPrompt(workflowContext.prompt);
        hydrated.current = true;
      }
    }).catch(() => {
      if (alive) {
        setMessage("无法读取可框选的当前图片。");
        hydrated.current = true;
      }
    });
    return () => {
      alive = false;
      if (version === loadVersion.current) loadVersion.current += 1;
      if (sourceObjectUrl) URL.revokeObjectURL(sourceObjectUrl);
      if (resultObjectUrl) URL.revokeObjectURL(resultObjectUrl);
    };
  }, [api, initialWorkflow, projectId]);
  useEffect(() => () => { if (url) URL.revokeObjectURL(url); }, [url]);
  useEffect(() => () => { if (resultUrl) URL.revokeObjectURL(resultUrl); }, [resultUrl]);
  useEffect(() => {
    if (initialWorkflow !== "boxsplit" || !hydrated.current || !asset) return;
    const next = { source_asset_id: asset.id, selection_id: selection?.id ?? null, result_asset_id: resultAsset?.id ?? null, workflow, prompt: customPrompt, job_id: jobId };
    if (workflowContext
      && workflowContext.source_asset_id === next.source_asset_id
      && workflowContext.selection_id === next.selection_id
      && workflowContext.result_asset_id === next.result_asset_id
      && workflowContext.workflow === next.workflow
      && workflowContext.prompt === next.prompt
      && workflowContext.job_id === next.job_id) return;
    onWorkflowContextChange?.(next);
  }, [asset, customPrompt, initialWorkflow, jobId, onWorkflowContextChange, resultAsset?.id, selection?.id, workflow, workflowContext]);
  const snapshotBoxSplit = (): BoxSplitRestorePoint => ({
    context: {
      source_asset_id: asset?.id ?? null,
      selection_id: selection?.id ?? null,
      result_asset_id: resultAsset?.id ?? null,
      workflow,
      prompt: customPrompt,
      job_id: jobId,
    },
    rect,
    generationStatus,
  });
  const loadSourceAsset = async (next: AssetDto, preferredSelectionId: string | null | undefined, version: number) => {
    if (!isCompatibleImage(next)) throw new Error("Unsupported image asset");
    const blob = await api.assetContent(projectId, next.id);
    if (version !== loadVersion.current) return false;
    const preview = URL.createObjectURL(blob);
    const existing = await api.selections(projectId, next.id);
    if (version !== loadVersion.current) {
      URL.revokeObjectURL(preview);
      return false;
    }
    const saved = preferredSelectionId
      ? existing.find((item) => item.id === preferredSelectionId)
      : existing[0];
    baseImageSize.current = null;
    setAsset(next);
    setUrl((previous) => {
      if (previous) URL.revokeObjectURL(previous);
      return preview;
    });
    setSelection(saved ?? null);
    setRect(saved?.rects[0] ?? initial);
    history.current = [saved?.rects[0] ?? initial];
    historyIndex.current = 0;
    setHistoryVersion((value) => value + 1);
    return true;
  };
  const loadResultAsset = async (next: AssetDto | null, version: number) => {
    if (next && !isCompatibleImage(next)) throw new Error("Unsupported result asset");
    const blob = next ? await api.assetContent(projectId, next.id) : null;
    if (version !== loadVersion.current) return false;
    const preview = blob ? URL.createObjectURL(blob) : "";
    setResultAsset(next);
    setResultUrl((previous) => {
      if (previous) URL.revokeObjectURL(previous);
      return preview;
    });
    return true;
  };
  const refreshSource = async (version = ++loadVersion.current) => {
    const assets = await api.assets(projectId);
    if (version !== loadVersion.current) return;
    const next = assets.find((item) => item.is_current && isCompatibleImage(item));
    if (!next) return;
    await loadSourceAsset(next, null, version);
  };
  const loadCurrentAsset = async () => {
    const version = ++loadVersion.current;
    setBusy(true);
    try {
      const assets = await api.assets(projectId);
      if (version !== loadVersion.current) return;
      const current = assets.find((item) => item.is_current && isCompatibleImage(item));
      if (!current) {
        setMessage("项目当前资产不是可用于框选拆分的图片。");
        return;
      }
      if (current.id === asset?.id) {
        setMessage(`${current.name} 已经是本页框选拆分来源。`);
        return;
      }
      setRestorePoint(snapshotBoxSplit());
      if (!await loadSourceAsset(current, null, version)) return;
      if (!await loadResultAsset(null, version)) return;
      setJobId(null);
      setGenerationStatus("");
      setApprovalId(null);
      setMessage(`${current.name} 已加载到框选拆分页；其他页签未改变。`);
    } catch {
      setMessage("无法加载项目当前资产。");
    } finally {
      setBusy(false);
    }
  };
  const restoreLoadedAsset = async () => {
    if (!restorePoint) return;
    const version = ++loadVersion.current;
    setBusy(true);
    try {
      const current = snapshotBoxSplit();
      const assets = await api.assets(projectId);
      const previousSource = restorePoint.context.source_asset_id
        ? assets.find((item) => item.id === restorePoint.context.source_asset_id && isCompatibleImage(item)) ?? null
        : null;
      const previousResult = restorePoint.context.result_asset_id
        ? assets.find((item) => item.id === restorePoint.context.result_asset_id && isCompatibleImage(item)) ?? null
        : null;
      if (version !== loadVersion.current) return;
      if (previousSource) {
        if (!await loadSourceAsset(previousSource, restorePoint.context.selection_id, version)) return;
        setRect(restorePoint.rect);
        history.current = [restorePoint.rect];
        historyIndex.current = 0;
        setHistoryVersion((value) => value + 1);
      } else {
        if (url) URL.revokeObjectURL(url);
        setAsset(null);
        setUrl("");
        setSelection(null);
        setRect(restorePoint.rect);
      }
      if (!await loadResultAsset(previousResult, version)) return;
      setWorkflow(restorePoint.context.workflow as Workflow);
      setCustomPrompt(restorePoint.context.prompt);
      setJobId(restorePoint.context.job_id);
      setGenerationStatus(restorePoint.generationStatus);
      setApprovalId(null);
      setRestorePoint(current);
      setMessage("已恢复框选拆分页加载当前资产之前的状态。");
    } catch {
      setMessage("无法恢复框选拆分页加载前的状态。");
    } finally {
      setBusy(false);
    }
  };
  const clearSource = () => {
    loadVersion.current += 1;
    if (url) URL.revokeObjectURL(url);
    if (resultUrl) URL.revokeObjectURL(resultUrl);
    baseImageSize.current = null;
    history.current = [initial]; historyIndex.current = 0;
    setAsset(null); setUrl(""); setSelection(null); setRect(initial); setHistoryVersion((value) => value + 1);
    setResultAsset(null); setResultUrl(""); setJobId(null); setGenerationStatus(""); setApprovalId(null);
    setMessage("已清空当前框选拆分页的源图；项目资产不会被删除。");
    onWorkflowContextChange?.({ source_asset_id: null, selection_id: null, result_asset_id: null, workflow: "boxsplit", prompt: "", job_id: null });
  };
  const importedSource = async () => {
    setRestorePoint(snapshotBoxSplit());
    if (resultUrl) URL.revokeObjectURL(resultUrl);
    setResultAsset(null); setResultUrl(""); setJobId(null); setGenerationStatus("");
    await refreshSource();
    setMessage("已加载新的框选拆分源图。");
  };
  useEffect(() => {
    if (!isBoxSplit || !jobId) return;
    let active = true;
    let refreshing = false;
    let timer = 0;
    const stop = () => { if (timer) window.clearInterval(timer); };
    const refreshJob = () => {
      if (refreshing || consumedResultJob.current === jobId) {
        stop();
        return;
      }
      refreshing = true;
      void api.job(projectId, jobId).then(async (job) => {
      if (!active) return;
      if (job.status === "succeeded" && job.output_asset_ids?.[0]) {
        const assets = await api.assets(projectId); const output = assets.find((item) => item.id === job.output_asset_ids?.[0] && isCompatibleImage(item));
        if (!active || !output) return;
        const blob = await api.assetContent(projectId, output.id);
        if (!active) return;
        const preview = URL.createObjectURL(blob);
        await api.setCurrentAsset(projectId, output.id, requestId());
        if (!active) {
          URL.revokeObjectURL(preview);
          return;
        }
        consumedResultJob.current = jobId;
        setResultAsset(output);
        setResultUrl((previous) => {
          if (previous) URL.revokeObjectURL(previous);
          return preview;
        });
        setGenerationStatus("生成完成，可将该结果用于三视图。");
        stop();
      } else if (job.status === "failed") {
        setGenerationStatus(job.error?.user_message ?? "框选拆分失败。");
        stop();
      } else if (job.status === "cancelled") {
        setGenerationStatus("框选拆分已取消。");
        stop();
      }
      else setGenerationStatus(`正在生成 · ${job.stage}${job.progress == null ? "" : ` · ${job.progress}%`}`);
    }).catch(() => active && setGenerationStatus("正在生成，暂时无法读取进度。"))
      .finally(() => { refreshing = false; });
    };
    refreshJob();
    timer = window.setInterval(refreshJob, 2500);
    return () => { active = false; stop(); };
  }, [api, isBoxSplit, jobId, projectId]);
  const importDroppedSource = async (file: File | undefined) => {
    if (!file || !/^image\/(png|jpeg|webp|bmp)$/i.test(file.type)) { setMessage("请选择 PNG、JPG、WEBP 或 BMP 图片。"); return; }
    setBusy(true);
    try {
      setRestorePoint(snapshotBoxSplit());
      const bytes = Array.from(new Uint8Array(await file.arrayBuffer()));
      const capability = await new HostClient().stageDroppedFile(projectId, "source_image", file.name, bytes);
      const imported = await api.importImage(projectId, capability, requestId());
      await api.setCurrentAsset(projectId, imported.id, requestId());
      await refreshSource(); setMessage("已加载新的框选拆分源图。");
    } catch { setMessage("图片导入失败，请重新选择或拖入图片。"); }
    finally { setBusy(false); }
  };
  const commitRect = (next: SelectionRect) => { const base = history.current.slice(0, historyIndex.current + 1); if (!sameRect(base.at(-1) ?? initial, next)) { history.current = [...base, next]; historyIndex.current = history.current.length - 1; setHistoryVersion((value) => value + 1); } setRect(next); };
  const coordinate = (event: PointerEvent<HTMLElement>) => { if (!image.current) return null; const bounds = image.current.getBoundingClientRect(); return { x: Math.max(0, Math.min(image.current.naturalWidth - 1, Math.round((event.clientX - bounds.left) * image.current.naturalWidth / bounds.width))), y: Math.max(0, Math.min(image.current.naturalHeight - 1, Math.round((event.clientY - bounds.top) * image.current.naturalHeight / bounds.height))) }; };
  const constrain = (next: SelectionRect): SelectionRect => { const width = image.current?.naturalWidth || Number(asset?.metadata.width) || 1; const height = image.current?.naturalHeight || Number(asset?.metadata.height) || 1; const x = Math.max(0, Math.min(Math.round(next.x), Math.max(0, width - 1))); const y = Math.max(0, Math.min(Math.round(next.y), Math.max(0, height - 1))); return { x, y, width: Math.max(1, Math.min(Math.round(next.width), width - x)), height: Math.max(1, Math.min(Math.round(next.height), height - y)) }; };
  const updateOperation = (event: PointerEvent<HTMLDivElement>, current: EditOperation) => {
    const point = coordinate(event);
    if (!point) return current.latest;
    const clientDistance = Math.hypot(event.clientX - current.clientStart.x, event.clientY - current.clientStart.y);
    if (!current.moved && clientDistance < dragThreshold) return current.latest;
    current.moved = true;
    const dx = point.x - current.start.x;
    const dy = point.y - current.start.y;
    let next: SelectionRect;
    if (current.kind === "draw") {
      next = constrain({
        x: Math.min(current.start.x, point.x),
        y: Math.min(current.start.y, point.y),
        width: Math.max(1, Math.abs(dx)),
        height: Math.max(1, Math.abs(dy)),
      });
    } else if (current.kind === "move") {
      const imageWidth = image.current?.naturalWidth || Number(asset?.metadata.width) || 1;
      const imageHeight = image.current?.naturalHeight || Number(asset?.metadata.height) || 1;
      next = {
        ...current.rect,
        x: Math.max(0, Math.min(current.rect.x + dx, imageWidth - current.rect.width)),
        y: Math.max(0, Math.min(current.rect.y + dy, imageHeight - current.rect.height)),
      };
    } else {
      const left = current.handle?.includes("w") ? current.rect.x + dx : current.rect.x;
      const top = current.handle?.includes("n") ? current.rect.y + dy : current.rect.y;
      const right = current.handle?.includes("e") ? current.rect.x + current.rect.width + dx : current.rect.x + current.rect.width;
      const bottom = current.handle?.includes("s") ? current.rect.y + current.rect.height + dy : current.rect.y + current.rect.height;
      next = constrain({
        x: Math.min(left, right - 1),
        y: Math.min(top, bottom - 1),
        width: Math.max(1, Math.abs(right - left)),
        height: Math.max(1, Math.abs(bottom - top)),
      });
    }
    current.latest = next;
    setRect(next);
    return next;
  };
  const begin = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || busy) return;
    const point = coordinate(event);
    if (!point) return;
    event.preventDefault();
    const target = event.target as HTMLElement;
    const handle = target.dataset.handle as Handle | undefined;
    const kind = handle ? "resize" : target.dataset.selection === "rect" ? "move" : "draw";
    const startingRect = kind === "draw" ? { x: point.x, y: point.y, width: 1, height: 1 } : rect;
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      // Pointer capture can be unavailable during a WebView focus transition;
      // the operation still remains valid while the pointer is over the canvas.
    }
    operation.current = {
      kind,
      start: point,
      clientStart: { x: event.clientX, y: event.clientY },
      rect,
      latest: startingRect,
      moved: false,
      handle,
    };
  };
  const move = (event: PointerEvent<HTMLDivElement>) => {
    const current = operation.current;
    if (current) updateOperation(event, current);
  };
  const end = (event: PointerEvent<HTMLDivElement>) => {
    const current = operation.current;
    if (current) {
      const next = updateOperation(event, current);
      if (current.moved) commitRect(next);
      else {
        setRect(current.rect);
        setMessage("按住鼠标左键并拖动，才能创建新的选区。");
      }
    }
    operation.current = null;
    try {
      if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    } catch {
      // The WebView may already have released capture after pointer cancellation.
    }
  };
  const cancelEdit = () => {
    if (operation.current) setRect(operation.current.rect);
    operation.current = null;
  };
  const undo = () => { if (historyIndex.current <= 0) return; historyIndex.current -= 1; setRect(history.current[historyIndex.current]); setHistoryVersion((value) => value + 1); };
  const redo = () => { if (historyIndex.current >= history.current.length - 1) return; historyIndex.current += 1; setRect(history.current[historyIndex.current]); setHistoryVersion((value) => value + 1); };
  const updateScale = () => { if (!image.current || !image.current.naturalWidth || !image.current.naturalHeight || !image.current.clientWidth || !image.current.clientHeight) return; if (!baseImageSize.current) baseImageSize.current = { width: image.current.clientWidth, height: image.current.clientHeight }; setImageScale({ x: image.current.clientWidth / image.current.naturalWidth, y: image.current.clientHeight / image.current.naturalHeight }); };
  useEffect(() => { const frame = window.requestAnimationFrame(updateScale); return () => window.cancelAnimationFrame(frame); }, [zoom]);
  useEffect(() => {
    const node = image.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(updateScale);
    observer.observe(node);
    return () => observer.disconnect();
  }, [url]);
  const save = async (confirm = false): Promise<SelectionDto | null> => { if (!asset) return null; try { const next = await api.saveSelection(projectId, asset.id, rect, requestId(), selection?.id, selection?.revision); const final = confirm ? await api.confirmSelection(projectId, next.id, next.revision, requestId()) : next; setSelection(final); setMessage(confirm ? "框选已确认。" : "框选草稿已保存。"); return final; } catch { setMessage("无法保存框选，请检查区域是否在图片范围内。"); return null; } };
  const confirmed = async () => selection?.status === "confirmed" ? selection : save(true);
  const crop = async () => {
    if (rect.width <= 1 || rect.height <= 1) {
      setMessage("请先在图片上拖出要保留的范围。");
      return;
    }
    setBusy(true);
    const current = await confirmed();
    if (!current) {
      setBusy(false);
      return;
    }
    try {
      const outputs = await api.cropSelection(projectId, current.id, requestId());
      const output = outputs[0];
      if (output) await api.setCurrentAsset(projectId, output.id, requestId());
      setMessage("已裁切为新的受管资产。");
      onDone();
    } catch {
      setMessage("裁切失败，请先确认框选区域。");
    } finally {
      setBusy(false);
    }
  };
  const prepareSplit = async () => {
    if (!asset || workflow === "crop") return;
    const current = await confirmed(); if (!current) return;
    const copy = workflowCopy[workflow];
    const prompt = workflow === "boxsplit" ? boxSplitPrompt : workflow === "custom" ? customPrompt.trim() : copy.prompt;
    if (!prompt) { setMessage("请先输入自定义拆分提示词。"); return; }
    setBusy(true);
    try {
      const promptVersion = await api.savePromptVersion(projectId, { zhPrompt: prompt, enPrompt: prompt, kind: copy.kind, parentAssetId: asset.id }, requestId());
      const proposed = await api.invokeTool(projectId, "element.split", { source_asset_id: asset.id, selection_id: current.id, prompt_asset_id: promptVersion.asset.id, provider_profile: "image-generation/auto", channel: "auto", model: "auto", split_mode: isBoxSplit ? "boxsplit" : "element" }, requestId(), { providerProfile: "image-generation/auto" });
      if (proposed.status !== "awaiting_ui_action" || !proposed.ui_action?.action_id) throw new Error(proposed.error?.user_message ?? "图像服务没有返回可确认的任务。");
      setApprovalId(proposed.ui_action.action_id);
      setMessage("已准备好拆分任务；确认后才会把带红框的受管副本发送到外部服务。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "无法准备元素拆分任务。"); }
    finally { setBusy(false); }
  };
  const approveSplit = async () => {
    if (!approvalId) return;
    setBusy(true);
    try {
      const result = await api.decideApproval(projectId, approvalId, true, requestId());
      if (result.job?.job_id) setJobId(result.job.job_id);
      if (result.status !== "queued") throw new Error(result.error?.user_message ?? "拆分任务未进入队列。");
      setApprovalId(null); setMessage("拆分任务已在后台运行。完成后可在候选结果中继续。 "); onQueued();
    } catch (error) { setMessage(error instanceof Error ? error.message : "无法提交拆分任务。"); }
    finally { setBusy(false); }
  };
  const editHistory = history.current;
  if (isBoxSplit) return <section className="selection-workspace box-split-workspace" aria-labelledby="box-split-title">
    <div className={`box-split-drop-zone${draggingSource ? " dragging" : ""}`} onDragEnter={(event: DragEvent<HTMLElement>) => { event.preventDefault(); setDraggingSource(true); }} onDragOver={(event: DragEvent<HTMLElement>) => event.preventDefault()} onDragLeave={() => setDraggingSource(false)} onDrop={(event: DragEvent<HTMLElement>) => { event.preventDefault(); setDraggingSource(false); void importDroppedSource(event.dataTransfer.files[0]); }}>
    <header className="box-split-header"><div><p className="eyebrow">Box split</p><h1 id="box-split-title">框选拆分</h1><p>用途：从复杂原图中指定一个目标，生成干净的独立参考图，供后续三视图和 3D 建模使用；这不是本地裁切。</p></div><div className="box-split-source-actions"><button type="button" onClick={() => void loadCurrentAsset()} disabled={busy}><ArrowClockwise size={17} />加载当前资产</button><button type="button" onClick={() => void restoreLoadedAsset()} disabled={busy || !restorePoint}><ArrowCounterClockwise size={17} />恢复加载前状态</button><ImportImageAction projectId={projectId} api={api} label="选择图片（系统文件）" onImported={() => void importedSource()} /><CaptureScreenAction projectId={projectId} api={api} onImported={() => void importedSource()} /><button type="button" onClick={clearSource} disabled={busy || !asset}><Trash size={17} />清空图片</button><span className="box-split-source-hint">也可将 PNG、JPG、WEBP 或 BMP 拖入左侧画布</span></div></header>
    <div className="box-split-layout"><section className="box-split-stage"><div className="box-split-stage-head"><strong><Selection size={18} />目标框</strong><span>按住左键拖动新建；拖动框体移动；拖动圆点调整大小。</span></div>{url ? <><div className="selection-zoom" aria-label="Canvas zoom"><button aria-label="缩小画布" disabled={zoom <= 0.5} onClick={() => setZoom((value) => Math.max(0.5, Number((value - 0.25).toFixed(2))))}>−</button><output>{Math.round(zoom * 100)}%</output><button aria-label="放大画布" disabled={zoom >= 2} onClick={() => setZoom((value) => Math.min(2, Number((value + 0.25).toFixed(2))))}>+</button></div><div className="selection-canvas-viewport box-split-canvas-viewport"><div className="selection-canvas" onPointerDown={begin} onPointerMove={move} onPointerUp={end} onPointerCancel={cancelEdit}><img ref={image} src={url} draggable={false} onDragStart={(event) => event.preventDefault()} onLoad={updateScale} style={baseImageSize.current ? { width: `${baseImageSize.current.width * zoom}px`, height: "auto", maxWidth: "none", maxHeight: "none" } : undefined} alt={asset?.name ?? "框选拆分源图"} /><div className="selection-rect box-split-rect" data-selection="rect" style={{ left: rect.x * imageScale.x, top: rect.y * imageScale.y, width: rect.width * imageScale.x, height: rect.height * imageScale.y }}>{(["nw", "ne", "se", "sw"] as Handle[]).map((handle) => <span key={handle} className={`selection-handle ${handle}`} data-handle={handle} aria-label={`调整目标框 ${handle}`} />)}</div></div></div></> : <div className="box-split-empty"><ImageSquare size={28} /><p>请从图像生成结果重新加载，或导入/截图一张源图。</p></div>}</section>
      <aside className="box-split-panel"><h2>生成独立目标图</h2><p>绿色框会直接标注在提交给图像模型的受管副本中，不会改写原图。</p><div className="box-split-purpose"><strong>生成后得到什么？</strong><span>一张只保留目标物体、移除原场景干扰的独立参考图。它会自动成为“三视图”的可选来源。</span></div><div className="selection-controls">{(["x", "y", "width", "height"] as const).map((key) => <label key={key}>{key}<input type="number" min={key === "width" || key === "height" ? 1 : 0} value={rect[key]} onChange={(event) => commitRect(constrain({ ...rect, [key]: Number(event.target.value) }))} /></label>)}</div><div className="selection-edit-actions"><button disabled={historyIndex.current === 0} onClick={undo}><ArrowCounterClockwise size={17} />撤销</button><button disabled={historyIndex.current >= editHistory.length - 1} onClick={redo}><ArrowClockwise size={17} />重做</button><button disabled={busy} onClick={() => setRect(initial)}><Trash size={17} />清除绿框</button></div><button className="primary box-split-generate" disabled={!asset || busy} onClick={() => void prepareSplit()}>{busy ? "正在准备…" : "生成框选拆分图"}</button>{approvalId && <section className="selection-approval"><strong>确认外部图像生成</strong><p>仅会提交带绿色目标框的受管副本与内置提示词。</p><button disabled={busy} onClick={() => setApprovalId(null)}>取消</button><button className="primary" disabled={busy} onClick={() => void approveSplit()}>批准并提交</button></section>}<p className="selection-message" role="status">{message}</p></aside>
      <section className="box-split-result" aria-live="polite"><div><p className="eyebrow">Result</p><h2>框选拆分结果</h2><p>{generationStatus || "完成生成后，独立目标图会显示在这里。"}</p></div>{resultUrl ? <img src={resultUrl} alt={resultAsset?.name ?? "框选拆分结果"} /> : <div className="box-split-result-empty">等待生成结果</div>}</section></div>
    </div>
   </section>;
  const hasSelection = rect.width > 1 && rect.height > 1;
  const labels = { x: "X 起点", y: "Y 起点", width: "宽度", height: "高度" } as const;
  return <section className="selection-workspace selection-crop-workspace" aria-labelledby="selection-title">
    <header className="selection-crop-header">
      <button type="button" className="selection-back" onClick={onCancel}><ArrowLeft size={18} />返回当前图片</button>
      <div><p className="eyebrow">框选与裁切</p><h1 id="selection-title">拖框选择要保留的图片范围</h1><p>适用于屏幕截图和普通图片。拖动框体可移动，拖动四角可调整大小。</p></div>
      <span className="selection-source-name">{asset?.name ?? "尚未加载图片"}</span>
    </header>
    <div className="selection-crop-layout">
      <section className="selection-canvas-card" aria-label="框选画布">
        <div className="selection-canvas-toolbar">
          <strong><Selection size={18} />截图范围</strong>
          <div className="selection-zoom" aria-label="Canvas zoom">
            <button aria-label="缩小画布" disabled={zoom <= 0.5} onClick={() => setZoom((value) => Math.max(0.5, Number((value - 0.25).toFixed(2))))}>−</button>
            <output aria-label="画布缩放">{Math.round(zoom * 100)}%</output>
            <button aria-label="放大画布" disabled={zoom >= 2} onClick={() => setZoom((value) => Math.min(2, Number((value + 0.25).toFixed(2))))}>+</button>
          </div>
        </div>
        {url ? <div className="selection-canvas-viewport"><div className="selection-canvas" onPointerDown={begin} onPointerMove={move} onPointerUp={end} onPointerCancel={cancelEdit}>
          <img ref={image} src={url} draggable={false} onDragStart={(event) => event.preventDefault()} onLoad={updateScale} style={baseImageSize.current ? { width: `${baseImageSize.current.width * zoom}px`, height: "auto", maxWidth: "none", maxHeight: "none" } : undefined} alt={asset?.name ?? "当前图片"} />
          {hasSelection && <div className="selection-rect" data-selection="rect" style={{ left: rect.x * imageScale.x, top: rect.y * imageScale.y, width: rect.width * imageScale.x, height: rect.height * imageScale.y }}>
            {(["nw", "ne", "se", "sw"] as Handle[]).map((handle) => <span key={handle} className={`selection-handle ${handle}`} data-handle={handle} aria-label={`调整选区 ${handle}`} />)}
          </div>}
        </div></div> : <div className="selection-empty"><ImageSquare size={30} /><strong>没有可框选的当前图片</strong><p>返回当前图片页导入图片或重新框选截屏。</p></div>}
      </section>
      <aside className="selection-crop-panel">
        <div><p className="eyebrow">选区</p><h2>{hasSelection ? `${rect.width} × ${rect.height} px` : "等待拖框"}</h2><p>{hasSelection ? "裁切会创建新的受管图片，原始截图或图片保持不变。" : "在左侧图片上按住鼠标左键并拖动，圈出要保留的范围。"}</p></div>
        <div className="selection-controls">{(["x", "y", "width", "height"] as const).map((key) => <label key={key}>{labels[key]}<input aria-label={key} type="number" min={key === "width" || key === "height" ? 1 : 0} value={rect[key]} onChange={(event) => commitRect(constrain({ ...rect, [key]: Number(event.target.value) }))} /></label>)}</div>
        <div className="selection-edit-actions">
          <button disabled={historyIndex.current === 0} onClick={undo}><ArrowCounterClockwise size={17} />撤销</button>
          <button disabled={historyIndex.current >= editHistory.length - 1} onClick={redo}><ArrowClockwise size={17} />重做</button>
          <button disabled={!hasSelection || busy} onClick={() => commitRect(initial)}><Trash size={17} />清除选区</button>
        </div>
        <div className="selection-primary-actions">
          <button type="button" disabled={!hasSelection || busy} onClick={() => void save()}><FloppyDisk size={17} />保存选区</button>
          <button type="button" disabled={!hasSelection || busy} onClick={() => void save(true)}><CheckCircle size={17} />仅确认选区</button>
          <button type="button" className="primary" disabled={!hasSelection || busy} onClick={() => void crop()}><Crop size={18} />{busy ? "正在裁切…" : "裁切并返回主页"}</button>
        </div>
        <p className="selection-message" role="status">{message}</p>
      </aside>
    </div>
  </section>;
}
