import { Cube, ImageSquare, WarningCircle } from "@phosphor-icons/react";
import { useState, type DragEvent } from "react";
import type { ApiClient } from "../../shared/api/client";
import { HostClient } from "../../shared/host/client";
import "./import-drop-zone.css";

function requestId() { return `asset-drop-${crypto.randomUUID()}`; }
function kindFor(file: File): "source_image" | "glb" | null {
  const extension = file.name.split(".").pop()?.toLowerCase();
  if (["png", "jpg", "jpeg", "webp"].includes(extension ?? "")) return "source_image";
  return extension === "glb" ? "glb" : null;
}

/** Browser drag events expose file bytes/name only; the native host owns the staging path. */
export function ImportDropZone({ projectId, api, host = new HostClient(), onImported }: { projectId: string; api: ApiClient; host?: HostClient; onImported(kind: "source_image" | "glb"): void }) {
  const [active, setActive] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const drop = async (event: DragEvent<HTMLElement>) => {
    event.preventDefault(); setActive(false); setMessage(null);
    const files = [...event.dataTransfer.files];
    if (files.length !== 1) { setMessage("Drop one PNG, JPG, WEBP, or GLB file at a time."); return; }
    const file = files[0]; const kind = kindFor(file);
    if (!kind) { setMessage("Only PNG, JPG, WEBP, or GLB files are supported."); return; }
    setBusy(true);
    try {
      const bytes = Array.from(new Uint8Array(await file.arrayBuffer()));
      const capabilityId = await host.stageDroppedFile(projectId, kind, file.name, bytes);
      const asset = kind === "glb" ? await api.importGlb(projectId, capabilityId, requestId()) : await api.importImage(projectId, capabilityId, requestId());
      await api.setCurrentAsset(projectId, asset.id, requestId());
      onImported(kind);
    } catch { setMessage("The dropped file could not be imported. No asset was created if validation failed."); } finally { setBusy(false); }
  };
  return <section className={`import-drop-zone${active ? " active" : ""}`} aria-label="Import file drop area" onDragOver={(event) => { event.preventDefault(); setActive(true); }} onDragLeave={() => setActive(false)} onDrop={(event) => void drop(event)}>
    <ImageSquare size={24} /><Cube size={24} /><strong>{busy ? "Importing file…" : "Drop an image or GLB here"}</strong><span>PNG, JPG, WEBP, or GLB. File locations are kept in the native host.</span>{message && <p role="alert"><WarningCircle size={18} />{message}</p>}
  </section>;
}
