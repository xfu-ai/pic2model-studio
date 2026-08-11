import {
  ArrowClockwise,
  CheckCircle,
  Cube,
  DownloadSimple,
  MagicWand,
  Trash,
} from "@phosphor-icons/react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type DragEvent,
  type PointerEvent,
} from "react";
import type {
  ApiClient,
  AssetDto,
  SelectionRect,
  WorkflowContexts,
} from "../../shared/api/client";
import { ImportImageAction } from "../assets/ImportImageAction";
import { HostClient } from "../../shared/host/client";
import { MULTIVIEW_BASE_PROMPT } from "../../shared/prompts/productionPrompts";
import "./multiview-workspace.css";

const directions = ["front", "side", "back"] as const;
type Direction = (typeof directions)[number];
type Handle = "nw" | "ne" | "se" | "sw";
type RegionEditOperation = {
  kind: "draw" | "move" | "resize";
  view: Direction;
  pointerId: number;
  start: { x: number; y: number };
  rect: SelectionRect;
  handle?: Handle;
};
type MultiviewRestorePoint = {
  sourceId: string;
  generatedViewIds: Record<Direction, string> | null;
  regions: Record<Direction, SelectionRect>;
  generationJobId: string | null;
  multiviewSetId: string | null;
};
const imageAssetTypes = new Set([
  "source_image",
  "generated_image",
  "annotation",
  "crop",
  "multiview",
]);
const labels: Record<Direction, string> = {
  front: "正视图",
  side: "侧视图（左）",
  back: "背视图",
};
const colors: Record<Direction, string> = {
  front: "#4a7ef5",
  side: "#2ea043",
  back: "#d97706",
};
const DEFAULT_FACE_LIMIT = 50_000;
const MIN_FACE_LIMIT = 500;
const MAX_FACE_LIMIT = 1_000_000;
const faceLimitPresets = [5_000, 10_000, 50_000, 100_000] as const;

function clampFaceLimit(value: number): number {
  return Math.max(
    MIN_FACE_LIMIT,
    Math.min(MAX_FACE_LIMIT, Math.round(value)),
  );
}

function isRestoredGeneratedSheet(
  selected: Record<string, string> | undefined,
  jobId: string | null | undefined,
): boolean {
  const sourceId = selected?.source;
  return Boolean(
    jobId &&
      sourceId &&
      directions.every((view) => selected?.[view] === sourceId),
  );
}
function restoredConfirmedViews(
  selected: Record<string, string> | undefined,
  setId: string | null | undefined,
): Record<Direction, string> | null {
  const sourceId = selected?.source;
  if (!setId || !sourceId || !directions.every((view) => selected?.[view])) {
    return null;
  }
  if (directions.every((view) => selected?.[view] === sourceId)) return null;
  return Object.fromEntries(
    directions.map((view) => [view, selected![view]]),
  ) as Record<Direction, string>;
}
function requestId() {
  return crypto.randomUUID();
}
function dimensions(asset?: AssetDto) {
  return {
    width: Number(asset?.metadata.width) || 1,
    height: Number(asset?.metadata.height) || 1,
  };
}
function defaultRegions(asset?: AssetDto): Record<Direction, SelectionRect> {
  const { width, height } = dimensions(asset);
  const part = Math.max(1, Math.floor(width / 3));
  return {
    front: {
      x: Math.round(width * 0.05),
      y: Math.round(height * 0.06),
      width: Math.round(part * 0.86),
      height: Math.round(height * 0.88),
    },
    side: {
      x: Math.round(width * 0.36),
      y: Math.round(height * 0.06),
      width: Math.round(part * 0.86),
      height: Math.round(height * 0.88),
    },
    back: {
      x: Math.round(width * 0.67),
      y: Math.round(height * 0.06),
      width: Math.round(part * 0.86),
      height: Math.round(height * 0.88),
    },
  };
}

