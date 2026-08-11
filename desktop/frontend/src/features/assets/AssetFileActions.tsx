import {
  Check,
  Export,
  FolderOpen,
  SpinnerGap,
  WarningCircle,
} from "@phosphor-icons/react";
import { useState } from "react";
import type { ApiClient, AssetDto } from "../../shared/api/client";
import { HostClient } from "../../shared/host/client";
import "./asset-file-actions.css";

type ActionState = "opening" | "exporting" | null;
type Feedback = { kind: "success" | "error" | "neutral"; text: string } | null;

function requestId(prefix: string) {
  return `${prefix}-${crypto.randomUUID()}`;
}

export function AssetFileActions({
  projectId,
  asset,
  api,
  host = new HostClient(),
}: {
  projectId: string;
  asset: AssetDto;
  api: ApiClient;
  host?: Pick<HostClient, "chooseExportDirectory">;
}) {
  const [busy, setBusy] = useState<ActionState>(null);
  const [feedback, setFeedback] = useState<Feedback>(null);

  const openDirectory = async () => {
    setBusy("opening");
    setFeedback(null);
    try {
      await api.revealAsset(projectId, asset.id, requestId("asset-reveal"));
      setFeedback({ kind: "success", text: "已打开资产所在目录。" });
    } catch {
      setFeedback({ kind: "error", text: "无法打开资产所在目录，请重试。" });
    } finally {
      setBusy(null);
    }
  };

  const exportAsset = async () => {
    setBusy("exporting");
    setFeedback(null);
    try {
      const capability = await host.chooseExportDirectory(projectId);
      if (!capability) {
        setFeedback({ kind: "neutral", text: "已取消导出。" });
        return;
      }
      const result = await api.exportAsset(
        projectId,
        asset.id,
        capability,
        requestId("asset-export"),
      );
      setFeedback({ kind: "success", text: `已导出 ${result.name}。` });
    } catch {
      setFeedback({ kind: "error", text: "资源导出失败，项目内文件未受影响。" });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="asset-file-actions">
      <div className="asset-file-action-buttons">
        <button
          type="button"
          aria-label="打开目录"
          disabled={busy !== null}
          onClick={() => void openDirectory()}
        >
          {busy === "opening" ? <SpinnerGap className="spin" size={16} /> : <FolderOpen size={16} />}
          {busy === "opening" ? "正在打开" : "打开目录"}
        </button>
        <button
          type="button"
          aria-label="导出资源"
          disabled={busy !== null}
          onClick={() => void exportAsset()}
        >
          {busy === "exporting" ? <SpinnerGap className="spin" size={16} /> : <Export size={16} />}
          {busy === "exporting" ? "正在导出" : "导出"}
        </button>
      </div>
      {feedback && (
        <span
          className={`asset-file-feedback ${feedback.kind}`}
          role={feedback.kind === "error" ? "alert" : "status"}
        >
          {feedback.kind === "error"
            ? <WarningCircle size={15} />
            : feedback.kind === "success"
              ? <Check size={15} />
              : null}
          {feedback.text}
        </span>
      )}
    </div>
  );
}
