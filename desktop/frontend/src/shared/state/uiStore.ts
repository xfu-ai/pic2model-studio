import type { ApiClient, WorkspaceMode, WorkspaceState, WorkspaceStatePatch } from "../api/client";

export const DEFAULT_WORKSPACE: WorkspaceState = {
  workspace_mode: "empty", agent_panel_width: 400, agent_panel_collapsed: false,
  parameter_drawer: "closed", canvas: { zoom: 1, pan_x: 0, pan_y: 0 }, selection_id: null, focus_target: null,
  reference_context: { content_asset_id: null, style_asset_id: null, content_analysis_asset_id: null, style_analysis_asset_id: null, content_prompt_asset_id: null, style_prompt_asset_id: null, merged_prompt_asset_id: null, suitability_asset_id: null, suitability_analysis_asset_id: null },
  dismissed_job_ids: [],
  image_generation_job_id: null,
  candidate_result: null,
  workflow_contexts: {
    prompt_image: { zh_prompt: "", en_prompt: "", display_language: "zh", source_prompt_asset_id: null, candidate_count: 2, aspect_ratio: "1:1", selected_candidate_id: null, job_id: null, rewrite_job_id: null },
    target_extract: {
      method: "direct", stage: "select_source", source_asset_id: null,
      source_selection_id: null, source_selection_rect: null,
      preset: "scene", custom_prompt: "", prompt_asset_id: null,
      breakdown_asset_id: null, breakdown_selection_id: null, breakdown_selection_rect: null,
      result_asset_ids: [], active_result_asset_id: null, job_id: null, pending_action_id: null,
      agent_action_id: null, agent_run_id: null, agent_instruction: "",
    },
    multiview: { selected: {}, regions: {}, checks: {}, quality_confirmed: false, set_id: null, job_id: null },
    model3d: { asset_id: null, target_triangles: 50000, generation_job_id: null },
  },
};
const MODES = new Set<WorkspaceMode>(["empty", "prompt_image", "image", "compare", "selection", "target_extract", "candidate", "multiview", "model3d", "task_waiting", "error_diagnostics"]);

const nullableId = (value: unknown) => typeof value === "string" && value.length <= 256 ? value : null;
const text = (value: unknown, fallback = "") => typeof value === "string" ? value.slice(0, 10_000) : fallback;
const rect = (value: unknown) => {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Record<string, unknown>;
  const values = [candidate.x, candidate.y, candidate.width, candidate.height];
  return values.every((entry) => typeof entry === "number" && Number.isFinite(entry))
    ? { x: Math.max(0, candidate.x as number), y: Math.max(0, candidate.y as number), width: Math.max(1, candidate.width as number), height: Math.max(1, candidate.height as number) }
    : null;
};
const object = (value: unknown) => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const idList = (value: unknown) => Array.isArray(value)
  ? value.flatMap((item) => { const parsed = nullableId(item); return parsed ? [parsed] : []; }).slice(0, 64)
  : [];
