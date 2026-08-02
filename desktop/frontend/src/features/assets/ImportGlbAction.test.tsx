import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ImportGlbAction } from "./ImportGlbAction";

describe("ImportGlbAction", () => {
  afterEach(() => document.body.replaceChildren());
  it("imports a project-bound GLB capability and switches the current asset", async () => {
    const host = { chooseImportGlb: vi.fn().mockResolvedValue("glb-capability") };
    const api = { importGlb: vi.fn().mockResolvedValue({ id: "glb-1" }), setCurrentAsset: vi.fn().mockResolvedValue({ id: "glb-1" }) };
    render(<ImportGlbAction projectId="project-1" host={host as never} api={api as never} onImported={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Choose GLB" }));
    await waitFor(() => expect(api.importGlb).toHaveBeenCalledWith("project-1", "glb-capability", expect.stringMatching(/^asset-import-/)));
    expect(api.setCurrentAsset).toHaveBeenCalledWith("project-1", "glb-1", expect.stringMatching(/^asset-import-/));
  });
});
