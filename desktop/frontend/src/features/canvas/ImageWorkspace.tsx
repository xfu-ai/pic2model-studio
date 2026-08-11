import {
  WarningCircle,
} from "@phosphor-icons/react";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent,
  type WheelEvent,
} from "react";
import type {
  ApiClient,
  ApiError,
  AssetDto,
  WorkspaceMode,
} from "../../shared/api/client";
import { HostClient } from "../../shared/host/client";
import { CanvasContextToolbar } from "./CanvasContextToolbar";
import { AssetFileActions } from "../assets/AssetFileActions";
import { CaptureScreenAction } from "../assets/CaptureScreenAction";
import { ImportImageAction } from "../assets/ImportImageAction";
import "./image-workspace.css";

type Pan = { x: number; y: number };
type DragOrigin = Pan & { clientX: number; clientY: number };

const MIN_ZOOM = 0.1;
const MAX_ZOOM = 8;
const cleanCoordinate = (value: number) =>
  Math.abs(value) < 0.001 ? 0 : Math.round(value * 1_000) / 1_000;

function isPreviewableImage(asset: AssetDto) {
  return asset.asset_type !== "glb" && asset.trashed_at == null;
}

function assetDimensions(asset: AssetDto | null) {
  const width = asset?.metadata.width;
  const height = asset?.metadata.height;
  return typeof width === "number" && typeof height === "number"
    ? `${width} × ${height}`
    : "Size unavailable";
}

