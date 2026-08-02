export type RendererSession = {
  base_url: string;
  bearer_token: string;
  origin: string;
};

export class ApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly recoverable: boolean,
  ) { super(message); }
}

export class ApiClient {
  constructor(private readonly session: RendererSession) {}

  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetch(`${this.session.base_url}${path}`, {
      ...init,
      headers: {
        Authorization: `Bearer ${this.session.bearer_token}`,
        Origin: this.session.origin,
        "Content-Type": "application/json",
        ...init.headers,
      },
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new ApiError(body.code ?? "NETWORK_ERROR", body.user_message ?? "本地服务暂时不可用。", body.recoverable ?? true);
    }
    return response.json() as Promise<T>;
  }

  health() { return this.request<Record<string, unknown>>("/v1/health"); }

  createProject(name: string, createCapabilityId: string, requestId: string) {
    return this.request<ProjectDto>("/v1/projects", { method: "POST", headers: { "X-Request-Id": requestId }, body: JSON.stringify({ name, create_capability_id: createCapabilityId }) });
  }

  openProject(openCapabilityId: string, requestId: string) {
    return this.request<ProjectDto>("/v1/projects/open", { method: "POST", headers: { "X-Request-Id": requestId }, body: JSON.stringify({ open_capability_id: openCapabilityId }) });
  }

  recentProjects() { return this.request<{ projects: RecentProjectDto[] }>("/v1/projects/recent"); }

  project(projectId: string) { return this.request<ProjectDto>(`/v1/projects/${projectId}`); }

  updateWorkspaceState(projectId: string, state: WorkspaceStatePatch, requestId: string) {
    return this.request<WorkspaceState>(`/v1/projects/${projectId}/workspace-state`, {
      method: "PATCH",
      headers: { "X-Request-Id": requestId },
      body: JSON.stringify({ state, request_id: requestId }),
    });
  }

  importImage(projectId: string, fileCapabilityId: string, requestId: string, parentAssetId?: string, name?: string) {
    return this.request<AssetDto>(`/v1/projects/${projectId}/assets/import`, {
      method: "POST", headers: { "X-Request-Id": requestId },
      body: JSON.stringify({ file_capability_id: fileCapabilityId, asset_type: "source_image", request_id: requestId, ...(parentAssetId ? { parent_asset_id: parentAssetId } : {}), ...(name ? { name } : {}) }),
    });
  }

  importGlb(projectId: string, fileCapabilityId: string, requestId: string) {
    return this.request<AssetDto>(`/v1/projects/${projectId}/assets/import`, {
      method: "POST", headers: { "X-Request-Id": requestId },
      body: JSON.stringify({ file_capability_id: fileCapabilityId, asset_type: "glb", request_id: requestId }),
    });
  }

  exportProject(projectId: string, exportCapabilityId: string, requestId: string) {
    return this.request<Record<string, unknown>>(`/v1/projects/${projectId}/export`, { method: "POST", headers: { "X-Request-Id": requestId }, body: JSON.stringify({ export_capability_id: exportCapabilityId, format: "project_v1", request_id: requestId }) });
  }

  setCurrentAsset(projectId: string, assetId: string, requestId: string) {
    return this.request<SetCurrentAssetResult>(`/v1/assets/${assetId}/set-current`, {
      method: "POST", headers: { "X-Request-Id": requestId },
      body: JSON.stringify({ project_id: projectId, decision_source: "user", request_id: requestId }),
    });
  }

