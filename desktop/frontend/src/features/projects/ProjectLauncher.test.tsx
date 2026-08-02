import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProjectLauncher } from "./ProjectLauncher";

const restoredProject = { id: "project-1", name: "Demo", current_asset_id: null, root_state: "available", workspace_state_json: "{}" };

describe("ProjectLauncher", () => {
  afterEach(cleanup);
  it("creates with a native capability and restores the full project DTO", async () => {
    const host = { chooseProjectDirectory: vi.fn().mockResolvedValue("create-capability") };
    const api = { recentProjects: vi.fn().mockResolvedValue({ projects: [] }), createProject: vi.fn().mockResolvedValue(restoredProject), project: vi.fn().mockResolvedValue(restoredProject) };
    const onProject = vi.fn();
    render(<ProjectLauncher host={host as never} api={api as never} onProject={onProject} />);
    fireEvent.change(screen.getByLabelText("Project name"), { target: { value: "Character" } });
    fireEvent.click(screen.getByRole("button", { name: "Choose folder and create" }));
    await waitFor(() => expect(api.createProject).toHaveBeenCalledWith("Character", "create-capability", expect.stringMatching(/^project-/)));
    expect(api.project).toHaveBeenCalledWith("project-1");
    expect(onProject).toHaveBeenCalledWith(restoredProject);
  });
  it("opens an existing project through an opaque native capability", async () => {
    const host = { chooseExistingProjectDirectory: vi.fn().mockResolvedValue("open-capability") };
    const api = { recentProjects: vi.fn().mockResolvedValue({ projects: [] }), openProject: vi.fn().mockResolvedValue(restoredProject), project: vi.fn().mockResolvedValue(restoredProject) };
    render(<ProjectLauncher host={host as never} api={api as never} onProject={vi.fn()} />);
    fireEvent.click(screen.getByRole("tab", { name: "Open project" }));
    fireEvent.click(screen.getByRole("button", { name: "Choose project folder" }));
    await waitFor(() => expect(api.openProject).toHaveBeenCalledWith("open-capability", expect.stringMatching(/^project-/)));
    expect(JSON.stringify(api.openProject.mock.calls)).not.toContain(":\\\\");
  });
});