const extractionStages = new Set(["select_source", "select_target", "configure_breakdown", "awaiting_approval", "generating", "select_breakdown_part", "result", "error"]);
function parseWorkflowContexts(value: unknown): WorkspaceState["workflow_contexts"] {
  const raw = object(value); const defaults = DEFAULT_WORKSPACE.workflow_contexts;
  const prompt = object(raw.prompt_image), target = object(raw.target_extract), multi = object(raw.multiview), model = object(raw.model3d);
  const promptLanguage = prompt.display_language === "zh" || prompt.display_language === "en"
    ? prompt.display_language
    : defaults.prompt_image.display_language;
  const idMap = (value: unknown): Record<string, string> => Object.fromEntries(Object.entries(object(value)).flatMap(([key, id]) => { const parsed = nullableId(id); return parsed ? [[key, parsed]] : []; }));
  const rectMap = (value: unknown) => Object.fromEntries(Object.entries(object(value)).flatMap(([key, item]) => { const parsed = rect(item); return parsed ? [[key, parsed]] : []; }));
  const checks: Record<string, string> = Object.fromEntries(Object.entries(object(multi.checks)).flatMap(([key, item]) => item === "passed" || item === "warning" || item === "blocking" ? [[key, item]] : []));
  const targetMethod = target.method === "breakdown" ? "breakdown" : "direct";
  const targetResults = idList(target.result_asset_ids);
  const inferredStage = targetResults.length
    ? "result"
    : nullableId(target.breakdown_asset_id)
      ? "select_breakdown_part"
      : nullableId(target.job_id)
        ? "generating"
        : nullableId(target.source_asset_id)
          ? targetMethod === "breakdown" ? "configure_breakdown" : "select_target"
          : "select_source";
  const parsedStage = typeof target.stage === "string" && extractionStages.has(target.stage) ? target.stage as WorkspaceState["workflow_contexts"]["target_extract"]["stage"] : inferredStage;
  const parsedPreset = target.preset === "character" || target.preset === "custom" ? target.preset : "scene";
  return {
    prompt_image: {
      zh_prompt: text(prompt.zh_prompt),
      en_prompt: text(prompt.en_prompt),
      display_language: promptLanguage,
      source_prompt_asset_id: nullableId(prompt.source_prompt_asset_id),
      candidate_count: typeof prompt.candidate_count === "number" && [1, 2, 4].includes(prompt.candidate_count)
        ? prompt.candidate_count
        : defaults.prompt_image.candidate_count,
      aspect_ratio: text(prompt.aspect_ratio, defaults.prompt_image.aspect_ratio).slice(0, 32),
      selected_candidate_id: nullableId(prompt.selected_candidate_id),
      job_id: nullableId(prompt.job_id),
      rewrite_job_id: nullableId(prompt.rewrite_job_id),
    },
    target_extract: {
      method: targetMethod,
      stage: parsedStage,
      source_asset_id: nullableId(target.source_asset_id),
      source_selection_id: nullableId(target.source_selection_id),
      source_selection_rect: rect(target.source_selection_rect),
      preset: parsedPreset,
      custom_prompt: text(target.custom_prompt),
      prompt_asset_id: nullableId(target.prompt_asset_id),
      breakdown_asset_id: nullableId(target.breakdown_asset_id),
      breakdown_selection_id: nullableId(target.breakdown_selection_id),
      breakdown_selection_rect: rect(target.breakdown_selection_rect),
      result_asset_ids: targetResults,
      active_result_asset_id: nullableId(target.active_result_asset_id),
      job_id: nullableId(target.job_id),
      pending_action_id: nullableId(target.pending_action_id),
      agent_action_id: nullableId(target.agent_action_id),
      agent_run_id: nullableId(target.agent_run_id),
      agent_instruction: text(target.agent_instruction),
    },
    multiview: { selected: idMap(multi.selected), regions: rectMap(multi.regions), checks, quality_confirmed: multi.quality_confirmed === true, set_id: nullableId(multi.set_id), job_id: nullableId(multi.job_id) },
    model3d: {
      asset_id: nullableId(model.asset_id),
      target_triangles: typeof model.target_triangles === "number" ? Math.max(4, Math.min(10_000_000, Math.round(model.target_triangles))) : defaults.model3d.target_triangles,
      generation_job_id: nullableId(model.generation_job_id),
    },
  };
}

