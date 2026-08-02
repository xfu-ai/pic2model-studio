import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PromptParameterDrawer } from "./PromptParameterDrawer";

describe("PromptParameterDrawer", () => {
  afterEach(cleanup);

  it("offers exactly 1, 2, or 4 candidates in the prompt drawer", () => {
    render(
      <PromptParameterDrawer
        projectId="project-1"
        api={{ jobs: vi.fn().mockResolvedValue({ items: [] }), assets: vi.fn().mockResolvedValue([]) } as never}
        contentAsset={null}
        styleAsset={null}
        onClose={vi.fn()}
        onModeChange={vi.fn()}
      />,
    );

    const select = screen.getByRole("combobox", { name: "候选数量" });
    expect(within(select).getAllByRole("option").map((option) => option.textContent)).toEqual(["1", "2", "4"]);
  });

  it("shows a malformed analysis response for manual editing instead of extracting it", async () => {
    const source = {
      id: "source-content",
      name: "content.png",
      asset_type: "source_image",
    };
    const analysis = {
      id: "analysis-content",
      name: "content-analysis.json",
      asset_type: "analysis",
      parent_asset_id: source.id,
    };
    const api = {
      jobs: vi.fn().mockResolvedValue({
        items: [{
          id: "analysis-job",
          job_type: "image.analyze_content",
          status: "succeeded",
          output_asset_ids: [analysis.id],
          input_asset_ids: [source.id],
        }],
      }),
      assets: vi.fn().mockResolvedValue([source, analysis]),
      assetText: vi.fn().mockResolvedValue(JSON.stringify({
        parse_error: "generation.en must be a non-empty string",
        raw_response: "raw model response",
      })),
      invokeTool: vi.fn(),
    };

    render(
      <PromptParameterDrawer
        projectId="project-1"
        api={api as never}
        contentAsset={source as never}
        styleAsset={null}
        onClose={vi.fn()}
        onModeChange={vi.fn()}
        referenceContext={{
          content_asset_id: source.id,
          style_asset_id: null,
          content_analysis_asset_id: null,
          style_analysis_asset_id: null,
          content_prompt_asset_id: null,
          style_prompt_asset_id: null,
          merged_prompt_asset_id: null,
        }}
      />,
    );

    await waitFor(() =>
      expect(
        screen.getByLabelText("English content Prompt"),
      ).toHaveValue("raw model response"),
    );
    expect(screen.getAllByText(/generation\.en must be a non-empty string/).length).toBeGreaterThan(0);
    expect(screen.getByText("查看模型原始诊断")).toBeVisible();
    expect(api.invokeTool).not.toHaveBeenCalledWith(
      "project-1",
      "prompt.extract_bilingual",
      expect.anything(),
      expect.any(String),
    );
  });
});
