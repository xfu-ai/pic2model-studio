import {
  ArrowClockwise,
  CheckCircle,
  CircleNotch,
  Clock,
  File,
  ImageSquare,
  MagnifyingGlass,
  XCircle,
} from "@phosphor-icons/react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ApiClient, AssetDto, JobDto } from "../../shared/api/client";
import { HostClient } from "../../shared/host/client";
import {
  jobPresentation,
  jobSummary,
  resultActionLabel,
  taskTypeOptions,
} from "./jobPresentation";
import "./jobs-panel.css";

const terminal = new Set<JobDto["status"]>([
  "succeeded",
  "failed",
  "cancelled",
  "interrupted",
]);
const activeStatuses = new Set<JobDto["status"]>(["queued", "running", "waiting"]);

const statusLabels: Record<JobDto["status"], string> = {
  queued: "排队中",
  running: "处理中",
  waiting: "等待处理",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
};

const stageLabels: Record<string, string> = {
  queued: "已加入队列，等待开始",
  creating: "正在创建任务",
  remote_queued: "服务端正在排队",
  remote_running: "服务端正在处理",
  downloading: "正在取回生成结果",
  verifying: "正在检查结果",
  postprocessing: "正在整理结果",
  cancel_requested: "正在请求取消",
  stop_waiting: "正在停止本地等待",
};

type StatusFilter = "all" | "active" | "attention" | "completed";
type RetryApproval = { actionId: string; jobId: string };

function requestId() {
  return crypto.randomUUID();
}

function duration(value: number | null | undefined) {
  if (value == null) return "暂不提供";
  const minutes = Math.floor(value / 60);
  return minutes ? `${minutes} 分 ${value % 60} 秒` : `${value} 秒`;
}

function formatTime(value: string | null | undefined) {
  if (!value) return "暂不提供";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "暂不提供";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
}

function providerLabel(provider: string | null) {
  if (!provider || provider === "local") return "本地处理";
  if (provider === "image-generation/auto") return "自动生图路由";
  if (provider.startsWith("tripo3d/")) return "Tripo3D";
  if (provider.startsWith("meshy/")) return "Meshy";
  if (provider.startsWith("gemini/")) return "Gemini";
  return provider;
}

function compareExecutionTime(left: JobDto, right: JobDto) {
  const leftTime = Date.parse(left.created_at ?? "");
  const rightTime = Date.parse(right.created_at ?? "");
  const normalizedLeft = Number.isNaN(leftTime) ? 0 : leftTime;
  const normalizedRight = Number.isNaN(rightTime) ? 0 : rightTime;

  // Latest submissions first. Status must never affect a task's position.
  return normalizedRight - normalizedLeft || right.id.localeCompare(left.id);
}

function matchesStatus(job: JobDto, filter: StatusFilter) {
  if (filter === "active") return activeStatuses.has(job.status);
  if (filter === "attention") return job.status === "failed" || job.status === "interrupted";
  if (filter === "completed") {
    return job.status === "succeeded" || job.status === "cancelled";
  }
  return true;
}

function isImageAsset(asset: AssetDto) {
  return (
    asset.mime_type?.startsWith("image/") ||
    ["source_image", "generated_image", "candidate", "multiview", "preview"].includes(
      asset.asset_type,
    )
  );
}

function AssetThumb({
  projectId,
  api,
  asset,
}: {
  projectId: string;
  api: ApiClient;
  asset: AssetDto;
}) {
  const [url, setUrl] = useState("");
  const imageLike = isImageAsset(asset);

  useEffect(() => {
    if (!imageLike) return;
    let active = true;
    let objectUrl = "";
    const contentId = asset.thumbnail_asset_id ?? asset.id;
    void api
      .assetContent(projectId, contentId)
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        if (active) setUrl(objectUrl);
      })
      .catch(() => undefined);
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [api, asset.id, asset.thumbnail_asset_id, imageLike, projectId]);

  if (url) return <img src={url} alt="" />;
  return imageLike ? <ImageSquare size={20} aria-hidden="true" /> : <File size={20} aria-hidden="true" />;
}

