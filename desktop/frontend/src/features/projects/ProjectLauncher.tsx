import { FolderOpen, Plus, WarningCircle } from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import { ApiError, type ApiClient, type ProjectDto, type RecentProjectDto } from "../../shared/api/client";
import { HostClient } from "../../shared/host/client";
import { NewProjectDialog } from "./NewProjectDialog";
import { ProjectDirectoryPicker } from "./ProjectDirectoryPicker";
import { RecentProjectsPage } from "./RecentProjectsPage";
import "./project-launcher.css";

type LauncherMode = "new" | "open";

function requestId() { return `project-${crypto.randomUUID()}`; }
function messageFor(error: unknown) { return error instanceof ApiError ? error.message : "The project could not be opened. Please try again."; }

/** The renderer receives an opaque capability, never a filesystem path. */
export function ProjectLauncher({ api, host = new HostClient(), onProject }: { api: ApiClient; host?: HostClient; onProject(project: ProjectDto): void }) {
  const [mode, setMode] = useState<LauncherMode>("new");
  const [name, setName] = useState("Untitled project");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recents, setRecents] = useState<RecentProjectDto[]>([]);
  useEffect(() => { void api.recentProjects().then((response) => setRecents(response.projects)).catch(() => setRecents([])); }, [api]);
  const create = async () => {
    setBusy(true); setError(null);
    try {
      const capabilityId = await host.chooseProjectDirectory();
      if (!capabilityId) return;
      const created = await api.createProject(name.trim(), capabilityId, requestId());
      onProject(await api.project(created.id));
    } catch (reason) { setError(messageFor(reason)); } finally { setBusy(false); }
  };
  const open = async () => {
    setBusy(true); setError(null);
    try {
      const capabilityId = await host.chooseExistingProjectDirectory();
      if (!capabilityId) return;
      const opened = await api.openProject(capabilityId, requestId());
      onProject(await api.project(opened.id));
    } catch (reason) { setError(messageFor(reason)); } finally { setBusy(false); }
  };
  const openRecent = async (recentProjectId: string) => {
    setBusy(true); setError(null);
    try {
      const capabilityId = await host.chooseRecentProject(recentProjectId);
      if (!capabilityId) throw new Error("The recent project is no longer available.");
      const opened = await api.openProject(capabilityId, requestId());
      onProject(await api.project(opened.id));
    } catch (reason) { setError(messageFor(reason)); } finally { setBusy(false); }
  };
  return <main className="project-launcher" aria-labelledby="project-launcher-title"><section className="project-launcher-card">
    <p className="eyebrow">AIPicToModel</p><h1 id="project-launcher-title">Open a local project</h1>
    <p className="project-launcher-intro">Choose a project folder through the desktop app. Its location stays private to the native host.</p>
    <div className="project-launcher-tabs" role="tablist" aria-label="Project action"><button role="tab" aria-selected={mode === "new"} onClick={() => setMode("new")}><Plus size={18} />New project</button><button role="tab" aria-selected={mode === "open"} onClick={() => setMode("open")}><FolderOpen size={18} />Open project</button></div>
    {mode === "new" ? <NewProjectDialog name={name} busy={busy} onNameChange={setName} onCreate={() => void create()} /> : <><p className="project-launcher-help">Select a folder that contains a valid AIPicToModel project. Read-only or damaged projects remain unchanged if opening fails.</p><ProjectDirectoryPicker actionLabel="Choose project folder" disabled={busy} onChoose={() => void open()} /></>}
    {error && <p className="project-launcher-error" role="alert"><WarningCircle size={18} />{error}</p>}
    <RecentProjectsPage projects={recents} busy={busy} onOpen={(recentProjectId) => void openRecent(recentProjectId)} />
  </section></main>;
}
