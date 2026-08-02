import { ImageSquare, WarningCircle } from "@phosphor-icons/react";
import { useState } from "react";
import type { ApiClient } from "../../shared/api/client";
import { HostClient } from "../../shared/host/client";
import "./import-image-action.css";

function requestId() { return `asset-import-${crypto.randomUUID()}`; }

/** Imports only through a native one-time capability; selected paths stay in the host. */
export function ImportImageAction({
  projectId,
  api,
  host = new HostClient(),
  onImported,
  label = "Choose image",
  className,
}: {
  projectId: string;
  api: ApiClient;
  host?: HostClient;
  onImported(): void;
  label?: string;
  className?: string;
}) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const importImage = async () => {
    setBusy(true); setError(null);
    try {
      const capabilityId = await host.chooseImportImage(projectId);
      if (!capabilityId) return;
      const request = requestId();
      const asset = await api.importImage(projectId, capabilityId, request);
      await api.setCurrentAsset(projectId, asset.id, requestId());
      onImported();
    } catch {
      setError("无法打开或导入图片，请重试。");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className={`import-image-action${className ? ` ${className}` : ""}`}>
      <button className="primary" type="button" onClick={() => void importImage()} disabled={busy}>
        <ImageSquare size={18} />
        {busy ? "正在打开…" : label}
      </button>
      {error && <p role="alert"><WarningCircle size={18} />{error}</p>}
    </div>
  );
}