function JobAssetStrip({
  label,
  ids,
  assets,
  projectId,
  api,
}: {
  label: string;
  ids: string[];
  assets: Map<string, AssetDto>;
  projectId: string;
  api: ApiClient;
}) {
  if (!ids.length) return null;
  return (
    <div className="job-assets">
      <span className="job-assets-label">{label}</span>
      <div className="job-asset-list">
        {ids.slice(0, 4).map((id) => {
          const asset = assets.get(id);
          return (
            <span className="job-asset" key={id} title={asset?.name ?? id}>
              {asset ? (
                <AssetThumb projectId={projectId} api={api} asset={asset} />
              ) : (
                <File size={20} aria-hidden="true" />
              )}
              <span>{asset?.name ?? `受管资产 ${id.slice(0, 8)}`}</span>
            </span>
          );
        })}
        {ids.length > 4 && <span className="job-asset-more">另有 {ids.length - 4} 项</span>}
      </div>
    </div>
  );
}

export function JobsPanel({
  projectId,
  api,
  showHistory = false,
  onOpenResult,
  host,
  dismissedJobIds = [],
  onDismiss,
}: {
  projectId: string;
  api: ApiClient;
  showHistory?: boolean;
  onOpenResult?(job: JobDto): void;
  host?: HostClient;
  dismissedJobIds?: string[];
  onDismiss?(ids: string[]): void;
}) {
  const [records, setRecords] = useState<JobDto[]>([]);
  const [assets, setAssets] = useState<AssetDto[]>([]);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [announcement, setAnnouncement] = useState("");
  const [retryApproval, setRetryApproval] = useState<RetryApproval | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const hostClient = useRef(host ?? new HostClient()).current;
  const known = useRef(new Set<string>());
  const initialized = useRef(false);

  const load = () => {
    void api
      .jobs(projectId, showHistory)
      .then(({ items }) => {
        const done = items.filter((job) => terminal.has(job.status));
        if (!initialized.current) {
          done.forEach((job) => known.current.add(job.id));
          initialized.current = true;
        } else {
          done
            .filter((job) => !known.current.has(job.id))
            .forEach((job) => {
              known.current.add(job.id);
              const title = jobPresentation(job).title;
              setAnnouncement(`${title}${job.status === "succeeded" ? "已完成" : "未完成"}`);
              void hostClient
                .notifyJobTerminal(
                  job.status as "succeeded" | "failed" | "cancelled" | "interrupted",
                )
                .catch(() => undefined);
            });
        }
        setRecords(items);
      })
      .catch(() => setRecords([]));
  };

  useEffect(() => {
    initialized.current = false;
    known.current.clear();
    load();
    const timer = window.setInterval(load, 2_500);
    return () => window.clearInterval(timer);
  }, [api, projectId, showHistory]);

  useEffect(() => {
    void api
      .assets(projectId)
      .then(setAssets)
      .catch(() => setAssets([]));
  }, [api, projectId, records]);

  const retry = async (jobId: string) => {
    setActionError(null);
    setActionBusy(true);
    try {
      const result = await api.retryJob(projectId, jobId, requestId());
      if (
        result.status === "awaiting_ui_action" &&
        result.ui_action?.action_id
      ) {
        setRetryApproval({ actionId: result.ui_action.action_id, jobId });
        return;
      }
      if (result.status !== "queued") {
        throw new Error(result.error?.user_message ?? "任务未能重新加入队列。");
      }
      load();
    } catch (error) {
      setActionError(
        error instanceof Error ? error.message : "重试任务失败，请稍后再试。",
      );
    } finally {
      setActionBusy(false);
    }
  };

  const decideRetry = async (approved: boolean) => {
    if (!retryApproval) return;
    setActionError(null);
    setActionBusy(true);
    try {
      const result = await api.decideApproval(
        projectId,
        retryApproval.actionId,
        approved,
        requestId(),
      );
      if (approved && result.status !== "queued") {
        throw new Error(result.error?.user_message ?? "任务未能重新加入队列。");
      }
      setRetryApproval(null);
      load();
    } catch (error) {
      setActionError(
        error instanceof Error ? error.message : "无法提交重试决定。",
      );
    } finally {
      setActionBusy(false);
    }
  };

  const assetMap = useMemo(
    () => new Map(assets.map((asset) => [asset.id, asset])),
    [assets],
  );
  const visibleRecords = records.filter((job) => !dismissedJobIds.includes(job.id));
  const counts = {
    active: visibleRecords.filter((job) => activeStatuses.has(job.status)).length,
    attention: visibleRecords.filter(
      (job) => job.status === "failed" || job.status === "interrupted",
    ).length,
    completed: visibleRecords.filter((job) => job.status === "succeeded").length,
  };
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const jobs = visibleRecords
    .filter((job) => matchesStatus(job, statusFilter))
    .filter((job) => typeFilter === "all" || job.job_type === typeFilter)
    .filter((job) => {
      if (!normalizedQuery) return true;
      const related = [...(job.input_asset_ids ?? []), ...job.output_asset_ids]
        .map((id) => assetMap.get(id)?.name ?? id)
        .join(" ");
      return `${jobPresentation(job).title} ${job.job_type ?? ""} ${related}`
        .toLocaleLowerCase()
        .includes(normalizedQuery);
    })
    .sort(compareExecutionTime);
  const removable = visibleRecords
    .filter((job) => terminal.has(job.status))
    .map((job) => job.id);
  const typeOptions = taskTypeOptions(visibleRecords);

  return (
    <section className="route-placeholder jobs-panel" aria-labelledby="task-center-title">
      <p className="jobs-live-region" aria-live="polite" aria-atomic="true">
        {announcement}
      </p>
      <header className="jobs-header">
        <div>
          <p className="eyebrow">任务中心</p>
          <h1 id="task-center-title">后台任务</h1>
          <p>查看后台进度、处理异常，并从完成的任务继续工作。</p>
        </div>
        <div className="jobs-header-actions">
          <span className="jobs-refresh">
            <Clock size={16} aria-hidden="true" />每 2.5 秒更新
          </span>
          {removable.length > 0 && (
            <button type="button" onClick={() => onDismiss?.(removable)}>
              隐藏已结束任务
            </button>
          )}
        </div>
      </header>

      <div className="jobs-overview" aria-label="任务概览">
        <button
          type="button"
          aria-pressed={statusFilter === "active"}
          onClick={() => setStatusFilter(statusFilter === "active" ? "all" : "active")}
        >
          <strong>{counts.active}</strong>
          <span>进行中</span>
        </button>
        <button
          type="button"
          aria-pressed={statusFilter === "attention"}
          onClick={() =>
            setStatusFilter(statusFilter === "attention" ? "all" : "attention")
          }
        >
          <strong>{counts.attention}</strong>
          <span>需要处理</span>
        </button>
        <button
          type="button"
          aria-pressed={statusFilter === "completed"}
          onClick={() =>
            setStatusFilter(statusFilter === "completed" ? "all" : "completed")
          }
        >
          <strong>{counts.completed}</strong>
          <span>已完成</span>
        </button>
      </div>

      <div className="jobs-filters">
        <label className="jobs-search">
          <span className="jobs-live-region">搜索任务或资产</span>
          <MagnifyingGlass size={18} aria-hidden="true" />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索任务或资产"
          />
        </label>
        <label>
          <span>任务类型</span>
          <select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
            <option value="all">全部类型</option>
            {typeOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {actionError && (
        <p className="job-error" role="alert">
          {actionError}
        </p>
      )}

      {jobs.length ? (
        <div className="job-list">
          {jobs.map((job) => {
            const presentation = jobPresentation(job);
            const done = terminal.has(job.status);
            const canRetry =
              ["failed", "cancelled", "interrupted"].includes(job.status) &&
              job.error?.safe_to_retry === true;
            return (
              <article key={job.id} className={`job-card status-${job.status}`}>
                <div className="job-main">
                  <div className="job-card-heading">
                    <div>
                      <p className="job-status">
                        <span className={`status-dot ${job.status}`} aria-hidden="true" />
                        {statusLabels[job.status]}
                      </p>
                      <h2>{presentation.title}</h2>
                    </div>
                    {job.status === "succeeded" && (
                      <CheckCircle
                        size={24}
                        weight="fill"
                        className="job-complete"
                        aria-label="任务已完成"
                      />
                    )}
                  </div>

                  <p className="job-summary">{jobSummary(job)}</p>

                  {!done && (
                    <>
                      <p className="job-stage" role="status">
                        当前进度：
                        <strong>{stageLabels[job.stage] ?? "正在处理此任务"}</strong>
                      </p>
                      {job.progress != null && (
                        <progress value={job.progress} max="100" aria-label="任务进度" />
                      )}
                    </>
                  )}

                  <JobAssetStrip
                    label="输入"
                    ids={job.input_asset_ids ?? []}
                    assets={assetMap}
                    projectId={projectId}
                    api={api}
                  />
                  <JobAssetStrip
                    label="输出"
                    ids={job.output_asset_ids}
                    assets={assetMap}
                    projectId={projectId}
                    api={api}
                  />

                  {job.status === "succeeded" && !job.output_asset_ids.length && (
                    <p className="job-result-ready">任务已完成，没有生成新的受管资产。</p>
                  )}
                  {!done && (job.provider?.startsWith("meshy/") || job.provider === "image-generation/auto") && (
                    <p className="job-result-pending">
                      “停止本地等待”只停止本机继续查询，不会撤销已提交的 Tripo3D / Meshy
                      远端任务或已产生的用量。
                    </p>
                  )}
                  {job.error?.recommended_action === "retry" && (
                    <p className="job-recommendation">建议操作：确认服务状态后创建一项新任务。</p>
                  )}

                  <details>
                    <summary>查看技术详情</summary>
                    <dl>
                      <dt>任务类型</dt>
                      <dd>{job.job_type ?? "未标注"}</dd>
                      <dt>处理阶段</dt>
                      <dd>{job.stage}</dd>
                      <dt>服务</dt>
                      <dd>{providerLabel(job.provider)}</dd>
                      <dt>创建时间</dt>
                      <dd>{formatTime(job.created_at)}</dd>
                      <dt>完成时间</dt>
                      <dd>{formatTime(job.completed_at)}</dd>
                      <dt>已用时间</dt>
                      <dd>{duration(job.elapsed_seconds)}</dd>
                      <dt>预计时间</dt>
                      <dd>{duration(job.estimated_seconds)}</dd>
                      {job.error?.code && (
                        <>
                          <dt>错误代码</dt>
                          <dd>{job.error.code}</dd>
                          {job.error.failed_step && (
                            <>
                              <dt>失败步骤</dt>
                              <dd>{job.error.failed_step}</dd>
                            </>
                          )}
                        </>
                      )}
                    </dl>
                  </details>
                </div>

                <div className="job-actions">
                  {job.status === "succeeded" &&
                    job.output_asset_ids.length > 0 &&
                    onOpenResult && (
                      <button
                        type="button"
                        className="primary job-result-action"
                        onClick={() => onOpenResult(job)}
                      >
                        {resultActionLabel(job)}
                      </button>
                    )}
                  {canRetry && (
                    <button
                      title="创建新任务"
                      aria-label="创建新任务"
                      disabled={actionBusy}
                      onClick={() => void retry(job.id)}
                    >
                      <ArrowClockwise size={20} />
                    </button>
                  )}
                  {activeStatuses.has(job.status) && (
                    <button
                      title={
                        job.provider?.startsWith("meshy/") || job.provider === "image-generation/auto"
                          ? "停止本地等待（不会撤销远端生图任务）"
                          : "取消任务"
                      }
                      aria-label={
                        job.provider?.startsWith("meshy/") || job.provider === "image-generation/auto" ? "停止本地等待" : "取消任务"
                      }
                      disabled={actionBusy}
                      onClick={() =>
                        void api.cancelJob(projectId, job.id, requestId()).then(load)
                      }
                    >
                      <XCircle size={20} />
                    </button>
                  )}
                  {done && (
                    <button type="button" onClick={() => onDismiss?.([job.id])}>
                      从列表隐藏
                    </button>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      ) : (
        <p className="jobs-empty">
          <CircleNotch size={16} aria-hidden="true" />
          {visibleRecords.length
            ? "没有符合当前筛选条件的任务。"
            : "当前还没有后台任务。"}
        </p>
      )}

      {retryApproval && (
        <div className="dialog-backdrop external-transfer-backdrop">
          <section
            className="dialog external-transfer-dialog"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="retry-approval-title"
          >
            <h2 id="retry-approval-title">确认创建新的外部任务</h2>
            <p>这会保留失败记录，并创建一项新的任务再次调用外部 AI 服务；可能产生新的用量或费用。</p>
            <div className="dialog-actions">
              <button
                type="button"
                disabled={actionBusy}
                onClick={() => void decideRetry(false)}
              >
                不创建
              </button>
              <button
                type="button"
                className="primary"
                disabled={actionBusy}
                onClick={() => void decideRetry(true)}
              >
                确认并创建新任务
              </button>
            </div>
          </section>
        </div>
      )}
    </section>
  );
}
