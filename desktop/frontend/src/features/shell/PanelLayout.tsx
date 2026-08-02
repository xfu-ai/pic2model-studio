import { CaretLeft, CaretRight, SidebarSimple } from "@phosphor-icons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { AssetBrowser } from "../assets/AssetBrowser";
import { ProjectPackageActions } from "../projects/ProjectPackageActions";
import type { ApiClient, JobDto, ProjectDto, WorkspaceState } from "../../shared/api/client";
import { Navigation, type PrimaryRoute } from "./Navigation";
import { TaskBar } from "./TaskBar";
import { TopBar } from "./TopBar";
import { WorkspaceRouter } from "./WorkspaceRouter";
import { AgentPanel, type AgentWorkspaceAction } from "../agent/AgentPanel";
import { SettingsDialog } from "../settings/SettingsDialog";
import { JobsPanel } from "../jobs/JobsPanel";
import { jobPresentation } from "../jobs/jobPresentation";
import { HostClient } from "../../shared/host/client";
import { DiagnosticsPanel } from "../diagnostics/DiagnosticsPanel";
import { WorkflowSwitcher, type WorkflowMode } from "./WorkflowSwitcher";

const defaultWidth = 400;
export function PanelLayout({ state, projectId, projectName, readOnly, project, api, onProject, onPatch }: { state: WorkspaceState; projectId?: string; projectName: string; readOnly: boolean; project?: ProjectDto; api?: ApiClient; onProject?(project: ProjectDto): void; onPatch(patch: Partial<WorkspaceState>): void }) {
  const [route, setRoute] = useState<PrimaryRoute>("workspace");
  const [activeWorkflowMode, setActiveWorkflowMode] = useState(state.workspace_mode);
  const [currentAssetName, setCurrentAssetName] = useState<string | null>(null);
  const [focusedAssetId, setFocusedAssetId] = useState<string | null>(null);
 const [settingsOpen, setSettingsOpen] = useState(false);
 const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
 const [candidateResult, setCandidateResult] = useState<{ jobId: string; assetIds: string[] } | null>(null);
  const drag = useRef(false);
  const workspaceContextsRef = useRef(state.workflow_contexts);
  const referenceContextRef = useRef(state.reference_context);
  workspaceContextsRef.current = state.workflow_contexts;
  referenceContextRef.current = state.reference_context;
  const patchWorkflowContexts = useCallback((patch: Partial<WorkspaceState["workflow_contexts"]>) => {
    onPatch({ workflow_contexts: { ...workspaceContextsRef.current, ...patch } });
  }, [onPatch]);
  const patchReferenceContext = useCallback((patch: Partial<WorkspaceState["reference_context"]>) => {
    onPatch({ reference_context: { ...referenceContextRef.current, ...patch } });
  }, [onPatch]);
  const refreshCurrentAsset = () => {
    if (!projectId || !api) { setCurrentAssetName(null); return; }
    void api.assets(projectId).then((assets) => setCurrentAssetName(assets.find((asset) => asset.is_current)?.name ?? null)).catch(() => setCurrentAssetName(null));
  };
 const focusWorkspaceHeading = () => {
  window.requestAnimationFrame(() => {
   window.requestAnimationFrame(() => {
    const heading = document.querySelector<HTMLElement>(".workspace-region h1");
    if (!heading) return;
    heading.tabIndex = -1;
    heading.focus();
   });
  });
 };
 const openJobResult = (job: JobDto) => {
  const assetIds = job.output_asset_ids;
  const jobId = job.id;
  if (!projectId || !api || !assetIds[0]) return;
  const resultKind = jobPresentation(job).resultKind;
  if (resultKind === "candidates") {
   setCandidateResult({ jobId, assetIds });
   setRoute("workspace");
   setActiveWorkflowMode("candidate");
   onPatch({ workspace_mode: "candidate", focus_target: "workspace-title" });
   focusWorkspaceHeading();
   return;
  }
  if (resultKind === "fbx" || resultKind === "asset") {
   setFocusedAssetId(assetIds[0]);
   setRoute("assets");
   return;
  }
  if (resultKind === "model3d") {
   const model3d = { ...workspaceContextsRef.current.model3d, asset_id: assetIds[0], generation_job_id: null };
   setRoute("workspace");
   setActiveWorkflowMode("model3d");
   onPatch({
    workspace_mode: "model3d",
    focus_target: "workspace-title",
    workflow_contexts: { ...workspaceContextsRef.current, model3d },
   });
   focusWorkspaceHeading();
   return;
  }
  if (resultKind === "multiview") {
   const multiview = {
    ...workspaceContextsRef.current.multiview,
    selected: { ...workspaceContextsRef.current.multiview.selected, source: assetIds[0] },
    job_id: jobId,
   };
   setRoute("workspace");
   setActiveWorkflowMode("multiview");
   onPatch({
    workspace_mode: "multiview",
    focus_target: "workspace-title",
    workflow_contexts: { ...workspaceContextsRef.current, multiview },
   });
   focusWorkspaceHeading();
   return;
  }
    void api.assets(projectId).then(async (assets) => {
      const result = assets.find((asset) => asset.id === assetIds[0]);
      if (!result) return;
      if (result.asset_type === "fbx") {
        const capability = await new HostClient().chooseExportDirectory(projectId);
        if (capability) await api.exportAsset(projectId, result.id, capability, crypto.randomUUID());
        return;
      }
   if (resultKind === "target_extract") {
        const parameters = result.provenance?.parameters;
        const splitMode = parameters && typeof parameters === "object"
          ? (parameters as Record<string, unknown>).split_mode
          : undefined;
        const current = workspaceContextsRef.current.target_extract;
        const method: "direct" | "breakdown" = splitMode === "boxsplit" || (splitMode == null && current.job_id === jobId && current.method === "direct")
          ? "direct"
          : "breakdown";
        const target_extract = method === "direct"
          ? { ...current, method, stage: "result" as const, result_asset_ids: assetIds, active_result_asset_id: assetIds[0], job_id: jobId ?? current.job_id, pending_action_id: null }
          : { ...current, method, stage: "select_breakdown_part" as const, breakdown_asset_id: assetIds[0], breakdown_selection_id: null, breakdown_selection_rect: null, job_id: jobId ?? current.job_id, pending_action_id: null };
        setRoute("workspace");
        setActiveWorkflowMode("target_extract");
        onPatch({
          workspace_mode: "target_extract",
          focus_target: "workspace-title",
          workflow_contexts: { ...workspaceContextsRef.current, target_extract },
        });
        return;
      }
      const workspace_mode = result.asset_type === "glb" ? "model3d"
        : result.asset_type === "multiview" ? "multiview"
          : result.asset_type === "generated_image" ? "candidate"
            : ["prompt", "analysis"].includes(result.asset_type) ? "compare"
              : "image";
      const needsCurrentAsset = !["prompt", "analysis"].includes(result.asset_type);
      if (needsCurrentAsset) {
        await api.setCurrentAsset(projectId, result.id, crypto.randomUUID());
        refreshCurrentAsset();
      }
      setRoute("workspace");
      setActiveWorkflowMode(workspace_mode);
      onPatch({ workspace_mode, focus_target: "workspace-title" });
    }).catch(() => undefined);
  };
  useEffect(refreshCurrentAsset, [api, projectId]);
  useEffect(() => { setActiveWorkflowMode(state.workspace_mode); }, [state.workspace_mode]);
  useEffect(() => { const move = (event: MouseEvent) => { if (drag.current) onPatch({ agent_panel_width: Math.min(520, Math.max(360, window.innerWidth - event.clientX)) }); }; const up = () => { drag.current = false; }; window.addEventListener("mousemove", move); window.addEventListener("mouseup", up); return () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); }; }, [onPatch]);
  const collapsed = state.agent_panel_collapsed;
  const placeholder = route === "tasks" ? "Task center" : "Export";
  const handleImported = (workspace_mode: "image" | "model3d") => { refreshCurrentAsset(); onPatch({ workspace_mode, focus_target: "workspace-title" }); };
  const openWorkflow = (workspace_mode: WorkflowMode) => {
    setRoute("workspace");
    setActiveWorkflowMode(workspace_mode);
    onPatch({ workspace_mode, focus_target: "workspace-title" });
  };
  const continueToMultiview = (assetId: string) => {
    const multiview = {
      ...workspaceContextsRef.current.multiview,
      selected: { source: assetId },
      regions: {},
      checks: {},
      quality_confirmed: false,
      set_id: null,
      job_id: null,
    };
    setRoute("workspace");
    setActiveWorkflowMode("multiview");
    onPatch({
      workspace_mode: "multiview",
      focus_target: "workspace-title",
      workflow_contexts: { ...workspaceContextsRef.current, multiview },
    });
  };
  const openAgentWorkspace = (action: AgentWorkspaceAction) => {
    setRoute("workspace");
    setActiveWorkflowMode(action.mode);
    if (action.mode !== "target_extract") {
      onPatch({ workspace_mode: action.mode, focus_target: "workspace-title" });
      return;
    }
    const current = workspaceContextsRef.current.target_extract;
    const method = action.method ?? current.method;
    const resultAssetId = action.resultAssetIds?.[0];
    const target_extract = {
      ...current,
      method,
      source_asset_id: action.assetId ?? current.source_asset_id,
      stage: resultAssetId
        ? method === "breakdown" ? "select_breakdown_part" as const : "result" as const
        : (action.assetId ?? current.source_asset_id)
          ? method === "breakdown" ? "configure_breakdown" as const : "select_target" as const
          : "select_source" as const,
      breakdown_asset_id: resultAssetId && method === "breakdown" ? resultAssetId : current.breakdown_asset_id,
      result_asset_ids: resultAssetId && method === "direct" ? action.resultAssetIds ?? [] : current.result_asset_ids,
      active_result_asset_id: resultAssetId && method === "direct" ? resultAssetId : current.active_result_asset_id,
      job_id: action.jobId ?? current.job_id,
      agent_action_id: action.actionId ?? current.agent_action_id,
      agent_run_id: action.runId ?? current.agent_run_id,
      agent_instruction: action.instruction ?? current.agent_instruction,
    };
    onPatch({
      workspace_mode: "target_extract",
      focus_target: "workspace-title",
      workflow_contexts: { ...workspaceContextsRef.current, target_extract },
    });
  };
  const useAssetImage = () => {
    refreshCurrentAsset();
    setRoute("workspace");
    setActiveWorkflowMode("image");
    onPatch({ workspace_mode: "image", focus_target: "workspace-title" });
  };
  const openAssetModel = (assetId: string) => {
    const model3d = { ...workspaceContextsRef.current.model3d, asset_id: assetId, generation_job_id: null };
    setRoute("workspace");
    setActiveWorkflowMode("model3d");
    onPatch({
      workspace_mode: "model3d",
      focus_target: "workspace-title",
      workflow_contexts: { ...workspaceContextsRef.current, model3d },
    });
  };
 const workspaceContent = <WorkspaceRouter mode={activeWorkflowMode} projectId={projectId} api={api} onModeChange={(workspace_mode) => { if (workspace_mode === "task_waiting") { setRoute("tasks"); return; } setActiveWorkflowMode(workspace_mode); onPatch({ workspace_mode, focus_target: "workspace-title" }); }} onCurrentAssetChange={refreshCurrentAsset} onContinueToMultiview={continueToMultiview} onOpenTasks={() => setRoute("tasks")} onOpenAssets={(assetId) => { setFocusedAssetId(assetId); setRoute("assets"); }} onImageImported={() => handleImported("image")} onGlbImported={() => handleImported("model3d")} onRecover={() => { setActiveWorkflowMode("empty"); onPatch({ workspace_mode: "empty", focus_target: "workspace-title" }); }} referenceContext={state.reference_context} onReferenceContextChange={patchReferenceContext} workflowContexts={state.workflow_contexts} onWorkflowContextChange={patchWorkflowContexts} imageGenerationJobId={state.image_generation_job_id} onImageJobQueued={(jobId) => onPatch({ image_generation_job_id: jobId })} candidateResult={candidateResult} />;
  const content = route === "workspace" ? <div className="workspace-flow"><WorkflowSwitcher mode={activeWorkflowMode} onSelect={(workspace_mode) => void openWorkflow(workspace_mode)} />{workspaceContent}</div> : route === "assets" && projectId && api ? <AssetBrowser projectId={projectId} api={api} readOnly={readOnly} focusAssetId={focusedAssetId} onCurrent={useAssetImage} onOpenModel={openAssetModel} /> : route === "tasks" && projectId && api ? <JobsPanel projectId={projectId} api={api} showHistory onOpenResult={openJobResult} dismissedJobIds={state.dismissed_job_ids} onDismiss={(ids) => onPatch({ dismissed_job_ids: [...new Set([...state.dismissed_job_ids, ...ids])].slice(-200) })} /> : route === "exports" && project && api && onProject ? <ProjectPackageActions project={project} api={api} onProject={onProject} /> : <section className="route-placeholder"><h1>{placeholder}</h1><p>打开项目后即可查看受管任务。</p></section>;
  return <div className="workbench">
    <TopBar projectName={projectName} currentAssetName={currentAssetName} readOnly={readOnly} diagnosticsDisabled={!projectId || !api} onTasks={() => setRoute("tasks")} onExports={() => setRoute("exports")} onDiagnostics={() => setDiagnosticsOpen(true)} onSettings={() => setSettingsOpen(true)} />
    <div className="workbench-body"><Navigation route={route} onChange={setRoute} /><main className="workspace-region">{content}</main>
      <div className="agent-divider" role="separator" aria-orientation="vertical" aria-label="Resize Agent panel" tabIndex={0} onMouseDown={() => { drag.current = true; }} onDoubleClick={() => onPatch({ agent_panel_width: defaultWidth })} onKeyDown={(event) => { if (event.key === "ArrowLeft") onPatch({ agent_panel_width: Math.min(520, state.agent_panel_width + 20) }); if (event.key === "ArrowRight") onPatch({ agent_panel_width: Math.max(360, state.agent_panel_width - 20) }); }} />
      <aside className={collapsed ? "agent-panel collapsed" : "agent-panel"} style={{ width: collapsed ? undefined : state.agent_panel_width }} aria-label="AI Agent">
        <header><span>{collapsed ? "AI" : "AI Agent"}</span><button title={collapsed ? "Expand Agent" : "Collapse Agent"} aria-label={collapsed ? "Expand Agent" : "Collapse Agent"} onClick={() => onPatch({ agent_panel_collapsed: !collapsed })}>{collapsed ? <CaretLeft size={18} /> : <CaretRight size={18} />}</button></header>
        {collapsed ? <button className="collapsed-run" onClick={() => onPatch({ agent_panel_collapsed: false })}><SidebarSimple size={20} />Restore panel</button> : <>
          {projectId && api ? <AgentPanel projectId={projectId} api={api} onJobQueued={() => setRoute("tasks")} onWorkspaceAction={openAgentWorkspace} /> : <div className="agent-conversation"><p>打开项目后即可使用 Agent。</p></div>}
        </>}
      </aside>
    </div><TaskBar projectId={projectId} api={api} />
    {settingsOpen && api && <SettingsDialog api={api} onClose={() => setSettingsOpen(false)} />}
    {diagnosticsOpen && projectId && api && <DiagnosticsPanel projectId={projectId} api={api} onClose={() => setDiagnosticsOpen(false)} />}
  </div>;
}
