import { Cube, WarningCircle } from "@phosphor-icons/react";
import { useState } from "react";
import type { ApiClient } from "../../shared/api/client";
import { HostClient } from "../../shared/host/client";
import "./import-image-action.css";

function requestId() { return `asset-import-${crypto.randomUUID()}`; }
export function ImportGlbAction({ projectId, api, host = new HostClient(), onImported }: { projectId: string; api: ApiClient; host?: HostClient; onImported(): void }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const importGlb = async () => { setBusy(true); setError(null); try { const capabilityId = await host.chooseImportGlb(projectId); if (!capabilityId) return; const asset = await api.importGlb(projectId, capabilityId, requestId()); await api.setCurrentAsset(projectId, asset.id, requestId()); onImported(); } catch { setError("The GLB could not be imported. No asset was created if validation failed."); } finally { setBusy(false); } };
  return <div className="import-image-action"><button className="primary" onClick={() => void importGlb()} disabled={busy}><Cube size={18} />Choose GLB</button>{error && <p role="alert"><WarningCircle size={18} />{error}</p>}</div>;
}
