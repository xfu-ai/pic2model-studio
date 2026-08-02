import { ClockCounterClockwise } from "@phosphor-icons/react";
import type { RecentProjectDto } from "../../shared/api/client";

export function RecentProjectsPage({ projects, busy, onOpen }: { projects: RecentProjectDto[]; busy: boolean; onOpen(projectId: string): void }) {
  if (projects.length === 0) return null;
  return <section className="recent-projects" aria-labelledby="recent-projects-title"><h2 id="recent-projects-title"><ClockCounterClockwise size={18} />Recent projects</h2>{projects.map((recent) => <button key={recent.id} disabled={busy || recent.availability !== "available"} onClick={() => onOpen(recent.id)}><span>{recent.name}</span><small>{recent.availability === "available" ? "Available" : "Unavailable"}</small></button>)}</section>;
}
