import { CircleNotch, CloudSlash, WarningCircle, X } from "@phosphor-icons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ErrorBoundary } from "./ErrorBoundary";
import { useFocusTrap } from "../shared/a11y/useFocusTrap";
import { ApiClient, type RendererSession } from "../shared/api/client";
import { PanelLayout } from "../features/shell/PanelLayout";
import { ProjectLauncher } from "../features/projects/ProjectLauncher";
import { DEFAULT_WORKSPACE, parseWorkspaceState, WorkspaceStore } from "../shared/state/uiStore";
import type { ProjectDto, WorkspaceState } from "../shared/api/client";
import "../features/shell/shell.css";
import "./app-shell.css";
import "../features/model/model-viewport.css";

type AppState = "loading" | "ready" | "offline" | "error";
type Toast = { id: number; tone: "success" | "warning"; text: string };

declare global { interface Window { __TAURI__?: unknown; } }

async function readSession(): Promise<RendererSession | null> {
  const tauri = await import("@tauri-apps/api/core").catch(() => null);
  if (!tauri) return null;
  return tauri.invoke<RendererSession>("get_renderer_session").catch(() => null);
}

function ConfirmDialog({ open, onCancel, onConfirm }: { open: boolean; onCancel(): void; onConfirm(): void }) {
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(open, ref);
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") onCancel(); };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onCancel]);
  if (!open) return null;
  return <div className="dialog-backdrop" role="presentation"><div ref={ref} className="dialog" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title"><h2 id="confirm-title">确认离开当前工作区？</h2><p>正在编辑的本地草稿会自动保存。</p><div className="dialog-actions"><button onClick={onCancel}>继续编辑</button><button className="primary" onClick={onConfirm}>确认离开</button></div></div></div>;
}

function ShellContent() {
  const [state, setState] = useState<AppState>("loading");
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [confirm, setConfirm] = useState(false);
  const [connectionAttempt, setConnectionAttempt] = useState(0);
  const [workspace, setWorkspace] = useState<WorkspaceState>(DEFAULT_WORKSPACE);
  const [project, setProject] = useState<ProjectDto | null>(null);
  const [api, setApi] = useState<ApiClient | null>(null);
  const store = useRef<WorkspaceStore | null>(null);
  const patchWorkspace = useCallback((patch: Partial<WorkspaceState>) => {
    store.current?.update(patch);
  }, []);
  const addToast = (tone: Toast["tone"], text: string) => setToasts((items) => [...items, { id: Date.now(), tone, text }]);
  const activateProject = (nextProject: ProjectDto) => {
    if (!api) return;
    store.current?.dispose();
    const nextStore = new WorkspaceStore(parseWorkspaceState(nextProject.workspace_state_json), nextProject.id, api);
    store.current = nextStore;
    setWorkspace(nextStore.snapshot());
    setProject(nextProject);
  };
  useEffect(() => {
    let alive = true;
    readSession().then(async (session) => {
      if (!session) { if (alive) setState("offline"); return; }
      try {
        const nextApi = new ApiClient(session);
        await nextApi.health();
        if (alive) { setApi(nextApi); setState("ready"); }
      }
      catch { if (alive) setState("offline"); }
    }).catch(() => { if (alive) setState("error"); });
    return () => { alive = false; store.current?.dispose(); };
  }, [connectionAttempt]);
  useEffect(() => {
    const unsubscribe = store.current?.subscribe(setWorkspace);
    return () => { unsubscribe?.(); };
  }, [project]);
  if (state === "ready" && !project) {
    if (api) return <ProjectLauncher api={api} onProject={activateProject} />;
    return <main className="app-recovery" role="status"><p>Preparing the local project service.</p></main>;
  }
  if (state === "ready" && project && api) return <PanelLayout state={workspace} projectId={project.id} projectName={project.name} readOnly={project.root_state === "read_only"} project={project} api={api} onProject={activateProject} onPatch={patchWorkspace} />;
  if (state === "ready") return <PanelLayout state={workspace} projectName="尚未打开项目" readOnly={false} onPatch={patchWorkspace} />;
  return <main className="app-shell">
    <header className="boot-topbar"><div className="brand-mark" aria-hidden="true" /><strong>AIPicToModel</strong><span className="boot-caption">桌面工作台</span></header>
    <section className="boot-content" aria-live="polite">
      {state === "loading" && <><CircleNotch size={28} aria-hidden="true" className="spin" /><h1>正在连接本地工作台</h1><p>正在验证受保护的本地服务。</p></>}
      {state === "offline" && <><CloudSlash size={28} aria-hidden="true" className="warning" /><h1>本地服务暂时不可用</h1><p>你仍可查看已打开的本地内容。需要服务的操作将在恢复后继续可用。</p><button className="primary" onClick={() => { setState("loading"); setConnectionAttempt((attempt) => attempt + 1); }}>重新连接</button></>}
      {state === "error" && <><WarningCircle size={28} aria-hidden="true" className="danger" /><h1>工作台需要恢复</h1><p>服务启动未完成；请查看诊断后重试。</p><button className="primary" onClick={() => { setState("loading"); setConnectionAttempt((attempt) => attempt + 1); }}>重新尝试</button></>}
    </section>
    <button className="leave-button" onClick={() => setConfirm(true)}>测试确认对话框</button>
    <div className="toast-center" aria-live="polite">{toasts.map((toast) => <div className={`toast ${toast.tone}`} key={toast.id}><span>{toast.text}</span><button aria-label="关闭提示" onClick={() => setToasts((items) => items.filter((item) => item.id !== toast.id))}><X size={16} /></button></div>)}</div>
    <ConfirmDialog open={confirm} onCancel={() => setConfirm(false)} onConfirm={() => { setConfirm(false); addToast("warning", "离开操作已确认。") }} />
  </main>;
}

export function AppShell() { return <ErrorBoundary><ShellContent /></ErrorBoundary>; }
