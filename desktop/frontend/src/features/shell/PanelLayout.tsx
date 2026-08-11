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
const multiviewViews = ["front", "side", "back"] as const;
const localPartPostprocessOperations = new Set([
  "trim_transparent",
  "normalize",
  "remove_background_local",
]);

function operationOf(asset: { provenance?: { parameters?: Record<string, unknown> | null } | null }) {
  const operation = asset.provenance?.parameters?.operation;
  return typeof operation === "string" ? operation : null;
}

function promptLanguage(text: string): "zh" | "en" {
  return /[\u3400-\u9fff]/.test(text) ? "zh" : "en";
}

function recoveredLocalParts(assets: Awaited<ReturnType<ApiClient["assets"]>>, sourceId: string) {
  const children = new Map<string, typeof assets>();
  assets.forEach((asset) => {
    if (!asset.parent_asset_id) return;
    children.set(asset.parent_asset_id, [...(children.get(asset.parent_asset_id) ?? []), asset]);
  });
  const roots = assets
    .filter((asset) => asset.parent_asset_id === sourceId && operationOf(asset) === "split_local")
    .sort((left, right) => {
      const leftParams = (left.provenance?.parameters ?? {}) as Record<string, unknown>;
      const rightParams = (right.provenance?.parameters ?? {}) as Record<string, unknown>;
      return Number(leftParams.row ?? 0) - Number(rightParams.row ?? 0)
        || Number(leftParams.column ?? 0) - Number(rightParams.column ?? 0)
        || (left.created_at ?? "").localeCompare(right.created_at ?? "");
    });
  return roots.map((root) => {
    let current = root;
    const visited = new Set([root.id]);
    while (true) {
      const next = (children.get(current.id) ?? [])
        .filter((asset) => localPartPostprocessOperations.has(operationOf(asset) ?? ""))
        .sort((left, right) => (right.created_at ?? "").localeCompare(left.created_at ?? ""))[0];
      if (!next || visited.has(next.id)) return current.id;
      visited.add(next.id);
      current = next;
    }
  });
}

