import { MonitorArrowUp, WarningCircle } from "@phosphor-icons/react";
import { useState } from "react";
import type { ApiClient } from "../../shared/api/client";
import { HostClient } from "../../shared/host/client";
import "./import-image-action.css";

function requestId() { return `screen-capture-${crypto.randomUUID()}`; }

export function CaptureScreenAction({ projectId, api, host = new HostClient(), onImported }: { projectId: string; api: ApiClient; host?: HostClient; onImported(): void }) {
  const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null);
  const capture = async () => {
    setBusy(true);
    setError(null);
    try {
      // Let React paint the progress state before the native host briefly hides
      // this window so the workbench is not included in the desktop snapshot.
      await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()));
      const capabilityId = await host.captureScreen(projectId);
      const asset = await api.importImage(projectId, capabilityId, requestId());
      await api.setCurrentAsset(projectId, asset.id, requestId());
      onImported();
    } catch (unknownError) {
      const detail = unknownError instanceof Error ? unknownError.message : String(unknownError);
      setError(detail.toLowerCase().includes("cancel")
        ? "已取消截屏。"
        : "未能完成框选截屏，应用窗口应已自动恢复；请重试。");
    } finally {
      setBusy(false);
    }
  };
  return <div className="import-image-action"><button type="button" onClick={() => void capture()} disabled={busy} title="窗口会短暂隐藏，随后在桌面遮罩上拖框选择区域"><MonitorArrowUp size={18} />{busy ? "正在框选截屏…" : "框选截屏"}</button>{busy && <p role="status">请在屏幕遮罩上拖框；Esc 可取消。</p>}{error && <p role="alert"><WarningCircle size={18} />{error}</p>}</div>;
}