  assets(projectId: string, includeTrashed = false) { return this.request<AssetDto[]>(`/v1/projects/${projectId}/assets?include_trashed=${includeTrashed}`); }
  async assetContent(projectId: string, assetId: string, signal?: AbortSignal): Promise<Blob> {
    const response = await fetch(
      `${this.session.base_url}/v1/assets/${assetId}/content?project_id=${encodeURIComponent(projectId)}`,
      {
      cache: "no-store",
      signal,
      headers: {
        Accept: "image/*,application/octet-stream",
        Authorization: `Bearer ${this.session.bearer_token}`,
      },
      },
    );
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new ApiError(
        body.code ?? "ASSET_CONTENT_UNAVAILABLE",
        body.user_message ?? "The managed image preview could not be loaded.",
        body.recoverable ?? true,
      );
    }
    return response.blob();
  }
  async assetText(projectId: string, assetId: string, signal?: AbortSignal) {
    return (await this.assetContent(projectId, assetId, signal)).text();
  }
  async assetThumbnail(projectId: string, assetId: string, signal?: AbortSignal): Promise<Blob> {
    const response = await fetch(
      `${this.session.base_url}/v1/assets/${assetId}/thumbnail?project_id=${encodeURIComponent(projectId)}`,
      {
        cache: "force-cache",
        signal,
        headers: {
          Accept: "image/*",
          Authorization: `Bearer ${this.session.bearer_token}`,
        },
      },
    );
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new ApiError(
        body.code ?? "ASSET_THUMBNAIL_UNAVAILABLE",
        body.user_message ?? "The managed thumbnail could not be loaded.",
        body.recoverable ?? true,
      );
    }
    return response.blob();
  }
  exportAsset(projectId: string, assetId: string, exportCapabilityId: string, requestId: string) {
    return this.request<{ asset_id: string; name: string; bytes: number }>(`/v1/assets/${assetId}/export`, {
      method: "POST",
      headers: { "X-Request-Id": requestId },
      body: JSON.stringify({ project_id: projectId, export_capability_id: exportCapabilityId, request_id: requestId }),
    });
  }
  assetLineage(projectId: string, assetId: string) { return this.request<AssetLineageDto>(`/v1/assets/${assetId}/lineage?project_id=${encodeURIComponent(projectId)}`); }
  assetImpact(projectId: string, assetId: string) { return this.request<AssetImpactDto>(`/v1/assets/${assetId}/impact?project_id=${encodeURIComponent(projectId)}`); }
  compareAssets(projectId: string, leftId: string, rightId: string, requestId: string) {
    return this.request<AssetComparisonDto>("/v1/assets/compare", { method: "POST", headers: { "X-Request-Id": requestId }, body: JSON.stringify({ project_id: projectId, left_id: leftId, right_id: rightId, request_id: requestId }) });
  }
  invokeTool(
    projectId: string,
    toolName: string,
    arguments_: Record<string, unknown>,
    requestId: string,
    options: { runId?: string; roundIndex?: number; providerProfile?: string } = {},
  ) {
    return this.request<ToolResultDto>("/v1/tools/invoke", {
      method: "POST",
      headers: { "X-Request-Id": requestId },
      body: JSON.stringify({
        project_id: projectId,
        run_id: options.runId ?? null,
        round_index: options.roundIndex ?? 0,
        tool_name: toolName,
        tool_version: "1.0.0",
        arguments: arguments_,
        request_id: requestId,
        provider_profile: options.providerProfile ?? null,
      }),
    });
  }
  decideApproval(
    projectId: string,
    approvalId: string,
    approved: boolean,
    requestId: string,
  ) {
    return this.request<ToolResultDto>(`/v1/approvals/${approvalId}/decision`, {
      method: "POST",
      headers: { "X-Request-Id": requestId },
      body: JSON.stringify({
        project_id: projectId,
        approved,
        request_id: requestId,
      }),
    });
  }
  job(projectId: string, jobId: string) {
    return this.request<JobDto>(
      `/v1/jobs/${jobId}?project_id=${encodeURIComponent(projectId)}`,
    );
  }
  jobs(projectId: string, includeTerminal = false) { return this.request<{ items: JobDto[] }>(`/v1/projects/${projectId}/jobs?include_terminal=${includeTerminal}`); }
  cancelJob(projectId: string, jobId: string, requestId: string) {
    return this.request<ToolResultDto>(`/v1/jobs/${jobId}/cancel`, { method: "POST", headers: { "X-Request-Id": requestId }, body: JSON.stringify({ project_id: projectId, request_id: requestId }) });
  }
  retryJob(projectId: string, jobId: string, requestId: string) {
    return this.request<ToolResultDto>(`/v1/jobs/${jobId}/retry`, { method: "POST", headers: { "X-Request-Id": requestId }, body: JSON.stringify({ project_id: projectId, request_id: requestId }) });
  }
  confirmNewSubmission(projectId: string, jobId: string, requestId: string) {
    return this.request<ToolResultDto>(`/v1/jobs/${jobId}/confirm-new-submission`, { method: "POST", headers: { "X-Request-Id": requestId }, body: JSON.stringify({ project_id: projectId, request_id: requestId }) });
  }
  settings() { return this.request<Record<string, unknown>>("/v1/settings"); }
  imageGenerationProviders() { return this.request<ImageProviderStatusDto>("/v1/settings/image-generation-providers"); }
  refreshImageGenerationProviders(requestId: string) { return this.request<ImageProviderStatusDto>("/v1/settings/image-generation-providers/refresh", { method: "POST", headers: { "X-Request-Id": requestId } }); }
  serviceProviders() { return this.request<ServiceProviderStatusDto>("/v1/settings/service-providers"); }
  refreshServiceProviders(requestId: string) { return this.request<ServiceProviderStatusDto>("/v1/settings/service-providers/refresh", { method: "POST", headers: { "X-Request-Id": requestId } }); }
  probeServiceProvider(providerProfile: string, requestId: string) { return this.request<ServiceProviderStatusDto>("/v1/settings/service-providers/probe", { method: "POST", headers: { "X-Request-Id": requestId }, body: JSON.stringify({ provider_profile: providerProfile, request_id: requestId }) }); }
  updateSettings(patch: Record<string, unknown>, requestId: string) {
    return this.request<Record<string, unknown>>("/v1/settings", { method: "PATCH", headers: { "X-Request-Id": requestId }, body: JSON.stringify({ scope: "app", patch, request_id: requestId }) });
  }
  setSecret(providerProfile: string, secret: string, requestId: string) { return this.request<{ configured: boolean; mask: string }>("/v1/settings/secrets", { method: "POST", headers: { "X-Request-Id": requestId }, body: JSON.stringify({ provider_profile: providerProfile, secret, request_id: requestId }) }); }
  diagnosticsPreview(projectId: string, requestId: string) { return this.request<DiagnosticsPreviewDto>("/v1/diagnostics/preview", { method: "POST", headers: { "X-Request-Id": requestId }, body: JSON.stringify({ project_id: projectId }) }); }
  exportDiagnostics(projectId: string, exportCapabilityId: string, manifestHash: string, requestId: string) { return this.request<{ path: string; manifest_hash: string }>("/v1/diagnostics/export", { method: "POST", headers: { "X-Request-Id": requestId }, body: JSON.stringify({ project_id: projectId, export_capability_id: exportCapabilityId, confirmed_manifest_hash: manifestHash, request_id: requestId }) }); }
  registerPreview(projectId: string, assetId: string, pngBase64: string, requestId: string) {
    return this.request<AssetDto>(`/v1/assets/${assetId}/previews?project_id=${encodeURIComponent(projectId)}`, { method: "POST", headers: { "X-Request-Id": requestId }, body: JSON.stringify({ preview_png_base64: pngBase64, registration: { view: "front", camera: { position: [0, 0, 2], target: [0, 0, 0], fov_degrees: 45 } }, request_id: requestId }) });
  }
  selections(projectId: string, assetId: string) { return this.request<SelectionDto[]>(`/v1/assets/${assetId}/selections?project_id=${encodeURIComponent(projectId)}`); }
  saveSelection(projectId: string, assetId: string, rect: SelectionRect, requestId: string, selectionId?: string, expectedRevision?: number) {
    return this.request<SelectionDto>(`/v1/assets/${assetId}/selections`, { method: "POST", headers: { "X-Request-Id": requestId }, body: JSON.stringify({ project_id: projectId, rects: [rect], label: "Selection", source: "user", status: selectionId ? "edited" : "draft", selection_id: selectionId, expected_revision: expectedRevision, request_id: requestId }) });
  }
  createMultiviewSet(projectId: string, sourceAssetId: string, viewAssetIds: Record<string, string>, requestId: string) { return this.request<{ id: string }>(`/v1/projects/${projectId}/multiview-sets`, { method: "POST", headers: { "X-Request-Id": requestId }, body: JSON.stringify({ source_asset_id: sourceAssetId, view_asset_ids: viewAssetIds, request_id: requestId }) }); }
  confirmSelection(projectId: string, selectionId: string, expectedRevision: number, requestId: string) { return this.request<SelectionDto>(`/v1/selections/${selectionId}/confirm`, { method: "POST", headers: { "X-Request-Id": requestId }, body: JSON.stringify({ project_id: projectId, expected_revision: expectedRevision, request_id: requestId }) }); }
  cropSelection(projectId: string, selectionId: string, requestId: string) { return this.request<AssetDto[]>(`/v1/selections/${selectionId}/crop`, { method: "POST", headers: { "X-Request-Id": requestId }, body: JSON.stringify({ project_id: projectId, request_id: requestId }) }); }
  savePromptVersion(
    projectId: string,
    prompt: {
      zhPrompt: string;
      enPrompt: string;
      kind?: "content" | "style" | "merged" | "image" | "multiview" | "element" | "boxsplit";
      parentAssetId?: string | null;
    },
    requestId: string,
  ) {
    return this.request<{ asset: AssetDto; versions: { id: string; language: string }[] }>(
      `/v1/projects/${projectId}/prompts`,
      {
        method: "POST",
        headers: { "X-Request-Id": requestId },
        body: JSON.stringify({
          zh_prompt: prompt.zhPrompt,
          en_prompt: prompt.enPrompt,
          kind: prompt.kind ?? "merged",
          parent_asset_id: prompt.parentAssetId ?? null,
          request_id: requestId,
        }),
      },
    );
  }
  createAgentConversation(projectId: string, systemPrompt: string, requestId: string, model?: string) {
    return this.request<AgentConversationDto>("/v1/agent/conversations", {
      method: "POST",
      headers: { "X-Request-Id": requestId },
      body: JSON.stringify({
        project_id: projectId,
        system_prompt: systemPrompt,
        model: model ?? null,
      }),
    });
  }
  sendAgentMessage(
    projectId: string,
    conversationId: string,
    content: string,
    requestId: string,
    wait = true,
    assetRefs: string[] = [],
  ) {
    return this.request<AgentConversationDto>(
      `/v1/agent/conversations/${conversationId}/messages`,
      {
        method: "POST",
        headers: { "X-Request-Id": requestId },
        body: JSON.stringify({
          project_id: projectId,
          content,
          asset_refs: assetRefs,
          request_id: requestId,
          wait,
        }),
      },
    );
  }
  agentConversations(projectId: string) {
    return this.request<{ items: AgentConversationDto[] }>(
      `/v1/agent/conversations?project_id=${encodeURIComponent(projectId)}`,
    );
  }
  agentMessages(projectId: string, conversationId: string, limit?: number, before?: number) {
    const query = new URLSearchParams({ project_id: projectId });
    if (limit !== undefined) query.set("limit", String(limit));
    if (before !== undefined) query.set("before", String(before));
    return this.request<AgentMessagesDto>(
      `/v1/agent/conversations/${conversationId}/messages?${query.toString()}`,
    );
  }
  agentEvents(projectId: string, conversationId: string, after: number) {
    return this.request<AgentEventsDto>(
      `/v1/agent/conversations/${conversationId}/events?project_id=${encodeURIComponent(projectId)}&after=${after}`,
    );
  }
  hideAsset(projectId: string, assetId: string, requestId: string) { return this.assetAction(`/v1/assets/${assetId}/hide`, projectId, requestId); }
  restoreHiddenAsset(projectId: string, assetId: string, requestId: string) { return this.assetAction(`/v1/assets/${assetId}/restore-hidden`, projectId, requestId); }
  trashAsset(projectId: string, assetId: string, impactToken: string, requestId: string) {
    return this.request<AssetDto>(`/v1/assets/${assetId}/trash`, { method: "POST", headers: { "X-Request-Id": requestId }, body: JSON.stringify({ project_id: projectId, impact_token: impactToken, request_id: requestId }) });
  }
  restoreAsset(projectId: string, assetId: string, requestId: string) { return this.assetAction(`/v1/assets/${assetId}/restore`, projectId, requestId); }

  private assetAction(path: string, projectId: string, requestId: string) {
    return this.request<AssetDto>(path, { method: "POST", headers: { "X-Request-Id": requestId }, body: JSON.stringify({ project_id: projectId, request_id: requestId }) });
  }
}

