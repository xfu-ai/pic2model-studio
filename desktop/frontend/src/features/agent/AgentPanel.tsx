import {
  CaretDown,
  CaretRight,
  CheckCircle,
  ClockCounterClockwise,
  ImageSquare,
  PaperPlaneRight,
  Plus,
  Robot,
  SpinnerGap,
  UserCircle,
  WarningCircle,
  Wrench,
  X,
} from "@phosphor-icons/react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type DragEvent, type ReactNode } from "react";
import ReactMarkdown, { defaultUrlTransform, type Components, type UrlTransform } from "react-markdown";
import type {
  AgentContentBlock,
  AgentEventDto,
  AgentExecutionPlanDto,
  AgentMessageDto,
  AgentMessagesDto,
  AgentToolResultDto,
  AgentConversationDto,
  ApiClient,
  AssetDto,
  WorkspaceMode,
} from "../../shared/api/client";
import { HostClient, type AgentImageDropItem } from "../../shared/host/client";

function requestId() { return crypto.randomUUID(); }
const defaultHostClient = new HostClient();
const initialMessageLimit = 20;
const suspendedSendRetryDelays = [50, 100, 200, 400];

function apiErrorCode(error: unknown) {
  if (typeof error === "object" && error !== null && "code" in error) {
    const code = (error as { code?: unknown }).code;
    if (typeof code === "string") return code;
  }
  return error instanceof Error ? error.message : "";
}

const agentSystemPrompt = [
  "You are the Pic2Model Studio desktop workflow assistant.",
  "Reply in the language of the user's latest natural-language request; Chinese is the default when ambiguous.",
  "The Tool area uses progressively disclosed, single-operation managed Tools. When the required Tool is not active, call toolbox.status with a capability query, then toolbox.load with the exact returned Tool name; loaded schemas are callable on the next model turn.",
  "Use image.normalize for deterministic dimensions, orientation, format, quality, and alpha changes. Use image.upscale_local for bundled offline 2x/4x super-resolution. Use image.upscale_provider only when the user explicitly requests external Provider processing or the local model is unsuitable.",
  "When an attached image is included directly in the current multimodal message, understand it directly and do not call image.understand_for_agent for the same question. When only a managed image reference is available, use image.understand_for_agent for ordinary visual facts.",
  "Use image.analyze_content, image.analyze_style, or image.evaluate_3d_suitability only when a persisted workflow analysis is requested or genuinely required downstream.",
  "Paid or external actions must wait for the user's desktop approval. Project creation, opening, switching, import, export destinations, settings, credentials, approvals, and visual confirmations belong to the user and desktop host.",
  "For multiview region detection, use project.get_state only when no current Tool Result already supplies the persisted set and crops. After desktop crop confirmation, its Tool Result is authoritative: call multiview 3D generation directly with that exact multiview_ref and front, side, back crop references. Never rediscover, substitute, or repair them through project.get_state, asset.list, filenames, or image analysis. A multiview_ref is the persisted set_id, never an image or selection reference.",
  "Before presenting an existing managed image, use asset.get_metadata for the known asset reference. Never use read, write, edit, or bash to locate or modify managed assets. Do not show opaque asset or Job references to the user.",
  "To place a managed image inside the final Markdown answer, write ![short descriptive label](asset:<exact_output_asset_ref>) at the intended position. Use only exact output_asset_refs returned by managed Tools; never use a filename, local path, blob URL, or remote URL for a managed image. The desktop resolves the asset reference without exposing it to the user.",
  "A Tool result with status=awaiting_ui_action means the requested desktop action has not completed yet. Explain the required desktop action and do not claim it happened until a later result confirms it.",
  "When a task terminal event arrives, continue the original goal with the next safe Tool step. Pause only for approval, a material ambiguity, or a required desktop action. Always finish with a concise user-facing summary.",
].join(" ");

