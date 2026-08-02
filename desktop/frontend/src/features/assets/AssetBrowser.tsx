import {
  ArrowSquareOut,
  Check,
  Copy,
  Cube,
  File,
  ImageSquare,
  SpinnerGap,
  WarningCircle,
} from "@phosphor-icons/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ApiClient, AssetDto } from "../../shared/api/client";
import { readablePrompt } from "../../shared/prompts/promptDocument";
import "./asset-browser.css";

const IMAGE_ASSET_TYPES = new Set([
  "source_image",
  "generated_image",
  "annotation",
  "crop",
  "multiview",
]);
// The library is user-facing: durable implementation records such as analysis
// JSON, previews, textures, and exported ZIPs stay with their workflows but
// do not appear alongside project assets.
const LIBRARY_ASSET_TYPES = new Set([
  "prompt",
  ...IMAGE_ASSET_TYPES,
  "glb",
  "fbx",
]);
const PAGE_SIZE = 30;
const MAX_PREVIEW_REQUESTS = 4;
const MAX_PREVIEW_ITEMS = 32;
const MAX_PREVIEW_BYTES = 64 * 1024 * 1024;

type PreviewQueueItem = {
  run(): void;
};

let activePreviewRequests = 0;
const previewQueue: PreviewQueueItem[] = [];
const previewBlobCache = new Map<string, Blob>();
let previewBlobCacheBytes = 0;
const promptTextCache = new Map<string, string>();
let promptTextCacheChars = 0;
const MAX_PROMPT_CACHE_CHARS = 2_000_000;
const assetScrollPositions = new Map<string, number>();

type AssetFilter = "all" | "image" | "prompt" | "model";

const ASSET_FILTERS: Array<{ id: AssetFilter; label: string }> = [
  { id: "all", label: "全部" },
  { id: "image", label: "图片" },
  { id: "prompt", label: "Prompt" },
  { id: "model", label: "3D 资产" },
];

function matchesAssetFilter(asset: AssetDto, filter: AssetFilter) {
  if (filter === "all") return true;
  if (filter === "image") return IMAGE_ASSET_TYPES.has(asset.asset_type);
  if (filter === "prompt") return asset.asset_type === "prompt";
  return asset.asset_type === "glb" || asset.asset_type === "fbx";
}

function newestFirst(left: AssetDto, right: AssetDto) {
  const leftTime = Date.parse(left.created_at ?? "");
  const rightTime = Date.parse(right.created_at ?? "");
  const safeLeftTime = Number.isNaN(leftTime) ? Number.NEGATIVE_INFINITY : leftTime;
  const safeRightTime = Number.isNaN(rightTime) ? Number.NEGATIVE_INFINITY : rightTime;
  if (safeLeftTime !== safeRightTime) return safeRightTime - safeLeftTime;
  return right.id.localeCompare(left.id);
}

function requestId() {
  return `asset-${crypto.randomUUID()}`;
}

function drainPreviewQueue() {
  while (activePreviewRequests < MAX_PREVIEW_REQUESTS && previewQueue.length > 0) {
    previewQueue.shift()?.run();
  }
}

function schedulePreview<T>(task: () => Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    previewQueue.push({
      run() {
        if (signal.aborted) {
          reject(new DOMException("Preview request was cancelled.", "AbortError"));
          drainPreviewQueue();
          return;
        }
        activePreviewRequests += 1;
        void task()
          .then(resolve, reject)
          .finally(() => {
            activePreviewRequests -= 1;
            drainPreviewQueue();
          });
      },
    });
    drainPreviewQueue();
  });
}

function cachedBlob(key: string) {
  const value = previewBlobCache.get(key);
  if (!value) return null;
  previewBlobCache.delete(key);
  previewBlobCache.set(key, value);
  return value;
}

function cacheBlob(key: string, blob: Blob) {
  const previous = previewBlobCache.get(key);
  if (previous) previewBlobCacheBytes -= previous.size;
  previewBlobCache.delete(key);
  previewBlobCache.set(key, blob);
  previewBlobCacheBytes += blob.size;
  while (
    previewBlobCache.size > MAX_PREVIEW_ITEMS
    || previewBlobCacheBytes > MAX_PREVIEW_BYTES
  ) {
    const oldestKey = previewBlobCache.keys().next().value as string | undefined;
    if (!oldestKey) break;
    const oldest = previewBlobCache.get(oldestKey);
    previewBlobCache.delete(oldestKey);
    previewBlobCacheBytes -= oldest?.size ?? 0;
  }
}