export type WorkspaceMode =
  | "empty" | "prompt_image" | "image" | "compare" | "selection" | "target_extract" | "candidate"
  | "multiview" | "model3d" | "task_waiting" | "error_diagnostics";

export type WorkspaceStatePatch = Partial<{
  workspace_mode: WorkspaceMode;
  agent_panel_width: number;
  agent_panel_collapsed: boolean;
  parameter_drawer: "closed" | "pinned" | "context";
  canvas: { zoom: number; pan_x: number; pan_y: number };
  selection_id: string | null;
  focus_target: string | null;
  reference_context: ReferenceContextState;
  dismissed_job_ids: string[];
  image_generation_job_id: string | null;
  workflow_contexts: WorkflowContexts;
}>;

export type WorkflowContexts = {
  prompt_image: {
    zh_prompt: string;
    en_prompt: string;
    display_language: "zh" | "en";
    source_prompt_asset_id: string | null;
    candidate_count: number;
    aspect_ratio: string;
    selected_candidate_id: string | null;
    job_id: string | null;
    rewrite_job_id: string | null;
  };
  target_extract: {
    method: "direct" | "breakdown";
    stage: "select_source" | "select_target" | "configure_breakdown" | "awaiting_approval" | "generating" | "select_breakdown_part" | "result" | "error";
    source_asset_id: string | null;
    source_selection_id: string | null;
    source_selection_rect: SelectionRect | null;
    preset: "scene" | "character" | "custom";
    custom_prompt: string;
    prompt_asset_id: string | null;
    breakdown_asset_id: string | null;
    breakdown_selection_id: string | null;
    breakdown_selection_rect: SelectionRect | null;
    result_asset_ids: string[];
    active_result_asset_id: string | null;
    job_id: string | null;
    pending_action_id: string | null;
    agent_action_id: string | null;
    agent_run_id: string | null;
    agent_instruction: string;
  };
  multiview: { selected: Record<string, string>; regions: Record<string, SelectionRect>; checks: Record<string, string>; quality_confirmed: boolean; set_id: string | null; job_id: string | null };
  model3d: { asset_id: string | null; target_triangles: number; generation_job_id: string | null };
};

