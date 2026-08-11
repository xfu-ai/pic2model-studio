import {
  ArrowsOut,
  Crosshair,
  Cube,
  GitDiff,
  MagicWand,
  Selection,
  SquaresFour,
} from "@phosphor-icons/react";
import { useId, useState, type FormEvent, type ReactNode } from "react";
import type {
  ApiClient,
  AssetDto,
  ToolResultDto,
  WorkspaceMode,
} from "../../shared/api/client";

type PaidAction = "variants" | "multiview" | "model3d";
type LocalImageAction = "resize" | "upscale";

const productionProfile = "image-generation/auto";
const productionModel = "auto";
const tripoProductionProfile = "tripo3d/default";
const tripoProductionModel =
  import.meta.env.VITE_TRIPO_MODEL || "v3.1-20260211";

const actionCopy: Record<
  PaidAction,
  { title: string; service: string; tool: string }
> = {
  variants: {
    title: "生成图片变体",
    service: "自动路由 · Tripo3D / Meshy",
    tool: "image.generate_variants",
  },
  multiview: {
    title: "生成三视图",
    service: "自动路由 · Tripo3D / Meshy",
    tool: "multiview.generate",
  },
  model3d: {
    title: "从当前图片生成 3D 模型",
    service: "Tripo3D",
    tool: "model3d.generate",
  },
};

function requestId(prefix: string) {
  return `${prefix}-${crypto.randomUUID()}`;
}

function ToolButton({
  children,
  reason,
  onClick,
}: {
  children: ReactNode;
  reason?: string;
  onClick(): void;
}) {
  const reasonId = useId();
  return (
    <span className="canvas-tool">
      <button
        type="button"
        aria-disabled={reason ? "true" : undefined}
        aria-describedby={reason ? reasonId : undefined}
        onClick={() => {
          if (!reason) onClick();
        }}
      >
        {children}
      </button>
      {reason && (
        <span id={reasonId} className="canvas-tool-reason" role="tooltip">
          {reason}
        </span>
      )}
    </span>
  );
}