export function ImageWorkspace({
  projectId,
  api,
  onModeChange,
  onModelJobQueued,
  onCurrentAssetChange,
  host = new HostClient(),
}: {
  projectId: string;
  api: ApiClient;
  onModeChange(mode: WorkspaceMode): void;
  onModelJobQueued?(jobId: string): void;
  onCurrentAssetChange?(): void;
  host?: Pick<HostClient, "chooseExportDirectory">;
}) {
  const [assets, setAssets] = useState<AssetDto[]>([]);
  const [familyAssetIds, setFamilyAssetIds] = useState<Set<string>>(new Set());
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);
  const [source, setSource] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [retryToken, setRetryToken] = useState(0);
  const [assetRevision, setAssetRevision] = useState(0);
  const [fit, setFit] = useState(true);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState<Pan>({ x: 0, y: 0 });
  const drag = useRef<DragOrigin | null>(null);
  const spacePressed = useRef(false);
  const panPrimedUntil = useRef(0);

  const previewableAssets = useMemo(
    () =>
      assets.filter(
        (item) => familyAssetIds.has(item.id) && isPreviewableImage(item),
      ),
    [assets, familyAssetIds],
  );
  const asset = useMemo(
    () => assets.find((item) => item.id === selectedAssetId) ?? null,
    [assets, selectedAssetId],
  );
  const promptAsset = useMemo(
    () =>
      assets
        .filter((item) => item.asset_type === "prompt" && item.trashed_at == null)
        .sort((left, right) => (right.version_no ?? 0) - (left.version_no ?? 0))[0] ??
      null,
    [assets],
  );

  useEffect(() => {
    let active = true;
    void api
      .assets(projectId)
      .then((items) => {
        if (!active) return;
        setAssets(items);
        setSelectedAssetId(
          (previous) =>
            (previous && items.some((item) => item.id === previous)
              ? previous
              : items.find((item) => item.is_current)?.id) ?? null,
        );
      })
      .catch((error: unknown) => {
        if (!active) return;
        setPreviewError(
          error instanceof Error
            ? error.message
            : "The project asset list could not be loaded.",
        );
      });
    return () => {
      active = false;
    };
  }, [api, assetRevision, projectId]);

  useEffect(() => {
    const current = assets.find((item) => item.is_current);
    if (!current) {
      setFamilyAssetIds(new Set());
      return;
    }
    let active = true;
    void api
      .assetLineage(projectId, current.id)
      .then((lineage) => {
        if (!active) return;
        const nextFamily = new Set([current.id, ...lineage.siblings]);
        setFamilyAssetIds(nextFamily);
        setSelectedAssetId((previous) =>
          previous && nextFamily.has(previous) ? previous : current.id,
        );
      })
      .catch(() => {
        if (!active) return;
        setFamilyAssetIds(new Set([current.id]));
        setSelectedAssetId(current.id);
      });
    return () => {
      active = false;
    };
  }, [api, assets, projectId]);

  useEffect(() => {
    if (!asset) {
      setSource(null);
      return;
    }

    const controller = new AbortController();
    let objectUrl: string | null = null;
    setSource(null);
    setPreviewError(null);

    void api
      .assetContent(projectId, asset.id, controller.signal)
      .then((blob) => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setSource(objectUrl);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        const apiError = error as ApiError;
        setPreviewError(
          apiError.message || "The managed image preview could not be loaded.",
        );
      });

    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [api, asset, projectId, retryToken]);

  useEffect(() => {
    const keyDown = (event: KeyboardEvent) => {
      if (event.code !== "Space") return;
      spacePressed.current = true;
      panPrimedUntil.current = performance.now() + 1_200;
      if (document.activeElement?.classList.contains("image-stage")) {
        event.preventDefault();
      }
    };
    const keyUp = (event: KeyboardEvent) => {
      if (event.code === "Space") spacePressed.current = false;
    };
    const blur = () => {
      spacePressed.current = false;
      drag.current = null;
    };
    window.addEventListener("keydown", keyDown);
    window.addEventListener("keyup", keyUp);
    window.addEventListener("blur", blur);
    return () => {
      window.removeEventListener("keydown", keyDown);
      window.removeEventListener("keyup", keyUp);
      window.removeEventListener("blur", blur);
    };
  }, []);

  const resetView = (nextFit: boolean) => {
    setFit(nextFit);
    setZoom(1);
    setPan({ x: 0, y: 0 });
  };

  const wheel = (event: WheelEvent<HTMLDivElement>) => {
    event.preventDefault();
    const nextZoom = Math.min(
      MAX_ZOOM,
      Math.max(MIN_ZOOM, zoom * (event.deltaY > 0 ? 0.9 : 1.1)),
    );
    const rect = event.currentTarget.getBoundingClientRect();
    const cursor = {
      x: event.clientX - rect.left - rect.width / 2,
      y: event.clientY - rect.top - rect.height / 2,
    };
    const ratio = nextZoom / zoom;
    setFit(false);
    setPan({
      x: cleanCoordinate(cursor.x - (cursor.x - pan.x) * ratio),
      y: cleanCoordinate(cursor.y - (cursor.y - pan.y) * ratio),
    });
    setZoom(nextZoom);
  };

  const beginPan = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button === 1) {
      panPrimedUntil.current = performance.now() + 1_200;
    }
    const canPan =
      event.button === 1 ||
      (event.button === 0 &&
        (spacePressed.current ||
          performance.now() <= panPrimedUntil.current));
    if (!canPan) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = {
      clientX: event.clientX,
      clientY: event.clientY,
      x: pan.x,
      y: pan.y,
    };
  };

  const continuePan = (event: PointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    setFit(false);
    setPan({
      x: cleanCoordinate(
        drag.current.x + event.clientX - drag.current.clientX,
      ),
      y: cleanCoordinate(
        drag.current.y + event.clientY - drag.current.clientY,
      ),
    });
  };

  const endPan = (event: PointerEvent<HTMLDivElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    drag.current = null;
  };

  return (
    <section className="image-workspace" aria-labelledby="workspace-title">
      <header className="current-asset-summary">
        <div className="current-asset-title">
          <p className="eyebrow">素材工作台</p>
          <h1 id="workspace-title">{asset?.name ?? "还没有当前图片"}</h1>
        </div>
        <label className="version-switcher">
          <span>Version</span>
          <select
            aria-label="Preview asset version"
            value={selectedAssetId ?? ""}
            disabled={previewableAssets.length < 2}
            onChange={(event) => {
              setSelectedAssetId(event.target.value);
              resetView(true);
            }}
          >
            {previewableAssets.map((item) => (
              <option key={item.id} value={item.id}>
                v{item.version_no ?? 1} · {item.name}
              </option>
            ))}
          </select>
        </label>
        <span>{assetDimensions(asset)}</span>
        <span>{String(asset?.metadata.format ?? "PNG").toUpperCase()}</span>
        <span>{asset?.metadata.has_alpha ? "Transparency" : "Opaque"}</span>
        <span>Managed project asset</span>
      </header>

      <div className="image-workspace-tools">
        <CanvasContextToolbar
          projectId={projectId}
          api={api}
          asset={asset}
          promptAsset={promptAsset}
          referenceAvailable={previewableAssets.length >= 1}
          onModeChange={onModeChange}
          onModelJobQueued={onModelJobQueued}
          onLocalImageCompleted={(assetId) => {
            setSelectedAssetId(assetId);
            setAssetRevision((value) => value + 1);
            onCurrentAssetChange?.();
          }}
        />
        <div className="image-capture-action">
          {asset && <AssetFileActions projectId={projectId} asset={asset} api={api} host={host} />}
          <ImportImageAction projectId={projectId} api={api} label="导入图片" onImported={() => setAssetRevision((value) => value + 1)} />
          <CaptureScreenAction projectId={projectId} api={api} onImported={() => setAssetRevision((value) => value + 1)} />
        </div>
      </div>

      <div
        className="image-stage"
        aria-label="Image preview canvas"
        tabIndex={0}
        onWheel={wheel}
        onPointerDown={beginPan}
        onPointerMove={continuePan}
        onPointerUp={endPan}
        onPointerCancel={endPan}
      >
        {source ? (
          <img
            src={source}
            alt={asset?.name ?? "Current asset"}
            data-managed-asset-id={asset?.id}
            className={fit ? "fit" : ""}
            draggable={false}
            style={
              fit
                ? undefined
                : {
                    transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
                  }
            }
          />
        ) : previewError ? (
          <div className="preview-feedback" role="alert">
            <WarningCircle size={28} />
            <strong>Preview unavailable</strong>
            <p>{previewError}</p>
            <button type="button" onClick={() => setRetryToken((value) => value + 1)}>
              Retry preview
            </button>
          </div>
        ) : asset ? (
          <p role="status">Loading managed image preview…</p>
        ) : (
          <div className="image-home-empty">
            <strong>从一张图片开始</strong>
            <p>在上方导入图片或框选截屏，再选择内容与风格分析、建模准备或 3D 模型处理工具。</p>
          </div>
        )}

        <div className="zoom-controls" aria-label="Zoom controls">
          <button type="button" onClick={() => resetView(true)}>
            适应
          </button>
          <button type="button" onClick={() => resetView(false)}>
            100%
          </button>
          <output aria-label="Zoom level">{fit ? "Fit" : `${Math.round(zoom * 100)}%`}</output>
        </div>
        <p className="pan-hint">滚轮缩放 · 空格或中键平移</p>
      </div>
    </section>
  );
}
