import {
  ArrowClockwise,
  ArrowCounterClockwise,
  BoundingBox,
  CheckCircle,
  Crop,
  ImageSquare,
  MagicWand,
  MonitorArrowUp,
  Selection,
  SpinnerGap,
  SquaresFour,
  Trash,
} from "@phosphor-icons/react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type DragEvent,
  type PointerEvent,
} from "react";
import type {
  ApiClient,
  AssetDto,
  SelectionDto,
  SelectionRect,
  WorkflowContexts,
} from "../../shared/api/client";
import { HostClient } from "../../shared/host/client";
import { CHARACTER_BREAKDOWN_PROMPT, DIRECT_TARGET_PROMPT, SCENE_BREAKDOWN_PROMPT } from "../../shared/prompts/productionPrompts";
import "./target-extraction-workspace.css";

type ExtractionContext = WorkflowContexts["target_extract"];
type Handle = "nw" | "ne" | "se" | "sw";
type EditOperation = {
  kind: "draw" | "move" | "resize";
  start: { x: number; y: number };
  rect: SelectionRect;
  latest: SelectionRect;
  handle?: Handle;
};

const initialRect: SelectionRect = { x: 0, y: 0, width: 1, height: 1 };
const imageAssetTypes = new Set(["source_image", "generated_image", "annotation", "crop", "multiview"]);
const productionProfile = "image-generation/auto";
const productionModel = "auto";

function requestId(prefix: string) {
  return `${prefix}-${crypto.randomUUID()}`;
}

function isImageAsset(asset: AssetDto | null | undefined): asset is AssetDto {
  return Boolean(asset && imageAssetTypes.has(asset.asset_type) && asset.trashed_at == null);
}

function sameRect(left: SelectionRect, right: SelectionRect) {
  return left.x === right.x && left.y === right.y && left.width === right.width && left.height === right.height;
}

function useManagedImageUrl(
  api: ApiClient,
  projectId: string,
  assetId: string | null,
) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    let active = true;
    let objectUrl = "";
    setUrl("");
    if (!assetId) return () => undefined;
    void api.assetContent(projectId, assetId).then((blob) => {
      objectUrl = URL.createObjectURL(blob);
      if (active) setUrl(objectUrl);
    }).catch(() => undefined);
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [api, assetId, projectId]);
  return url;
}

