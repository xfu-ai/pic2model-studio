import { CaretDown, FolderOpen } from "@phosphor-icons/react";
import type { RecentProjectDto } from "../../shared/api/client";

/** Compact switcher for shells that already have an open project. */
export function ProjectSwitcher({ projectName, recents, disabled, onOpenProject, onOpenRecent }: { projectName: string; recents: RecentProjectDto[]; disabled?: boolean; onOpenProject(): void; onOpenRecent(projectId: string): void }) {
  return <details className="project-switcher"><summary aria-label="Switch project">{projectName}<CaretDown size={14} /></summary><div role="menu"><button role="menuitem" disabled={disabled} onClick={onOpenProject}><FolderOpen size={16} />Open another project</button>{recents.filter((recent) => recent.availability === "available").map((recent) => <button role="menuitem" key={recent.id} disabled={disabled} onClick={() => onOpenRecent(recent.id)}>{recent.name}</button>)}</div></details>;
}
