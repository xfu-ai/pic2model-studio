import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import type { RendererSession } from "../api/client";

export type AgentImageDropItem = {
  capabilityId: string;
  fileName: string;
};

/** Native-only bridge. It accepts no renderer supplied filesystem paths. */
export class HostClient {
  rendererSession() { return invoke<RendererSession>("get_renderer_session"); }
  chooseProjectDirectory() { return invoke<string | null>("choose_project_directory"); }
  chooseExistingProjectDirectory() { return invoke<string | null>("choose_existing_project_directory"); }
  chooseRecentProject(recentProjectId: string) { return invoke<string | null>("choose_recent_project", { recentProjectId }); }
  chooseImportImage(projectId: string) { return invoke<string | null>("choose_import_image", { projectId }); }
  chooseImportGlb(projectId: string) { return invoke<string | null>("choose_import_glb", { projectId }); }
  captureScreen(projectId: string) { return invoke<string>("capture_screen_capability", { projectId }); }
  screenCapturePreview(token: string) { return invoke<ArrayBuffer>("screen_capture_preview", { token }); }
  completeScreenCapture(token: string, rect: { x: number; y: number; width: number; height: number }) { return invoke<void>("complete_screen_capture", { token, rect }); }
  cancelScreenCapture(token: string) { return invoke<void>("cancel_screen_capture", { token }); }
  stageDroppedFile(projectId: string, assetKind: "source_image" | "glb", fileName: string, bytes: number[]) {
    return invoke<string>("stage_dropped_file", { projectId, assetKind, fileName, bytes });
  }
  setAgentDropProject(projectId: string | null) {
    return invoke<void>("set_agent_drop_project", { projectId });
  }
  listenAgentImageDrop(handler: (items: AgentImageDropItem[]) => void): Promise<UnlistenFn> {
    return listen<AgentImageDropItem[]>("agent-image-drop", (event) => handler(event.payload));
  }
  chooseExportDirectory(projectId: string) { return invoke<string | null>("choose_export_directory", { projectId }); }
  chooseDiagnosticsExportDirectory(projectId: string) { return invoke<string | null>("choose_diagnostics_export_directory", { projectId }); }
  notifyJobTerminal(status: "succeeded" | "failed" | "cancelled" | "interrupted") { return invoke<void>("notify_job_terminal", { status }); }
  openModelBrowser(bytes: number[]) { return invoke<void>("open_model_browser_preview", { bytes }); }
}