function conversationAge(timestamp?: string) {
  const milliseconds = timestamp ? Date.parse(timestamp) : Number.NaN;
  if (Number.isNaN(milliseconds)) return "";
  const minutes = Math.max(0, Math.floor((Date.now() - milliseconds) / 60_000));
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d`;
  return `${Math.floor(days / 7)}w`;
}

type LiveTool = {
  id: string;
  toolName: string;
  arguments: Record<string, unknown>;
  state: "pending" | "running" | "completed";
  result?: AgentToolResultDto;
  isError?: boolean;
};

type ConversationStatus = "ready" | "working" | "waiting_approval" | "completed" | "failed";

type PlanPresentation = {
  label: string;
  tone: "ready" | "working" | "review" | "failed" | "completed";
  currentLabel: string;
};

function planPresentation(plan: AgentExecutionPlanDto): PlanPresentation {
  if (plan.state === "waiting_user") {
    const failed = plan.steps.find((step) => step.state === "failed");
    return { label: "Needs your input", tone: "review", currentLabel: failed?.label ?? "Waiting for your answer" };
  }
  const failed = plan.steps.find((step) => step.state === "failed");
  if (failed) return { label: "Needs attention", tone: "failed", currentLabel: failed.label };
  if (plan.state === "completed_with_warnings") {
    return { label: "Completed with warnings", tone: "review", currentLabel: "All planned steps finished" };
  }
  const review = plan.steps.find((step) => step.state === "review_required");
  if (review) return { label: "Review required", tone: "review", currentLabel: review.label };
  const current = plan.steps.find((step) => step.id === plan.current_step_id);
  if (plan.state === "executing" && current?.state === "running") {
    return { label: "In progress", tone: "working", currentLabel: current.label };
  }
  if (plan.state === "completed" || !current) return { label: "Completed", tone: "completed", currentLabel: "All planned steps finished" };
  return { label: "Next step", tone: "ready", currentLabel: current.label };
}

function ExecutionPlanCard({ plan, expanded, onExpandedChange }: {
  plan: AgentExecutionPlanDto;
  expanded: boolean;
  onExpandedChange: (expanded: boolean) => void;
}) {
  if (plan.fallback || !plan.steps.length) return null;
  const presentation = planPresentation(plan);
  const isRunning = presentation.tone === "working";
  return <section className={`agent-execution-plan ${presentation.tone}${expanded ? " expanded" : ""}`} aria-label="Execution plan">
    <header>
      <div className="agent-plan-heading">
        {isRunning ? <SpinnerGap className="spin" aria-hidden="true" /> : presentation.tone === "failed" || presentation.tone === "review" ? <WarningCircle weight="fill" aria-hidden="true" /> : presentation.tone === "completed" ? <CheckCircle weight="fill" aria-hidden="true" /> : <span className="agent-plan-dot" aria-hidden="true" />}
        <strong>Plan</strong>
        <span>{presentation.label}</span>
      </div>
      <button type="button" aria-expanded={expanded} onClick={() => onExpandedChange(!expanded)}>
        {expanded ? <CaretDown size={14} /> : <CaretRight size={14} />}
        {expanded ? "Hide details" : "View details"}
      </button>
    </header>
    <p className="agent-plan-summary"><strong>{presentation.currentLabel}</strong><span>{plan.goal}</span></p>
    {expanded ? <div className="agent-plan-details">
      {plan.constraints?.length ? <p className="agent-plan-constraints">Constraints: {plan.constraints.join(" · ")}</p> : null}
      <ol>
        {plan.steps.map((step) => <li key={step.id} className={step.state}>
          <span>{step.state === "succeeded" ? <CheckCircle weight="fill" /> : step.state === "review_required" || step.state === "failed" ? <WarningCircle weight="fill" /> : step.state === "running" ? <SpinnerGap className="spin" /> : <span className="agent-plan-dot" />}</span>
          <span>{step.label}{step.warning ? ` — ${step.warning}` : ""}</span>
        </li>)}
      </ol>
    </div> : null}
  </section>;
}

function providerFailureMessage(reason?: string): string {
  switch (reason) {
    case "resource_exhausted":
      return "Local Qwen stopped because GPU or system memory was exhausted. Close GPU-heavy apps, retry, or select Qwen3-VL 4B in Settings.";
    case "runner_unavailable":
      return "The local Qwen model runner stopped unexpectedly. The app will keep Ollama running; retry this message.";
    case "model_load_failed":
      return "The local Qwen model could not be loaded. Check the model status in Settings and retry.";
    case "context_overflow":
      return "This Agent conversation exceeded the local model context window. Start a new conversation or shorten the request.";
    case "vision_request":
      return "Local Qwen could not process the attached image. Try a smaller PNG, JPG, BMP, or WEBP image.";
    case "request_format":
      return "The local model rejected the Agent request format. Retry once, then check the model status in Settings.";
    default:
      return "The Agent stopped before it could finish its response. You can try again.";
  }
}
type UiAction = NonNullable<NonNullable<AgentMessageDto["details"]>["ui_action"]>;
export type AgentWorkspaceAction = {
  mode: WorkspaceMode;
  method?: "direct" | "breakdown";
  assetId?: string;
  actionId?: string;
  runId?: string;
  instruction?: string;
  jobId?: string;
  jobType?: string;
  resultAssetIds?: string[];
  prompt?: string;
  promptAssetId?: string;
  candidateCount?: number;
  aspectRatio?: string;
  analysisKind?: "content" | "style" | "suitability";
};

const workspaceModes = new Set<WorkspaceMode>([
  "empty",
  "prompt_image",
  "image",
  "compare",
  "selection",
  "target_extract",
  "candidate",
  "multiview",
  "model3d",
  "task_waiting",
  "error_diagnostics",
]);

const actionWorkspaceModes: Record<string, WorkspaceMode> = {
  compare_assets: "compare",
  open_output_folder: "image",
  confirm_multiview_regions: "multiview",
  confirm_multiview_quality: "multiview",
  capture_model_preview: "model3d",
};

const workspaceLabels: Record<WorkspaceMode, string> = {
  empty: "workspace",
  prompt_image: "image generation",
  image: "image",
  compare: "comparison",
  selection: "selection",
  target_extract: "target extraction",
  candidate: "candidates",
  multiview: "multiview",
  model3d: "3D preview",
  task_waiting: "tasks",
  error_diagnostics: "diagnostics",
};

function workspaceModeForApprovalTool(toolName: string | undefined, argumentsValue?: Record<string, unknown>): WorkspaceMode | null {
  if (toolName === "generate_images") return "prompt_image";
  if (toolName === "split_image") return "target_extract";
  if (toolName === "prepare_multiview") return "multiview";
  if (toolName === "generate_model3d") return "model3d";
  if (toolName === "edit_image" && argumentsValue?.operation === "inpaint") return "selection";
  return null;
}

function workspaceModeForAction(
  action: UiAction | null | undefined,
  toolName?: string,
  argumentsValue?: Record<string, unknown>,
): WorkspaceMode | null {
  if (!action) return null;
  if (action.type === "select_rectangle") {
    return toolName === "split_image" || action.workspace_mode === "target_extract" || action.purpose === "target_extract"
      ? "target_extract"
      : "selection";
  }
  const typeMode = action.type ? actionWorkspaceModes[action.type] : undefined;
  if (typeMode) return typeMode;
  if (action.type === "approval_required" || action.type === "confirm_external_paid") {
    const approvalMode = workspaceModeForApprovalTool(toolName, argumentsValue);
    if (approvalMode) return approvalMode;
  }
  const requested = action.workspace_mode;
  if (!requested || requested === "working") return null;
  return workspaceModes.has(requested as WorkspaceMode) ? requested as WorkspaceMode : null;
}

function workspaceModeForJobType(jobType: string | undefined): WorkspaceMode | null {
  if (!jobType) return null;
  if (jobType === "prompt.rewrite") return "prompt_image";
  if (jobType.startsWith("image.analyze_") || jobType === "image.evaluate_3d_suitability") return "compare";
  if ([
    "image.generate",
    "image.transform",
    "image.generate_variants",
  ].includes(jobType)) return "prompt_image";
  if ([
    "image.upscale",
    "image.upscale_local",
    "image.remove_background",
    "image.remove_background_local",
    "image.inpaint_selection",
    "element.export_transparent",
  ].includes(jobType)) return "candidate";
  if (jobType === "element.split") return "target_extract";
  if (jobType.startsWith("multiview.")) return "multiview";
  if (jobType.startsWith("model3d.")) return "model3d";
  if (jobType.startsWith("image.")) return "image";
  return null;
}

function workspaceModeForCompletedTool(tool: LiveTool): WorkspaceMode | null {
  if (tool.state !== "completed" || tool.isError || tool.result?.details?.status !== "succeeded") return null;
  if (["split_image", "image.split_alpha_components", "image.split_grid"].includes(tool.toolName)) return "target_extract";
  if (
    tool.toolName === "edit_image"
  ) {
    const operation = typeof tool.arguments.operation === "string"
      ? tool.arguments.operation
      : "";
    if (operation === "normalize") return "image";
    if (["trim_transparent", "remove_background_local"].includes(operation)) return "target_extract";
  }
  if (tool.toolName === "image.normalize") return "image";
  if (["image.trim_transparent", "image.remove_background_local"].includes(tool.toolName)) return "target_extract";
  return null;
}

function actionKey(action: UiAction) {
  return action.action_id || `${action.type ?? "action"}:${action.workspace_mode ?? "workspace"}`;
}

function workspaceRequest(
  mode: WorkspaceMode,
  action?: UiAction | null,
  argumentsValue?: Record<string, unknown>,
): AgentWorkspaceAction {
  const splitMode = argumentsValue?.split_mode;
  const source = action?.asset_id
    ?? (typeof argumentsValue?.source_asset_ref === "string" ? argumentsValue.source_asset_ref : undefined)
    ?? (typeof argumentsValue?.source_asset_id === "string" ? argumentsValue.source_asset_id : undefined);
  const promptAssetId = typeof argumentsValue?.prompt_asset_ref === "string"
    ? argumentsValue.prompt_asset_ref
    : typeof argumentsValue?.prompt_asset_id === "string"
      ? argumentsValue.prompt_asset_id
      : undefined;
  const prompt = typeof argumentsValue?.prompt === "string" ? argumentsValue.prompt : undefined;
  return {
    mode,
    method: mode === "target_extract"
      ? action?.method ?? (splitMode === "element" ? "breakdown" : "direct")
      : undefined,
    assetId: source,
    actionId: action?.action_id,
    runId: action?.run_id,
    instruction: action?.instruction ?? (
      mode === "target_extract" && splitMode === "boxsplit"
        ? "请框选要提取的目标；确认后由本页提交生成独立目标图。"
        : undefined
    ),
    prompt: mode === "prompt_image" ? prompt : undefined,
    promptAssetId: mode === "prompt_image" ? promptAssetId : undefined,
    candidateCount: mode === "prompt_image" && typeof argumentsValue?.candidate_count === "number"
      ? argumentsValue.candidate_count
      : undefined,
    aspectRatio: mode === "prompt_image" && typeof argumentsValue?.aspect_ratio === "string"
      ? argumentsValue.aspect_ratio
      : undefined,
  };
}

function initialConversationStatus(conversation: AgentConversationDto) : ConversationStatus {
  if (conversation.state === "running") return "working";
  if (conversation.state === "error") return "failed";
  return conversation.message_count > 0 ? "completed" : "ready";
}

function contentBlocks(message: AgentMessageDto): AgentContentBlock[] {
  return Array.isArray(message.content)
    ? message.content
    : [{ type: "text", text: message.content }];
}

function textContent(content: AgentContentBlock[] | undefined) {
  return (content ?? [])
    .filter((item): item is Extract<AgentContentBlock, { type: "text" }> => item.type === "text")
    .map((item) => item.text)
    .join("\n");
}

function visibleAssistantText(text: string) {
  // Older conversations may contain the now-retired model-authored outline.
  // The durable structured Plan card is the single user-visible planning surface.
  return text.replace(/<execution_outline>[\s\S]*?<\/execution_outline>\s*/gi, "").trim();
}

const chatImageAssetTypes = new Set(["generated_image", "source_image", "annotation", "crop", "multiview", "preview"]);
const maxChatImageAttachments = 4;
const maxAgentInputAttachments = 8;
const agentImageExtensions = new Set(["png", "jpg", "jpeg", "bmp", "webp"]);
const managedImagePrefixes = ["managed-asset://", "asset://", "asset:"];

function decodedMarkdownImageSource(source: string) {
  const trimmed = source.trim().replace(/^<|>$/g, "");
  try {
    return decodeURIComponent(trimmed);
  } catch {
    return trimmed;
  }
}

function managedImageCandidate(source: string) {
  const decoded = decodedMarkdownImageSource(source);
  const prefix = managedImagePrefixes.find((item) => decoded.toLowerCase().startsWith(item));
  return (prefix ? decoded.slice(prefix.length) : decoded).split(/[?#]/, 1)[0].trim();
}

function managedAssetFromMarkdownSource(source: string | undefined, assets: AssetDto[]) {
  if (!source) return undefined;
  const candidate = managedImageCandidate(source);
  const exactId = assets.find((asset) => asset.id === candidate);
  if (exactId) return exactId;
  const fileName = candidate.replace(/\\/g, "/").split("/").pop();
  if (!fileName) return undefined;
  const matchingNames = assets.filter((asset) => asset.name === fileName);
  return matchingNames.length === 1 ? matchingNames[0] : undefined;
}

function markdownImageSources(markdown: string) {
  const sources: string[] = [];
  const pattern = /!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))/g;
  for (const match of markdown.matchAll(pattern)) {
    const source = match[1] ?? match[2];
    if (source) sources.push(source);
  }
  return sources;
}

const managedMarkdownUrlTransform: UrlTransform = (url, key, node) => {
  if (key === "src" && node.tagName === "img" && managedImagePrefixes.some((prefix) => url.toLowerCase().startsWith(prefix))) {
    return url;
  }
  return defaultUrlTransform(url);
};

function useChatImageAssets(projectId: string | undefined, api: ApiClient | undefined, assetIds: string[]) {
  const [assets, setAssets] = useState<AssetDto[]>([]);
  const stableIds = assetIds.join(",");

  useEffect(() => {
    let active = true;
    if (!projectId || !api || !assetIds.length) {
      setAssets([]);
      return () => { active = false; };
    }
    void api.assets(projectId).then((allAssets) => {
      if (!active) return;
      const byId = new Map(allAssets.map((asset) => [asset.id, asset]));
      setAssets(assetIds.map((id) => byId.get(id)).filter((asset): asset is AssetDto => Boolean(asset) && chatImageAssetTypes.has(asset!.asset_type)));
    }).catch(() => { if (active) setAssets([]); });
    return () => { active = false; };
  }, [api, projectId, stableIds]); // assetIds is reconstructed from durable messages on each render.

  return assets;
}

function useAssetPreviewUrl(projectId: string, api: ApiClient, asset: AssetDto) {
  const [url, setUrl] = useState("");
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    let objectUrl = "";
    setUrl("");
    setFailed(false);
    const load = async () => {
      let blob: Blob;
      if (typeof api.assetThumbnail === "function") {
        try {
          blob = await api.assetThumbnail(projectId, asset.id, controller.signal);
        } catch {
          if (controller.signal.aborted) return;
          blob = await api.assetContent(projectId, asset.id, controller.signal);
        }
      } else {
        blob = await api.assetContent(projectId, asset.id, controller.signal);
      }
      if (!blob.type.startsWith("image/")) throw new Error("Managed asset is not an image");
      objectUrl = URL.createObjectURL(blob);
      if (active) setUrl(objectUrl);
    };
    void load().catch(() => { if (active && !controller.signal.aborted) setFailed(true); });
    return () => {
      active = false;
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [api, asset.id, projectId]);

  return { url, failed };
}

function InlineManagedImage({ projectId, api, asset, alt }: {
  projectId: string;
  api: ApiClient;
  asset: AssetDto | undefined;
  alt?: string;
}) {
  if (!asset) return <span className="agent-inline-image unavailable" role="note">{alt || "Image preview unavailable"}</span>;
  return <InlineManagedImageAsset projectId={projectId} api={api} asset={asset} alt={alt} />;
}

function InlineManagedImageAsset({ projectId, api, asset, alt }: {
  projectId: string;
  api: ApiClient;
  asset: AssetDto;
  alt?: string;
}) {
  const { url, failed } = useAssetPreviewUrl(projectId, api, asset);
  const label = alt?.trim() || asset.name;
  if (failed) return <span className="agent-inline-image unavailable" role="note">{label}</span>;
  return <span className={`agent-inline-image${url ? "" : " loading"}`}>
    {url ? <img src={url} alt={label} /> : <span aria-label={`Loading image: ${label}`} />}
    <small title={label}>{label}</small>
  </span>;
}

function MarkdownContent({ children, projectId, api, imageAssets = [] }: {
  children: string;
  projectId?: string;
  api?: ApiClient;
  imageAssets?: AssetDto[];
}) {
  const components: Components = {
    img: ({ src, alt }) => projectId && api
      ? <InlineManagedImage projectId={projectId} api={api} asset={managedAssetFromMarkdownSource(src, imageAssets)} alt={alt ?? undefined} />
      : <span className="agent-inline-image unavailable" role="note">{alt || "Image preview unavailable"}</span>,
  };
  return <ReactMarkdown components={components} urlTransform={managedMarkdownUrlTransform}>{children}</ReactMarkdown>;
}

function toolPreview(toolName: string, result: AgentToolResultDto | undefined) {
  const output = textContent(result?.content);
  if (!output) return "";
  if (result?.is_error) return output.split("\n").slice(0, 10).join("\n");
  if (toolName === "bash") {
    const lines = output.trim().split("\n");
    const tail = lines.slice(-5);
    return [
      ...(lines.length > tail.length ? [`… (${lines.length - tail.length} earlier lines; show tool output to expand)`] : []),
      ...tail,
    ].join("\n");
  }
  // Pi's registered tool renderers keep successful output out of the
  // transcript. AIPic only renders a compact, purpose-built asset summary.
  if (toolName !== "asset.list") return "";
  try {
    const assets = JSON.parse(output) as Array<{ name?: string; asset_type?: string; is_current?: boolean }>;
    if (!Array.isArray(assets)) return "";
    const current = assets.find((asset) => asset.is_current);
    const names = assets.slice(0, 20).map((asset) => {
      const label = asset.name || "Unnamed asset";
      return asset.asset_type ? `${label} · ${asset.asset_type}` : label;
    });
    return [
      `${assets.length} assets`,
      current ? `Current: ${current.name || "Unnamed asset"}` : "Current: none",
    ].join("\n");
  } catch {
    return "";
  }
}

function resultMessage(tool: LiveTool): AgentMessageDto | undefined {
  if (!tool.result) return undefined;
  return {
    id: `live-result-${tool.id}`,
    role: "tool_result",
    tool_call_id: tool.id,
    tool_name: tool.toolName,
    content: tool.result.content,
    details: tool.result.details,
    is_error: tool.result.is_error,
  };
}

function AssistantMessage({ message, streamingText, projectId, api, imageAssetIds }: {
  message: AgentMessageDto;
  streamingText?: string;
  projectId?: string;
  api?: ApiClient;
  imageAssetIds?: string[];
}) {
  const managedAssets = useChatImageAssets(projectId, api, imageAssetIds ?? []);
  const blocks = streamingText === undefined
    ? contentBlocks(message)
    : [
        ...(streamingText
          ? [{ type: "text", text: streamingText } satisfies AgentContentBlock]
          : []),
      ];
  const rendered: ReactNode[] = [];
  for (let index = 0; index < blocks.length; index += 1) {
    const block = blocks[index];
    const visibleText = block.type === "text" ? visibleAssistantText(block.text) : "";
    if (visibleText) {
      rendered.push(<div className="agent-assistant-text" key={`text-${index}`}><MarkdownContent projectId={projectId} api={api} imageAssets={managedAssets}>{visibleText}</MarkdownContent></div>);
      continue;
    }
  }
  if (!rendered.length) return null;
  return <article className="agent-message assistant">
    <header className="agent-message-role">
      <span className="agent-role-icon" aria-hidden="true"><Robot size={16} weight="fill" /></span>
      <strong>Agent</strong>
    </header>
    <div className="agent-assistant-content">
      {rendered}
      {projectId && api && managedAssets.length ? <ChatImages projectId={projectId} api={api} assets={managedAssets.filter((asset) => !renderedMarkdownAssetIds(blocks, managedAssets).has(asset.id))} /> : null}
    </div>
  </article>;
}

function UserMessage({ message, projectId, api }: { message: AgentMessageDto; projectId: string; api: ApiClient }) {
  const attachments = (message.attachments ?? []).map((attachment) => ({
    id: attachment.asset_id,
    name: attachment.name,
    mime_type: attachment.mime_type,
    asset_type: "source_image",
    is_current: false,
    metadata: {},
  } satisfies AssetDto));
  return <article className="agent-message user">
    <header className="agent-message-role">
      <span className="agent-role-icon" aria-hidden="true"><UserCircle size={16} weight="fill" /></span>
      <strong>You</strong>
    </header>
    <div className="agent-message-body"><MarkdownContent>{textContent(contentBlocks(message))}</MarkdownContent></div>
    {attachments.length ? <section className="agent-user-attachments" aria-label="Attached images">
      {attachments.map((asset) => <ChatImage key={asset.id} projectId={projectId} api={api} asset={asset} />)}
    </section> : null}
  </article>;
}

function ChatImage({ projectId, api, asset }: { projectId: string; api: ApiClient; asset: AssetDto }) {
  const { url, failed } = useAssetPreviewUrl(projectId, api, asset);
  if (!url) return failed ? <span className="agent-chat-image-error">{asset.name} could not be previewed.</span> : null;
  return <figure className="agent-chat-image"><img src={url} alt={`Project image: ${asset.name}`} /><figcaption>{asset.name}</figcaption></figure>;
}

function ChatImages({ projectId, api, assets }: { projectId: string; api: ApiClient; assets: AssetDto[] }) {
  if (!assets.length) return null;
  const visible = assets.slice(0, maxChatImageAttachments);
  return <section className="agent-chat-images" aria-label="Project images"><p>Images</p><div>{visible.map((asset) => <ChatImage key={asset.id} projectId={projectId} api={api} asset={asset} />)}</div>{assets.length > visible.length && <small>{assets.length - visible.length} more images are available in Assets.</small>}</section>;
}

function outputAssetIds(message: AgentMessageDto | undefined) {
  return (message?.details?.output_asset_ids ?? []).filter((id): id is string => typeof id === "string" && id.length > 0);
}

function completedJobResult(message: AgentMessageDto) {
  const jobId = message.details?.job?.job_id;
  const assetIds = message.details?.output_asset_ids;
  return typeof jobId === "string" && Array.isArray(assetIds)
    ? { jobId, assetIds: assetIds.filter((id): id is string => typeof id === "string" && id.length > 0) }
    : null;
}

function renderedMarkdownAssetIds(blocks: AgentContentBlock[], assets: AssetDto[]) {
  const ids = new Set<string>();
  for (const block of blocks) {
    if (block.type !== "text") continue;
    for (const source of markdownImageSources(block.text)) {
      const asset = managedAssetFromMarkdownSource(source, assets);
      if (asset) ids.add(asset.id);
    }
  }
  return ids;
}

function terminalJobId(message: AgentMessageDto) {
  return (message.details?.status === "succeeded" || message.details?.status === "failed")
    ? message.details?.job?.job_id ?? null
    : null;
}

function queuedJobId(message: AgentMessageDto) {
  const detailed = message.details?.job?.job_id;
  if (detailed && message.details?.status === "queued") return detailed;
  return null;
}

function pendingJobFromMessages(messages: AgentMessageDto[]) {
  const terminal = new Set(
    messages.map(terminalJobId).filter((id): id is string => Boolean(id)),
  );
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const jobId = queuedJobId(messages[index]);
    if (jobId && !terminal.has(jobId)) return jobId;
  }
  return null;
}

function pendingJobWorkspaceActionFromMessages(messages: AgentMessageDto[], jobId: string | null): AgentWorkspaceAction | null {
  if (!jobId) return null;
  const calls = new Map<string, Extract<AgentContentBlock, { type: "tool_call" }>>();
  for (const message of messages) {
    for (const block of contentBlocks(message)) {
      if (block.type === "tool_call") calls.set(block.id, block);
    }
  }
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (queuedJobId(message) !== jobId) continue;
    const call = message.tool_call_id ? calls.get(message.tool_call_id) : undefined;
    const toolName = message.tool_name ?? call?.name;
    const argumentsValue = call?.arguments;
    const mode = workspaceModeForApprovalTool(toolName, argumentsValue)
      ?? workspaceModeForJobType(message.details?.job?.job_type);
    return mode ? workspaceRequest(mode, undefined, argumentsValue) : null;
  }
  return null;
}

function isInternalJobContinuation(message: AgentMessageDto) {
  return Boolean(terminalJobId(message));
}

function finalAssistantImageAssetIds(messages: AgentMessageDto[], assistantIndex: number) {
  const message = messages[assistantIndex];
  if (
    message.role !== "assistant"
    || message.stop_reason === "error"
    || message.stop_reason === "aborted"
    || contentBlocks(message).some((block) => block.type === "tool_call")
  ) return [];
  const outputGroups: string[][] = [];
  for (let index = assistantIndex - 1; index >= 0; index -= 1) {
    const prior = messages[index];
    const completedJob = completedJobResult(prior);
    if (completedJob) {
      if (completedJob.assetIds.length) outputGroups.push(completedJob.assetIds);
      continue;
    }
    if (prior.role === "user") break;
    if (prior.role !== "tool_result" || prior.is_error || prior.tool_name === "asset.list") continue;
    const assetIds = outputAssetIds(prior);
    if (assetIds.length) outputGroups.push(assetIds);
  }
  const seen = new Set<string>();
  return outputGroups.reverse().flat().filter((id) => {
    if (seen.has(id)) return false;
    seen.add(id);
    return true;
  });
}

function ToolExecution({ tool, expanded, children }: { tool: LiveTool; expanded: boolean; children?: ReactNode }) {
  const result = resultMessage(tool);
  const failed = Boolean(tool.isError || result?.is_error || result?.details?.status === "failed");
  const state = failed ? "Failed" : tool.state === "pending" ? "Pending" : tool.state === "running" ? "Running" : "Completed";
  const output = textContent(tool.result?.content);
  const preview = toolPreview(tool.toolName, tool.result);
  const StateIcon = failed ? WarningCircle : tool.state === "completed" ? CheckCircle : SpinnerGap;
  return <article className={`agent-tool-execution ${failed ? "error" : ""} ${tool.state}`} aria-label={`${tool.toolName} tool ${state}`}>
    <header className="agent-tool-heading">
      <div className="agent-tool-identity">
        <span className="agent-tool-icon" aria-hidden="true"><Wrench size={16} weight="bold" /></span>
        <strong>{tool.toolName}</strong>
      </div>
      <span className="agent-tool-state"><StateIcon className={tool.state === "completed" || failed ? undefined : "spin"} size={16} weight="bold" aria-hidden="true" />{state}</span>
    </header>
    {preview && <div className="agent-tool-preview">{preview}</div>}
    {expanded && output && <pre className="agent-tool-output">{output}</pre>}
    {children}
  </article>;
}

function upsertMessage(messages: AgentMessageDto[], message: AgentMessageDto) {
  const index = messages.findIndex((item) => item.id === message.id);
  if (index === -1) return [...messages, message];
  return messages.map((item) => item.id === message.id ? message : item);
}

export function AgentPanel({ projectId, api, host = defaultHostClient, onJobQueued, onWorkspaceAction }: { projectId: string; api: ApiClient; host?: HostClient; onJobQueued?(jobId: string, workspaceMode: WorkspaceMode | null, jobType?: string): void; onWorkspaceAction?(action: AgentWorkspaceAction): void }) {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<AgentMessageDto[]>([]);
  const [liveTools, setLiveTools] = useState<Record<string, LiveTool>>({});
  const [executionPlan, setExecutionPlan] = useState<AgentExecutionPlanDto | null>(null);
  const [planExpanded, setPlanExpanded] = useState(false);
  const [planning, setPlanning] = useState(false);
  const [streamingText, setStreamingText] = useState("");
  const [draft, setDraft] = useState("");
  const [attachments, setAttachments] = useState<AssetDto[]>([]);
  const [attachmentBusy, setAttachmentBusy] = useState(false);
  const [attachmentError, setAttachmentError] = useState("");
  const [attachmentDragActive, setAttachmentDragActive] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [conversationStatus, setConversationStatus] = useState<ConversationStatus>("ready");
  const [expanded, setExpanded] = useState(false);
  const [approvalState, setApprovalState] = useState<Record<string, string>>({});
  const [pendingJobId, setPendingJobId] = useState<string | null>(null);
  const [pendingJobWorkspaceMode, setPendingJobWorkspaceMode] = useState<WorkspaceMode | null>(null);
  const [pendingJobWorkspaceAction, setPendingJobWorkspaceAction] = useState<AgentWorkspaceAction | null>(null);
  const [conversations, setConversations] = useState<AgentConversationDto[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [historyBefore, setHistoryBefore] = useState<number | null>(null);
  const [hasOlderMessages, setHasOlderMessages] = useState(false);
  const [loadingOlderMessages, setLoadingOlderMessages] = useState(false);
  const conversationRef = useRef<HTMLDivElement>(null);
  const attachmentInputRef = useRef<HTMLInputElement>(null);
  const eventCursor = useRef(0);
  const switchVersion = useRef(0);
  const initializedProject = useRef<string | null>(null);
  const handledWorkspaceActions = useRef(new Set<string>());
  const toolArguments = useRef(new Map<string, Record<string, unknown>>());
  const pendingJobWorkspaceActionRef = useRef<AgentWorkspaceAction | null>(null);
  const approvalWorkspaceContext = useRef(new Map<string, AgentWorkspaceAction>());
  const suppressedJobTerminalNotifications = useRef(new Set<string>());
  const nativeDropHandler = useRef<(items: AgentImageDropItem[]) => void>(() => undefined);
  const loadingOlderMessagesRef = useRef(false);
  const prependScrollAnchor = useRef<{ scrollHeight: number; scrollTop: number } | null>(null);
  // The desktop shell can rerender while workspace state is being persisted.
  // Keep the poll cadence durable across such renders so a recreated effect
  // cannot turn its immediate poll into a request loop.
  const eventPollInFlight = useRef(false);
  const nextEventPollAt = useRef(0);
  const eventApi = (api as Partial<ApiClient>).agentEvents;
  const liveToolsById = useMemo(() => new Map(Object.values(liveTools).map((tool) => [tool.id, tool])), [liveTools]);

  const restorePendingUiActions = useCallback((page: AgentMessagesDto) => {
    const pending = page.pending_ui_actions ?? [];
    const pendingIds = new Set(pending.map((action) => action.tool_call_id));
    setLiveTools((tools) => {
      const synchronized = Object.fromEntries(Object.entries(tools).filter(([toolCallId, tool]) => (
        tool.result?.details?.status !== "awaiting_ui_action" || pendingIds.has(toolCallId)
      )));
      for (const action of pending) {
        if (!action.tool_call_id || !action.tool_name || !action.result) continue;
        synchronized[action.tool_call_id] = {
          id: action.tool_call_id,
          toolName: action.tool_name,
          arguments: tools[action.tool_call_id]?.arguments ?? {},
          state: "completed",
          isError: Boolean(action.result.is_error),
          result: action.result,
        };
      }
      return synchronized;
    });
  }, []);

  const applyLatestMessagePage = useCallback((page: AgentMessagesDto) => {
    setMessages(page.items);
    setHistoryBefore(page.next_before ?? null);
    setHasOlderMessages(Boolean(page.has_more));
    restorePendingUiActions(page);
    setExecutionPlan(page.execution_plan ?? null);
    const hasPendingUiAction = (page.pending_ui_actions?.length ?? 0) > 0;
    if (hasPendingUiAction) {
      setBusy(false);
      setConversationStatus("waiting_approval");
    } else {
      setConversationStatus((status) => status === "waiting_approval" ? "ready" : status);
    }
  }, [restorePendingUiActions]);

  const rememberExistingWorkspaceActions = useCallback((items: AgentMessageDto[]) => {
    handledWorkspaceActions.current = new Set(
      items
        .map((message) => message.details?.ui_action)
        .filter((action): action is UiAction => Boolean(action))
        .map(actionKey),
    );
  }, []);

  const openWorkspaceAction = useCallback((
    action: UiAction | null | undefined,
    automatic = false,
    toolName?: string,
    argumentsValue?: Record<string, unknown>,
  ) => {
    const mode = workspaceModeForAction(action, toolName, argumentsValue);
    if (!mode || !action) return;
    const key = actionKey(action);
    if (automatic && handledWorkspaceActions.current.has(key)) return;
    handledWorkspaceActions.current.add(key);
    onWorkspaceAction?.(workspaceRequest(mode, action, argumentsValue));
  }, [onWorkspaceAction]);

  const refreshConversations = useCallback(async () => {
    const conversationApi = (api as Partial<ApiClient>).agentConversations;
    if (typeof conversationApi !== "function") return [];
    const result = await conversationApi.call(api, projectId);
    setConversations(result.items);
    return result.items;
  }, [api, projectId]);

  const activateConversation = useCallback(async (conversation: AgentConversationDto) => {
    const version = switchVersion.current + 1;
    switchVersion.current = version;
    eventCursor.current = 0;
    setSwitching(true);
    setHistoryOpen(false);
    setError("");
    // Do not begin replaying events until the bounded transcript establishes
    // the durable event cursor; otherwise historical message events would
    // repopulate the whole conversation after the initial 20-message load.
    setConversationId(null);
    setAttachments([]);
    setAttachmentError("");
    setMessages([]);
    setHistoryBefore(null);
    setHasOlderMessages(false);
    setLoadingOlderMessages(false);
    loadingOlderMessagesRef.current = false;
    prependScrollAnchor.current = null;
    setLiveTools({});
    setExecutionPlan(null);
    setPlanExpanded(false);
    setPlanning(false);
    toolArguments.current.clear();
    suppressedJobTerminalNotifications.current.clear();
    setStreamingText("");
    setApprovalState({});
    setPendingJobId(null);
    setPendingJobWorkspaceMode(null);
    setPendingJobWorkspaceAction(null);
    pendingJobWorkspaceActionRef.current = null;
    setConversationStatus(initialConversationStatus(conversation));
    try {
      const restored = await api.agentMessages(projectId, conversation.id, initialMessageLimit);
      if (switchVersion.current !== version) return;
      eventCursor.current = restored.event_cursor ?? 0;
      setConversationId(conversation.id);
      rememberExistingWorkspaceActions(restored.items);
      applyLatestMessagePage(restored);
      const pendingJob = pendingJobFromMessages(restored.items);
      setPendingJobId(pendingJob);
      const pendingWorkspaceAction = pendingJobWorkspaceActionFromMessages(restored.items, pendingJob);
      pendingJobWorkspaceActionRef.current = pendingWorkspaceAction;
      setPendingJobWorkspaceAction(pendingWorkspaceAction);
      setBusy(conversation.state === "running");
    } catch {
      if (switchVersion.current === version) {
        setBusy(false);
        setError("This conversation could not be restored. Try another conversation.");
      }
    } finally {
      if (switchVersion.current === version) setSwitching(false);
    }
  }, [api, applyLatestMessagePage, projectId, rememberExistingWorkspaceActions]);

  const createConversation = useCallback(async () => {
    setSwitching(true);
    setHistoryOpen(false);
    setError("");
    try {
      const created = await api.createAgentConversation(projectId, agentSystemPrompt, requestId());
      switchVersion.current += 1;
      eventCursor.current = 0;
      handledWorkspaceActions.current.clear();
      toolArguments.current.clear();
      suppressedJobTerminalNotifications.current.clear();
      setConversationId(created.id);
      setAttachments([]);
      setAttachmentError("");
      setMessages([]);
      setHistoryBefore(null);
      setHasOlderMessages(false);
      setLoadingOlderMessages(false);
      loadingOlderMessagesRef.current = false;
      prependScrollAnchor.current = null;
      setLiveTools({});
      setStreamingText("");
    setApprovalState({});
    setPendingJobId(null);
    setPendingJobWorkspaceMode(null);
    setPendingJobWorkspaceAction(null);
    pendingJobWorkspaceActionRef.current = null;
      setBusy(false);
      setConversationStatus("ready");
      setConversations((items) => [created, ...items.filter((item) => item.id !== created.id)]);
    } catch {
      setError("Unable to create a new Agent conversation. Configure a model provider first.");
    } finally {
      setSwitching(false);
    }
  }, [api, projectId]);

  useEffect(() => {
    // React Strict Mode intentionally runs effects twice in development.  A
    // bootstrap session is durable, so running this path twice created pairs
    // of empty conversations in the same project.
    if (initializedProject.current === projectId) return;
    initializedProject.current = projectId;
    let live = true;
    const restoreOrCreate = async () => {
      try {
        const recent = (await refreshConversations())[0];
        if (recent) {
          await activateConversation(recent);
          return;
        }
        await createConversation();
      } catch {
        if (live) setError("Unable to start the Agent conversation. Configure a model provider first.");
      }
    };
    void restoreOrCreate();
    return () => { live = false; };
  }, [activateConversation, createConversation, refreshConversations]);

  useEffect(() => {
    if (!conversationId || typeof eventApi !== "function") return;
    let live = true;
    const process = (event: AgentEventDto) => {
      const payload = event.payload;
      if (event.event_type === "message.started") {
        setStreamingText("");
        setBusy(true);
        setConversationStatus("working");
      }
      if (event.event_type === "message.accepted") setError("");
      if (event.event_type === "execution.planning.started") {
        setError("");
        setPlanning(true);
      }
      if (event.event_type === "execution.plan.updated" && payload.plan) {
        setExecutionPlan(payload.plan);
        setPlanExpanded(false);
        setPlanning(false);
      }
      if (event.event_type === "message.delta") setStreamingText((text) => text + (payload.text ?? ""));
      if (event.event_type === "message.completed" && payload.message) {
        setMessages((items) => upsertMessage(items, payload.message!));
        if (payload.message.role === "assistant") {
          setStreamingText("");
        }
      }
      if (event.event_type === "tool.call" && payload.tool_call) {
        const call = payload.tool_call;
        toolArguments.current.set(call.id, call.arguments);
        setLiveTools((tools) => ({
          ...tools,
          [call.id]: { id: call.id, toolName: call.name, arguments: call.arguments, state: "pending" },
        }));
      }
      if (event.event_type === "tool.running" && payload.tool_call_id) {
        if (payload.arguments) toolArguments.current.set(payload.tool_call_id, payload.arguments);
        setLiveTools((tools) => ({
          ...tools,
          [payload.tool_call_id!]: {
            id: payload.tool_call_id!,
            toolName: payload.tool_name ?? tools[payload.tool_call_id!]?.toolName ?? "AIPic",
            arguments: payload.arguments ?? tools[payload.tool_call_id!]?.arguments ?? {},
            state: "running",
          },
        }));
      }
      if (event.event_type === "tool.completed" && payload.tool_call_id) {
        const completedArguments = toolArguments.current.get(payload.tool_call_id) ?? {};
        if (
          payload.tool_name === "control_job"
          && completedArguments.action === "cancel"
          && typeof completedArguments.job_ref === "string"
          && !payload.is_error
        ) {
          suppressedJobTerminalNotifications.current.add(completedArguments.job_ref);
          setPendingJobId((current) => current === completedArguments.job_ref ? null : current);
          setPendingJobWorkspaceMode(null);
          setPendingJobWorkspaceAction(null);
          pendingJobWorkspaceActionRef.current = null;
        }
        const directPromptAssetId = typeof payload.result?.details?.data?.prompt_asset_id === "string"
          ? payload.result.details.data.prompt_asset_id
          : undefined;
        const requestedPromptWorkspaceAction = workspaceRequest(
          "prompt_image", undefined, completedArguments,
        );
        const promptWorkspaceAction = payload.tool_name === "generate_images"
          ? {
              ...requestedPromptWorkspaceAction,
              promptAssetId: directPromptAssetId ?? requestedPromptWorkspaceAction.promptAssetId,
            }
          : null;
        const approvalId = payload.result?.details?.ui_action?.action_id;
        if (approvalId && promptWorkspaceAction) {
          approvalWorkspaceContext.current.set(approvalId, promptWorkspaceAction);
          handledWorkspaceActions.current.add(approvalId);
          onWorkspaceAction?.({ ...promptWorkspaceAction, actionId: approvalId });
        }
        const queuedJobId = payload.result?.details?.status === "queued"
          ? payload.result.details.job?.job_id
          : undefined;
        if (queuedJobId) {
          const queuedWorkspaceMode = workspaceModeForApprovalTool(
            payload.tool_name,
            completedArguments,
          );
          const queuedWorkspaceAction = promptWorkspaceAction ?? (queuedWorkspaceMode
            ? workspaceRequest(queuedWorkspaceMode, undefined, completedArguments)
            : null);
          setPendingJobId(queuedJobId);
          setPendingJobWorkspaceMode(queuedWorkspaceMode);
          pendingJobWorkspaceActionRef.current = queuedWorkspaceAction;
          setPendingJobWorkspaceAction(queuedWorkspaceAction);
          onJobQueued?.(queuedJobId, queuedWorkspaceMode, payload.result?.details?.job?.job_type);
        }
        setLiveTools((tools) => ({
          ...tools,
          [payload.tool_call_id!]: {
            id: payload.tool_call_id!,
            toolName: payload.tool_name ?? tools[payload.tool_call_id!]?.toolName ?? "AIPic",
            arguments: tools[payload.tool_call_id!]?.arguments ?? {},
            state: "completed",
            isError: payload.is_error,
            result: payload.result ?? undefined,
          },
        }));
      }
      if (event.event_type === "agent.idle") {
        setStreamingText("");
        void api.agentMessages(projectId, conversationId, initialMessageLimit).then((result) => {
          if (live) applyLatestMessagePage(result);
        }).catch(() => { if (live) setError("The Agent stopped, but its conversation could not be refreshed."); });
      }
      if (event.event_type === "conversation.suspended") {
        setStreamingText("");
        setPlanning(false);
        setBusy(false);
        setConversationStatus("waiting_approval");
        void refreshConversations().catch(() => undefined);
      }
      if (event.event_type === "conversation.completed") {
        setStreamingText("");
        setPlanning(false);
        setLiveTools({});
        setBusy(false);
        setError("");
        setConversationStatus("completed");
        void api.agentMessages(projectId, conversationId, initialMessageLimit).then((result) => {
          if (live) applyLatestMessagePage(result);
        }).catch(() => { if (live) setError("The Agent completed, but its conversation could not be refreshed."); });
        void refreshConversations().catch(() => undefined);
      }
      if (event.event_type === "conversation.failed") {
        setStreamingText("");
        setPlanning(false);
      setLiveTools({});
      setExecutionPlan(null);
      setPlanning(false);
        setBusy(false);
        setConversationStatus("failed");
        setError(providerFailureMessage(payload.reason));
        void api.agentMessages(projectId, conversationId, initialMessageLimit).then((result) => {
          if (live) applyLatestMessagePage(result);
        }).catch(() => undefined);
        void refreshConversations().catch(() => undefined);
      }
    };
    const poll = async () => {
      const now = Date.now();
      if (eventPollInFlight.current || now < nextEventPollAt.current) return;
      eventPollInFlight.current = true;
      nextEventPollAt.current = now + 250;
      try {
        const page = await eventApi.call(api, projectId, conversationId, eventCursor.current);
        if (!live) return;
        for (const event of page.items) process(event);
        eventCursor.current = page.next_cursor;
      } catch {
        // A later replay request resumes from the durable event cursor.
      } finally {
        eventPollInFlight.current = false;
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 250);
    return () => { live = false; window.clearInterval(timer); };
  }, [api, applyLatestMessagePage, conversationId, eventApi, onJobQueued, projectId, refreshConversations]);

  useLayoutEffect(() => {
    const conversation = conversationRef.current;
    if (!conversation) return;
    const anchor = prependScrollAnchor.current;
    if (anchor) {
      conversation.scrollTop = anchor.scrollTop + conversation.scrollHeight - anchor.scrollHeight;
      prependScrollAnchor.current = null;
      return;
    }
    conversation.scrollTop = conversation.scrollHeight;
  }, [busy, error, liveTools, messages, streamingText]);

  const loadOlderMessages = useCallback(async () => {
    if (
      !conversationId
      || !hasOlderMessages
      || historyBefore === null
      || loadingOlderMessagesRef.current
    ) return;
    const conversation = conversationRef.current;
    if (!conversation) return;
    loadingOlderMessagesRef.current = true;
    setLoadingOlderMessages(true);
    prependScrollAnchor.current = {
      scrollHeight: conversation.scrollHeight,
      scrollTop: conversation.scrollTop,
    };
    try {
      const page = await api.agentMessages(
        projectId,
        conversationId,
        initialMessageLimit,
        historyBefore,
      );
      setMessages((current) => {
        const currentIds = new Set(current.map((message) => message.id));
        return [
          ...page.items.filter((message) => !currentIds.has(message.id)),
          ...current,
        ];
      });
      setHistoryBefore(page.next_before ?? null);
      setHasOlderMessages(Boolean(page.has_more));
      if (!page.items.length) prependScrollAnchor.current = null;
    } catch {
      prependScrollAnchor.current = null;
      setError("Earlier messages could not be loaded. Scroll up to try again.");
    } finally {
      loadingOlderMessagesRef.current = false;
      setLoadingOlderMessages(false);
    }
  }, [api, conversationId, hasOlderMessages, historyBefore, projectId]);

  const handleConversationScroll = useCallback(() => {
    const conversation = conversationRef.current;
    if (conversation && conversation.scrollTop <= 80) void loadOlderMessages();
  }, [loadOlderMessages]);

  useEffect(() => {
    for (const message of messages) openWorkspaceAction(message.details?.ui_action, true, message.tool_name);
    for (const tool of Object.values(liveTools)) {
      openWorkspaceAction(tool.result?.details?.ui_action, true, tool.toolName, tool.arguments);
      const completedMode = workspaceModeForCompletedTool(tool);
      const completedKey = completedMode ? `completed-tool:${tool.id}:${completedMode}` : null;
      if (completedMode && completedKey && !handledWorkspaceActions.current.has(completedKey)) {
        handledWorkspaceActions.current.add(completedKey);
        const operation = typeof tool.arguments.operation === "string"
          ? tool.arguments.operation
          : undefined;
        onWorkspaceAction?.({
          ...workspaceRequest(completedMode, undefined, tool.arguments),
          jobType: ["split_image", "image.split_alpha_components", "image.split_grid"].includes(tool.toolName)
            ? "image.split_local"
            : operation
              ? `${tool.toolName}.${operation}`
              : tool.toolName,
          resultAssetIds: tool.result?.details?.output_asset_ids ?? [],
        });
      }
    }
  }, [liveTools, messages, onWorkspaceAction, openWorkspaceAction]);

  useEffect(() => {
    if (!pendingJobId || !conversationId) return;
    let live = true;
    let sending = false;
    const poll = async () => {
      if (sending) return;
      try {
        const job = await api.job(projectId, pendingJobId);
        if (suppressedJobTerminalNotifications.current.has(pendingJobId)) {
          setPendingJobId(null);
          setPendingJobWorkspaceMode(null);
          return;
        }
        const terminal = ["succeeded", "failed", "cancelled"].includes(job.status)
          || (job.status === "interrupted" && Boolean(job.error));
        // A no-error interrupted state is a resumable handoff boundary (for
        // example remote completion before artifact download). The worker
        // resumes it automatically, so reporting failure here would race the
        // real terminal result.
        if (!terminal || !live) return;
        sending = true;
        if (job.status === "succeeded") {
          const resultMode = pendingJobWorkspaceMode ?? workspaceModeForJobType(job.job_type);
          const pendingWorkspaceAction = pendingJobWorkspaceAction ?? pendingJobWorkspaceActionRef.current;
          const analysisKind = job.job_type === "image.analyze_content"
            ? "content"
            : job.job_type === "image.analyze_style"
              ? "style"
              : job.job_type === "image.evaluate_3d_suitability"
                ? "suitability"
              : undefined;
          if (resultMode) onWorkspaceAction?.({
            ...(pendingWorkspaceAction?.mode === resultMode
              ? pendingWorkspaceAction
              : { mode: resultMode }),
            mode: resultMode,
            jobId: job.id,
            jobType: job.job_type,
            resultAssetIds: job.output_asset_ids,
            ...(analysisKind ? { assetId: job.input_asset_ids?.[0] } : {}),
            analysisKind,
          });
        }
        setPendingJobId(null);
        setPendingJobWorkspaceMode(null);
        setPendingJobWorkspaceAction(null);
        pendingJobWorkspaceActionRef.current = null;
      } catch {
        // The task center remains authoritative; transient polling failures are retried.
      } finally {
        sending = false;
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 2_500);
    return () => { live = false; window.clearInterval(timer); };
  }, [api, applyLatestMessagePage, conversationId, eventApi, onWorkspaceAction, pendingJobId, pendingJobWorkspaceAction, pendingJobWorkspaceMode, projectId]);

  const send = async () => {
    if ((!draft.trim() && !attachments.length) || !conversationId || busy || switching || attachmentBusy) return;
    const content = draft.trim() || "Use the attached images as inputs.";
    const sentAttachments = attachments;
    const optimisticMessageId = requestId();
    const priorConversationStatus = conversationStatus;
    setDraft("");
    setAttachments([]);
    setBusy(true);
    setPlanning(false);
    setError("");
    setConversationStatus("working");
    setMessages((items) => [...items, {
      id: optimisticMessageId,
      role: "user",
      content,
      attachments: sentAttachments.map((attachment) => ({
        asset_id: attachment.id,
        name: attachment.name,
        mime_type: attachment.mime_type ?? "image/*",
      })),
    }]);
    try {
      // A replacement instruction entered while an approval is visible means
      // the pending external action must not run. Close those suspended Tool
      // Calls before starting the new turn so the model transcript remains
      // ordered (Tool Result before the replacement User message).
      const pendingApprovalIds = Object.values(liveTools)
        .map((tool) => tool.result?.details?.ui_action)
        .filter((action) => action?.type === "approval_required" || action?.type === "confirm_external_paid")
        .map((action) => action!.action_id)
        .filter((approvalId): approvalId is string => (
          typeof approvalId === "string" && approvalId.length > 0 && !approvalState[approvalId]
        ));
      for (const approvalId of [...new Set(pendingApprovalIds)]) {
        setApprovalState((state) => ({ ...state, [approvalId]: "Submitting…" }));
        await api.decideApproval(projectId, approvalId, false, requestId());
        setApprovalState((state) => ({
          ...state,
          [approvalId]: "The external operation was declined by your new instruction.",
        }));
      }

      const sendRequestId = requestId();
      const submit = () => api.sendAgentMessage(
        projectId,
        conversationId,
        content,
        sendRequestId,
        typeof eventApi === "function" ? false : true,
        sentAttachments.map((attachment) => attachment.id),
      );
      let retryIndex = 0;
      while (true) {
        try {
          await submit();
          break;
        } catch (error) {
          if (apiErrorCode(error) !== "AGENT_BUSY" || retryIndex >= suspendedSendRetryDelays.length) {
            throw error;
          }
          await new Promise((resolve) => window.setTimeout(resolve, suspendedSendRetryDelays[retryIndex]));
          retryIndex += 1;
        }
      }
      const result = await api.agentMessages(projectId, conversationId, initialMessageLimit);
      applyLatestMessagePage(result);
      if (typeof eventApi !== "function") setBusy(false);
    } catch (sendError) {
      setMessages((items) => items.filter((message) => message.id !== optimisticMessageId));
      setDraft(content);
      setAttachments(sentAttachments);
      setBusy(false);
      if (apiErrorCode(sendError) === "AGENT_BUSY") {
        setConversationStatus(priorConversationStatus);
        setError("The Agent is still finishing the previous step. Your message was kept below; send it again in a moment.");
      } else {
        setConversationStatus("failed");
        setError("The Agent could not complete this request. Check the model settings and try again.");
      }
    }
  };

  const importAttachmentFiles = async (files: File[]) => {
    if (busy || switching || attachmentBusy) return;
    const remaining = maxAgentInputAttachments - attachments.length;
    if (remaining <= 0) {
      setAttachmentError(`You can attach up to ${maxAgentInputAttachments} images to one message.`);
      return;
    }
    const candidates = files.slice(0, remaining);
    const unsupported = candidates.find((file) => {
      const extension = file.name.split(".").pop()?.toLowerCase();
      return !agentImageExtensions.has(extension ?? "");
    });
    if (!candidates.length || unsupported) {
      setAttachmentError("Choose PNG, JPG, BMP, or WEBP images.");
      return;
    }
    setAttachmentBusy(true);
    setAttachmentError("");
    const imported: AssetDto[] = [];
    let failed = 0;
    for (const file of candidates) {
      try {
        const bytes = Array.from(new Uint8Array(await file.arrayBuffer()));
        const capabilityId = await host.stageDroppedFile(projectId, "source_image", file.name, bytes);
        imported.push(await api.importImage(projectId, capabilityId, `agent-image-import-${requestId()}`, undefined, file.name));
      } catch {
        failed += 1;
      }
    }
    if (imported.length) {
      setAttachments((current) => {
        const byId = new Map(current.map((item) => [item.id, item]));
        for (const item of imported) byId.set(item.id, item);
        return [...byId.values()].slice(0, maxAgentInputAttachments);
      });
    }
    if (files.length > remaining) {
      setAttachmentError(`Only the first ${remaining} images were added; the per-message limit is ${maxAgentInputAttachments}.`);
    } else if (failed) {
      setAttachmentError(`${failed} image${failed === 1 ? "" : "s"} could not be imported.`);
    }
    setAttachmentBusy(false);
  };

  const importNativeAttachmentCapabilities = useCallback(async (items: AgentImageDropItem[]) => {
    if (busy || switching || attachmentBusy || !items.length) return;
    const remaining = maxAgentInputAttachments - attachments.length;
    if (remaining <= 0) {
      setAttachmentError(`You can attach up to ${maxAgentInputAttachments} images to one message.`);
      return;
    }
    const candidates = items.slice(0, remaining);
    setAttachmentBusy(true);
    setAttachmentError("");
    const imported: AssetDto[] = [];
    let failed = 0;
    for (const item of candidates) {
      try {
        imported.push(await api.importImage(
          projectId,
          item.capabilityId,
          `agent-native-image-import-${requestId()}`,
          undefined,
          item.fileName,
        ));
      } catch {
        failed += 1;
      }
    }
    if (imported.length) {
      setAttachments((current) => {
        const byId = new Map(current.map((item) => [item.id, item]));
        for (const item of imported) byId.set(item.id, item);
        return [...byId.values()].slice(0, maxAgentInputAttachments);
      });
    }
    if (items.length > remaining) {
      setAttachmentError(`Only the first ${remaining} images were added; the per-message limit is ${maxAgentInputAttachments}.`);
    } else if (failed) {
      setAttachmentError(`${failed} image${failed === 1 ? "" : "s"} could not be imported.`);
    }
    setAttachmentBusy(false);
  }, [api, attachmentBusy, attachments.length, busy, projectId, switching]);
  nativeDropHandler.current = (items) => {
    void importNativeAttachmentCapabilities(items);
  };

  useEffect(() => {
    if (
      typeof host.setAgentDropProject !== "function"
      || typeof host.listenAgentImageDrop !== "function"
    ) return;
    let disposed = false;
    let unlisten: (() => void) | undefined;
    const connect = async () => {
      try {
        unlisten = await host.listenAgentImageDrop((items) => {
          if (!disposed) nativeDropHandler.current(items);
        });
        if (disposed) {
          unlisten();
          return;
        }
        await host.setAgentDropProject(projectId);
      } catch {
        // The HTML file-drop path remains available outside the Tauri host.
      }
    };
    void connect();
    return () => {
      disposed = true;
      unlisten?.();
      void host.setAgentDropProject(null).catch(() => undefined);
    };
  }, [host, projectId]);

  const dropAttachment = async (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setAttachmentDragActive(false);
    if (busy || switching || attachmentBusy) return;
    await importAttachmentFiles([...event.dataTransfer.files]);
  };

  const decide = async (
    approvalId: string,
    approved: boolean,
    queuedWorkspaceMode?: WorkspaceMode | null,
  ) => {
    setApprovalState((state) => ({ ...state, [approvalId]: "Submitting…" }));
    try {
      const result = await api.decideApproval(projectId, approvalId, approved, requestId());
      setApprovalState((state) => ({
        ...state,
        [approvalId]: approved ? (result.status === "queued" ? `Approved; task queued${result.job?.job_id ? ` (${result.job.job_id})` : ""}.` : result.summary) : "The external operation was declined.",
      }));
      setBusy(false);
      setPlanning(false);
      setConversationStatus("ready");
      if (approved) {
        const queuedJobId = result.status === "queued" ? result.job?.job_id : undefined;
        if (queuedJobId) {
          const queuedWorkspaceAction = approvalWorkspaceContext.current.get(approvalId);
          const workspaceMode = queuedWorkspaceAction?.mode ?? queuedWorkspaceMode ?? null;
          const actionWithJob = queuedWorkspaceAction
            ? { ...queuedWorkspaceAction, jobId: queuedJobId }
            : null;
          setPendingJobId(queuedJobId);
          setPendingJobWorkspaceMode(workspaceMode);
          pendingJobWorkspaceActionRef.current = actionWithJob;
          setPendingJobWorkspaceAction(actionWithJob);
          if (actionWithJob) onWorkspaceAction?.(actionWithJob);
          onJobQueued?.(queuedJobId, workspaceMode, result.job?.job_type);
        }
      }
    } catch {
      setBusy(false);
      setApprovalState((state) => ({ ...state, [approvalId]: "The approval could not be submitted. Try again." }));
    }
  };

  const toolActions = (
    message: AgentMessageDto | undefined,
    argumentsValue?: Record<string, unknown>,
  ) => {
    const action = message?.details?.ui_action;
    const approvalId = action?.type === "approval_required" || action?.type === "confirm_external_paid" ? action.action_id : undefined;
    const workspaceMode = workspaceModeForAction(action, message?.tool_name, argumentsValue);
    return <>
      {workspaceMode && <div className="agent-approval"><p>{action?.type === "capture_model_preview" ? "The model preview must be captured explicitly in the desktop workspace." : `Continue this action in the ${workspaceLabels[workspaceMode]} workspace.`}</p><button className="primary" onClick={() => openWorkspaceAction(action, false, message?.tool_name, argumentsValue)}>Open {workspaceLabels[workspaceMode]}</button></div>}
      {approvalId && <div className="agent-approval"><p>{approvalState[approvalId] ?? "This action invokes an external service. Confirm before continuing."}</p>{!approvalState[approvalId] && <div><button onClick={() => void decide(approvalId, false)}>Decline</button><button className="primary" onClick={() => void decide(approvalId, true, workspaceMode)}>Approve and run</button></div>}</div>}
    </>;
  };

  const resultByCallId = new Map(messages.filter((message) => message.role === "tool_result" && message.tool_call_id).map((message) => [message.tool_call_id!, message]));
  const renderedCallIds = new Set<string>();
  const transcript: ReactNode[] = [];
  for (let messageIndex = 0; messageIndex < messages.length; messageIndex += 1) {
    const message = messages[messageIndex];
    const jobResult = completedJobResult(message);
    if (message.role === "user" && !jobResult && !isInternalJobContinuation(message)) {
      transcript.push(<UserMessage key={message.id} message={message} projectId={projectId} api={api} />);
    }
    if (message.role === "assistant") {
      transcript.push(<AssistantMessage key={message.id} message={message} projectId={projectId} api={api} imageAssetIds={finalAssistantImageAssetIds(messages, messageIndex)} />);
      for (const block of contentBlocks(message)) {
        if (block.type !== "tool_call") continue;
        renderedCallIds.add(block.id);
        const result = resultByCallId.get(block.id);
        const liveTool = liveToolsById.get(block.id);
        const tool: LiveTool = liveTool ?? {
          id: block.id,
          toolName: block.name,
          arguments: block.arguments,
          state: result ? "completed" : "pending",
          isError: result?.is_error,
          result: result ? { content: contentBlocks(result), details: result.details, is_error: Boolean(result.is_error) } : undefined,
        };
      transcript.push(<ToolExecution key={`tool-${block.id}`} tool={tool} expanded={expanded}>{toolActions(result ?? resultMessage(tool), tool.arguments)}</ToolExecution>);
      }
    }
    if (message.role === "tool_result" && message.tool_call_id && !renderedCallIds.has(message.tool_call_id)) {
      const tool: LiveTool = {
        id: message.tool_call_id,
        toolName: message.tool_name ?? "AIPic",
        arguments: {},
        state: "completed",
        isError: message.is_error,
        result: { content: contentBlocks(message), details: message.details, is_error: Boolean(message.is_error) },
      };
      transcript.push(<ToolExecution key={`orphan-tool-${message.id}`} tool={tool} expanded={expanded}>{toolActions(message, tool.arguments)}</ToolExecution>);
    }
  }
  for (const tool of liveToolsById.values()) {
    if (!renderedCallIds.has(tool.id)) transcript.push(<ToolExecution key={`live-tool-${tool.id}`} tool={tool} expanded={expanded}>{toolActions(resultMessage(tool), tool.arguments)}</ToolExecution>);
  }

  return <div className={`agent-live-panel${executionPlan && !executionPlan.fallback && executionPlan.steps.length > 0 ? " has-plan" : ""}`}>
    <div className="agent-session-toolbar">
      <div className="agent-session-actions">
        <button type="button" aria-label="Conversation history" aria-expanded={historyOpen} onClick={() => setHistoryOpen((open) => !open)} title="Conversation history"><ClockCounterClockwise size={17} />History</button>
        <button type="button" aria-label="New conversation" disabled={busy || switching} onClick={() => void createConversation()} title="New conversation"><Plus size={17} />New</button>
      </div>
      {false && historyOpen && <section className="agent-session-history" aria-label="Project conversations">
        <header><strong>Project conversations</strong><span>{conversations.length}</span></header>
        {conversations.length ? <div className="agent-session-list">
          {conversations.map((conversation) => {
            const active = conversation.id === conversationId;
            const unavailable = busy || switching || conversation.state === "running";
            return <button
              type="button"
              key={conversation.id}
              className={active ? "active" : ""}
              aria-current={active ? "true" : undefined}
              disabled={active || unavailable}
              onClick={() => void activateConversation(conversation)}
            >
              <span className="agent-session-preview">{conversation.preview?.trim() || "New conversation"}</span>
              <small>{conversation.message_count} messages{conversationAge(conversation.updated_at) ? ` · ${conversationAge(conversation.updated_at)}` : ""}{conversation.state === "running" ? " · working" : ""}</small>
            </button>;
          })}
        </div> : <p>No saved conversations yet.</p>}
      </section>}
    </div>
    {executionPlan && !executionPlan.fallback && executionPlan.steps.length > 0 && <div className="agent-plan-region">
      <ExecutionPlanCard plan={executionPlan} expanded={planExpanded} onExpandedChange={setPlanExpanded} />
    </div>}
    <div className="agent-conversation" ref={conversationRef} aria-live="polite" onScroll={handleConversationScroll}>
      {loadingOlderMessages && <p className="agent-history-loading" role="status">Loading earlier messages…</p>}
      {!hasOlderMessages && historyBefore === null && messages.length >= initialMessageLimit
        ? <p className="agent-history-start">Start of conversation</p>
        : null}
      <div className="agent-conversation-controls"><button type="button" onClick={() => setExpanded((value) => !value)}>{expanded ? <CaretDown size={14} /> : <CaretRight size={14} />}{expanded ? "Hide tool output" : "Show tool output"}</button></div>
      {transcript.length ? transcript : <p className="agent-empty-state">Describe the model you want to make, or ask the Agent to continue with the current asset.</p>}
      {streamingText && <AssistantMessage message={{ id: "streaming", role: "assistant", content: "" }} streamingText={streamingText} />}
      <p className={`agent-run-status ${conversationStatus}`} role="status">
        {conversationStatus === "working" && <>{planning ? <><SpinnerGap className="spin" /> Understanding the task and preparing a plan…</> : <><SpinnerGap className="spin" /> Agent is working…</>}</>}
        {!pendingJobId && conversationStatus === "waiting_approval" && "Waiting for your approval to continue this external action."}
        {pendingJobId && !busy && "Background task is running. You can keep using the Agent; it will continue here when the task finishes."}
        {!pendingJobId && conversationStatus === "completed" && executionPlan?.state === "waiting_user" && "Agent is waiting for your decision before it can continue."}
        {!pendingJobId && conversationStatus === "completed" && executionPlan?.state !== "waiting_user" && executionPlan?.steps.some((step) => step.state === "failed") && "This response is complete. The plan needs attention before it can continue."}
        {!pendingJobId && conversationStatus === "completed" && !executionPlan?.steps.some((step) => step.state === "failed") && executionPlan?.state === "completed_with_warnings" && "Agent completed this response with warnings."}
        {!pendingJobId && conversationStatus === "completed" && executionPlan?.state !== "waiting_user" && !(executionPlan?.steps.some((step) => step.state === "failed") || executionPlan?.state === "completed_with_warnings") && "Agent completed this response."}
        {conversationStatus === "failed" && "Agent stopped before completing this response. You can try again."}
        {!pendingJobId && conversationStatus === "ready" && "Agent is ready."}
      </p>
      {error && <p className="agent-error">{error}</p>}
    </div>
    <div
      className={`agent-compose${attachmentDragActive ? " drag-active" : ""}`}
      onDragEnter={(event) => { event.preventDefault(); setAttachmentDragActive(true); }}
      onDragOver={(event) => { event.preventDefault(); setAttachmentDragActive(true); }}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setAttachmentDragActive(false);
      }}
      onDrop={(event) => void dropAttachment(event)}
    >
      {attachments.length ? <section className="agent-compose-attachments" aria-label="Images ready to attach">
        <header><strong>{attachments.length} image{attachments.length === 1 ? "" : "s"} ready</strong><button type="button" onClick={() => setAttachments([])}>Clear</button></header>
        <div>{attachments.map((attachment) => <article className="agent-compose-attachment" key={attachment.id}>
          <ChatImage projectId={projectId} api={api} asset={attachment} />
          <strong>{attachment.name}</strong>
          <button type="button" aria-label={`Remove ${attachment.name}`} onClick={() => setAttachments((items) => items.filter((item) => item.id !== attachment.id))}><X size={16} /></button>
        </article>)}</div>
      </section> : null}
      {attachmentError ? <p className="agent-compose-error" role="alert"><WarningCircle size={16} />{attachmentError}</p> : null}
      <div className="agent-compose-row">
        <input
          ref={attachmentInputRef}
          className="agent-attachment-input"
          type="file"
          accept=".png,.jpg,.jpeg,.bmp,.webp,image/png,image/jpeg,image/bmp,image/webp"
          multiple
          aria-label="Choose images to attach"
          onChange={(event) => {
            const files = [...(event.currentTarget.files ?? [])];
            event.currentTarget.value = "";
            void importAttachmentFiles(files);
          }}
        />
        <button type="button" className="agent-attach-button" aria-label="Attach images" title="Attach images" disabled={busy || switching || attachmentBusy || attachments.length >= maxAgentInputAttachments} onClick={() => attachmentInputRef.current?.click()}>
          {attachmentBusy ? <SpinnerGap className="spin" size={18} /> : <ImageSquare size={18} />}
        </button>
        <textarea aria-label="Message the Agent" disabled={switching} value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(); } }} placeholder="Describe the next step, or drop an image here" />
        <button className="primary" aria-label="Send to Agent" disabled={!conversationId || busy || switching || attachmentBusy || (!draft.trim() && !attachments.length)} onClick={() => void send()}><PaperPlaneRight size={18} /></button>
      </div>
    </div>
    {historyOpen && <section className="agent-session-history" aria-label="Project conversations">
      <header><strong>Project conversations</strong><span>{conversations.length}</span><button type="button" aria-label="Close conversation history" onClick={() => setHistoryOpen(false)}>Close</button></header>
      {conversations.length ? <div className="agent-session-list">
        {conversations.map((conversation) => {
          const active = conversation.id === conversationId;
          const unavailable = busy || switching || conversation.state === "running";
          return <button
            type="button"
            key={conversation.id}
            className={active ? "active" : ""}
            aria-current={active ? "true" : undefined}
            disabled={active || unavailable}
            onClick={() => void activateConversation(conversation)}
          >
            <span className="agent-session-preview">{conversation.preview?.trim() || "New conversation"}</span>
            <small>{conversation.message_count} messages{conversationAge(conversation.updated_at) ? ` 路 ${conversationAge(conversation.updated_at)}` : ""}{conversation.state === "running" ? " 路 working" : ""}</small>
          </button>;
        })}
      </div> : <p>No saved conversations yet.</p>}
    </section>}
  </div>;
}