export function TargetExtractionWorkspace({
  projectId,
  api,
  workflowContext,
  onWorkflowContextChange,
  onContinueToMultiview,
  onOpenTasks,
  onOpenAssets,
  host = new HostClient(),
}: {
  projectId: string;
  api: ApiClient;
  workflowContext: ExtractionContext;
  onWorkflowContextChange(value: ExtractionContext): void;
  onContinueToMultiview(assetId: string): void;
  onOpenTasks?(): void;
  onOpenAssets?(assetId: string): void;
  host?: HostClient;
}) {
  const [context, setContext] = useState(workflowContext);
  const [assets, setAssets] = useState<AssetDto[]>([]);
  const [selection, setSelection] = useState<SelectionDto | null>(null);
  const [rect, setRect] = useState(
    workflowContext.method === "breakdown"
      ? workflowContext.breakdown_selection_rect ?? initialRect
      : workflowContext.source_selection_rect ?? initialRect,
  );
  const [message, setMessage] = useState("选择一张来源图片，然后框选需要提取的目标。");
  const [generationStatus, setGenerationStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [draggingSource, setDraggingSource] = useState(false);
  const [restorePoint, setRestorePoint] = useState<ExtractionContext | null>(null);
  const [zoom, setZoom] = useState(1);
  const [imageScale, setImageScale] = useState({ x: 1, y: 1 });
  const image = useRef<HTMLImageElement>(null);
  const baseImageSize = useRef<{ width: number; height: number } | null>(null);
  const operation = useRef<EditOperation | null>(null);
  const history = useRef<SelectionRect[]>([workflowContext.source_selection_rect ?? initialRect]);
  const historyIndex = useRef(0);
  const consumedJob = useRef<string | null>(
    workflowContext.active_result_asset_id || workflowContext.breakdown_asset_id
      ? workflowContext.job_id
      : null,
  );

  const patchContext = useCallback((patch: Partial<ExtractionContext>) => {
    setContext((current) => {
      const next = { ...current, ...patch };
      onWorkflowContextChange(next);
      return next;
    });
  }, [onWorkflowContextChange]);

  const refreshAssets = useCallback(async () => {
    const next = await api.assets(projectId);
    setAssets(next);
    return next;
  }, [api, projectId]);

  useEffect(() => {
    void refreshAssets().catch(() => setMessage("无法读取项目中的图片资产。"));
  }, [refreshAssets]);

  const source = assets.find((asset) => asset.id === context.source_asset_id && isImageAsset(asset)) ?? null;
  const breakdown = assets.find((asset) => asset.id === context.breakdown_asset_id && isImageAsset(asset)) ?? null;
  const activeResult = assets.find((asset) => asset.id === context.active_result_asset_id && isImageAsset(asset)) ?? null;
  const sourceUrl = useManagedImageUrl(api, projectId, source?.id ?? null);
  const breakdownUrl = useManagedImageUrl(api, projectId, breakdown?.id ?? null);
  const resultUrl = useManagedImageUrl(api, projectId, activeResult?.id ?? null);
  const editingAsset = context.method === "breakdown" ? breakdown : source;
  const editingUrl = context.method === "breakdown" ? breakdownUrl : sourceUrl;
  const editingSelectionId = context.method === "breakdown"
    ? context.breakdown_selection_id
    : context.source_selection_id;

  useEffect(() => {
    if (!editingAsset || !editingSelectionId) {
      setSelection(null);
      return;
    }
    let active = true;
    void api.selections(projectId, editingAsset.id).then((items) => {
      if (!active) return;
      const restored = items.find((item) => item.id === editingSelectionId) ?? null;
      setSelection(restored);
      if (restored?.rects[0]) {
        setRect(restored.rects[0]);
        history.current = [restored.rects[0]];
        historyIndex.current = 0;
      }
    }).catch(() => active && setSelection(null));
    return () => { active = false; };
  }, [api, editingAsset, editingSelectionId, projectId]);

  useEffect(() => {
    const next = context.method === "breakdown"
      ? context.breakdown_selection_rect ?? initialRect
      : context.source_selection_rect ?? initialRect;
    setSelection(null);
    setRect(next);
    history.current = [next];
    historyIndex.current = 0;
    baseImageSize.current = null;
    setZoom(1);
  }, [context.method]);

  useEffect(() => {
    if (!context.job_id || consumedJob.current === context.job_id) return;
    let active = true;
    let refreshing = false;
    let timer = 0;
    const stop = () => { if (timer) window.clearInterval(timer); };
    const refresh = () => {
      if (refreshing) return;
      refreshing = true;
      void api.job(projectId, context.job_id!).then(async (job) => {
        if (!active) return;
        if (job.status === "succeeded" && job.output_asset_ids?.[0]) {
          const all = await refreshAssets();
          if (!active) return;
          const output = all.find((asset) => asset.id === job.output_asset_ids[0] && isImageAsset(asset));
          if (!output) throw new Error("生成结果尚未成为受管图片。");
          consumedJob.current = context.job_id;
          if (context.method === "breakdown") {
            patchContext({
              stage: "select_breakdown_part",
              breakdown_asset_id: output.id,
              breakdown_selection_id: null,
              breakdown_selection_rect: initialRect,
              pending_action_id: null,
            });
            setRect(initialRect);
            history.current = [initialRect];
            historyIndex.current = 0;
            setGenerationStatus("拆解图生成完成，请在红框画布中选择一个部件。");
            setMessage(`已生成部件拆解图：${output.name}`);
          } else {
            const resultIds = [...new Set([...context.result_asset_ids, output.id])];
            patchContext({
              stage: "result",
              result_asset_ids: resultIds,
              active_result_asset_id: output.id,
              pending_action_id: null,
            });
            setGenerationStatus("生成完成，可以进入三视图或继续提取。");
            setMessage(`已生成独立目标图：${output.name}`);
          }
          stop();
        } else if (job.status === "failed") {
          patchContext({ stage: "error", pending_action_id: null });
          setGenerationStatus(job.error?.user_message ?? "目标提取失败，请在任务中心查看详情。");
          stop();
        } else if (job.status === "cancelled") {
          patchContext({
            stage: source
              ? context.method === "breakdown" ? "configure_breakdown" : "select_target"
              : "select_source",
            pending_action_id: null,
          });
          setGenerationStatus("目标提取已取消。");
          stop();
        } else {
          setGenerationStatus(`正在生成 · ${job.stage}${job.progress == null ? "" : ` · ${job.progress}%`}`);
        }
      }).catch(() => active && setGenerationStatus("正在生成，暂时无法读取进度。"))
        .finally(() => { refreshing = false; });
    };
    refresh();
    timer = window.setInterval(refresh, 2500);
    return () => { active = false; stop(); };
  }, [api, context.job_id, context.result_asset_ids, patchContext, projectId, refreshAssets, source]);

  const resetSelection = (next = initialRect) => {
    setSelection(null);
    setRect(next);
    history.current = [next];
    historyIndex.current = 0;
    patchContext(context.method === "breakdown"
      ? { breakdown_selection_id: null, breakdown_selection_rect: next }
      : { source_selection_id: null, source_selection_rect: next });
  };

  const applySource = (asset: AssetDto) => {
    if (!isImageAsset(asset)) return;
    baseImageSize.current = null;
    setZoom(1);
    setSelection(null);
    setRect(initialRect);
    history.current = [initialRect];
    historyIndex.current = 0;
    consumedJob.current = null;
    patchContext({
      stage: context.method === "direct" ? "select_target" : "configure_breakdown",
      source_asset_id: asset.id,
      source_selection_id: null,
      source_selection_rect: initialRect,
      prompt_asset_id: null,
      breakdown_asset_id: null,
      breakdown_selection_id: null,
      breakdown_selection_rect: null,
      result_asset_ids: [],
      active_result_asset_id: null,
      job_id: null,
      pending_action_id: null,
    });
    setGenerationStatus("");
    setMessage(`${asset.name} 已作为本页提取来源；项目当前资产未改变。`);
  };

  const loadCurrentAsset = async () => {
    setBusy(true);
    try {
      const all = await refreshAssets();
      const current = all.find((asset) => asset.is_current && isImageAsset(asset));
      if (!current) {
        setMessage("项目当前资产不是可用于目标提取的图片。");
        return;
      }
      if (current.id === source?.id) {
        setMessage(`${current.name} 已经是本页来源图片。`);
        return;
      }
      setRestorePoint(context);
      applySource(current);
    } catch {
      setMessage("无法加载项目当前图片。");
    } finally {
      setBusy(false);
    }
  };

  const restoreSource = () => {
    if (!restorePoint) return;
    const current = context;
    setContext(restorePoint);
    onWorkflowContextChange(restorePoint);
    const restoredRect = restorePoint.method === "breakdown"
      ? restorePoint.breakdown_selection_rect ?? initialRect
      : restorePoint.source_selection_rect ?? initialRect;
    setRect(restoredRect);
    history.current = [restoredRect];
    historyIndex.current = 0;
    setRestorePoint(current);
    setMessage("已恢复加载当前图片之前的目标提取状态。");
  };

  const importCapability = async (capabilityId: string, label: string) => {
    setBusy(true);
    try {
      setRestorePoint(context);
      const imported = await api.importImage(projectId, capabilityId, requestId("target-extract-import"));
      setAssets((current) => [imported, ...current.filter((asset) => asset.id !== imported.id)]);
      applySource(imported);
      setMessage(`${label}已导入并作为本页来源；项目当前资产未改变。`);
    } catch {
      setMessage(`${label}导入失败，没有创建可用来源。`);
    } finally {
      setBusy(false);
    }
  };

  const chooseSource = async () => {
    setBusy(true);
    try {
      const capability = await host.chooseImportImage(projectId);
      if (!capability) {
        setMessage("未选择图片，本页来源保持不变。");
        return;
      }
      await importCapability(capability, "图片");
    } catch {
      setMessage("无法打开系统文件选择窗口，请重试。");
    } finally {
      setBusy(false);
    }
  };

  const captureSource = async () => {
    try {
      const capability = await new HostClient().captureScreen(projectId);
      await importCapability(capability, "截图");
    } catch {
      setMessage("截图未能导入为目标提取来源。");
    }
  };

  const importDroppedSource = async (file: File | undefined) => {
    if (!file || !/^image\/(png|jpeg|webp|bmp)$/i.test(file.type)) {
      setMessage("请选择 PNG、JPG、WEBP 或 BMP 图片。");
      return;
    }
    const capability = await new HostClient().stageDroppedFile(
      projectId,
      "source_image",
      file.name,
      Array.from(new Uint8Array(await file.arrayBuffer())),
    );
    await importCapability(capability, "图片");
  };

  const clearSource = () => {
    setRestorePoint(context);
    setSelection(null);
    setRect(initialRect);
    history.current = [initialRect];
    historyIndex.current = 0;
    patchContext({
      stage: "select_source",
      source_asset_id: null,
      source_selection_id: null,
      source_selection_rect: null,
      prompt_asset_id: null,
      breakdown_asset_id: null,
      breakdown_selection_id: null,
      breakdown_selection_rect: null,
      result_asset_ids: [],
      active_result_asset_id: null,
      job_id: null,
      pending_action_id: null,
    });
    setMessage("已清空本页来源，项目中的受管资产不会被删除。");
  };

  const updateScale = () => {
    const node = image.current;
    if (!node || !node.naturalWidth || !node.naturalHeight) return;
    if (!baseImageSize.current) {
      baseImageSize.current = { width: node.clientWidth, height: node.clientHeight };
    }
    setImageScale({
      x: node.clientWidth / node.naturalWidth,
      y: node.clientHeight / node.naturalHeight,
    });
  };

  useEffect(() => {
    const frame = window.requestAnimationFrame(updateScale);
    return () => window.cancelAnimationFrame(frame);
  }, [zoom, sourceUrl]);

  const constrain = (next: SelectionRect): SelectionRect => {
    const width = image.current?.naturalWidth || Number(editingAsset?.metadata.width) || 1;
    const height = image.current?.naturalHeight || Number(editingAsset?.metadata.height) || 1;
    const x = Math.max(0, Math.min(Math.round(next.x), Math.max(0, width - 1)));
    const y = Math.max(0, Math.min(Math.round(next.y), Math.max(0, height - 1)));
    return {
      x,
      y,
      width: Math.max(1, Math.min(Math.round(next.width), width - x)),
      height: Math.max(1, Math.min(Math.round(next.height), height - y)),
    };
  };

  const commitRect = (next: SelectionRect) => {
    const constrained = constrain(next);
    const base = history.current.slice(0, historyIndex.current + 1);
    if (!sameRect(base.at(-1) ?? initialRect, constrained)) {
      history.current = [...base, constrained];
      historyIndex.current = history.current.length - 1;
    }
    setRect(constrained);
    setSelection(null);
    patchContext(context.method === "breakdown"
      ? { breakdown_selection_id: null, breakdown_selection_rect: constrained }
      : { source_selection_id: null, source_selection_rect: constrained });
  };

  const coordinate = (event: PointerEvent<HTMLElement>) => {
    const node = image.current;
    if (!node?.naturalWidth || !node.naturalHeight) return null;
    const bounds = node.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(node.naturalWidth - 1, Math.round((event.clientX - bounds.left) * node.naturalWidth / bounds.width))),
      y: Math.max(0, Math.min(node.naturalHeight - 1, Math.round((event.clientY - bounds.top) * node.naturalHeight / bounds.height))),
    };
  };

  const begin = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    const point = coordinate(event);
    if (!point) return;
    const target = event.target as HTMLElement;
    const handle = target.dataset.handle as Handle | undefined;
    const kind = handle ? "resize" : target.dataset.selection === "rect" ? "move" : "draw";
    const startingRect = kind === "draw" ? { x: point.x, y: point.y, width: 1, height: 1 } : rect;
    event.currentTarget.setPointerCapture(event.pointerId);
    operation.current = { kind, start: point, rect, latest: startingRect, handle };
    if (kind === "draw") setRect(startingRect);
  };

  const move = (event: PointerEvent<HTMLDivElement>) => {
    const point = coordinate(event);
    const current = operation.current;
    if (!point || !current) return;
    const dx = point.x - current.start.x;
    const dy = point.y - current.start.y;
    if (current.kind === "draw") {
      const next = constrain({
        x: Math.min(current.start.x, point.x),
        y: Math.min(current.start.y, point.y),
        width: Math.max(1, Math.abs(dx)),
        height: Math.max(1, Math.abs(dy)),
      });
      current.latest = next;
      setRect(next);
      return;
    }
    if (current.kind === "move") {
      const next = constrain({ ...current.rect, x: current.rect.x + dx, y: current.rect.y + dy });
      current.latest = next;
      setRect(next);
      return;
    }
    const left = current.handle?.includes("w") ? current.rect.x + dx : current.rect.x;
    const top = current.handle?.includes("n") ? current.rect.y + dy : current.rect.y;
    const right = current.handle?.includes("e") ? current.rect.x + current.rect.width + dx : current.rect.x + current.rect.width;
    const bottom = current.handle?.includes("s") ? current.rect.y + current.rect.height + dy : current.rect.y + current.rect.height;
    const next = constrain({
      x: Math.min(left, right - 1),
      y: Math.min(top, bottom - 1),
      width: Math.max(1, Math.abs(right - left)),
      height: Math.max(1, Math.abs(bottom - top)),
    });
    current.latest = next;
    setRect(next);
  };

  const end = (event: PointerEvent<HTMLDivElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    if (operation.current) commitRect(operation.current.latest);
    operation.current = null;
  };

  const undo = () => {
    if (historyIndex.current <= 0) return;
    historyIndex.current -= 1;
    const next = history.current[historyIndex.current];
    setRect(next);
    setSelection(null);
    patchContext(context.method === "breakdown"
      ? { breakdown_selection_id: null, breakdown_selection_rect: next }
      : { source_selection_id: null, source_selection_rect: next });
  };

  const redo = () => {
    if (historyIndex.current >= history.current.length - 1) return;
    historyIndex.current += 1;
    const next = history.current[historyIndex.current];
    setRect(next);
    setSelection(null);
    patchContext(context.method === "breakdown"
      ? { breakdown_selection_id: null, breakdown_selection_rect: next }
      : { source_selection_id: null, source_selection_rect: next });
  };

  const confirmedSelection = async () => {
    if (!editingAsset || rect.width <= 1 || rect.height <= 1) {
      setMessage(context.method === "breakdown"
        ? "请先在拆解图上框选一个有效部件。"
        : "请先在来源图片上框选一个有效目标。");
      return null;
    }
    if (selection?.status === "confirmed" && sameRect(selection.rects[0], rect)) return selection;
    const draft = await api.saveSelection(
      projectId,
      editingAsset.id,
      rect,
      requestId("target-extract-selection"),
      selection?.id,
      selection?.revision,
    );
    const confirmed = await api.confirmSelection(
      projectId,
      draft.id,
      draft.revision,
      requestId("target-extract-confirm"),
    );
    setSelection(confirmed);
    patchContext(context.method === "breakdown"
      ? {
          breakdown_selection_id: confirmed.id,
          breakdown_selection_rect: confirmed.rects[0] ?? rect,
        }
      : {
          source_selection_id: confirmed.id,
          source_selection_rect: confirmed.rects[0] ?? rect,
        });
    return confirmed;
  };

  const localCrop = async () => {
    setBusy(true);
    try {
      const confirmed = await confirmedSelection();
      if (!confirmed) return;
      const output = (await api.cropSelection(projectId, confirmed.id, requestId("target-extract-crop")))[0];
      if (!output) throw new Error("本地裁切没有生成结果。");
      setAssets((current) => [output, ...current.filter((asset) => asset.id !== output.id)]);
      const resultIds = [...new Set([...context.result_asset_ids, output.id])];
      patchContext({
        stage: "result",
        result_asset_ids: resultIds,
        active_result_asset_id: output.id,
      });
      setMessage(context.method === "breakdown"
        ? `已从拆解图裁出部件：${output.name}；可以继续提取其他部件。`
        : `已在本地裁出目标：${output.name}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "本地裁切失败。");
    } finally {
      setBusy(false);
    }
  };

  const prepareDirect = async () => {
    if (!source) return;
    setBusy(true);
    try {
      const confirmed = await confirmedSelection();
      if (!confirmed) return;
      const prompt = await api.savePromptVersion(projectId, {
        zhPrompt: DIRECT_TARGET_PROMPT,
        enPrompt: DIRECT_TARGET_PROMPT,
        kind: "boxsplit",
        parentAssetId: source.id,
      }, requestId("target-extract-prompt"));
      const proposed = await api.invokeTool(projectId, "element.split", {
        source_asset_id: source.id,
        selection_id: confirmed.id,
        prompt_asset_id: prompt.asset.id,
        provider_profile: productionProfile,
        channel: "auto",
        model: productionModel,
        split_mode: "boxsplit",
      }, requestId("target-extract-propose"), { providerProfile: productionProfile });
      const actionId = proposed.ui_action?.action_id;
      if (proposed.status !== "awaiting_ui_action" || !actionId) {
        throw new Error(proposed.error?.user_message ?? "图像服务没有返回可确认的目标提取任务。");
      }
      patchContext({
        stage: "awaiting_approval",
        prompt_asset_id: prompt.asset.id,
        pending_action_id: actionId,
      });
      setMessage("目标提取任务已准备好；批准后才会发送带绿色目标框的受管副本。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "无法准备目标提取任务。");
    } finally {
      setBusy(false);
    }
  };

  const prepareBreakdown = async () => {
    if (!source) return;
    const promptText = context.preset === "character"
      ? CHARACTER_BREAKDOWN_PROMPT
      : context.preset === "custom"
        ? context.custom_prompt.trim()
        : SCENE_BREAKDOWN_PROMPT;
    if (!promptText) {
      setMessage("请先填写自定义拆解要求。");
      return;
    }
    setBusy(true);
    try {
      const prompt = await api.savePromptVersion(projectId, {
        zhPrompt: promptText,
        enPrompt: promptText,
        kind: "element",
        parentAssetId: source.id,
      }, requestId("target-breakdown-prompt"));
      const proposed = await api.invokeTool(projectId, "element.split", {
        source_asset_id: source.id,
        prompt_asset_id: prompt.asset.id,
        provider_profile: productionProfile,
        channel: "auto",
        model: productionModel,
        split_mode: "element",
      }, requestId("target-breakdown-propose"), { providerProfile: productionProfile });
      const actionId = proposed.ui_action?.action_id;
      if (proposed.status !== "awaiting_ui_action" || !actionId) {
        throw new Error(proposed.error?.user_message ?? "图像服务没有返回可确认的 AI 拆解任务。");
      }
      patchContext({
        stage: "awaiting_approval",
        prompt_asset_id: prompt.asset.id,
        pending_action_id: actionId,
      });
      setMessage("AI 拆解任务已准备好；批准后只会发送无选框的受管来源图和拆解要求。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "无法准备 AI 拆解任务。");
    } finally {
      setBusy(false);
    }
  };

  const decideApproval = async (approved: boolean) => {
    if (!context.pending_action_id) return;
    setBusy(true);
    try {
      if (!approved) {
        await api.decideApproval(
          projectId,
          context.pending_action_id,
          false,
          requestId("target-extract-decline"),
        );
        patchContext({
          stage: context.method === "breakdown" ? "configure_breakdown" : "select_target",
          pending_action_id: null,
        });
        setMessage("已取消外部图像生成，选框和来源仍然保留。");
        return;
      }
      const result = await api.decideApproval(
        projectId,
        context.pending_action_id,
        true,
        requestId("target-extract-approve"),
      );
      if (result.status !== "queued" || !result.job?.job_id) {
        throw new Error(result.error?.user_message ?? "目标提取任务未进入队列。");
      }
      consumedJob.current = null;
      patchContext({
        stage: "generating",
        job_id: result.job.job_id,
        pending_action_id: null,
      });
      setGenerationStatus("任务已提交，正在获取生成进度…");
      setMessage("目标提取正在后台生成；你可以继续留在本页查看进度。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "无法提交目标提取任务。");
    } finally {
      setBusy(false);
    }
  };

  const setCurrentResult = async () => {
    if (!activeResult) return;
    setBusy(true);
    try {
      await api.setCurrentAsset(projectId, activeResult.id, requestId("target-extract-current"));
      setAssets((current) => current.map((asset) => ({ ...asset, is_current: asset.id === activeResult.id })));
      setMessage(`${activeResult.name} 已设为项目当前资产。`);
    } catch {
      setMessage("无法将目标结果设为项目当前资产。");
    } finally {
      setBusy(false);
    }
  };

  const canExtract = Boolean(editingAsset && rect.width > 1 && rect.height > 1 && !busy);

  return (
    <section
      className={`target-extraction-workspace${draggingSource ? " dragging" : ""}`}
      aria-labelledby="target-extraction-title"
      onDragEnter={(event: DragEvent<HTMLElement>) => { event.preventDefault(); setDraggingSource(true); }}
      onDragOver={(event) => event.preventDefault()}
      onDragLeave={() => setDraggingSource(false)}
      onDrop={(event: DragEvent<HTMLElement>) => {
        event.preventDefault();
        setDraggingSource(false);
        void importDroppedSource(event.dataTransfer.files[0]);
      }}
    >
      <header className="target-extraction-header">
        <div>
          <p className="eyebrow">Modeling subject</p>
          <h1 id="target-extraction-title">提取可建模主体</h1>
          <p>从复杂图片中提取干净的建模主体，可继续制作三视图。</p>
        </div>
        <div className="target-source-actions">
          <button type="button" disabled={busy} onClick={() => void loadCurrentAsset()}>
            <ArrowClockwise size={17} />加载当前图片
          </button>
          <button className="target-choose-source primary" type="button" disabled={busy} onClick={() => void chooseSource()}>
            <ImageSquare size={17} />选择图片
          </button>
          <button type="button" disabled={busy} onClick={() => void captureSource()}>
            <MonitorArrowUp size={17} />截图导入
          </button>
          <button type="button" disabled={busy || !restorePoint} onClick={restoreSource}>
            <ArrowCounterClockwise size={17} />恢复加载前状态
          </button>
          <button type="button" disabled={busy || !source} onClick={clearSource}>
            <Trash size={17} />清空
          </button>
        </div>
      </header>

      {context.agent_instruction && (
        <section className="target-agent-request" role="status">
          <strong>Agent 正在等待你的确认</strong>
          <span>{context.agent_instruction}</span>
        </section>
      )}

      <div className="target-extraction-layout">
        <aside className="target-settings-panel">
          <section>
            <h2>① 来源图片</h2>
            {sourceUrl ? <img src={sourceUrl} alt={source?.name ?? "目标提取来源"} /> : <div className="target-source-empty">拖入图片，或点击“选择图片”</div>}
            <p>{source ? `本页来源：${source.name}${source.is_current ? " · 项目当前图片" : " · 仅本页使用"}` : "尚未选择来源图片"}</p>
          </section>
          <section>
            <h2>② 提取方式</h2>
            <div className="target-methods" role="radiogroup" aria-label="提取方式">
              <button
                type="button"
                role="radio"
                aria-checked={context.method === "direct"}
                className={context.method === "direct" ? "active" : undefined}
                onClick={() => patchContext({ method: "direct", stage: source ? "select_target" : "select_source" })}
              >
                <BoundingBox size={20} />
                <span><strong>直接框选目标</strong><small>推荐 · 最快</small></span>
              </button>
              <button
                type="button"
                role="radio"
                aria-checked={context.method === "breakdown"}
                className={context.method === "breakdown" ? "active" : undefined}
                onClick={() => patchContext({ method: "breakdown", stage: source ? "configure_breakdown" : "select_source" })}
              >
                <MagicWand size={20} />
                <span><strong>先生成 AI 拆解图</strong><small>复杂角色与机械部件</small></span>
              </button>
            </div>
          </section>
          {context.method === "direct" && (
            <section className="target-intent">
              <strong>直接框选适合什么？</strong>
              <p>已经知道要提取哪个对象时使用。绿色框会标注在受管副本中，AI 将移除背景和周围干扰；这不是普通截图。</p>
            </section>
          )}
          {context.method === "breakdown" && (
            <>
              <section className="target-intent breakdown">
                <strong>AI 拆解适合什么？</strong>
                <p>复杂角色、机械和道具会先生成一张部件拆解图。生成后可连续框选并在本地裁出多个部件。</p>
              </section>
              <section className="target-breakdown-settings">
                <h2>③ 拆解方式</h2>
                <div role="radiogroup" aria-label="AI 拆解方式">
                  <button type="button" role="radio" aria-checked={context.preset === "scene"} className={context.preset === "scene" ? "active" : undefined} onClick={() => patchContext({ preset: "scene" })}>场景 / 道具</button>
                  <button type="button" role="radio" aria-checked={context.preset === "character"} className={context.preset === "character" ? "active" : undefined} onClick={() => patchContext({ preset: "character" })}>角色部件</button>
                  <button type="button" role="radio" aria-checked={context.preset === "custom"} className={context.preset === "custom" ? "active" : undefined} onClick={() => patchContext({ preset: "custom" })}>自定义</button>
                </div>
                {context.preset === "custom" && (
                  <label>
                    自定义拆解要求
                    <textarea
                      aria-label="自定义拆解要求"
                      value={context.custom_prompt}
                      onChange={(event) => patchContext({ custom_prompt: event.target.value })}
                      placeholder="说明要拆成哪些独立部件，以及需要保留的材质和结构"
                    />
                  </label>
                )}
              </section>
            </>
          )}
        </aside>

        <section className="target-canvas-panel">
          <div className="target-canvas-heading">
            <div><Selection size={19} /><strong>{context.method === "direct" ? "框选原图目标" : breakdown ? "从拆解图选择部件" : "AI 拆解预览"}</strong></div>
            <span>{context.method === "direct" || breakdown ? "拖动新建；拖动框体移动；拖动圆点调整大小。" : "先选择拆解方式，再生成一张可框选的部件拆解图。"}</span>
          </div>
          {editingUrl ? (
            <>
              <div className="target-zoom" aria-label="目标提取画布缩放">
                <button type="button" aria-label="缩小画布" disabled={zoom <= 0.5} onClick={() => setZoom((value) => Math.max(0.5, Number((value - 0.25).toFixed(2))))}>−</button>
                <output>{Math.round(zoom * 100)}%</output>
                <button type="button" aria-label="放大画布" disabled={zoom >= 8} onClick={() => setZoom((value) => Math.min(8, Number((value + 0.25).toFixed(2))))}>+</button>
                <button type="button" onClick={() => { setZoom(1); baseImageSize.current = null; }}>适合窗口</button>
              </div>
              <div className="target-canvas-viewport">
                <div className="target-canvas" onPointerDown={begin} onPointerMove={move} onPointerUp={end} onPointerCancel={end}>
              <img
                ref={image}
                src={editingUrl}
                alt={editingAsset?.name ?? "目标提取编辑图"}
                data-managed-asset-id={editingAsset?.id}
                onLoad={updateScale}
                    style={baseImageSize.current ? { width: `${baseImageSize.current.width * zoom}px`, height: "auto", maxWidth: "none", maxHeight: "none" } : undefined}
                  />
                  <div
                    className={`target-selection-rect ${context.method}`}
                    data-selection="rect"
                    style={{
                      left: rect.x * imageScale.x,
                      top: rect.y * imageScale.y,
                      width: rect.width * imageScale.x,
                      height: rect.height * imageScale.y,
                    }}
                  >
                    {(["nw", "ne", "se", "sw"] as Handle[]).map((handle) => (
                      <span key={handle} className={`target-selection-handle ${handle}`} data-handle={handle} aria-label={`调整${context.method === "breakdown" ? "部件" : "目标"}框 ${handle}`} />
                    ))}
                  </div>
                </div>
              </div>
            </>
          ) : context.method === "direct" ? (
            <div className="target-canvas-empty"><ImageSquare size={32} /><span>先选择一张来源图片</span></div>
          ) : (
            <div className="target-canvas-empty"><MagicWand size={32} /><span>{source ? "配置拆解方式，然后生成部件拆解图" : "先选择一张来源图片"}</span></div>
          )}
        </section>

        <aside className="target-result-panel">
          {context.method === "direct" && (
            <>
              <h2>③ 生成独立目标图</h2>
              <p>将带绿色目标框的受管副本发送给图像服务。原图不会被修改，提交前需要确认。</p>
              <div className="target-selection-controls">
                {(["x", "y", "width", "height"] as const).map((key) => (
                  <label key={key}>{key}<input aria-label={key} type="number" min={key === "width" || key === "height" ? 1 : 0} value={rect[key]} onChange={(event) => commitRect({ ...rect, [key]: Number(event.target.value) })} /></label>
                ))}
              </div>
              <div className="target-edit-actions">
                <button type="button" disabled={historyIndex.current === 0} onClick={undo}><ArrowCounterClockwise size={16} />撤销</button>
                <button type="button" disabled={historyIndex.current >= history.current.length - 1} onClick={redo}><ArrowClockwise size={16} />重做</button>
                <button type="button" disabled={!source || busy} onClick={() => resetSelection()}><Trash size={16} />清除选框</button>
              </div>
              <button type="button" disabled={!canExtract} onClick={() => void localCrop()}><Crop size={17} />仅裁切选区（本地）</button>
              <button className="primary target-generate" type="button" disabled={!canExtract} onClick={() => void prepareDirect()}>
                {busy ? <><SpinnerGap className="spin" size={18} />正在准备…</> : <><MagicWand size={18} />生成独立目标图</>}
              </button>
            </>
          )}
          {context.method === "breakdown" && (
            <>
              {!breakdown ? (
                <>
                  <h2>④ 生成部件拆解图</h2>
                  <p>向图像服务发送无选框的受管来源图和拆解要求。拆解图生成后，再在本地选择并裁出部件。</p>
                  <button
                    className="primary target-generate"
                    type="button"
                    disabled={!source || busy || (context.preset === "custom" && !context.custom_prompt.trim())}
                    onClick={() => void prepareBreakdown()}
                  >
                    {busy ? <><SpinnerGap className="spin" size={18} />正在准备…</> : <><MagicWand size={18} />生成部件拆解图</>}
                  </button>
                </>
              ) : (
                <>
                  <h2>④ 裁出选中部件</h2>
                  <p>红色框只用于本地裁切，不会再次发送给图像服务。裁切完成后可以继续选择其他部件。</p>
                  <div className="target-selection-controls">
                    {(["x", "y", "width", "height"] as const).map((key) => (
                      <label key={key}>{key}<input aria-label={`部件 ${key}`} type="number" min={key === "width" || key === "height" ? 1 : 0} value={rect[key]} onChange={(event) => commitRect({ ...rect, [key]: Number(event.target.value) })} /></label>
                    ))}
                  </div>
                  <div className="target-edit-actions">
                    <button type="button" disabled={historyIndex.current === 0} onClick={undo}><ArrowCounterClockwise size={16} />撤销</button>
                    <button type="button" disabled={historyIndex.current >= history.current.length - 1} onClick={redo}><ArrowClockwise size={16} />重做</button>
                    <button type="button" disabled={busy} onClick={() => resetSelection()}><Trash size={16} />清除红框</button>
                  </div>
                  <button className="primary target-generate" type="button" disabled={!canExtract} onClick={() => void localCrop()}><Crop size={17} />裁出选中部件</button>
                  <button type="button" disabled={busy} onClick={() => void prepareBreakdown()}><ArrowClockwise size={17} />重新生成拆解图</button>
                </>
              )}
            </>
          )}

          {context.pending_action_id && (
            <section className="target-approval" role="alert">
              <strong>确认外部图像生成</strong>
              <p>{context.method === "breakdown"
                ? "仅发送无选框的受管来源图和当前拆解要求。"
                : "仅发送受管副本、绿色目标框和内置目标独立化提示词。"}</p>
              <div>
                <button type="button" disabled={busy} onClick={() => void decideApproval(false)}>取消</button>
                <button type="button" className="primary" disabled={busy} onClick={() => void decideApproval(true)}><CheckCircle size={17} />批准并提交</button>
              </div>
            </section>
          )}

          {context.job_id && (
            <section className="target-job-status" role="status">
              <strong>生成状态</strong>
              <span>{generationStatus || "任务已提交，正在获取进度…"}</span>
              {onOpenTasks && <button type="button" onClick={onOpenTasks}>打开任务中心</button>}
            </section>
          )}

          <section className="target-result-card" aria-live="polite">
            <h2>目标结果</h2>
            {context.result_asset_ids.length > 1 && (
              <div className="target-result-selector" aria-label="已提取结果">
                {context.result_asset_ids.map((assetId, index) => (
                  <button
                    type="button"
                    key={assetId}
                    aria-pressed={assetId === context.active_result_asset_id}
                    onClick={() => patchContext({ active_result_asset_id: assetId, stage: "result" })}
                  >
                    部件 {index + 1}
                  </button>
                ))}
              </div>
            )}
          {resultUrl ? <img src={resultUrl} alt={activeResult?.name ?? "目标提取结果"} data-managed-asset-id={activeResult?.id} /> : <div className="target-result-empty">完成提取后，结果会显示在这里</div>}
            {activeResult && (
              <div className="target-result-actions">
                <button className="primary" type="button" onClick={() => onContinueToMultiview(activeResult.id)}><SquaresFour size={17} />进入三视图制作</button>
                <button type="button" disabled={busy || activeResult.is_current} onClick={() => void setCurrentResult()}>{activeResult.is_current ? "当前资产" : "设为当前资产"}</button>
                <button type="button" onClick={() => {
                  if (context.method === "breakdown") {
                    resetSelection();
                    patchContext({ stage: "select_breakdown_part" });
                    setMessage("拆解图和已有结果已保留，可以继续框选另一个部件。");
                  } else {
                    patchContext({ stage: "select_target" });
                    setMessage("来源和结果已保留，可以继续框选新的目标。");
                  }
                }}>{context.method === "breakdown" ? "继续提取另一个部件" : "继续提取"}</button>
                {onOpenAssets && <button type="button" onClick={() => onOpenAssets(activeResult.id)}>在资产中查看</button>}
              </div>
            )}
          </section>
          <p className="target-message" role="status">{message}</p>
        </aside>
      </div>
    </section>
  );
}