function ExternalTransferApproval({
  action,
  asset,
  promptAsset,
  api,
  projectId,
  onCancel,
  onQueued,
}: {
  action: PaidAction;
  asset: AssetDto;
  promptAsset: AssetDto | null;
  api: ApiClient;
  projectId: string;
  onCancel(): void;
  onQueued(result: ToolResultDto): void;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const copy = actionCopy[action];

  const approve = async () => {
    setSubmitting(true);
    setError(null);
    const invokeRequestId = requestId("canvas-production");
    const providerProfile =
      action === "model3d" ? tripoProductionProfile : productionProfile;
    const arguments_: Record<string, unknown> =
      action === "model3d"
        ? {
            mode: "image",
            image_asset_id: asset.id,
            provider_profile: providerProfile,
            model: tripoProductionModel,
            parameters: {
              model_version: tripoProductionModel,
              texture: true,
              pbr: true,
              auto_size: true,
              orientation: "align_image",
            },
          }
        : {
            source_asset_id: asset.id,
            provider_profile: providerProfile,
            channel: "auto",
            model: productionModel,
            ...(action === "variants"
              ? {
                  prompt_asset_id: promptAsset?.id,
                  candidate_count: 4,
                  size: "1536x1024",
                  quality: "high",
                  output_format: "png",
                }
              : promptAsset
                ? { prompt_asset_id: promptAsset.id }
                : {}),
          };
    try {
      const proposed = await api.invokeTool(
        projectId,
        copy.tool,
        arguments_,
        invokeRequestId,
        { providerProfile },
      );
      const approvalId = proposed.ui_action?.action_id;
      if (proposed.status !== "awaiting_ui_action" || !approvalId) {
        throw new Error(
          proposed.error?.user_message ??
            "生产服务没有返回可确认的审批，请刷新后重试。",
        );
      }
      const approved = await api.decideApproval(
        projectId,
        approvalId,
        true,
        requestId("canvas-approval"),
      );
      if (!approved.ok || approved.status !== "queued") {
        throw new Error(
          approved.error?.user_message ?? "生产任务未能进入队列。",
        );
      }
      onQueued(approved);
    } catch (unknownError) {
      setError(
        unknownError instanceof Error
          ? unknownError.message
          : "生产任务提交失败，请检查服务设置后重试。",
      );
      setSubmitting(false);
    }
  };

  return (
    <div className="dialog-backdrop external-transfer-backdrop">
      <section
        className="dialog external-transfer-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="external-transfer-title"
      >
        <p className="eyebrow">首次外传与付费审批</p>
        <h2 id="external-transfer-title">{copy.title}</h2>
        <p>
          仅在你确认后，受管素材副本才会发送到外部服务。原文件仍在本机。
        </p>
        <dl className="external-transfer-summary">
          <div>
            <dt>素材</dt>
            <dd>{asset.name}</dd>
          </div>
          <div>
            <dt>服务</dt>
            <dd>{copy.service} · {action === "model3d" ? tripoProductionProfile : productionProfile}</dd>
          </div>
          <div>
            <dt>模型</dt>
            <dd>{action === "model3d" ? tripoProductionModel : "按可用性与优先级选择"}</dd>
          </div>
          <div>
            <dt>费用</dt>
            <dd>外部付费操作，实际金额由服务商结算</dd>
          </div>
        </dl>
        {error && <p className="external-transfer-error" role="alert">{error}</p>}
        <div className="dialog-actions">
          <button type="button" disabled={submitting} onClick={onCancel}>
            取消
          </button>
          <button
            type="button"
            className="primary"
            disabled={submitting}
            onClick={() => void approve()}
          >
            {submitting ? "正在提交…" : "批准并提交"}
          </button>
        </div>
      </section>
    </div>
  );
}

function LocalImageSizeDialog({
  asset,
  api,
  projectId,
  onCancel,
  onCompleted,
  onQueued,
}: {
  asset: AssetDto;
  api: ApiClient;
  projectId: string;
  onCancel(): void;
  onCompleted(assetId: string): void;
  onQueued(): void;
}) {
  const sourceWidth = typeof asset.metadata.width === "number" ? asset.metadata.width : 1024;
  const sourceHeight = typeof asset.metadata.height === "number" ? asset.metadata.height : 1024;
  const [action, setAction] = useState<LocalImageAction>("resize");
  const [width, setWidth] = useState(String(sourceWidth));
  const [height, setHeight] = useState(String(sourceHeight));
  const [lockAspectRatio, setLockAspectRatio] = useState(true);
  const [outputFormat, setOutputFormat] = useState("png");
  const [quality, setQuality] = useState(90);
  const [scale, setScale] = useState<2 | 4>(2);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      if (action === "resize") {
        const targetWidth = Number(width);
        const targetHeight = Number(height);
        if (!Number.isInteger(targetWidth) || !Number.isInteger(targetHeight)
          || targetWidth < 1 || targetHeight < 1
          || targetWidth > 16384 || targetHeight > 16384) {
          throw new Error("宽度和高度必须是 1–16384 之间的整数。");
        }
        const result = await api.invokeTool(
          projectId,
          "image.normalize",
          {
            source_asset_id: asset.id,
            target_width: targetWidth,
            target_height: targetHeight,
            lock_aspect_ratio: lockAspectRatio,
            output_format: outputFormat,
            quality,
            preserve_alpha: true,
          },
          requestId("canvas-resize"),
        );
        const outputAssetId = result.output_asset_ids?.[0];
        if (!result.ok || result.status !== "succeeded" || !outputAssetId) {
          throw new Error(result.error?.user_message ?? "图片尺寸调整失败，请重试。");
        }
        await api.setCurrentAsset(projectId, outputAssetId, requestId("canvas-resize-current"));
        onCompleted(outputAssetId);
        return;
      }

      const result = await api.invokeTool(
        projectId,
        "image.upscale_local",
        { source_asset_id: asset.id, scale },
        requestId("canvas-upscale"),
      );
      if (!result.ok || result.status !== "queued" || !result.job?.job_id) {
        throw new Error(result.error?.user_message ?? "本地超分任务未能进入队列。");
      }
      onQueued();
    } catch (unknownError) {
      setError(
        unknownError instanceof Error
          ? unknownError.message
          : "图片处理失败，请重试。",
      );
      setSubmitting(false);
    }
  };

  return (
    <div className="dialog-backdrop local-image-size-backdrop">
      <form
        className="dialog local-image-size-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="local-image-size-title"
        onSubmit={(event) => void submit(event)}
      >
        <p className="eyebrow">本地图片处理</p>
        <h2 id="local-image-size-title">调整尺寸与超分</h2>
        <p>原图不会被覆盖，处理结果会保存为新的受管资产。</p>
        <div className="local-image-mode" aria-label="处理方式">
          <button type="button" aria-pressed={action === "resize"} onClick={() => setAction("resize")}>普通缩放</button>
          <button type="button" aria-pressed={action === "upscale"} onClick={() => setAction("upscale")}>本地超分</button>
        </div>
        {action === "resize" ? (
          <div className="local-image-fields">
            <label>宽度<input aria-label="目标宽度" type="number" min="1" max="16384" value={width} onChange={(event) => setWidth(event.target.value)} /></label>
            <label>高度<input aria-label="目标高度" type="number" min="1" max="16384" value={height} onChange={(event) => setHeight(event.target.value)} /></label>
            <label>输出格式<select aria-label="输出格式" value={outputFormat} onChange={(event) => setOutputFormat(event.target.value)}><option value="png">PNG</option><option value="jpeg">JPEG</option><option value="webp">WebP</option></select></label>
            <label>质量<input aria-label="输出质量" type="number" min="1" max="100" value={quality} onChange={(event) => setQuality(Number(event.target.value))} /></label>
            <label className="local-image-checkbox"><input type="checkbox" checked={lockAspectRatio} onChange={(event) => setLockAspectRatio(event.target.checked)} />保持宽高比</label>
          </div>
        ) : (
          <div className="local-image-fields">
            <label>放大倍数<select aria-label="放大倍数" value={scale} onChange={(event) => setScale(Number(event.target.value) as 2 | 4)}><option value={2}>2×</option><option value={4}>4×</option></select></label>
            <p className="local-image-note">使用内置 Real-ESRGAN 模型离线处理，不会上传图片。</p>
          </div>
        )}
        {error && <p className="local-image-error" role="alert">{error}</p>}
        <div className="dialog-actions">
          <button type="button" disabled={submitting} onClick={onCancel}>取消</button>
          <button type="submit" className="primary" disabled={submitting}>{submitting ? "正在处理…" : action === "resize" ? "生成缩放结果" : "开始本地超分"}</button>
        </div>
      </form>
    </div>
  );
}

