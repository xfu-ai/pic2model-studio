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
import ReactMarkdown from "react-markdown";
import type {
  AgentContentBlock,
  AgentEventDto,
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

const agentSystemPrompt = "You are the AIPicToModel desktop workflow assistant. Use the fixed managed facade tools for image, selection, prompt, multiview, 3D generation, preview, conversion, packaging, and durable Job requests. Paid actions must wait for the user's desktop approval. Project creation, opening, switching, import, export destinations, settings, credentials, approvals, and visual confirmations belong to the user and desktop host. Images belong with a final user-facing answer, never in a tool explanation. Before presenting an existing managed image, inspect it with inspect_workspace(view=asset_details). Do not show opaque asset or Job references to the user. Never use read, write, edit, or bash to locate, inspect, read, or modify managed result assets; if a managed result is not directly inspectable through a facade Tool, report only the structured result already provided by the desktop. A Tool result with status=awaiting_ui_action means the requested desktop action has not completed yet: say that the matching workspace was opened and tell the user what must be completed there; never claim that a preview was opened, captured, confirmed, or otherwise completed until a later result explicitly confirms it. Selection references returned by multiview region detection are not asset references and must never be passed to inspect_workspace(view=asset_details). When an internal terminal-event instruction says FINAL RESPONSE ONLY or says not to call more tools, reply immediately without calling any Tool; that instruction takes precedence over optional inspection or continuation guidance. After tool work is complete, always provide a concise final summary; never intentionally finish with an empty response.";

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

type ConversationStatus = "ready" | "working" | "completed" | "failed";
type UiAction = NonNullable<NonNullable<AgentMessageDto["details"]>["ui_action"]>;
export type AgentWorkspaceAction = {
  mode: WorkspaceMode;
  method?: "direct" | "breakdown";
  assetId?: string;
  actionId?: string;
  runId?: string;
  instruction?: string;
  jobId?: string;
  resultAssetIds?: string[];
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
    "image.upscale",
    "image.upscale_local",
    "image.remove_background",
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
  if (tool.toolName === "prepare_prompt") return "prompt_image";
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

function MarkdownContent({ children }: { children: string }) {
  return <ReactMarkdown>{children}</ReactMarkdown>;
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

function AssistantMessage({ message, hideThinking, streamingText, projectId, api, imageAssetIds }: {
  message: AgentMessageDto;
  hideThinking: boolean;
  streamingText?: string;
  projectId?: string;
  api?: ApiClient;
  imageAssetIds?: string[];
}) {
  const blocks = streamingText === undefined
    ? contentBlocks(message)
    : [{ type: "text", text: streamingText } satisfies AgentContentBlock];
  const rendered: ReactNode[] = [];
  for (let index = 0; index < blocks.length; index += 1) {
    const block = blocks[index];
    if (block.type === "text" && block.text.trim()) {
      rendered.push(<div className="agent-assistant-text" key={`text-${index}`}><MarkdownContent>{block.text.trim()}</MarkdownContent></div>);
      continue;
    }
    if (block.type === "thinking") {
      const thinking: string[] = [];
      while (blocks[index]?.type === "thinking") {
        const next = blocks[index] as Extract<AgentContentBlock, { type: "thinking" }>;
        if (next.thinking.trim()) thinking.push(next.thinking.trim());
        index += 1;
      }
      index -= 1;
      if (thinking.length) {
        rendered.push(
          <div className="agent-thinking" key={`thinking-${index}`}>
            {hideThinking ? "Thinking..." : <MarkdownContent>{thinking.join("\n\n")}</MarkdownContent>}
          </div>,
        );
      }
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
      {projectId && api && imageAssetIds?.length ? <ChatImages projectId={projectId} api={api} assetIds={imageAssetIds} /> : null}
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

const chatImageAssetTypes = new Set(["generated_image", "source_image", "annotation", "crop", "multiview", "preview"]);
const maxChatImageAttachments = 4;
const maxAgentInputAttachments = 8;
const agentImageExtensions = new Set(["png", "jpg", "jpeg", "bmp", "webp"]);

function ChatImage({ projectId, api, asset }: { projectId: string; api: ApiClient; asset: AssetDto }) {
  const [url, setUrl] = useState("");

  useEffect(() => {
    let active = true;
    let objectUrl = "";
    void api.assetContent(projectId, asset.id).then((blob) => {
      if (!blob.type.startsWith("image/")) return;
      objectUrl = URL.createObjectURL(blob);
      if (active) setUrl(objectUrl);
    }).catch(() => undefined);
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [api, asset.id, projectId]);

  if (!url) return null;
  return <figure className="agent-chat-image"><img src={url} alt={`Project image: ${asset.name}`} /><figcaption>{asset.name}</figcaption></figure>;
}

function ChatImages({ projectId, api, assetIds }: { projectId: string; api: ApiClient; assetIds: string[] }) {
  const [assets, setAssets] = useState<AssetDto[]>([]);
  const stableIds = assetIds.join(",");

  useEffect(() => {
    let active = true;
    if (!assetIds.length) {
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

  if (!assets.length) return null;
  const visible = assets.slice(0, maxChatImageAttachments);
  return <section className="agent-chat-images" aria-label="Project images"><p>Images</p><div>{visible.map((asset) => <ChatImage key={asset.id} projectId={projectId} api={api} asset={asset} />)}</div>{assets.length > visible.length && <small>{assets.length - visible.length} more images are available in Assets.</small>}</section>;
}

function outputAssetIds(message: AgentMessageDto | undefined) {
  return (message?.details?.output_asset_ids ?? []).filter((id): id is string => typeof id === "string" && id.length > 0);
}

function completedJobResult(message: AgentMessageDto) {
  if (message.role !== "user") return null;
  const text = textContent(contentBlocks(message));
  const match = /^Task job_id=([^\s]+) completed, result_(asset|selection)_ids=([^.]*)\./.exec(text);
  if (!match) return null;
  return {
    jobId: match[1],
    assetIds: match[2] === "asset" ? match[3].split(",").filter((id) => id && id !== "none") : [],
  };
}

function terminalJobId(message: AgentMessageDto) {
  if (message.role !== "user") return null;
  return /^Task job_id=([^\s]+) (?:completed|ended with status=)/.exec(
    textContent(contentBlocks(message)),
  )?.[1] ?? null;
}

function queuedJobId(message: AgentMessageDto) {
  const detailed = message.details?.job?.job_id;
  if (detailed && message.details?.status === "queued") return detailed;
  if (message.role !== "user") return null;
  return /The user approved the external operation; job_id=([^\s.]+)/.exec(
    textContent(contentBlocks(message)),
  )?.[1] ?? null;
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

function isInternalJobContinuation(message: AgentMessageDto) {
  return Boolean(terminalJobId(message))
    || (
      message.role === "user"
      && textContent(contentBlocks(message)).startsWith(
        "The user approved the external operation",
      )
    );
}

function finalAssistantImageAssetIds(messages: AgentMessageDto[], assistantIndex: number) {
  const message = messages[assistantIndex];
  if (
    message.role !== "assistant"
    || message.stop_reason === "error"
    || message.stop_reason === "aborted"
    || contentBlocks(message).some((block) => block.type === "tool_call")
  ) return [];
  for (let index = assistantIndex - 1; index >= 0; index -= 1) {
    const prior = messages[index];
    const completedJob = completedJobResult(prior);
    if (completedJob) return completedJob.assetIds;
    if (prior.role === "user") return [];
    if (prior.role !== "tool_result" || prior.is_error || prior.tool_name === "asset.list") continue;
    const assetIds = outputAssetIds(prior);
    if (assetIds.length) return assetIds;
  }
  return [];
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

export function AgentPanel({ projectId, api, host = defaultHostClient, onJobQueued, onWorkspaceAction }: { projectId: string; api: ApiClient; host?: HostClient; onJobQueued?(): void; onWorkspaceAction?(action: AgentWorkspaceAction): void }) {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<AgentMessageDto[]>([]);
  const [liveTools, setLiveTools] = useState<Record<string, LiveTool>>({});
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
  const [hideThinking, setHideThinking] = useState(false);
  const [approvalState, setApprovalState] = useState<Record<string, string>>({});
  const [pendingJobId, setPendingJobId] = useState<string | null>(null);
  const [pendingJobWorkspaceMode, setPendingJobWorkspaceMode] = useState<WorkspaceMode | null>(null);
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
  const suppressedJobTerminalNotifications = useRef(new Set<string>());
  const nativeDropHandler = useRef<(items: AgentImageDropItem[]) => void>(() => undefined);
  const loadingOlderMessagesRef = useRef(false);
  const prependScrollAnchor = useRef<{ scrollHeight: number; scrollTop: number } | null>(null);
  const eventApi = (api as Partial<ApiClient>).agentEvents;
  const liveToolsById = useMemo(() => new Map(Object.values(liveTools).map((tool) => [tool.id, tool])), [liveTools]);

  const applyLatestMessagePage = useCallback((page: AgentMessagesDto) => {
    setMessages(page.items);
    setHistoryBefore(page.next_before ?? null);
    setHasOlderMessages(Boolean(page.has_more));
  }, []);

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
    toolArguments.current.clear();
    suppressedJobTerminalNotifications.current.clear();
    setStreamingText("");
    setApprovalState({});
    setPendingJobId(null);
    setPendingJobWorkspaceMode(null);
    setConversationStatus(initialConversationStatus(conversation));
    try {
      const restored = await api.agentMessages(projectId, conversation.id, initialMessageLimit);
      if (switchVersion.current !== version) return;
      eventCursor.current = restored.event_cursor ?? 0;
      setConversationId(conversation.id);
      rememberExistingWorkspaceActions(restored.items);
      applyLatestMessagePage(restored);
      setPendingJobId(pendingJobFromMessages(restored.items));
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
    let polling = false;
    const process = (event: AgentEventDto) => {
      const payload = event.payload;
      if (event.event_type === "message.started") setStreamingText("");
      if (event.event_type === "message.delta") setStreamingText((text) => text + (payload.text ?? ""));
      if (event.event_type === "message.completed" && payload.message) {
        setMessages((items) => upsertMessage(items, payload.message!));
        if (payload.message.role === "assistant") setStreamingText("");
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
        }
        const queuedJobId = payload.result?.details?.status === "queued"
          ? payload.result.details.job?.job_id
          : undefined;
        if (queuedJobId) {
          setPendingJobId(queuedJobId);
          onJobQueued?.();
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
        setLiveTools({});
        void api.agentMessages(projectId, conversationId, initialMessageLimit).then((result) => {
          if (live) applyLatestMessagePage(result);
        }).catch(() => { if (live) setError("The Agent stopped, but its conversation could not be refreshed."); });
      }
      if (event.event_type === "conversation.completed") {
        setStreamingText("");
        setLiveTools({});
        setBusy(false);
        setConversationStatus("completed");
        void api.agentMessages(projectId, conversationId, initialMessageLimit).then((result) => {
          if (live) applyLatestMessagePage(result);
        }).catch(() => { if (live) setError("The Agent completed, but its conversation could not be refreshed."); });
        void refreshConversations().catch(() => undefined);
      }
      if (event.event_type === "conversation.failed") {
        setStreamingText("");
        setLiveTools({});
        setBusy(false);
        setConversationStatus("failed");
        setError("The Agent stopped before it could finish its response. You can try again.");
        void api.agentMessages(projectId, conversationId, initialMessageLimit).then((result) => {
          if (live) applyLatestMessagePage(result);
        }).catch(() => undefined);
        void refreshConversations().catch(() => undefined);
      }
    };
    const poll = async () => {
      if (polling) return;
      polling = true;
      try {
        const page = await eventApi.call(api, projectId, conversationId, eventCursor.current);
        if (!live) return;
        for (const event of page.items) process(event);
        eventCursor.current = page.next_cursor;
      } catch {
        // A later replay request resumes from the durable event cursor.
      } finally {
        polling = false;
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
        onWorkspaceAction?.(workspaceRequest(completedMode));
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
        setBusy(true);
        const completion = job.status === "succeeded"
          ? job.job_type === "multiview.detect_regions"
            ? `Task job_id=${job.id} completed, result_selection_ids=${job.output_asset_ids.join(",") || "none"}. The multiview regions were detected successfully. These are selection references, not asset references: do not pass them to inspect_workspace(view=asset_details). Reply to the user now with a concise completion summary and do not call more tools unless the user asks.`
            : job.job_type?.startsWith("image.analyze_") || job.job_type === "image.evaluate_3d_suitability"
              ? `Task job_id=${job.id} completed, result_asset_ids=${job.output_asset_ids.join(",") || "none"}. This is a managed analysis document, not a displayable image. FINAL RESPONSE ONLY: report that the requested analysis succeeded and name its analysis type. Do not call inspect_workspace, read, write, edit, bash, or any other Tool, and do not expose opaque references.`
              : job.job_type?.startsWith("image.")
              ? `Task job_id=${job.id} completed, result_asset_ids=${job.output_asset_ids.join(",") || "none"}. The desktop has already validated and attached these generated images. FINAL RESPONSE ONLY: reply now with a concise completion summary. Do not call inspect_workspace or any other Tool, and do not expose opaque references.`
              : job.job_type === "prompt.rewrite"
                ? `Task job_id=${job.id} completed, result_asset_ids=${job.output_asset_ids.join(",") || "none"}. The managed prompt rewrite succeeded. Continue the user's requested multi-step workflow if one remains; otherwise reply with a concise completion summary.`
                : `Task job_id=${job.id} completed, result_asset_ids=${job.output_asset_ids.join(",") || "none"}. FINAL RESPONSE ONLY: reply with a concise completion summary. Do not call inspect_workspace or any other Tool, do not expose opaque references, and do not claim a UI action completed unless its result explicitly confirms completion.`
          : `Task job_id=${job.id} ended with status=${job.status}, error=${job.error?.user_message ?? "none"}. Explain the reason and provide a continuation path.`;
        await api.sendAgentMessage(projectId, conversationId, completion, `agent-job-terminal-${job.id}`, typeof eventApi === "function" ? false : true);
        if (!live) return;
        if (job.status === "succeeded") {
          const resultMode = pendingJobWorkspaceMode ?? workspaceModeForJobType(job.job_type);
          if (resultMode) onWorkspaceAction?.({
            mode: resultMode,
            jobId: job.id,
            resultAssetIds: job.output_asset_ids,
          });
        }
        setPendingJobId(null);
        setPendingJobWorkspaceMode(null);
        if (typeof eventApi !== "function") {
          const updated = await api.agentMessages(projectId, conversationId, initialMessageLimit);
          if (live) applyLatestMessagePage(updated);
        }
      } catch {
        // The task center remains authoritative; transient polling failures are retried.
      } finally {
        if (live && typeof eventApi !== "function") setBusy(false);
        sending = false;
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 2_500);
    return () => { live = false; window.clearInterval(timer); };
  }, [api, applyLatestMessagePage, conversationId, eventApi, onWorkspaceAction, pendingJobId, pendingJobWorkspaceMode, projectId]);

  const send = async () => {
    if ((!draft.trim() && !attachments.length) || !conversationId || busy || switching || attachmentBusy) return;
    const content = draft.trim() || "Use the attached images as inputs.";
    const sentAttachments = attachments;
    setDraft("");
    setAttachments([]);
    setBusy(true);
    setError("");
    setConversationStatus("working");
    setMessages((items) => [...items, {
      id: requestId(),
      role: "user",
      content,
      attachments: sentAttachments.map((attachment) => ({
        asset_id: attachment.id,
        name: attachment.name,
        mime_type: attachment.mime_type ?? "image/*",
      })),
    }]);
    try {
      const sendRequestId = requestId();
      if (sentAttachments.length) {
        await api.sendAgentMessage(
          projectId,
          conversationId,
          content,
          sendRequestId,
          typeof eventApi === "function" ? false : true,
          sentAttachments.map((attachment) => attachment.id),
        );
      } else {
        await api.sendAgentMessage(
          projectId,
          conversationId,
          content,
          sendRequestId,
          typeof eventApi === "function" ? false : true,
        );
      }
      const result = await api.agentMessages(projectId, conversationId, initialMessageLimit);
      applyLatestMessagePage(result);
      if (typeof eventApi !== "function") setBusy(false);
    } catch {
      setDraft(content);
      setAttachments(sentAttachments);
      setBusy(false);
      setConversationStatus("failed");
      setError("The Agent could not complete this request. Check the model settings and try again.");
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
      if (approved && conversationId) {
        const queuedJobId = result.status === "queued" ? result.job?.job_id : undefined;
        if (queuedJobId) {
          setPendingJobId(queuedJobId);
          setPendingJobWorkspaceMode(queuedWorkspaceMode ?? null);
          onJobQueued?.();
        }
        setBusy(true);
        const continuation = result.status === "queued"
          ? `The user approved the external operation${queuedJobId ? `; job_id=${queuedJobId}` : ""}. Follow the task and continue with preview, conversion, or export once it completes.`
          : `The user approved the external operation. Approval result: ${result.summary}. Continue the request.`;
        try {
          await api.sendAgentMessage(projectId, conversationId, continuation, requestId(), typeof eventApi === "function" ? false : true);
        } catch (error) {
          // The approval can race with the Agent's final explanation of the
          // approval request. A queued Job is already durable, so keep
          // monitoring it instead of misreporting the successful approval.
          if (!queuedJobId) throw error;
        }
        if (typeof eventApi !== "function") {
          const afterApproval = await api.agentMessages(
            projectId,
            conversationId,
            initialMessageLimit,
          );
          applyLatestMessagePage(afterApproval);
          setBusy(false);
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
      {workspaceMode && <div className="agent-approval"><p>{action?.type === "capture_model_preview" ? "The model preview must be captured explicitly in the desktop workspace." : `Continue this action in the ${workspaceLabels[workspaceMode]} workspace.`}</p><button className="primary" onClick={() => openWorkspaceAction(action)}>Open {workspaceLabels[workspaceMode]}</button></div>}
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
      transcript.push(<AssistantMessage key={message.id} message={message} hideThinking={hideThinking} projectId={projectId} api={api} imageAssetIds={finalAssistantImageAssetIds(messages, messageIndex)} />);
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

  return <div className="agent-live-panel">
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
    <div className="agent-conversation" ref={conversationRef} aria-live="polite" onScroll={handleConversationScroll}>
      {loadingOlderMessages && <p className="agent-history-loading" role="status">Loading earlier messages…</p>}
      {!hasOlderMessages && historyBefore === null && messages.length >= initialMessageLimit
        ? <p className="agent-history-start">Start of conversation</p>
        : null}
      <div className="agent-conversation-controls"><button type="button" onClick={() => setExpanded((value) => !value)}>{expanded ? <CaretDown size={14} /> : <CaretRight size={14} />}{expanded ? "Hide tool output" : "Show tool output"}</button><button type="button" onClick={() => setHideThinking((value) => !value)}>{hideThinking ? "Show thinking" : "Hide thinking"}</button></div>
      {transcript.length ? transcript : <p className="agent-empty-state">Describe the model you want to make, or ask the Agent to continue with the current asset.</p>}
      {streamingText && <AssistantMessage message={{ id: "streaming", role: "assistant", content: "" }} hideThinking={hideThinking} streamingText={streamingText} />}
      <p className={`agent-run-status ${conversationStatus}`} role="status">
        {conversationStatus === "working" && <><SpinnerGap className="spin" /> Agent is working…</>}
        {pendingJobId && !busy && "Background task is running. You can keep using the Agent; it will continue here when the task finishes."}
        {!pendingJobId && conversationStatus === "completed" && "Agent completed this response."}
        {conversationStatus === "failed" && "Agent stopped before completing this response. You can try again."}
        {conversationStatus === "ready" && "Agent is ready."}
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