export function MultiviewWorkspace({
  projectId,
  api,
  onQueued,
  onModeChange,
  workflowContext,
  onWorkflowContextChange,
  modelWorkflowContext,
  onModelWorkflowContextChange,
  host = new HostClient(),
}: {
  projectId: string;
  api: ApiClient;
  onQueued(): void;
  onModeChange?(mode: "model3d"): void;
  workflowContext?: WorkflowContexts["multiview"];
  onWorkflowContextChange?(value: WorkflowContexts["multiview"]): void;
  modelWorkflowContext?: WorkflowContexts["model3d"];
  onModelWorkflowContextChange?(value: WorkflowContexts["model3d"]): void;
  host?: HostClient;
}) {
  const persistedSourceId = workflowContext?.selected.source ?? null;
  const restoredGeneratedSheet = isRestoredGeneratedSheet(
    workflowContext?.selected,
    workflowContext?.job_id,
  );
  const restoredConfirmed = restoredConfirmedViews(
    workflowContext?.selected,
    workflowContext?.set_id,
  );
  const [assets, setAssets] = useState<AssetDto[]>([]);
  const [sourceId, setSourceId] = useState(persistedSourceId ?? "");
  const [generatedViewIds, setGeneratedViewIds] =
    useState<Record<Direction, string> | null>(restoredConfirmed);
  const [multiviewSetId, setMultiviewSetId] = useState<string | null>(
    workflowContext?.set_id ?? null,
  );
  const [generatedViewUrls, setGeneratedViewUrls] = useState<
    Partial<Record<Direction, string>>
  >({});
  const [generationJobId, setGenerationJobId] = useState<string | null>(
    workflowContext?.job_id ?? null,
  );
  const [regions, setRegions] = useState<Record<Direction, SelectionRect>>(
    () => {
      const saved = workflowContext?.regions as
        | Partial<Record<Direction, SelectionRect>>
        | undefined;
      const fallback = defaultRegions();
      return {
        front: saved?.front ?? fallback.front,
        side: saved?.side ?? fallback.side,
        back: saved?.back ?? fallback.back,
      };
    },
  );
  const [active, setActive] = useState<Direction>("front");
  const [prompt, setPrompt] = useState("");
  const [url, setUrl] = useState("");
  const [cropPreviews, setCropPreviews] = useState<Partial<Record<Direction, string>>>({});
  const [faceLimit, setFaceLimit] = useState(
    modelWorkflowContext?.target_triangles ?? DEFAULT_FACE_LIMIT,
  );
  const [approvalId, setApprovalId] = useState<string | null>(null);
  const [modelApproval, setModelApproval] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [draggingSource, setDraggingSource] = useState(false);
  const [sourceCleared, setSourceCleared] = useState(false);
  const [sourceReady, setSourceReady] = useState(false);
  const [restorePoint, setRestorePoint] = useState<MultiviewRestorePoint | null>(null);
  const [imageScale, setImageScale] = useState({ x: 1, y: 1 });
  const [message, setMessage] = useState(
    "选择来源后，可自动生成横向正/侧/背三视图。",
  );
  const image = useRef<HTMLImageElement>(null);
  const operation = useRef<RegionEditOperation | null>(null);
  const consumedGenerationJob = useRef<string | null>(
    restoredGeneratedSheet || restoredConfirmed
      ? (workflowContext?.job_id ?? null)
      : null,
  );
  const source = assets.find((asset) => asset.id === sourceId);
  const applyGeneratedSheet = useCallback(
    async (
      assetId: string,
      jobId: string,
      automatic: boolean,
      isActive: () => boolean = () => true,
    ) => {
      const all = await api.assets(projectId);
      if (!isActive()) return false;
      const images = all.filter(
        (asset) =>
          imageAssetTypes.has(asset.asset_type) && asset.trashed_at == null,
      );
      const sheet = images.find((asset) => asset.id === assetId);
      if (!sheet)
        throw new Error("三视图拼图已生成，但暂时无法读取受管结果。");
      consumedGenerationJob.current = jobId;
      setAssets(images);
      setSourceCleared(false);
      setSourceId(sheet.id);
      setGeneratedViewIds(null);
      setMultiviewSetId(null);
      setRegions(defaultRegions(sheet));
      setActive("front");
      setMessage(
        automatic
          ? "三视图拼图生成完成，已自动加载；请调整三个框后裁切。"
          : "已手动检查并加载三视图拼图；请调整三个框后裁切。",
      );
      return true;
    },
    [api, projectId],
  );
  const useSource = (asset: AssetDto) => {
    setSourceCleared(false);
    setSourceId(asset.id);
    setRegions(defaultRegions(asset));
    setApprovalId(null);
    setModelApproval(null);
    setGeneratedViewIds(null);
    setMultiviewSetId(null);
    setGenerationJobId(null);
    consumedGenerationJob.current = null;
  };
  const snapshotSource = (): MultiviewRestorePoint => ({
    sourceId,
    generatedViewIds,
    regions,
    generationJobId,
    multiviewSetId,
  });
  const selectSource = (asset: AssetDto) => {
    setRestorePoint(snapshotSource());
    useSource(asset);
    setMessage(`${asset.name} 已加载为本页三视图来源；项目当前资产未改变。`);
  };
  const loadCurrentAsset = async () => {
    setBusy(true);
    try {
      const [project, all] = await Promise.all([
        api.project(projectId),
        api.assets(projectId),
      ]);
      const currentAssetId = project.current_asset_id
        ?? all.find((asset) => asset.is_current)?.id
        ?? null;
      const normalized = all.map((asset) => ({
        ...asset,
        is_current: asset.id === currentAssetId,
      }));
      const current = normalized.find((asset) => (
        asset.id === currentAssetId
        && imageAssetTypes.has(asset.asset_type)
        && asset.trashed_at == null
      ));
      if (!current) {
        setMessage("项目当前资产不是可用于三视图的图片；请先在图像结果或资产页将目标图片设为当前资产。");
        return;
      }
      setAssets(normalized.filter((asset) => imageAssetTypes.has(asset.asset_type) && asset.trashed_at == null));
      if (current.id === sourceId) {
        setMessage(`${current.name} 已经是本页三视图来源。`);
        return;
      }
      setRestorePoint(snapshotSource());
      useSource(current);
      setMessage(`${current.name} 已按项目当前资产加载为本页三视图来源；其他页签未改变。`);
    } catch {
      setMessage("无法加载项目当前资产。");
    } finally {
      setBusy(false);
    }
  };
  const restoreSource = () => {
    if (!restorePoint) return;
    const current = snapshotSource();
    setSourceCleared(false);
    setSourceId(restorePoint.sourceId);
    setGeneratedViewIds(restorePoint.generatedViewIds);
    setRegions(restorePoint.regions);
    setGenerationJobId(restorePoint.generationJobId);
    setMultiviewSetId(restorePoint.multiviewSetId);
    consumedGenerationJob.current = restorePoint.generatedViewIds
      ? restorePoint.generationJobId
      : null;
    setApprovalId(null);
    setModelApproval(null);
    setRestorePoint(current);
    setMessage("已恢复本页加载当前资产之前的三视图状态。");
  };
  const clearSource = () => {
    setSourceCleared(true);
    setSourceId("");
    setUrl("");
    setRegions(defaultRegions());
    setApprovalId(null);
    setModelApproval(null);
    setGeneratedViewIds(null);
    setGeneratedViewUrls({});
    setMultiviewSetId(null);
    setGenerationJobId(null);
    consumedGenerationJob.current = null;
    setMessage("已清空当前三视图制作页的来源；项目资产不会被删除。");
    onWorkflowContextChange?.({
      selected: {},
      regions: {},
      checks: {},
      quality_confirmed: false,
      set_id: null,
      job_id: null,
    });
  };
  const importDroppedSource = async (file: File | undefined) => {
    if (!file) return;
    if (!/^image\/(png|jpeg|webp|bmp)$/i.test(file.type)) {
      setMessage("仅支持 PNG、JPG、WEBP 或 BMP 图片。");
      return;
    }
    setBusy(true);
    try {
      const bytes = Array.from(new Uint8Array(await file.arrayBuffer()));
      const capability = await new HostClient().stageDroppedFile(
        projectId,
        "source_image",
        file.name,
        bytes,
      );
      const imported = await api.importImage(
        projectId,
        capability,
        requestId(),
      );
      await api.setCurrentAsset(projectId, imported.id, requestId());
      setAssets((items) => [
        ...items.filter((asset) => asset.id !== imported.id),
        imported,
      ]);
      selectSource(imported);
      setMessage("已导入并选中新的三视图来源图。");
    } catch {
      setMessage("图片导入失败，请重新选择或拖入图片。");
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    let active = true;
    setSourceReady(false);
    void api
      .assets(projectId)
      .then((all) => {
        if (!active) return;
        const images = all.filter(
          (asset) =>
            imageAssetTypes.has(asset.asset_type) &&
            asset.trashed_at == null,
        );
        setAssets(images);
        const restoredSource = persistedSourceId
          ? images.find((asset) => asset.id === persistedSourceId)
          : null;
        const initialSource = restoredSource
          ?? images.find((asset) => asset.is_current)
          ?? images[0];
        if (!sourceCleared && initialSource) {
          setSourceId(initialSource.id);
        const saved = workflowContext?.regions as
            | Partial<Record<Direction, SelectionRect>>
            | undefined;
          if (!directions.every((view) => saved?.[view])) {
            setRegions(defaultRegions(initialSource));
          }
        }
        setSourceReady(true);
      })
      .catch(() => { if (active) setMessage("无法读取可用图片。"); });
    return () => { active = false; };
  }, [api, projectId]);
  useEffect(() => {
    let active = true;
    let objectUrl = "";
    if (source) {
      void api
        .assetContent(projectId, source.id)
        .then((blob) => {
          if (!active) return;
          objectUrl = URL.createObjectURL(blob);
          setUrl((previous) => {
            if (previous) URL.revokeObjectURL(previous);
            return objectUrl;
          });
        })
        .catch(() => {
          if (active) setUrl("");
        });
    } else {
      setUrl((previous) => {
        if (previous) URL.revokeObjectURL(previous);
        return "";
      });
    }
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [api, projectId, source]);
  useEffect(() => {
    let active = true;
    const objectUrls: string[] = [];
    if (!generatedViewIds) {
      setGeneratedViewUrls({});
      return () => {
        active = false;
      };
    }
    void Promise.all(
      directions.map(async (view) => {
        const blob = await api.assetContent(projectId, generatedViewIds[view]);
        const objectUrl = URL.createObjectURL(blob);
        if (!active) {
          URL.revokeObjectURL(objectUrl);
          return [view, ""] as const;
        }
        objectUrls.push(objectUrl);
        return [view, objectUrl] as const;
      }),
    )
      .then((entries) => {
        if (active) {
          setGeneratedViewUrls(
            Object.fromEntries(entries) as Record<Direction, string>,
          );
        }
      })
      .catch(() => {
        if (active) setMessage("三视图结果已生成，但预览加载失败。");
      });
    return () => {
      active = false;
      objectUrls.forEach((objectUrl) => URL.revokeObjectURL(objectUrl));
    };
  }, [api, generatedViewIds, projectId]);
  useEffect(() => {
    if (
      !generationJobId ||
      consumedGenerationJob.current === generationJobId
    )
      return;
    let active = true;
    let refreshing = false;
    let timer = 0;
    const stop = () => {
      if (timer) window.clearInterval(timer);
      timer = 0;
    };
    const refresh = () => {
      if (
        refreshing ||
        consumedGenerationJob.current === generationJobId
      ) {
        if (consumedGenerationJob.current === generationJobId) stop();
        return;
      }
      refreshing = true;
      void api
        .job(projectId, generationJobId)
        .then((job) => {
          if (!active) return;
          if (job.status === "succeeded") {
            if (job.output_asset_ids.length !== 1) {
              setMessage(
                "该任务返回的是旧版三张独立图片，无法作为可裁切拼图应用；请重新执行自动拆分三视图。",
              );
              stop();
              return;
            }
            return applyGeneratedSheet(
              job.output_asset_ids[0],
              generationJobId,
              true,
              () => active,
            ).then((applied) => {
              if (applied) stop();
            });
          }
          if (
            job.status === "failed" ||
            job.status === "cancelled" ||
            job.status === "interrupted"
          ) {
            setMessage(
              job.error?.user_message ??
                (job.status === "cancelled"
                  ? "三视图生成已取消。"
                  : "三视图生成未完成，请到任务中心查看详情。"),
            );
            stop();
            return;
          }
          setMessage(
            `三视图正在生成 · ${job.stage}${
              job.progress == null ? "" : ` · ${job.progress}%`
            }`,
          );
        })
        .catch(() => {
          if (active)
            setMessage(
              "三视图仍在生成，暂时无法读取进度；系统会继续自动检查，也可手动检查结果。",
            );
        })
        .finally(() => {
          refreshing = false;
        });
    };
    refresh();
    timer = window.setInterval(refresh, 2500);
    return () => {
      active = false;
      stop();
    };
  }, [api, applyGeneratedSheet, generationJobId, projectId]);
  useEffect(() => {
    if (!url) {
      setCropPreviews({});
      return;
    }
    const sourceImage = new Image();
    const objectUrls: string[] = [];
    let active = true;
    sourceImage.onload = () => {
      void Promise.all(
        directions.map((view) => new Promise<readonly [Direction, string]>((resolve) => {
          const rect = regions[view];
          const canvas = document.createElement("canvas");
          canvas.width = Math.max(1, rect.width);
          canvas.height = Math.max(1, rect.height);
          canvas.getContext("2d")?.drawImage(
            sourceImage,
            rect.x,
            rect.y,
            rect.width,
            rect.height,
            0,
            0,
            canvas.width,
            canvas.height,
          );
          canvas.toBlob((blob) => {
            if (!blob) {
              resolve([view, ""] as const);
              return;
            }
            const objectUrl = URL.createObjectURL(blob);
            if (!active) {
              URL.revokeObjectURL(objectUrl);
              resolve([view, ""] as const);
              return;
            }
            objectUrls.push(objectUrl);
            resolve([view, objectUrl] as const);
          }, "image/jpeg", 0.9);
        })),
      ).then((entries) => {
        if (!active) return;
        setCropPreviews(Object.fromEntries(
          entries.filter(([, previewUrl]) => Boolean(previewUrl)),
        ) as Partial<Record<Direction, string>>);
      });
    };
    sourceImage.onerror = () => {
      if (active) setCropPreviews({});
    };
    sourceImage.src = url;
    return () => {
      active = false;
      objectUrls.forEach((objectUrl) => URL.revokeObjectURL(objectUrl));
    };
  }, [regions, url]);
  useEffect(() => {
    if (!sourceReady) return;
    const selected: Record<string, string> = generatedViewIds
      ? { source: sourceId, ...generatedViewIds }
      : sourceId
        ? { source: sourceId, front: sourceId, side: sourceId, back: sourceId }
        : {};
    const next: WorkflowContexts["multiview"] = {
      selected,
      regions,
      checks: {},
      quality_confirmed: false,
      set_id: multiviewSetId,
      job_id: generationJobId,
      pending_action_id: workflowContext?.pending_action_id ?? null,
    };
    if (workflowContext && JSON.stringify(workflowContext) === JSON.stringify(next)) return;
    onWorkflowContextChange?.(next);
  }, [
    generatedViewIds,
    generationJobId,
    multiviewSetId,
    onWorkflowContextChange,
    regions,
    sourceReady,
    sourceId,
    workflowContext,
  ]);
  useEffect(() => {
    if (modelWorkflowContext?.target_triangles === faceLimit) return;
    onModelWorkflowContextChange?.({
      asset_id: modelWorkflowContext?.asset_id ?? null,
      target_triangles: faceLimit,
      generation_job_id: modelWorkflowContext?.generation_job_id ?? null,
    });
  }, [
    faceLimit,
    modelWorkflowContext,
    onModelWorkflowContextChange,
  ]);
  const updateImageScale = () => {
    const node = image.current;
    if (
      !node?.naturalWidth ||
      !node.naturalHeight ||
      !node.clientWidth ||
      !node.clientHeight
    )
      return;
    setImageScale({
      x: node.clientWidth / node.naturalWidth,
      y: node.clientHeight / node.naturalHeight,
    });
  };
  useEffect(() => {
    const node = image.current;
    if (!node || generatedViewIds) return;
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(updateImageScale);
    observer?.observe(node);
    window.addEventListener("resize", updateImageScale);
    const frame = window.requestAnimationFrame(updateImageScale);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", updateImageScale);
      observer?.disconnect();
    };
  }, [generatedViewIds, url]);
  const point = (event: PointerEvent<HTMLDivElement>) => {
    const node = image.current;
    if (!node?.naturalWidth || !node.naturalHeight) return null;
    const box = node.getBoundingClientRect();
    if (!box.width || !box.height) return null;
    return {
      x: Math.max(
        0,
        Math.min(
          node.naturalWidth - 1,
          Math.round(
            ((event.clientX - box.left) * node.naturalWidth) / box.width,
          ),
        ),
      ),
      y: Math.max(
        0,
        Math.min(
          node.naturalHeight - 1,
          Math.round(
            ((event.clientY - box.top) * node.naturalHeight) / box.height,
          ),
        ),
      ),
    };
  };
  const constrainRegion = (next: SelectionRect): SelectionRect => {
    const width = image.current?.naturalWidth || dimensions(source).width;
    const height = image.current?.naturalHeight || dimensions(source).height;
    const regionWidth = Math.max(
      1,
      Math.min(Math.round(next.width), width),
    );
    const regionHeight = Math.max(
      1,
      Math.min(Math.round(next.height), height),
    );
    const x = Math.max(
      0,
      Math.min(Math.round(next.x), Math.max(0, width - regionWidth)),
    );
    const y = Math.max(
      0,
      Math.min(Math.round(next.y), Math.max(0, height - regionHeight)),
    );
    return {
      x,
      y,
      width: regionWidth,
      height: regionHeight,
    };
  };
  const beginRegionEdit = (event: PointerEvent<HTMLDivElement>) => {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    const next = point(event);
    if (!next) return;
    event.preventDefault();
    const target = event.target as HTMLElement;
    const regionNode = target.closest<HTMLElement>("[data-selection='rect']");
    const handleNode = target.closest<HTMLElement>("[data-handle]");
    const view = (regionNode?.dataset.view as Direction | undefined) ?? active;
    const handle = handleNode?.dataset.handle as Handle | undefined;
    const kind = handle ? "resize" : regionNode ? "move" : "draw";
    const currentRect = regions[view];
    event.currentTarget.setPointerCapture(event.pointerId);
    operation.current = {
      kind,
      view,
      pointerId: event.pointerId,
      start: next,
      rect: currentRect,
      handle,
    };
    setActive(view);
    if (kind === "draw") {
      setRegions((value) => ({
        ...value,
        [view]: { x: next.x, y: next.y, width: 1, height: 1 },
      }));
    }
  };
  const continueRegionEdit = (event: PointerEvent<HTMLDivElement>) => {
    const next = point(event);
    const current = operation.current;
    if (!next || !current || current.pointerId !== event.pointerId) return;
    event.preventDefault();
    const dx = next.x - current.start.x;
    const dy = next.y - current.start.y;
    let nextRect: SelectionRect;
    if (current.kind === "draw") {
      nextRect = constrainRegion({
        x: Math.min(current.start.x, next.x),
        y: Math.min(current.start.y, next.y),
        width: Math.max(1, Math.abs(dx)),
        height: Math.max(1, Math.abs(dy)),
      });
    } else if (current.kind === "move") {
      nextRect = constrainRegion({
        ...current.rect,
        x: current.rect.x + dx,
        y: current.rect.y + dy,
      });
    } else {
      const left = current.handle?.includes("w")
        ? current.rect.x + dx
        : current.rect.x;
      const top = current.handle?.includes("n")
        ? current.rect.y + dy
        : current.rect.y;
      const right = current.handle?.includes("e")
        ? current.rect.x + current.rect.width + dx
        : current.rect.x + current.rect.width;
      const bottom = current.handle?.includes("s")
        ? current.rect.y + current.rect.height + dy
        : current.rect.y + current.rect.height;
      nextRect = constrainRegion({
        x: Math.min(left, right - 1),
        y: Math.min(top, bottom - 1),
        width: Math.max(1, Math.abs(right - left)),
        height: Math.max(1, Math.abs(bottom - top)),
      });
    }
    setRegions((value) => ({ ...value, [current.view]: nextRect }));
  };
  const finishRegionEdit = (event: PointerEvent<HTMLDivElement>) => {
    if (operation.current?.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
    operation.current = null;
  };
  const generate = async (custom: boolean) => {
    if (!source) return;
    if (custom && !prompt.trim()) {
      setMessage("请输入自定义提示词，或使用自动拆分三视图。");
      return;
    }
    setBusy(true);
    try {
      const saved = await api.savePromptVersion(
        projectId,
        {
          zhPrompt: custom ? prompt.trim() : "自动生成横向正、侧、背三视图",
          enPrompt: custom ? prompt.trim() : MULTIVIEW_BASE_PROMPT,
          kind: "multiview",
          parentAssetId: source.id,
        },
        requestId(),
      );
      const result = await api.invokeTool(
        projectId,
        "multiview.generate",
        {
          source_asset_id: source.id,
          prompt_asset_id: saved.asset.id,
          provider_profile: "image-generation/auto",
          channel: "auto",
          model: "auto",
        },
        requestId(),
        { providerProfile: "image-generation/auto" },
      );
      if (
        result.status !== "awaiting_ui_action" ||
        !result.ui_action?.action_id
      )
        throw new Error(result.error?.user_message ?? "未取得生成确认。");
      setApprovalId(result.ui_action.action_id);
      setMessage("已准备生成请求；确认后会在当前页保留生成状态。");
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : "无法准备三视图生成。",
      );
    } finally {
      setBusy(false);
    }
  };
  const approve = async () => {
    if (!approvalId) return;
    setBusy(true);
    try {
      const result = await api.decideApproval(
        projectId,
        approvalId,
        true,
        requestId(),
      );
      if (result.status !== "queued" || !result.job?.job_id)
        throw new Error(result.error?.user_message ?? "生成任务未进入队列。");
      setApprovalId(null);
      consumedGenerationJob.current = null;
      setGenerationJobId(result.job.job_id);
      setGeneratedViewIds(null);
      setMessage("三视图拼图正在生成；完成后会自动加载并显示三个裁切框。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "提交失败。");
    } finally {
      setBusy(false);
    }
  };
  const reloadGeneratedViews = async () => {
    if (!generationJobId) {
      setMessage("当前页面没有可手动检查的三视图生成任务。");
      return;
    }
    setBusy(true);
    try {
      const job = await api.job(projectId, generationJobId);
      if (job.status !== "succeeded") {
        if (
          job.status === "failed" ||
          job.status === "cancelled" ||
          job.status === "interrupted"
        ) {
          throw new Error(job.error?.user_message ?? "三视图生成未完成。");
        }
        setMessage(`三视图仍在生成：${job.stage}`);
        return;
      }
      if (job.output_asset_ids.length !== 1) {
        throw new Error(
          "该任务不是单张三视图拼图结果，请重新执行自动拆分三视图。",
        );
      }
      await applyGeneratedSheet(
        job.output_asset_ids[0],
        generationJobId,
        false,
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "无法加载三视图结果。");
    } finally {
      setBusy(false);
    }
  };
  const confirmCropRegions = async () => {
    if (!source || generatedViewIds) return;
    setBusy(true);
    try {
      const set = await api.createMultiviewSet(
        projectId,
        source.id,
        { front: source.id, side: source.id, back: source.id },
        requestId(),
      );
      const savedRegions = await api.invokeTool(
        projectId,
        "multiview.set_regions",
        { multiview_set_id: set.id, regions },
        requestId(),
      );
      if (!savedRegions.ok) {
        throw new Error(
          savedRegions.error?.user_message ?? "无法保存三视图裁切框。",
        );
      }
      const crops = await api.invokeTool(
        projectId,
        "multiview.crop_views",
        { multiview_set_id: set.id },
        requestId(),
      );
      if (!crops.ok || crops.output_asset_ids.length !== 3) {
        throw new Error(
          crops.error?.user_message ?? "三视图裁切失败，请重新检查三个框。",
        );
      }
      const viewAssetIds = Object.fromEntries(
        directions.map((view, index) => [view, crops.output_asset_ids[index]]),
      ) as Record<Direction, string>;
      if (workflowContext?.pending_action_id) {
        await api.completeAgentMultiviewAction(
          projectId,
          workflowContext.pending_action_id,
          set.id,
          viewAssetIds,
          requestId(),
        );
      }
      setGeneratedViewIds(viewAssetIds);
      setMultiviewSetId(set.id);
      onWorkflowContextChange?.({
        selected: { source: source.id, ...viewAssetIds },
        regions,
        checks: {},
        quality_confirmed: false,
        set_id: set.id,
        job_id: generationJobId,
        pending_action_id: null,
      });
      setMessage("裁切框已确认，正、侧、背三张受管图片已生成；可以直接进入 3D 模型处理。");
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : "无法确认三视图裁切框。",
      );
    } finally {
      setBusy(false);
    }
  };
  const reopenCropRegions = () => {
    setGeneratedViewIds(null);
    setMultiviewSetId(null);
    setModelApproval(null);
    setMessage("裁切框已重新进入编辑状态；再次确认前，旧裁切结果不会用于建模。");
  };
  const submit3d = async () => {
    if (!generatedViewIds) return;
    setBusy(true);
    try {
      const set = multiviewSetId
        ? { id: multiviewSetId }
        : await api.createMultiviewSet(
            projectId,
            source?.id ?? generatedViewIds.front,
            generatedViewIds,
            requestId(),
          );
      const result = await api.invokeTool(
        projectId,
        "model3d.generate",
        {
          mode: "multiview",
          multiview_set_id: set.id,
          view_asset_ids: generatedViewIds,
          provider_profile: "tripo3d/default",
          model: "v3.1-20260211",
          parameters: { model_version: "v3.1-20260211", texture: true, pbr: true, face_limit: faceLimit, auto_size: true, orientation: "align_image" },
        },
        requestId(),
        { providerProfile: "tripo3d/default" },
      );
      if (
        result.status !== "awaiting_ui_action" ||
        !result.ui_action?.action_id
      )
        throw new Error(result.error?.user_message ?? "未取得 3D 任务确认。");
      setModelApproval(result.ui_action.action_id);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "无法输出三视图。");
    } finally {
      setBusy(false);
    }
  };
  const valid = useMemo(() => Boolean(generatedViewIds), [generatedViewIds]);
  return (
    <section className="multiview-workspace" aria-labelledby="multiview-title">
      <header className="multiview-header">
        <div>
          <p className="eyebrow">Structure setup</p>
          <h1 id="multiview-title">生成和校准三视图</h1>
          <p>
            先将一个目标图转换为横向正/侧/背三视图，再在同一张拼图上精确框选三个区域，供后续
            3D 建模使用。
          </p>
        </div>
        <Cube size={36} />
      </header>
      <div className="multiview-layout">
        <aside className={`multiview-source-panel${draggingSource ? " dragging" : ""}`} onDragEnter={(event: DragEvent<HTMLElement>) => { event.preventDefault(); setDraggingSource(true); }} onDragOver={(event: DragEvent<HTMLElement>) => event.preventDefault()} onDragLeave={() => setDraggingSource(false)} onDrop={(event: DragEvent<HTMLElement>) => { event.preventDefault(); setDraggingSource(false); void importDroppedSource(event.dataTransfer.files[0]); }}>
          <h2>① 来源与生成</h2>
          <label>
            三视图来源
            <select
              aria-label="三视图来源"
              value={sourceId}
              disabled={busy}
              onChange={(event) => {
                const chosen = assets.find(
                  (asset) => asset.id === event.target.value,
                );
                if (chosen) void selectSource(chosen);
              }}
            >
              {!sourceId && <option value="">未选择三视图来源</option>}
              {assets.map((asset) => (
                <option key={asset.id} value={asset.id}>
                  {asset.name}{asset.is_current ? "（项目当前图片）" : ""}
                </option>
              ))}
            </select>
          </label>
          <p className="multiview-source-identity">
            {source
              ? `本页来源：${source.name}${source.is_current ? " · 项目当前图片" : " · 仅本页使用"}`
              : "本页尚未选择来源图片"}
          </p>
          {url ? (
            <img
              className="multiview-source-preview"
              src={url}
              alt="三视图源图"
              data-managed-asset-id={source?.id}
            />
          ) : (
            <div className="multiview-source-preview empty">暂无来源图</div>
          )}
          <label>
            自定义三视图提示词
            <textarea
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              placeholder="留空时可使用内置横向三视图提示词"
            />
          </label>
          <ImportImageAction
            projectId={projectId}
            api={api}
            host={host}
            className="multiview-import-primary"
            label="选择图片（系统文件）"
            onImported={() => {
              void api.assets(projectId).then((all) => {
                const current = all.find((asset) => asset.is_current);
                if (!current) return;
                setAssets(all);
                selectSource(current);
                setMessage("已选择新的三视图来源图。");
              });
            }}
          />
          <button type="button" disabled={busy} onClick={() => void loadCurrentAsset()}>
            加载项目当前图片
          </button>
          <button type="button" disabled={busy || !restorePoint} onClick={restoreSource}>
            恢复加载前状态
          </button>
          <p className="multiview-drop-hint">也可将 PNG、JPG、WEBP 或 BMP 拖入此区域</p>
          <button
            className="secondary"
            disabled={!source || busy}
            onClick={() => void generate(false)}
          >
            <MagicWand size={17} />
            自动拆分三视图
          </button>
          <button
            disabled={!source || busy || !prompt.trim()}
            onClick={() => void generate(true)}
          >
            使用自定义提示词生成
          </button>
          {generationJobId
            && consumedGenerationJob.current !== generationJobId
            && (
              <>
                <button
                  onClick={() => void reloadGeneratedViews()}
                  disabled={busy}
                  title="生成任务会自动刷新；需要立即查询后台进度时使用"
                >
                  <ArrowClockwise size={17} />
                  立即刷新生成进度
                </button>
                <small className="multiview-generation-refresh-hint">
                  系统会自动加载完成结果，此按钮只用于立即查询生成任务。
                </small>
              </>
            )}
          <button type="button" disabled={busy || !source} onClick={clearSource}><Trash size={17} />清空图片</button>
          <p className="multiview-status" role="status">
            {message}
          </p>
        </aside>
        <main className="multiview-editor">
          <div className="multiview-editor-toolbar">
            <strong>② 三视图拼图与裁切框</strong>
            <span>选中视图后，在图上从鼠标按下点拖动形成框</span>
            <div>
              {directions.map((view) => (
                <button
                  key={view}
                  type="button"
                  className={active === view ? "active" : ""}
                  onClick={() => setActive(view)}
                  style={
                    { "--view-color": colors[view] } as React.CSSProperties
                  }
                >
                  {labels[view]}
                </button>
              ))}
            </div>
          </div>
          {generatedViewIds ? (
            <div className="multiview-sheet" aria-label="AI 生成的独立三视图">
              {directions.map((view) =>
                generatedViewUrls[view] ? (
                  <img
                    key={view}
                    src={generatedViewUrls[view]}
                    alt={labels[view]}
                  />
                ) : (
                  <div key={view} className="multiview-crop-empty">
                    正在加载{labels[view]}
                  </div>
                ),
              )}
            </div>
          ) : url ? (
            <div className="multiview-sheet">
              <div
                className="multiview-sheet-canvas"
                onPointerDown={beginRegionEdit}
                onPointerMove={continueRegionEdit}
                onPointerUp={finishRegionEdit}
                onPointerCancel={finishRegionEdit}
              >
              <img
                ref={image}
                src={url}
                alt="三视图拼图"
                draggable={false}
                onLoad={updateImageScale}
              />
              {directions.map((view) => (
                <div
                  key={view}
                  className={`multiview-region ${active === view ? "selected" : ""}`}
                  data-selection="rect"
                  data-view={view}
                  style={{
                    left: regions[view].x * imageScale.x,
                    top: regions[view].y * imageScale.y,
                    width: regions[view].width * imageScale.x,
                    height: regions[view].height * imageScale.y,
                    borderColor: colors[view],
                    color: colors[view],
                  }}
                >
                  <span style={{ backgroundColor: colors[view] }}>
                    {labels[view]}
                  </span>
                  {active === view &&
                    (["nw", "ne", "se", "sw"] as Handle[]).map((handle) => (
                      <i
                        key={handle}
                        className={`multiview-handle ${handle}`}
                        data-handle={handle}
                        aria-label={`${labels[view]} ${handle}`}
                      />
                    ))}
                </div>
              ))}
              </div>
            </div>
          ) : (
            <div className="multiview-sheet empty">
              选择一张来源图后，在这里生成并编辑三视图。
            </div>
          )}
        </main>
        <aside className="multiview-output-panel">
          <h2>③ 输出与建模</h2>
          <p>
            {generatedViewIds
              ? "已确认的三个独立受管结果会直接用于建模，不再从单张图片重复裁切。"
              : "三个框会按原图坐标裁切为受管副本，原图不会被改写。"}
          </p>
          {directions.map((view) => (
            <div key={view} className="multiview-crop-card">
              <strong style={{ color: colors[view] }}>{labels[view]}</strong>
              {(generatedViewIds ? generatedViewUrls[view] : cropPreviews[view]) ? <img src={(generatedViewIds ? generatedViewUrls[view] : cropPreviews[view])!} alt={`${labels[view]}预览`} /> : <div className="multiview-crop-empty">等待结果</div>}
              <span>
                {generatedViewIds
                  ? "独立受管资产"
                  : `${regions[view].width} × ${regions[view].height}`}
              </span>
            </div>
          ))}
          {generatedViewIds ? (
            <button
              type="button"
              disabled={busy}
              onClick={reopenCropRegions}
            >
              重新调整裁切框
            </button>
          ) : (
            <button
              type="button"
              className="primary multiview-confirm-crops"
              disabled={!source || busy}
              onClick={() => void confirmCropRegions()}
            >
              <CheckCircle size={18} />
              确认裁切框并生成三张视图
            </button>
          )}
          <section className="multiview-model-settings" aria-labelledby="multiview-face-limit-title">
            <div className="multiview-model-settings-heading">
              <strong id="multiview-face-limit-title">生成目标面数</strong>
              <span>{faceLimit.toLocaleString()}</span>
            </div>
            <label>
              <input
                aria-label="Tripo 生成目标面数"
                type="number"
                min={MIN_FACE_LIMIT}
                max={MAX_FACE_LIMIT}
                step={500}
                value={faceLimit}
                disabled={busy}
                onChange={(event) => {
                  if (Number.isFinite(event.target.valueAsNumber)) {
                    setFaceLimit(clampFaceLimit(event.target.valueAsNumber));
                  }
                }}
              />
            </label>
            <div className="multiview-face-presets" aria-label="目标面数预设">
              {faceLimitPresets.map((value) => (
                <button
                  key={value}
                  type="button"
                  className={faceLimit === value ? "active" : undefined}
                  disabled={busy}
                  aria-pressed={faceLimit === value}
                  onClick={() => setFaceLimit(value)}
                >
                  {value.toLocaleString()}
                </button>
              ))}
            </div>
            <small>
              Tripo 的 face_limit 是生成目标上限，实际结果可能略有差异；最低建议 500。
            </small>
          </section>
          <button
            className="primary multiview-submit"
            disabled={!valid || busy}
            onClick={() => void submit3d()}
          >
            <DownloadSimple size={18} />
            确认裁切并进入 3D 模型处理
          </button>
        </aside>
      </div>
      {approvalId && (
        <section className="multiview-approval">
          <strong>确认外部图像生成</strong>
          <p>将发送所选受管来源图与三视图提示词；原文件仍保留在本机。</p>
          <button onClick={() => setApprovalId(null)}>取消</button>
          <button
            className="primary"
            disabled={busy}
            onClick={() => void approve()}
          >
            批准并生成
          </button>
        </section>
      )}
      {modelApproval && (
        <section className="multiview-approval">
          <strong>确认外部 Tripo3D 任务</strong>
          <p>
            仅发送已裁切的正、侧、背受管副本；目标面数：
            {faceLimit.toLocaleString()}。
          </p>
          <button onClick={() => setModelApproval(null)}>取消</button>
          <button
            className="primary"
            disabled={busy}
            onClick={() => {
              setBusy(true);
              void api
                .decideApproval(projectId, modelApproval, true, requestId())
                .then((result) => {
                  if (result.status !== "queued" || !result.job?.job_id) {
                    throw new Error(
                      result.error?.user_message ?? "3D 生成任务未进入队列。",
                    );
                  }
                  onModelWorkflowContextChange?.({
                    asset_id: modelWorkflowContext?.asset_id ?? null,
                    target_triangles: faceLimit,
                    generation_job_id: result.job.job_id,
                  });
                  setModelApproval(null);
                  setMessage("3D 任务已提交，正在打开 3D 模型处理页显示生成进度。");
                  onModeChange?.("model3d");
                })
                .catch((error: unknown) => {
                  setMessage(
                    error instanceof Error
                      ? error.message
                      : "3D 任务提交失败，请重试。",
                  );
                })
                .finally(() => setBusy(false));
            }}
          >
            批准并提交
          </button>
        </section>
      )}
    </section>
  );
}