export type ReferenceContextState = {
  content_asset_id: string | null;
  style_asset_id: string | null;
  content_analysis_asset_id: string | null;
  style_analysis_asset_id: string | null;
  content_prompt_asset_id: string | null;
  style_prompt_asset_id: string | null;
  merged_prompt_asset_id: string | null;
};

export type WorkspaceState = {
  workspace_mode: WorkspaceMode;
  agent_panel_width: number;
  agent_panel_collapsed: boolean;
  parameter_drawer: "closed" | "pinned" | "context";
  canvas: { zoom: number; pan_x: number; pan_y: number };
  selection_id: string | null;
  focus_target: string | null;
  reference_context: ReferenceContextState;
  dismissed_job_ids: string[];
  image_generation_job_id: string | null;
  workflow_contexts: WorkflowContexts;
};
export type ProjectDto = {
  id: string;
  name: string;
  current_asset_id: string | null;
  root_state: string;
  workspace_mode?: WorkspaceMode;
  workspace_state_json?: string;
};

export type RecentProjectDto = { id: string; name: string; availability: "available" | "unavailable"; last_opened_at: string };
export type ImageProviderStatusDto = {
  profile: string;
  active_provider: string | null;
  priority: string[];
  probe_interval_seconds: number;
  probes_consume_generation_credits: boolean;
  providers: Array<{
    profile: string;
    label: string;
    channel: string;
    configured: boolean;
    available: boolean;
    reason: string | null;
    last_checked_at: string | null;
    priority: number;
    model: string;
    models: Record<string, string>;
    modes: string[];
  }>;
};
export type ServiceProviderStatusDto = {
  probe_interval_seconds: number;
  probes_consume_generation_credits: false;
  providers: Array<{
    profile: string;
    label: string;
    channel: string;
    configured: boolean;
    available: boolean;
    reason: string | null;
    last_checked_at: string | null;
    display_order: number;
    model: string;
    models: Record<string, string>;
    modes: string[];
    capabilities: string[];
  }>;
};
export type AssetDto = { id: string; asset_type: string; name: string; parent_asset_id?: string | null; thumbnail_asset_id?: string | null; is_current: boolean; is_hidden?: boolean; trashed_at?: string | null; mime_type?: string; size_bytes?: number; metadata: Record<string, unknown>; provenance?: Record<string, unknown>; version_no?: number; created_at?: string };
export type AssetLineageDto = { asset_id: string; parent_asset_id?: string | null; children: string[]; siblings: string[]; usage: Record<string, unknown> };
export type AssetImpactDto = { impact_token: string; children?: unknown[]; incoming_links?: unknown[]; [key: string]: unknown };
export type AssetComparisonDto = { left: AssetDto; right: AssetDto; same_family: boolean; version_delta: number; [key: string]: unknown };
export type SetCurrentAssetResult = { decision: { asset_id: string; previous_asset_id: string | null }; event: Record<string, unknown> };
export type ToolResultDto = {
  ok: boolean;
  status: string;
  tool_call_id: string;
  output_asset_ids: string[];
  summary: string;
  warnings: string[];
  expected_action?: Record<string, unknown> | null;
  ui_action?: {
    action_id?: string;
    type?: string;
    workspace_mode?: string;
  } | null;
  job?: {
    job_id: string;
    status: string;
    job_type: string;
    stage: string;
    provider: string;
  } | null;
  error?: {
    code?: string;
    user_message?: string;
    recoverable?: boolean;
  } | null;
  reused?: boolean;
};
export type JobDto = {
  schema_version: number;
  id: string;
  job_type?: string;
  status:
    | "queued"
    | "running"
    | "waiting"
    | "succeeded"
    | "failed"
    | "cancelled"
    | "interrupted";
  stage: string;
  progress: number | null;
  elapsed_seconds: number;
  estimated_seconds: number | null;
  provider: string | null;
  resume_class?: "fresh" | "local_restartable" | "remote_poll" | "download_retry" | "unknown_submission" | "manual_review" | "stop_waiting";
  recovery_actions?: Array<"query_remote" | "confirm_new_submission">;
  cancel_capability: "cancel_local" | "cancel_remote" | "stop_waiting" | "not_cancellable";
  can_cancel: boolean;
  can_stop_waiting: boolean;
  output_asset_ids: string[];
  input_asset_ids?: string[];
  created_at?: string;
  updated_at?: string;
  completed_at?: string | null;
  error?: { user_message?: string; code?: string; category?: string; failed_step?: string; recommended_action?: string; safe_to_retry?: boolean } | null;
};
export type AgentConversationDto = {
  id: string;
  project_id: string;
  state: "idle" | "running" | "error";
  message_count: number;
  created_at?: string;
  updated_at?: string;
  preview?: string;
  error_code?: string | null;
};
export type AgentMessageDto = {
  id: string;
  role: string;
  content: string | AgentContentBlock[];
  tool_name?: string;
  tool_call_id?: string;
  is_error?: boolean;
  stop_reason?: "stop" | "length" | "tool_use" | "error" | "aborted";
  error_message?: string | null;
  attachments?: AgentAttachmentDto[];
  details?: {
    status?: string;
    output_asset_ids?: string[];
    warnings?: string[];
    ui_action?: {
      action_id?: string;
      type?: string;
      workspace_mode?: string;
      asset_id?: string;
      run_id?: string;
      method?: "direct" | "breakdown";
      purpose?: string;
      instruction?: string;
    } | null;
    job?: {
      job_id?: string;
      status?: string;
      job_type?: string;
      stage?: string;
      provider?: string;
    } | null;
    error?: { user_message?: string; code?: string } | null;
  } | null;
};
export type AgentAttachmentDto = {
  asset_id: string;
  name: string;
  mime_type: string;
};
export type AgentContentBlock =
  | { type: "text"; text: string }
  | { type: "thinking"; thinking: string; redacted?: boolean }
  | { type: "tool_call"; id: string; name: string; arguments: Record<string, unknown> }
  | { type: "unknown" };
export type AgentToolResultDto = {
  content: AgentContentBlock[];
  details?: AgentMessageDto["details"];
  is_error: boolean;
};
export type AgentEventDto = {
  sequence_no: number;
  event_type: string;
  payload: {
    conversation_id: string;
    text?: string;
    phase?: string;
    tool_call?: Extract<AgentContentBlock, { type: "tool_call" }>;
    tool_call_id?: string;
    tool_name?: string;
    arguments?: Record<string, unknown>;
    is_error?: boolean;
    result?: AgentToolResultDto | null;
    message?: AgentMessageDto;
    code?: string;
  };
  created_at: string;
};
export type AgentEventsDto = { items: AgentEventDto[]; next_cursor: number };
export type AgentMessagesDto = {
  items: AgentMessageDto[];
  event_cursor?: number;
  next_before?: number | null;
  has_more?: boolean;
};
export type SelectionRect = { x: number; y: number; width: number; height: number };
export type SelectionDto = { id: string; rects: SelectionRect[]; revision: number; status: string; visual_state?: string };
export type DiagnosticsPreviewDto = { manifest: { build: Record<string, string>; files: { name: string; size: number }[] }; manifest_hash: string; estimated_size: number };