function cachedPrompt(key: string) {
  const value = promptTextCache.get(key);
  if (value == null) return null;
  promptTextCache.delete(key);
  promptTextCache.set(key, value);
  return value;
}

function cachePrompt(key: string, text: string) {
  const previous = promptTextCache.get(key);
  if (previous) promptTextCacheChars -= previous.length;
  promptTextCache.delete(key);
  promptTextCache.set(key, text);
  promptTextCacheChars += text.length;
  while (
    promptTextCache.size > MAX_PREVIEW_ITEMS
    || promptTextCacheChars > MAX_PROMPT_CACHE_CHARS
  ) {
    const oldestKey = promptTextCache.keys().next().value as string | undefined;
    if (!oldestKey) break;
    promptTextCacheChars -= promptTextCache.get(oldestKey)?.length ?? 0;
    promptTextCache.delete(oldestKey);
  }
}

function useNearViewport() {
  const elementRef = useRef<HTMLDivElement>(null);
  const [nearViewport, setNearViewport] = useState(
    () => typeof IntersectionObserver === "undefined",
  );
  useEffect(() => {
    const element = elementRef.current;
    if (!element) return;
    if (typeof IntersectionObserver === "undefined") {
      setNearViewport(true);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => setNearViewport(entry.isIntersecting),
      { rootMargin: "300px 0px" },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return { elementRef, nearViewport };
}

function usePreviewUrl(
  api: ApiClient,
  projectId: string,
  assetId: string | null,
  mode: "thumbnail" | "content",
  enabled: boolean,
) {
  const [url, setUrl] = useState("");
  const [state, setState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  useEffect(() => {
    if (!assetId || !enabled) {
      setState(assetId ? "idle" : "error");
      return;
    }
    const cacheKey = `${projectId}:${mode}:${assetId}`;
    const controller = new AbortController();
    let objectUrl = "";
    const show = (blob: Blob) => {
      if (controller.signal.aborted) return;
      objectUrl = URL.createObjectURL(blob);
      setUrl(objectUrl);
      setState("ready");
    };
    const cached = cachedBlob(cacheKey);
    if (cached) {
      show(cached);
    } else {
      setState("loading");
      void schedulePreview(
        () => mode === "thumbnail"
          ? api.assetThumbnail(projectId, assetId, controller.signal)
          : api.assetContent(projectId, assetId, controller.signal),
        controller.signal,
      ).then((blob) => {
        cacheBlob(cacheKey, blob);
        show(blob);
      }).catch((error) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        if (!controller.signal.aborted) setState("error");
      });
    }
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      setUrl("");
    };
  }, [api, assetId, enabled, mode, projectId]);
  return { url, state };
}

function usePromptPreview(
  api: ApiClient,
  projectId: string,
  assetId: string,
  enabled: boolean,
) {
  const cacheKey = `${projectId}:prompt:${assetId}`;
  const [text, setText] = useState(() => cachedPrompt(cacheKey) ?? "");
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!enabled || text) return;
    const cached = cachedPrompt(cacheKey);
    if (cached != null) {
      setText(cached);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    void schedulePreview(
      () => api.assetText(projectId, assetId, controller.signal),
      controller.signal,
    ).then((value) => {
      cachePrompt(cacheKey, value);
      setText(value);
      setFailed(false);
    }).catch((error) => {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setFailed(true);
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [api, assetId, cacheKey, enabled, projectId, text]);
  return { text, loading, failed, cacheKey };
}

function formatBytes(value?: number) {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function promptLanguage(text: string) {
  const hasChinese = /[\u3400-\u9fff]/.test(text);
  const hasEnglish = /[A-Za-z]/.test(text);
  if (hasChinese && hasEnglish) return "中 / EN";
  return hasChinese ? "中文" : hasEnglish ? "EN" : "Prompt";
}

function promptContent(text: string) {
  return readablePrompt(text);
}

function assetTypeLabel(asset: AssetDto) {
  if (IMAGE_ASSET_TYPES.has(asset.asset_type)) return "图片";
  if (asset.asset_type === "prompt") return "Prompt";
  if (asset.asset_type === "glb") return "3D 模型";
  return asset.asset_type.replaceAll("_", " ");
}

function ImagePreview({
  api,
  projectId,
  asset,
  current,
}: {
  api: ApiClient;
  projectId: string;
  asset: AssetDto;
  current: boolean;
}) {
  const { elementRef, nearViewport } = useNearViewport();
  const { url, state } = usePreviewUrl(
    api,
    projectId,
    asset.id,
    "thumbnail",
    nearViewport,
  );
  const [contained, setContained] = useState(false);
  return (
    <div ref={elementRef} className={`asset-media image-media${contained ? " contained" : ""}`}>
      {current && <span className="asset-current-badge"><Check size={13} weight="bold" />当前图片</span>}
      {url
        ? <button type="button" aria-label={`预览 ${asset.name}`} aria-pressed={contained} onClick={() => setContained((value) => !value)}><img src={url} alt={asset.name} /></button>
        : <div className="asset-media-state">
          {state === "loading" ? <SpinnerGap className="spin" size={24} /> : <ImageSquare size={30} />}
          <span>{state === "error" ? "缩略图不可用" : "图片预览"}</span>
        </div>}
    </div>
  );
}

function ModelPreview({
  api,
  projectId,
  asset,
  previewAssetId,
}: {
  api: ApiClient;
  projectId: string;
  asset: AssetDto;
  previewAssetId: string | null;
}) {
  const { elementRef, nearViewport } = useNearViewport();
  const { url, state } = usePreviewUrl(
    api,
    projectId,
    previewAssetId,
    "content",
    nearViewport,
  );
  const [contained, setContained] = useState(false);
  return (
    <div ref={elementRef} className={`asset-media model-media${contained ? " contained" : ""}`}>
      {url
        ? <button type="button" aria-label={`预览 ${asset.name} 的静态快照`} aria-pressed={contained} onClick={() => setContained((value) => !value)}><img src={url} alt={`${asset.name} 静态预览`} /></button>
        : <div className="asset-media-state model-fallback">
          {state === "loading" ? <SpinnerGap className="spin" size={28} /> : <Cube size={38} />}
          <strong>{previewAssetId ? "正在加载静态快照" : "暂无静态快照"}</strong>
          <span>{previewAssetId ? "不会加载实时 3D" : "打开模型后可生成预览"}</span>
        </div>}
    </div>
  );
}

function AssetCard({
  asset,
  previewAssetId,
  projectId,
  api,
  readOnly,
  focused,
  registerElement,
  onUseImage,
  onOpenModel,
}: {
  asset: AssetDto;
  previewAssetId: string | null;
  projectId: string;
  api: ApiClient;
  readOnly: boolean;
  focused: boolean;
  registerElement(element: HTMLElement | null): void;
  onUseImage(asset: AssetDto): Promise<void>;
  onOpenModel(assetId: string): void;
}) {
  const image = IMAGE_ASSET_TYPES.has(asset.asset_type);
  const prompt = asset.asset_type === "prompt";
  const model = asset.asset_type === "glb";
  const currentImage = image && asset.is_current;
  const { elementRef: promptRef, nearViewport } = useNearViewport();
  const {
    text: promptText,
    loading: promptLoading,
    failed: promptFailed,
    cacheKey: promptCacheKey,
  } = usePromptPreview(api, projectId, asset.id, prompt && nearViewport);
  const [promptExpanded, setPromptExpanded] = useState(false);
  const [actionState, setActionState] = useState<"idle" | "working" | "copied" | "error">("idle");
  const resetTimer = useRef<number | null>(null);
  useEffect(() => () => {
    if (resetTimer.current != null) window.clearTimeout(resetTimer.current);
  }, []);

  const copyPrompt = async () => {
    setActionState("working");
    try {
      let value = promptText;
      if (!value) {
        const controller = new AbortController();
        value = await schedulePreview(
          () => api.assetText(projectId, asset.id, controller.signal),
          controller.signal,
        );
        cachePrompt(promptCacheKey, value);
      }
      await navigator.clipboard.writeText(promptContent(value));
      setActionState("copied");
      resetTimer.current = window.setTimeout(() => setActionState("idle"), 1800);
    } catch {
      setActionState("error");
      resetTimer.current = window.setTimeout(() => setActionState("idle"), 2400);
    }
  };

  const dimensions = typeof asset.metadata.width === "number"
    && typeof asset.metadata.height === "number"
    ? `${asset.metadata.width} × ${asset.metadata.height}`
    : null;
  const readablePrompt = prompt ? promptContent(promptText) : "";
  const detail = image
    ? dimensions ?? "受管图片"
    : prompt
      ? readablePrompt ? `${promptLanguage(readablePrompt)} · ${readablePrompt.length} 字符` : "受管文本"
      : model
        ? `GLB${formatBytes(asset.size_bytes) ? ` · ${formatBytes(asset.size_bytes)}` : ""}`
        : formatBytes(asset.size_bytes) ?? "受管项目资产";

  return (
    <article
      ref={registerElement}
      tabIndex={focused ? -1 : undefined}
      className={`asset-card${currentImage ? " current" : ""}${focused ? " focused" : ""}`}
      data-asset-id={asset.id}
      data-asset-type={asset.asset_type}
    >
      {image && <ImagePreview api={api} projectId={projectId} asset={asset} current={currentImage} />}
      {prompt && <div ref={promptRef} className="asset-media prompt-media">
        <button
          type="button"
          aria-label={`查看完整 Prompt：${asset.name}`}
          aria-expanded={promptExpanded}
          onClick={() => setPromptExpanded((value) => !value)}
        >
          {promptLoading && !promptText
            ? <span className="asset-media-state"><SpinnerGap className="spin" size={24} />正在读取 Prompt</span>
            : promptFailed
              ? <span className="asset-media-state"><File size={28} />Prompt 预览不可用</span>
              : <span className={promptExpanded ? "prompt-copy expanded" : "prompt-copy"}>{readablePrompt ? promptExpanded ? readablePrompt : readablePrompt.slice(0, 1200) : "Prompt 内容将在卡片进入视野后加载。"}</span>}
        </button>
      </div>}
      {model && <ModelPreview api={api} projectId={projectId} asset={asset} previewAssetId={previewAssetId} />}
      {!image && !prompt && !model && <div className="asset-media generic-media"><File size={38} /><span>{assetTypeLabel(asset)}</span></div>}

      <div className="asset-card-copy">
        <h2 title={asset.name}>{asset.name}</h2>
        <p>{assetTypeLabel(asset)}</p>
        <p>{detail}</p>
      </div>

      <div className="asset-card-actions">
        {image && (currentImage
          ? <span className="asset-current-label"><Check size={16} weight="bold" />当前图片</span>
          : <button
              type="button"
              disabled={readOnly || actionState === "working"}
              onClick={() => {
                setActionState("working");
                void onUseImage(asset).finally(() => setActionState("idle"));
              }}
            >
              {actionState === "working" ? <SpinnerGap className="spin" size={17} /> : <ImageSquare size={17} />}
              使用此图片
            </button>)}
        {prompt && <button type="button" disabled={actionState === "working"} onClick={() => void copyPrompt()}>
          {actionState === "working"
            ? <SpinnerGap className="spin" size={17} />
            : actionState === "copied"
              ? <Check size={17} weight="bold" />
              : actionState === "error"
                ? <WarningCircle size={17} />
              : <Copy size={17} />}
          {actionState === "copied" ? "已复制" : actionState === "error" ? "复制失败" : "复制 Prompt"}
        </button>}
        {model && <button type="button" onClick={() => onOpenModel(asset.id)}>
          <ArrowSquareOut size={17} />查看 3D
        </button>}
      </div>
    </article>
  );
}

export function AssetBrowser({
  projectId,
  api,
  readOnly,
  onCurrent,
  onOpenModel = () => undefined,
  focusAssetId,
}: {
  projectId: string;
  api: ApiClient;
  readOnly: boolean;
  onCurrent(): void;
  onOpenModel?(assetId: string): void;
  focusAssetId?: string | null;
}) {
  const [assets, setAssets] = useState<AssetDto[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [visibleLimit, setVisibleLimit] = useState(PAGE_SIZE);
  const [filter, setFilter] = useState<AssetFilter>("all");
  const browserRef = useRef<HTMLElement>(null);
  const loadMoreRef = useRef<HTMLDivElement>(null);
  const assetElements = useRef(new Map<string, HTMLElement>());
  const reload = useCallback(async () => {
    try {
      setAssets(await api.assets(projectId));
      setNotice(null);
    } catch {
      setNotice("资产无法加载，请刷新本地服务后重试。");
    }
  }, [api, projectId]);

  useEffect(() => {
    setVisibleLimit(PAGE_SIZE);
    void reload();
  }, [projectId, reload]);
  useEffect(() => {
    const browser = browserRef.current;
    if (!browser || assets.length === 0 || focusAssetId) return;
    const saved = assetScrollPositions.get(projectId);
    if (saved == null) return;
    const frame = window.requestAnimationFrame(() => {
      browser.scrollTop = saved;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [assets.length, focusAssetId, projectId]);

  const visibleAssets = useMemo(
    () => assets
      .filter((asset) => LIBRARY_ASSET_TYPES.has(asset.asset_type))
      .filter((asset) => matchesAssetFilter(asset, filter))
      .sort(newestFirst),
    [assets, filter],
  );
  const previewByModel = useMemo(() => {
    const result = new Map<string, string>();
    for (const asset of assets) {
      if (asset.asset_type === "preview" && asset.parent_asset_id && !result.has(asset.parent_asset_id)) {
        result.set(asset.parent_asset_id, asset.id);
      }
    }
    return result;
  }, [assets]);

  useEffect(() => {
    if (!focusAssetId) return;
    const index = visibleAssets.findIndex((asset) => asset.id === focusAssetId);
    if (index >= visibleLimit) setVisibleLimit(index + 1);
  }, [focusAssetId, visibleAssets, visibleLimit]);
  useEffect(() => {
    if (!focusAssetId) return;
    const element = assetElements.current.get(focusAssetId);
    element?.scrollIntoView({ block: "center" });
    element?.focus({ preventScroll: true });
  }, [focusAssetId, visibleLimit, visibleAssets]);
  useEffect(() => {
    const sentinel = loadMoreRef.current;
    if (!sentinel || visibleLimit >= visibleAssets.length) return;
    if (typeof IntersectionObserver === "undefined") {
      setVisibleLimit(visibleAssets.length);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setVisibleLimit((value) => Math.min(value + PAGE_SIZE, visibleAssets.length));
        }
      },
      { root: browserRef.current, rootMargin: "500px 0px" },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [visibleAssets.length, visibleLimit]);

  const useImage = async (asset: AssetDto) => {
    try {
      await api.setCurrentAsset(projectId, asset.id, requestId());
      await reload();
      onCurrent();
    } catch {
      setNotice("图片未能设为当前图片，已重新加载最新项目状态。");
      await reload();
      throw new Error("use-image-failed");
    }
  };

  return (
    <section
      ref={browserRef}
      className="asset-browser"
      aria-labelledby="asset-browser-title"
      onScroll={(event) => assetScrollPositions.set(projectId, event.currentTarget.scrollTop)}
    >
      <header>
        <div>
          <p className="eyebrow">Project assets</p>
          <h1 id="asset-browser-title">Assets</h1>
        </div>
      </header>
      <div className="asset-filters" role="group" aria-label="资产类型筛选">
        {ASSET_FILTERS.map((option) => (
          <button
            key={option.id}
            type="button"
            aria-pressed={filter === option.id}
            onClick={() => {
              setFilter(option.id);
              setVisibleLimit(PAGE_SIZE);
            }}
          >
            {option.label}
          </button>
        ))}
      </div>
      {notice && <p className="asset-notice" role="status"><WarningCircle size={18} />{notice}</p>}
      <div className="asset-list">
        {visibleAssets.slice(0, visibleLimit).map((asset) => (
          <AssetCard
            key={asset.id}
            asset={asset}
            previewAssetId={previewByModel.get(asset.id) ?? null}
            projectId={projectId}
            api={api}
            readOnly={readOnly}
            focused={asset.id === focusAssetId}
            registerElement={(element) => {
              if (element) assetElements.current.set(asset.id, element);
              else assetElements.current.delete(asset.id);
            }}
            onUseImage={useImage}
            onOpenModel={onOpenModel}
          />
        ))}
      </div>
      {visibleAssets.length === 0 && !notice && <div className="asset-empty"><File size={32} /><p>项目中还没有资产。</p></div>}
      {visibleLimit < visibleAssets.length && <div ref={loadMoreRef} className="asset-load-more" role="status">继续滚动以加载更多资产</div>}
    </section>
  );
}