export function parseWorkspaceState(raw: string | undefined): WorkspaceState {
  try {
    const parsed = JSON.parse(raw ?? "{}") as WorkspaceStatePatch & { workspace_mode?: string };
    if (parsed.workspace_mode && !MODES.has(parsed.workspace_mode)) return { ...DEFAULT_WORKSPACE, workspace_mode: "error_diagnostics" };
    const width = typeof parsed.agent_panel_width === "number" ? Math.min(520, Math.max(360, parsed.agent_panel_width)) : 400;
    const reference: Partial<WorkspaceState["reference_context"]> = parsed.reference_context && typeof parsed.reference_context === "object" ? parsed.reference_context : {};
    const reference_context = {
      content_asset_id: nullableId(reference.content_asset_id), style_asset_id: nullableId(reference.style_asset_id),
      content_analysis_asset_id: nullableId(reference.content_analysis_asset_id), style_analysis_asset_id: nullableId(reference.style_analysis_asset_id),
      content_prompt_asset_id: nullableId(reference.content_prompt_asset_id), style_prompt_asset_id: nullableId(reference.style_prompt_asset_id),
      merged_prompt_asset_id: nullableId(reference.merged_prompt_asset_id),
      suitability_asset_id: nullableId(reference.suitability_asset_id),
      suitability_analysis_asset_id: nullableId(reference.suitability_analysis_asset_id),
    };
    const dismissed_job_ids = Array.isArray(parsed.dismissed_job_ids) ? parsed.dismissed_job_ids.filter((value): value is string => typeof value === "string").slice(-200) : [];
    const image_generation_job_id = typeof parsed.image_generation_job_id === "string" ? parsed.image_generation_job_id : null;
    const candidate = object(parsed.candidate_result);
    const candidateJobId = nullableId(candidate.job_id);
    const candidateAssetIds = idList(candidate.asset_ids);
    const candidate_result = candidateJobId && candidateAssetIds.length
      ? { job_id: candidateJobId, asset_ids: candidateAssetIds }
      : null;
    const workflow_contexts = parseWorkflowContexts(parsed.workflow_contexts);
    return { ...DEFAULT_WORKSPACE, ...parsed, agent_panel_width: width, reference_context, dismissed_job_ids, image_generation_job_id, candidate_result, workflow_contexts };
  } catch { return { ...DEFAULT_WORKSPACE, workspace_mode: "error_diagnostics" }; }
}

export class WorkspaceStore {
  private state: WorkspaceState; private timer: ReturnType<typeof setTimeout> | undefined; private requestSequence = 0; private busyRetries = 0; private stateRevision = 0;
  private readonly listeners = new Set<(value: WorkspaceState) => void>(); private readonly seenActions = new Set<string>();
  constructor(initial: WorkspaceState, private readonly projectId: string | null, private readonly api: ApiClient | null, private readonly delayMs = 300) { this.state = initial; }
  snapshot = () => this.state;
  subscribe = (listener: (value: WorkspaceState) => void) => { this.listeners.add(listener); return () => this.listeners.delete(listener); };
  update(patch: WorkspaceStatePatch, persist = true) { this.state = { ...this.state, ...patch }; this.stateRevision += 1; this.listeners.forEach((listener) => listener(this.state)); if (persist) { this.busyRetries = 0; this.schedule(); } }
  applyWorkspaceAction(actionId: string, patch: WorkspaceStatePatch) { if (this.seenActions.has(actionId)) return false; this.seenActions.add(actionId); this.update(patch); return true; }
  flush = async () => { if (this.timer) { clearTimeout(this.timer); this.timer = undefined; } if (!this.api || !this.projectId) return this.state; const revision = this.stateRevision; const pendingState = this.state; const requestId = `workspace-${Date.now()}-${++this.requestSequence}`; const saved = await this.api.updateWorkspaceState(this.projectId, pendingState, requestId); if (this.stateRevision !== revision) return this.state; this.state = { ...DEFAULT_WORKSPACE, ...saved }; this.listeners.forEach((listener) => listener(this.state)); return this.state; };
  dispose() { if (this.timer) clearTimeout(this.timer); this.listeners.clear(); }
  private schedule(delayMs = this.delayMs) {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = undefined;
      void this.flush().then(() => { this.busyRetries = 0; }).catch((error: unknown) => {
        const code = typeof error === "object" && error && "code" in error ? (error as { code?: unknown }).code : null;
        if (code === "DATABASE_BUSY" && this.busyRetries < 3) { this.busyRetries += 1; this.schedule(1_000); }
      });
    }, delayMs);
  }
}
