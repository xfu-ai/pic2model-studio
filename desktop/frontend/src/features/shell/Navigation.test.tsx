import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Navigation } from "./Navigation";

describe("Navigation", () => {
  it("keeps primary navigation focused on workspace assets tasks and exports", () => {
    render(<Navigation route="workspace" onChange={vi.fn()} />);

    expect(screen.queryByRole("button", { name: "诊断" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "版本" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("button")).toHaveLength(4);
  });
});
