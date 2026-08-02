import { DownloadSimple, FileText, WarningCircle } from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import type { ApiClient, DiagnosticsPreviewDto } from "../../shared/api/client";
import { HostClient } from "../../shared/host/client";

function requestId() { return `diagnostics-${crypto.randomUUID()}`; }
function displayBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / 1024 / 1024).toFixed(1)} MiB`;
}

export function DiagnosticsPanel({ projectId, api, host = new HostClient(), onClose }: { projectId: string; api: ApiClient; host?: HostClient; onClose(): void }) {
  const [preview, setPreview] = useState<DiagnosticsPreviewDto | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const load = async () => {
    try { setPreview(await api.diagnosticsPreview(projectId, requestId())); setMessage(""); }
    catch { setMessage("无法生成诊断预览。请确认项目仍可访问。"); }
  };
  useEffect(() => { void load(); }, [api, projectId]);
  const exportBundle = async () => {
    if (!preview) return;
    setBusy(true); setMessage("");
    try {
      const capability = await host.chooseDiagnosticsExportDirectory(projectId);
      if (!capability) return;
      const result = await api.exportDiagnostics(projectId, capability, preview.manifest_hash, requestId());
      setMessage(`诊断包已导出：${result.path}`);
    } catch {
      setMessage("诊断包未导出。预览可能已过期，请刷新后重试。");
      await load();
    } finally { setBusy(false); }
  };
  return <div className="dialog-backdrop">
    <section className="dialog diagnostics-panel" role="dialog" aria-modal="true" aria-labelledby="diagnostics-title">
      <p className="eyebrow">Support bundle</p><h2 id="diagnostics-title">导出诊断支持包</h2>
      <p>遇到运行异常时，可导出脱敏日志并提交给开发人员排查。本功能不会自动检测或修复问题，也不会导出 Provider 密钥、会话令牌或绝对路径。</p>
      {preview && <><p><FileText size={17} /> {preview.manifest.files.length} 个日志文件，{displayBytes(preview.estimated_size)}</p><details><summary>查看将要导出的文件</summary><ul>{preview.manifest.files.map((file) => <li key={file.name}>{file.name} · {displayBytes(file.size)}</li>)}</ul><small>清单校验：{preview.manifest_hash}</small></details></>}
      {message && <p role="status"><WarningCircle size={18} />{message}</p>}
      <div className="dialog-actions"><button disabled={busy} onClick={onClose}>关闭</button>{preview && <><button disabled={busy} onClick={() => void load()}>刷新预览</button><button className="primary" disabled={busy} onClick={() => void exportBundle()}><DownloadSimple size={18} />导出支持包</button></>}</div>
    </section>
  </div>;
}
