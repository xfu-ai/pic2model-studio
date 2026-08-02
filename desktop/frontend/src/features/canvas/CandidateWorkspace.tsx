import { ArrowLeft, CheckCircle } from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import type { ApiClient, AssetDto } from "../../shared/api/client";
import "./candidate-workspace.css";

function requestId() {
  return crypto.randomUUID();
}

export function CandidateWorkspace({
  projectId,
  api,
  assetIds,
  sourceJobId,
  onSelected,
  onBackToTasks,
}: {
  projectId: string;
  api: ApiClient;
  assetIds?: string[];
  sourceJobId?: string | null;
  onSelected(): void;
  onBackToTasks?(): void;
}) {
  const [items, setItems] = useState<AssetDto[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    void api
      .assets(projectId)
      .then((assets) => {
        if (assetIds?.length) {
          const byId = new Map(assets.map((asset) => [asset.id, asset]));
          setItems(
            assetIds
              .map((id) => byId.get(id))
              .filter((asset): asset is AssetDto => Boolean(asset)),
          );
          return;
        }
        setItems(
          assets.filter((asset) =>
            ["generated_image", "candidate", "multiview"].includes(asset.asset_type),
          ),
        );
      })
      .catch(() => setError("无法加载候选资产。"));
  }, [api, assetIds, projectId]);

  const choose = async (asset: AssetDto) => {
    try {
      await api.setCurrentAsset(projectId, asset.id, requestId());
      onSelected();
    } catch {
      setError("无法设为当前版本。");
    }
  };

  return (
    <section
      className="candidate-workspace"
      aria-labelledby="workspace-title"
      data-result-scope={sourceJobId ? "job" : "project"}
    >
      <header>
        <div>
          <p className="eyebrow">Candidates</p>
          <h1 id="workspace-title" tabIndex={-1}>
            选择下一步使用的候选图
          </h1>
          <p>
            {sourceJobId
              ? `这里只显示任务 ${sourceJobId.slice(0, 8)} 生成的结果；选择后才会设为当前版本。`
              : "候选图是受管资产；选择后可以继续建模主体提取、制作三视图或发给 Agent。"}
          </p>
        </div>
        {onBackToTasks && (
          <button type="button" onClick={onBackToTasks}>
            <ArrowLeft size={17} aria-hidden="true" />
            返回任务中心
          </button>
        )}
      </header>
      {error && (
        <p className="candidate-error" role="alert">
          {error}
        </p>
      )}
      <div className="candidate-grid">
        {items.map((asset) => (
          <CandidateCard
            key={asset.id}
            projectId={projectId}
            api={api}
            asset={asset}
            onChoose={() => void choose(asset)}
          />
        ))}
      </div>
      {!items.length && !error && (
        <p>
          {assetIds?.length
            ? "这个任务的结果已不可用。可以返回任务中心查看技术详情。"
            : "当前没有可选候选图。先在创意图生成页创建候选。"}
        </p>
      )}
    </section>
  );
}

function CandidateCard({
  projectId,
  api,
  asset,
  onChoose,
}: {
  projectId: string;
  api: ApiClient;
  asset: AssetDto;
  onChoose(): void;
}) {
  const [url, setUrl] = useState("");

  useEffect(() => {
    let current = true;
    let objectUrl = "";
    void api
      .assetContent(projectId, asset.thumbnail_asset_id ?? asset.id)
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        if (current) setUrl(objectUrl);
      })
      .catch(() => undefined);
    return () => {
      current = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [api, asset.id, asset.thumbnail_asset_id, projectId]);

  return (
    <article className={asset.is_current ? "candidate-card selected" : "candidate-card"}>
      {url && <img src={url} alt={asset.name} />}
      <div>
        <strong>{asset.name}</strong>
        <span>版本 {asset.version_no ?? "—"}</span>
        <button className="primary" disabled={asset.is_current} onClick={onChoose}>
          <CheckCircle size={17} aria-hidden="true" />
          {asset.is_current ? "当前版本" : "选择此候选"}
        </button>
      </div>
    </article>
  );
}