export function CanvasContextToolbar({
  projectId,
  api,
  asset,
  promptAsset,
  referenceAvailable,
  onModeChange,
  onModelJobQueued,
  onLocalImageCompleted,
}: {
  projectId: string;
  api: ApiClient;
  asset: AssetDto | null;
  promptAsset: AssetDto | null;
  referenceAvailable: boolean;
  onModeChange(mode: WorkspaceMode): void;
  onModelJobQueued?(jobId: string): void;
  onLocalImageCompleted?(assetId: string): void;
}) {
  const [approval, setApproval] = useState<PaidAction | null>(null);
  const [moreOpen, setMoreOpen] = useState(false);
  const [localImageSizeOpen, setLocalImageSizeOpen] = useState(false);

  return (
    <>
      <nav className="canvas-context-toolbar" aria-label="Canvas tools">
        <ToolButton
          reason={asset ? undefined : "需要先选择一张受管图片。"}
          onClick={() => onModeChange("selection")}
        >
          <Crosshair size={18} />
          框选与裁切
        </ToolButton>
        <ToolButton
          reason={asset ? undefined : "需要先选择一张受管图片。"}
          onClick={() => onModeChange("target_extract")}
        >
          <Selection size={18} />
          提取建模主体
        </ToolButton>
        <ToolButton
          reason={referenceAvailable ? undefined : "需要先导入一张受管图片作为参考。"}
          onClick={() => onModeChange("compare")}
        >
          <GitDiff size={18} />
          分析内容与风格
        </ToolButton>
        <ToolButton
          reason={
            !asset
              ? "需要先选择一张受管图片。"
              : !promptAsset
                ? "需要先在内容与风格分析页保存受管 Prompt。"
                : undefined
          }
          onClick={() => setApproval("variants")}
        >
          <MagicWand size={18} />
          生成创意图
        </ToolButton>
        <ToolButton
          reason={asset ? undefined : "需要先选择一张受管图片。"}
          onClick={() => setApproval("multiview")}
        >
          <SquaresFour size={18} />
          制作三视图
        </ToolButton>
        <ToolButton
          reason={asset ? undefined : "需要先选择一张受管图片。"}
          onClick={() => setApproval("model3d")}
        >
          <Cube size={18} />
          发起 3D 生成
        </ToolButton>
        <span className="canvas-tool canvas-more-tool">
          <button
            type="button"
            aria-expanded={moreOpen}
            aria-haspopup="menu"
            onClick={() => setMoreOpen((value) => !value)}
          >
            <ArrowsOut size={18} />
            更多
          </button>
          {moreOpen && (
            <div className="canvas-more-menu" role="menu">
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setMoreOpen(false);
                  onModeChange("image");
                }}
              >
                返回当前图片
              </button>
              <button
                type="button"
                role="menuitem"
                aria-disabled={asset ? undefined : "true"}
                title={asset ? "普通缩放或使用内置模型进行本地超分。" : "需要先选择一张受管图片。"}
                onClick={() => {
                  if (!asset) return;
                  setMoreOpen(false);
                  setLocalImageSizeOpen(true);
                }}
              >
                调整尺寸与超分
                <small>本地处理</small>
              </button>
            </div>
          )}
        </span>
      </nav>
      {approval && asset && (
        <ExternalTransferApproval
          action={approval}
          asset={asset}
          promptAsset={promptAsset}
          api={api}
          projectId={projectId}
          onCancel={() => setApproval(null)}
          onQueued={(result) => {
            if (approval === "model3d" && result.job?.job_id) {
              onModelJobQueued?.(result.job.job_id);
            }
            setApproval(null);
            onModeChange("task_waiting");
          }}
        />
      )}
      {localImageSizeOpen && asset && (
        <LocalImageSizeDialog
          asset={asset}
          api={api}
          projectId={projectId}
          onCancel={() => setLocalImageSizeOpen(false)}
          onCompleted={(assetId) => {
            setLocalImageSizeOpen(false);
            onLocalImageCompleted?.(assetId);
            onModeChange("image");
          }}
          onQueued={() => {
            setLocalImageSizeOpen(false);
            onModeChange("task_waiting");
          }}
        />
      )}
    </>
  );
}