export function PanelLayout({ state, projectId, projectName, readOnly, project, api, onProject, onPatch }: { state: WorkspaceState; projectId?: string; projectName: string; readOnly: boolean; project?: ProjectDto; api?: ApiClient; onProject?(project: ProjectDto): void; onPatch(patch: Partial<WorkspaceState>): void }) {
  const [route, setRoute] = useState<PrimaryRoute>("workspace");
  const [activeWorkflowMode, setActiveWorkflowMode] = useState(state.workspace_mode);
  const [currentAssetName, setCurrentAssetName] = useState<string | null>(null);
  const [focusedAssetId, setFocusedAssetId] = useState<string | null>(null);
 const [settingsOpen, setSettingsOpen] = useState(false);
 const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
 const [candidateResult, setCandidateResult] = useState<{ jobId: string; assetIds: string[] } | null>(
  state.candidate_result
    ? { jobId: state.candidate_result.job_id, assetIds: state.candidate_result.asset_ids }
    : null,
 );
  const drag = useRef(false);
  const workspaceContextsRef = useRef(state.workflow_contexts);
  const referenceContextRef = useRef(state.reference_context);
  const recoveredTargetSource = useRef<string | null>(null);
  workspaceContextsRef.current = state.workflow_contexts;
  referenceContextRef.current = state.reference_context;
  useEffect(() => {
    setCandidateResult(state.candidate_result
      ? { jobId: state.candidate_result.job_id, assetIds: state.candidate_result.asset_ids }
      : null);
  }, [state.candidate_result]);
  const patchWorkflowContexts = useCallback((patch: Partial<WorkspaceState["workflow_contexts"]>) => {
    onPatch({ workflow_contexts: { ...workspaceContextsRef.current, ...patch } });
  }, [onPatch]);
  const patchReferenceContext = useCallback((patch: Partial<WorkspaceState["reference_context"]>) => {
    onPatch({ reference_context: { ...referenceContextRef.current, ...patch } });
  }, [onPatch]);
  const persistCandidateResult = (jobId: string, assetIds: string[]) => {
    const candidate = { jobId, assetIds };
    setCandidateResult(candidate);
    onPatch({ candidate_result: { job_id: jobId, asset_ids: assetIds } });
  };
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
   onPatch({
    workspace_mode: "candidate",
    focus_target: "workspace-title",
    candidate_result: { job_id: jobId, asset_ids: assetIds },
   });
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
  useEffect(() => {
    const current = workspaceContextsRef.current.target_extract;
    const sourceId = referenceContextRef.current.content_asset_id;
    if (
      !projectId
      || !api
      || !sourceId
      || current.result_asset_ids.length
      || recoveredTargetSource.current === sourceId
    ) return;
    recoveredTargetSource.current = sourceId;
    void api.assets(projectId).then((assets) => {
      const resultAssetIds = recoveredLocalParts(assets, sourceId);
      if (!resultAssetIds.length || workspaceContextsRef.current.target_extract.result_asset_ids.length) return;
      const latest = workspaceContextsRef.current.target_extract;
      const target_extract = {
        ...latest,
        method: "breakdown" as const,
        stage: "result" as const,
        source_asset_id: sourceId,
        breakdown_asset_id: sourceId,
        result_asset_ids: resultAssetIds,
        active_result_asset_id: resultAssetIds[0],
        pending_action_id: null,
      };
      onPatch({
        workflow_contexts: { ...workspaceContextsRef.current, target_extract },
      });
    }).catch(() => undefined);
  }, [api, projectId, state.reference_context.content_asset_id, state.workflow_contexts.target_extract.result_asset_ids.length, onPatch]);
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
      pending_action_id: null,
    };
    setRoute("workspace");
    setActiveWorkflowMode("multiview");
    onPatch({
      workspace_mode: "multiview",
      focus_target: "workspace-title",
      workflow_contexts: { ...workspaceContextsRef.current, multiview },
    });
  };
  const applyAgentMultiviewResult = async (action: AgentWorkspaceAction) => {
    const current = workspaceContextsRef.current.multiview;
    const resultAssetId = action.resultAssetIds?.[0];
    const persist = (multiview: typeof current) => onPatch({
      workspace_mode: "multiview",
      focus_target: "workspace-title",
      workflow_contexts: { ...workspaceContextsRef.current, multiview },
    });
    if (!resultAssetId || !action.jobType) {
      persist({
        ...current,
        selected: action.assetId
          ? { source: action.assetId, front: action.assetId, side: action.assetId, back: action.assetId }
          : current.selected,
        job_id: action.jobId ?? current.job_id,
        pending_action_id: action.actionId ?? current.pending_action_id,
      });
      return;
    }
    if (action.jobType === "multiview.generate") {
      persist({
        ...current,
        selected: {
          source: resultAssetId,
          front: resultAssetId,
          side: resultAssetId,
          back: resultAssetId,
        },
        regions: {},
        checks: {},
        quality_confirmed: false,
        set_id: null,
        job_id: action.jobId ?? current.job_id,
        pending_action_id: current.pending_action_id,
      });
      return;
    }
    if (action.jobType === "multiview.detect_regions" && projectId && api) {
      try {
        const detectedIds = new Set(action.resultAssetIds ?? []);
        const entries = await Promise.all(multiviewViews.map(async (view) => {
          const assetId = current.selected[view];
          if (!assetId) return null;
          const selections = await api.selections(projectId, assetId);
          const selection = selections.find((item) => detectedIds.has(item.id));
          return selection?.rects[0] ? [view, selection.rects[0]] as const : null;
        }));
        const detectedRegions = Object.fromEntries(entries.filter((item): item is NonNullable<typeof item> => Boolean(item)));
        persist({
          ...current,
          regions: Object.keys(detectedRegions).length ? detectedRegions : current.regions,
          checks: {},
          quality_confirmed: false,
          job_id: action.jobId ?? current.job_id,
        });
      } catch {
        persist({ ...current, job_id: action.jobId ?? current.job_id });
      }
      return;
    }
    if (action.jobType === "multiview.regenerate_view" && projectId && api) {
      try {
        const assets = await api.assets(projectId);
        const result = assets.find((asset) => asset.id === resultAssetId);
        const parameters = result?.provenance?.parameters;
        const view = parameters && typeof parameters === "object"
          ? (parameters as Record<string, unknown>).view
          : undefined;
        const setId = parameters && typeof parameters === "object"
          ? (parameters as Record<string, unknown>).multiview_set_id
          : undefined;
        if (view === "front" || view === "side" || view === "back") {
          persist({
            ...current,
            selected: { ...current.selected, [view]: resultAssetId },
            checks: {},
            quality_confirmed: false,
            set_id: typeof setId === "string" ? setId : current.set_id,
            job_id: action.jobId ?? current.job_id,
          });
          return;
        }
      } catch {
        // Preserve the current sheet when result provenance is unavailable.
      }
    }
    persist({ ...current, job_id: action.jobId ?? current.job_id });
  };
  const openAgentWorkspace = (action: AgentWorkspaceAction) => {
    if (
      (action.jobType?.startsWith("edit_image.")
        || ["image.normalize", "image.trim_transparent", "image.remove_background_local"].includes(action.jobType ?? ""))
      && action.assetId
      && action.resultAssetIds?.length
    ) {
      const current = workspaceContextsRef.current.target_extract;
      const sourceIndex = current.result_asset_ids.indexOf(action.assetId);
      if (sourceIndex >= 0) {
        const replacement = action.resultAssetIds[0];
        const resultAssetIds = [...current.result_asset_ids];
        resultAssetIds.splice(sourceIndex, 1, replacement);
        const target_extract = {
          ...current,
          stage: "result" as const,
          result_asset_ids: [...new Set(resultAssetIds)],
          active_result_asset_id: current.active_result_asset_id === action.assetId
            || !current.active_result_asset_id
            ? replacement
            : current.active_result_asset_id,
        };
        setRoute("workspace");
        setActiveWorkflowMode("target_extract");
        onPatch({
          workspace_mode: "target_extract",
          focus_target: "workspace-title",
          workflow_contexts: { ...workspaceContextsRef.current, target_extract },
        });
        return;
      }
    }
    if (action.mode === "image" && action.resultAssetIds?.[0] && projectId && api) {
      const resultAssetId = action.resultAssetIds[0];
      setRoute("workspace");
      setActiveWorkflowMode("image");
      void api
        .setCurrentAsset(projectId, resultAssetId, crypto.randomUUID())
        .then(refreshCurrentAsset)
        .catch(() => undefined);
      onPatch({ workspace_mode: "image", focus_target: "workspace-title" });
      return;
    }
    setRoute("workspace");
    setActiveWorkflowMode(action.mode);
    if (action.mode === "prompt_image") {
      const current = workspaceContextsRef.current.prompt_image;
      if (action.jobType === "prompt.rewrite" && action.jobId) {
        const prompt_image = { ...current, rewrite_job_id: action.jobId };
        onPatch({
          workspace_mode: "prompt_image",
          focus_target: "workspace-title",
          workflow_contexts: { ...workspaceContextsRef.current, prompt_image },
        });
        return;
      }
      const startsNewGeneration = Boolean(action.actionId && (action.prompt || action.promptAssetId));
      const jobId = action.jobId ?? (startsNewGeneration ? null : current.job_id);
      const receivedNewPromptAsset = Boolean(
        action.promptAssetId && action.promptAssetId !== current.source_prompt_asset_id,
      );
      const directPromptLanguage = action.prompt ? promptLanguage(action.prompt) : null;
      const prompt_image = {
        ...current,
        // An Agent-created prompt asset is the immutable source of truth.  Do
        // not also write its raw (usually single-language) tool argument into
        // both fields: the workspace restores the bilingual asset and these
        // competing updates made the editor flicker between languages.
        zh_prompt: receivedNewPromptAsset
          ? ""
          : action.prompt && !action.promptAssetId
            ? (directPromptLanguage === "zh" ? action.prompt : "")
            : current.zh_prompt,
        en_prompt: receivedNewPromptAsset
          ? ""
          : action.prompt && !action.promptAssetId
            ? (directPromptLanguage === "en" ? action.prompt : "")
            : current.en_prompt,
        display_language: action.prompt && !action.promptAssetId
          ? directPromptLanguage ?? current.display_language
          : current.display_language,
        source_prompt_asset_id: action.promptAssetId ?? current.source_prompt_asset_id,
        candidate_count: action.candidateCount ?? current.candidate_count,
        aspect_ratio: action.aspectRatio ?? current.aspect_ratio,
        job_id: jobId,
      };
      onPatch({
        workspace_mode: "prompt_image",
        focus_target: "workspace-title",
        image_generation_job_id: jobId,
        workflow_contexts: { ...workspaceContextsRef.current, prompt_image },
        ...(action.promptAssetId ? {
          reference_context: {
            ...referenceContextRef.current,
            merged_prompt_asset_id: action.promptAssetId,
          },
        } : {}),
      });
      return;
    }
    if (action.mode === "multiview") {
      void applyAgentMultiviewResult(action);
      return;
    }
    if (
      action.mode === "compare"
      && action.analysisKind
      && action.assetId
      && action.resultAssetIds?.[0]
    ) {
      const analysisAssetId = action.resultAssetIds[0];
      const reference_context = action.analysisKind === "content"
        ? {
            ...referenceContextRef.current,
            content_asset_id: action.assetId,
            content_analysis_asset_id: analysisAssetId,
            content_prompt_asset_id: null,
            merged_prompt_asset_id: null,
          }
        : action.analysisKind === "style" ? {
            ...referenceContextRef.current,
            style_asset_id: action.assetId,
            style_analysis_asset_id: analysisAssetId,
            style_prompt_asset_id: null,
            merged_prompt_asset_id: null,
          }
        : {
            ...referenceContextRef.current,
            suitability_asset_id: action.assetId,
            suitability_analysis_asset_id: analysisAssetId,
          };
      onPatch({
        workspace_mode: "compare",
        focus_target: "workspace-title",
        reference_context,
      });
      return;
    }
    if (action.mode === "candidate" && action.jobId && action.resultAssetIds?.length) {
      persistCandidateResult(action.jobId, action.resultAssetIds);
    }
    if (action.mode === "model3d") {
      const current = workspaceContextsRef.current.model3d;
      const resultAssetId = action.resultAssetIds?.[0];
      const writesModelAsset = action.jobType === "model3d.generate"
        || action.jobType === "model3d.import_local"
        || action.jobType === "model3d.optimize"
        || action.jobType === "model3d.download";
      const model3d = {
        ...current,
        asset_id: resultAssetId && writesModelAsset ? resultAssetId : current.asset_id,
        generation_job_id: action.jobType === "model3d.generate" && !resultAssetId
          ? action.jobId ?? current.generation_job_id
          : resultAssetId && writesModelAsset
            ? null
            : current.generation_job_id,
      };
      onPatch({
        workspace_mode: "model3d",
        focus_target: "workspace-title",
        workflow_contexts: { ...workspaceContextsRef.current, model3d },
      });
      return;
    }
    if (action.mode !== "target_extract") {
      onPatch({ workspace_mode: action.mode, focus_target: "workspace-title" });
      return;
    }
    const current = workspaceContextsRef.current.target_extract;
    const method = action.method ?? current.method;
    const resultAssetId = action.resultAssetIds?.[0];
    if (action.jobType === "image.split_local" && action.resultAssetIds?.length) {
      const target_extract = {
        ...current,
        method: "breakdown" as const,
        stage: "result" as const,
        source_asset_id: action.assetId ?? current.source_asset_id,
        breakdown_asset_id: action.assetId ?? current.breakdown_asset_id,
        result_asset_ids: [...new Set(action.resultAssetIds)],
        active_result_asset_id: action.resultAssetIds[0],
        pending_action_id: null,
      };
      onPatch({
        workspace_mode: "target_extract",
        focus_target: "workspace-title",
        workflow_contexts: { ...workspaceContextsRef.current, target_extract },
      });
      return;
    }
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
  const handleAgentJobQueued = (jobId: string, workspaceMode: WorkspaceState["workspace_mode"] | null, jobType?: string) => {
    setRoute("tasks");
    if (workspaceMode === "multiview") {
      const multiview = {
        ...workspaceContextsRef.current.multiview,
        job_id: jobId,
      };
      onPatch({
        workflow_contexts: { ...workspaceContextsRef.current, multiview },
      });
      return;
    }
    if (workspaceMode === "model3d" && jobType === "model3d.generate") {
      const model3d = {
        ...workspaceContextsRef.current.model3d,
        generation_job_id: jobId,
      };
      onPatch({ workflow_contexts: { ...workspaceContextsRef.current, model3d } });
      return;
    }
    if (workspaceMode !== "prompt_image") return;
    if (jobType === "prompt.rewrite") {
      const prompt_image = {
        ...workspaceContextsRef.current.prompt_image,
        rewrite_job_id: jobId,
      };
      onPatch({ workflow_contexts: { ...workspaceContextsRef.current, prompt_image } });
      return;
    }
    const prompt_image = {
      ...workspaceContextsRef.current.prompt_image,
      job_id: jobId,
    };
    onPatch({
      image_generation_job_id: jobId,
      workflow_contexts: { ...workspaceContextsRef.current, prompt_image },
    });
  };
 const workspaceContent = <WorkspaceRouter mode={activeWorkflowMode} projectId={projectId} api={api} onModeChange={(workspace_mode) => { if (workspace_mode === "task_waiting") { setRoute("tasks"); return; } setActiveWorkflowMode(workspace_mode); onPatch({ workspace_mode, focus_target: "workspace-title" }); }} onCurrentAssetChange={refreshCurrentAsset} onContinueToMultiview={continueToMultiview} onOpenTasks={() => setRoute("tasks")} onOpenAssets={(assetId) => { setFocusedAssetId(assetId); setRoute("assets"); }} onImageImported={() => handleImported("image")} onGlbImported={() => handleImported("model3d")} onRecover={() => { setActiveWorkflowMode("empty"); onPatch({ workspace_mode: "empty", focus_target: "workspace-title" }); }} referenceContext={state.reference_context} onReferenceContextChange={patchReferenceContext} workflowContexts={state.workflow_contexts} onWorkflowContextChange={patchWorkflowContexts} imageGenerationJobId={state.image_generation_job_id} onImageJobQueued={(jobId) => onPatch({ image_generation_job_id: jobId })} candidateResult={candidateResult} />;
 const content = route === "workspace" ? <div className="workspace-flow"><WorkflowSwitcher mode={activeWorkflowMode} onSelect={(workspace_mode) => void openWorkflow(workspace_mode)} />{workspaceContent}</div> : route === "assets" && projectId && api ? <AssetBrowser projectId={projectId} api={api} readOnly={readOnly} focusAssetId={focusedAssetId} onCurrent={useAssetImage} onAssetRemoved={refreshCurrentAsset} onOpenModel={openAssetModel} /> : route === "tasks" && projectId && api ? <JobsPanel projectId={projectId} api={api} showHistory onOpenResult={openJobResult} dismissedJobIds={state.dismissed_job_ids} onDismiss={(ids) => onPatch({ dismissed_job_ids: [...new Set([...state.dismissed_job_ids, ...ids])].slice(-200) })} /> : route === "exports" && project && api && onProject ? <ProjectPackageActions project={project} api={api} onProject={onProject} /> : <section className="route-placeholder"><h1>{placeholder}</h1><p>打开项目后即可查看受管任务。</p></section>;
  return <div className="workbench">
    <TopBar projectName={projectName} currentAssetName={currentAssetName} readOnly={readOnly} diagnosticsDisabled={!projectId || !api} onTasks={() => setRoute("tasks")} onExports={() => setRoute("exports")} onDiagnostics={() => setDiagnosticsOpen(true)} onSettings={() => setSettingsOpen(true)} />
    <div className="workbench-body"><Navigation route={route} onChange={setRoute} /><main className="workspace-region">{content}</main>
      <div className="agent-divider" role="separator" aria-orientation="vertical" aria-label="Resize Agent panel" tabIndex={0} onMouseDown={() => { drag.current = true; }} onDoubleClick={() => onPatch({ agent_panel_width: defaultWidth })} onKeyDown={(event) => { if (event.key === "ArrowLeft") onPatch({ agent_panel_width: Math.min(520, state.agent_panel_width + 20) }); if (event.key === "ArrowRight") onPatch({ agent_panel_width: Math.max(360, state.agent_panel_width - 20) }); }} />
      <aside className={collapsed ? "agent-panel collapsed" : "agent-panel"} style={{ width: collapsed ? undefined : state.agent_panel_width }} aria-label="AI Agent">
        <header><span>{collapsed ? "AI" : "AI Agent"}</span><button title={collapsed ? "Expand Agent" : "Collapse Agent"} aria-label={collapsed ? "Expand Agent" : "Collapse Agent"} onClick={() => onPatch({ agent_panel_collapsed: !collapsed })}>{collapsed ? <CaretLeft size={18} /> : <CaretRight size={18} />}</button></header>
        {collapsed ? <button className="collapsed-run" onClick={() => onPatch({ agent_panel_collapsed: false })}><SidebarSimple size={20} />Restore panel</button> : <>
          {projectId && api ? <AgentPanel projectId={projectId} api={api} onJobQueued={handleAgentJobQueued} onWorkspaceAction={openAgentWorkspace} /> : <div className="agent-conversation"><p>打开项目后即可使用 Agent。</p></div>}
        </>}
      </aside>
    </div><TaskBar projectId={projectId} api={api} />
    {settingsOpen && api && <SettingsDialog api={api} onClose={() => setSettingsOpen(false)} />}
    {diagnosticsOpen && projectId && api && <DiagnosticsPanel projectId={projectId} api={api} onClose={() => setDiagnosticsOpen(false)} />}
  </div>;
}
