import {
  ArrowsOut,
  Crosshair,
  Cube,
  GitDiff,
  MagicWand,
  Selection,
  SquaresFour,
} from "@phosphor-icons/react";
import { useId, useState, type ReactNode } from "react";
import type {
  ApiClient,
  AssetDto,
  ToolResultDto,
  WorkspaceMode,
} from "../../shared/api/client";

type PaidAction = "variants" | "multiview" | "model3d";

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

export function CanvasContextToolbar({
  projectId,
  api,
  asset,
  promptAsset,
  referenceAvailable,
  onModeChange,
  onModelJobQueued,
}: {
  projectId: string;
  api: ApiClient;
  asset: AssetDto | null;
  promptAsset: AssetDto | null;
  referenceAvailable: boolean;
  onModeChange(mode: WorkspaceMode): void;
  onModelJobQueued?(jobId: string): void;
}) {
  const [approval, setApproval] = useState<PaidAction | null>(null);
  const [moreOpen, setMoreOpen] = useState(false);

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
                aria-disabled="true"
                title="放大处理需要先在设置中启用图片编辑服务。"
              >
                放大处理
                <small>需要图片编辑服务</small>
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
    </>
  );
}
