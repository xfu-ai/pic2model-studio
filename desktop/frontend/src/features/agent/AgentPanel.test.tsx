import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AgentPanel } from "./AgentPanel";

describe("AgentPanel", () => {
  afterEach(() => { document.body.replaceChildren(); vi.unstubAllGlobals(); });

  it("does not create duplicate bootstrap conversations in Strict Mode", async () => {
    const api = {
      agentConversations: vi.fn().mockResolvedValue({ items: [] }),
      createAgentConversation: vi.fn().mockResolvedValue({ id: "first", state: "idle", message_count: 0 }),
      agentMessages: vi.fn().mockResolvedValue({ items: [] }),
    };
    render(<StrictMode><AgentPanel projectId="project-1" api={api as never} /></StrictMode>);
    await waitFor(() => expect(api.createAgentConversation).toHaveBeenCalledTimes(1));
    expect(api.createAgentConversation.mock.calls[0][1]).toContain(
      "status=awaiting_ui_action means the requested desktop action has not completed yet",
    );
    expect(api.createAgentConversation.mock.calls[0][1]).toContain(
      "attached image is included directly in the current multimodal message",
    );
    expect(api.createAgentConversation.mock.calls[0][1]).toContain(
      "only a managed image reference is available",
    );
    expect(api.createAgentConversation.mock.calls[0][1]).toContain(
      "Chinese is the default when ambiguous",
    );
    expect(api.createAgentConversation.mock.calls[0][1]).toContain(
      "call toolbox.status",
    );
    expect(api.createAgentConversation.mock.calls[0][1]).toContain(
      "Use image.normalize",
    );
    expect(api.createAgentConversation.mock.calls[0][1]).toContain(
      "Use image.upscale_local",
    );
    expect(api.createAgentConversation.mock.calls[0][1]).toContain(
      "![short descriptive label](asset:<exact_output_asset_ref>)",
    );
  });

  it("restores the most recent project conversation instead of creating a blank one", async () => {
    const onWorkspaceAction = vi.fn();
    const api = {
      agentConversations: vi.fn().mockResolvedValue({ items: [{ id: "restored", state: "idle", message_count: 2 }] }),
      createAgentConversation: vi.fn(),
      agentMessages: vi.fn().mockResolvedValue({ items: [
        { id: "user", role: "user", content: [{ type: "text", text: "Show my prior work" }] },
        {
          id: "assistant",
          role: "assistant",
          content: [{ type: "text", text: "Restored Agent answer" }],
          details: { ui_action: { action_id: "old-selection", type: "select_rectangle", workspace_mode: "rectangle_selection" } },
        },
      ] }),
    };
    render(<AgentPanel projectId="project-1" api={api as never} onWorkspaceAction={onWorkspaceAction} />);
    expect(await screen.findByText("Restored Agent answer")).toBeVisible();
    expect(api.agentMessages).toHaveBeenCalledWith("project-1", "restored", 20);
    expect(api.createAgentConversation).not.toHaveBeenCalled();
    expect(onWorkspaceAction).not.toHaveBeenCalled();
  });

  it("shows the durable execution plan and updates it from plan events", async () => {
    const api = {
      agentConversations: vi.fn().mockResolvedValue({ items: [{ id: "restored", state: "running", message_count: 1 }] }),
      createAgentConversation: vi.fn(),
      agentMessages: vi.fn().mockResolvedValue({
        items: [{ id: "user", role: "user", content: "Remove the background" }],
        execution_plan: {
          version: 1,
          goal: "Remove the background",
          constraints: ["Preserve the subject"],
          steps: [{ id: "remove", label: "Remove background", state: "pending" }],
          current_step_id: "remove",
          state: "executing",
          next_action: "execute",
        },
      }),
      agentEvents: vi.fn()
        .mockResolvedValueOnce({
          items: [{
            sequence_no: 1,
            event_type: "execution.plan.updated",
            payload: {
              conversation_id: "restored",
              plan: {
                version: 1,
                goal: "Remove the background",
                constraints: ["Preserve the subject"],
                steps: [{ id: "remove", label: "Remove background", state: "review_required", warning: "No alpha pixels" }],
                current_step_id: null,
                state: "completed_with_warnings",
                next_action: "execute",
              },
            },
            created_at: new Date().toISOString(),
          }],
          next_cursor: 1,
        })
        .mockResolvedValue({ items: [], next_cursor: 1 }),
      sendAgentMessage: vi.fn(),
    };

    render(<AgentPanel projectId="project-1" api={api as never} />);

    expect(await screen.findByLabelText("Execution plan")).toHaveTextContent("Remove the background");
    expect(screen.getByLabelText("Execution plan").closest(".agent-live-panel")).toHaveClass("has-plan");
    await waitFor(() => expect(screen.getByLabelText("Execution plan")).toHaveTextContent("Completed with warnings"));
    expect(screen.getByLabelText("Execution plan").querySelector(".spin")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "View details" }));
    expect(await screen.findByText(/No alpha pixels/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Hide details" })).toHaveAttribute("aria-expanded", "true");
  });

  it("shows an interrupted recovery as waiting for user input instead of completed", async () => {
    const api = {
      agentConversations: vi.fn().mockResolvedValue({
        items: [{ id: "restored", state: "idle", message_count: 2 }],
      }),
      createAgentConversation: vi.fn(),
      agentMessages: vi.fn().mockResolvedValue({
        items: [
          { id: "user", role: "user", content: "Create a character model" },
          { id: "assistant", role: "assistant", content: "Please confirm a new submission." },
        ],
        execution_plan: {
          version: 1,
          goal: "Create a character model",
          constraints: [],
          steps: [{
            id: "generate",
            label: "Generate 3D model",
            state: "failed",
            warning: "Submission state is unknown.",
          }],
          current_step_id: null,
          state: "waiting_user",
          next_action: "ask_user",
        },
      }),
    };

    render(<AgentPanel projectId="project-1" api={api as never} />);

    expect(await screen.findByText("Please confirm a new submission.")).toBeVisible();
    expect(screen.getByLabelText("Execution plan")).toHaveTextContent("Needs your input");
    expect(screen.getByRole("status")).toHaveTextContent(
      "Agent is waiting for your decision before it can continue.",
    );
  });

  it("loads earlier conversation messages in 20-message pages when scrolled to the top", async () => {
    const allMessages = Array.from({ length: 25 }, (_, index) => ({
      id: `message-${index + 1}`,
      role: "user",
      content: `History message ${index + 1}`,
    }));
    const api = {
      agentConversations: vi.fn().mockResolvedValue({
        items: [{ id: "restored", state: "idle", message_count: 25 }],
      }),
      createAgentConversation: vi.fn(),
      agentMessages: vi.fn()
        .mockResolvedValueOnce({
          items: allMessages.slice(5),
          event_cursor: 50,
          next_before: 6,
          has_more: true,
        })
        .mockResolvedValueOnce({
          items: allMessages.slice(0, 5),
          event_cursor: 50,
          next_before: null,
          has_more: false,
        }),
    };

    const { container } = render(<AgentPanel projectId="project-1" api={api as never} />);
    expect(await screen.findByText("History message 6")).toBeVisible();
    expect(screen.queryByText("History message 1")).toBeNull();

    const conversation = container.querySelector<HTMLElement>(".agent-conversation");
    expect(conversation).not.toBeNull();
    Object.defineProperty(conversation!, "scrollHeight", { configurable: true, value: 2_000 });
    conversation!.scrollTop = 0;
    fireEvent.scroll(conversation!);

    expect(await screen.findByText("History message 1")).toBeVisible();
    expect(api.agentMessages).toHaveBeenNthCalledWith(2, "project-1", "restored", 20, 6);
    expect(screen.getByText("Start of conversation")).toBeVisible();
  });

  it("imports, previews, sends, and restores multiple selected managed image attachments", async () => {
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:agent-input-image"),
      revokeObjectURL: vi.fn(),
    });
    const attachment = {
      id: "managed-image-1",
      name: "reference.png",
      mime_type: "image/png",
      asset_type: "source_image",
      is_current: true,
      metadata: {},
    };
    const secondAttachment = {
      ...attachment,
      id: "managed-image-2",
      name: "detail.webp",
      mime_type: "image/webp",
    };
    const api = {
      agentConversations: vi.fn().mockResolvedValue({ items: [] }),
      createAgentConversation: vi.fn().mockResolvedValue({ id: "conversation-1", state: "idle", message_count: 0 }),
      importImage: vi.fn()
        .mockResolvedValueOnce(attachment)
        .mockResolvedValueOnce(secondAttachment),
      assetContent: vi.fn().mockResolvedValue(new Blob(["image"], { type: "image/png" })),
      sendAgentMessage: vi.fn().mockResolvedValue({ state: "idle" }),
      agentMessages: vi.fn().mockResolvedValue({ items: [{
        id: "persisted-user",
        role: "user",
        content: "Use both references.",
        attachments: [
          { asset_id: attachment.id, name: attachment.name, mime_type: attachment.mime_type },
          { asset_id: secondAttachment.id, name: secondAttachment.name, mime_type: secondAttachment.mime_type },
        ],
      }] }),
    };
    const host = {
      stageDroppedFile: vi.fn()
        .mockResolvedValueOnce("image-capability-1")
        .mockResolvedValueOnce("image-capability-2"),
    };
    render(<AgentPanel projectId="project-1" api={api as never} host={host as never} />);
    await waitFor(() => expect(api.createAgentConversation).toHaveBeenCalledTimes(1));

    const fileInput = screen.getByLabelText("Choose images to attach");
    const openPicker = vi.spyOn(fileInput, "click");
    fireEvent.click(screen.getByRole("button", { name: "Attach images" }));
    expect(openPicker).toHaveBeenCalledTimes(1);

    const firstBytes = new Uint8Array([1, 2, 3]);
    const secondBytes = new Uint8Array([4, 5, 6]);
    fireEvent.change(fileInput, {
      target: {
        files: [
          { name: "reference.png", arrayBuffer: vi.fn().mockResolvedValue(firstBytes.buffer) },
          { name: "detail.webp", arrayBuffer: vi.fn().mockResolvedValue(secondBytes.buffer) },
        ],
      },
    });

    expect(await screen.findByText("2 images ready")).toBeVisible();
    expect(host.stageDroppedFile).toHaveBeenNthCalledWith(1, "project-1", "source_image", "reference.png", [1, 2, 3]);
    expect(host.stageDroppedFile).toHaveBeenNthCalledWith(2, "project-1", "source_image", "detail.webp", [4, 5, 6]);
    expect(api.importImage).toHaveBeenNthCalledWith(1, "project-1", "image-capability-1", expect.stringMatching(/^agent-image-import-/), undefined, "reference.png");
    expect(api.importImage).toHaveBeenNthCalledWith(2, "project-1", "image-capability-2", expect.stringMatching(/^agent-image-import-/), undefined, "detail.webp");
    expect(await screen.findByRole("img", { name: "Project image: reference.png" })).toHaveAttribute("src", "blob:agent-input-image");

    fireEvent.change(screen.getByLabelText("Message the Agent"), { target: { value: "Use both references." } });
    fireEvent.click(screen.getByLabelText("Send to Agent"));

    await waitFor(() => expect(api.sendAgentMessage).toHaveBeenCalledWith(
      "project-1",
      "conversation-1",
      "Use both references.",
      expect.any(String),
      true,
      [attachment.id, secondAttachment.id],
    ));
    expect(await screen.findByLabelText("Attached images")).toBeVisible();
    expect(screen.queryByText("source_asset_ref")).toBeNull();
  });

  it("stages a dropped image through the native host before attaching it", async () => {
    const attachment = {
      id: "managed-drop-1",
      name: "drop.webp",
      mime_type: "image/webp",
      asset_type: "source_image",
      is_current: true,
      metadata: {},
    };
    const api = {
      agentConversations: vi.fn().mockResolvedValue({ items: [] }),
      createAgentConversation: vi.fn().mockResolvedValue({ id: "conversation-1", state: "idle", message_count: 0 }),
      importImage: vi.fn().mockResolvedValue(attachment),
      assetContent: vi.fn().mockResolvedValue(new Blob(["image"], { type: "image/webp" })),
    };
    const host = {
      stageDroppedFile: vi.fn().mockResolvedValue("drop-capability"),
    };
    const { container } = render(<AgentPanel projectId="project-1" api={api as never} host={host as never} />);
    await waitFor(() => expect(api.createAgentConversation).toHaveBeenCalledTimes(1));
    const compose = container.querySelector(".agent-compose");
    expect(compose).not.toBeNull();
    const bytes = new Uint8Array([1, 2, 3]);

    fireEvent.drop(compose!, {
      dataTransfer: {
        files: [{
          name: "drop.webp",
          arrayBuffer: vi.fn().mockResolvedValue(bytes.buffer),
        }],
      },
    });

    await waitFor(() => expect(host.stageDroppedFile).toHaveBeenCalledWith(
      "project-1",
      "source_image",
      "drop.webp",
      [1, 2, 3],
    ));
    expect(await screen.findByText("1 image ready")).toBeVisible();
    expect(api.importImage).toHaveBeenCalledWith("project-1", "drop-capability", expect.stringMatching(/^agent-image-import-/), undefined, "drop.webp");
  });

  it("imports multiple Explorer drops from capability-only native events", async () => {
    const firstAttachment = {
      id: "managed-native-1",
      name: "front.png",
      mime_type: "image/png",
      asset_type: "source_image",
      is_current: false,
      metadata: {},
    };
    const secondAttachment = {
      ...firstAttachment,
      id: "managed-native-2",
      name: "side.jpg",
      mime_type: "image/jpeg",
    };
    const api = {
      agentConversations: vi.fn().mockResolvedValue({ items: [] }),
      createAgentConversation: vi.fn().mockResolvedValue({ id: "conversation-1", state: "idle", message_count: 0 }),
      importImage: vi.fn()
        .mockResolvedValueOnce(firstAttachment)
        .mockResolvedValueOnce(secondAttachment),
      assetContent: vi.fn().mockResolvedValue(new Blob(["image"], { type: "image/png" })),
    };
    let nativeDrop: ((items: Array<{ capabilityId: string; fileName: string }>) => void) | undefined;
    const unlisten = vi.fn();
    const host = {
      setAgentDropProject: vi.fn().mockResolvedValue(undefined),
      listenAgentImageDrop: vi.fn().mockImplementation(async (handler) => {
        nativeDrop = handler;
        return unlisten;
      }),
    };
    const { unmount } = render(<AgentPanel projectId="project-1" api={api as never} host={host as never} />);
    await waitFor(() => expect(host.setAgentDropProject).toHaveBeenCalledWith("project-1"));

    act(() => nativeDrop?.([
      { capabilityId: "native-capability-1", fileName: "front.png" },
      { capabilityId: "native-capability-2", fileName: "side.jpg" },
    ]));

    expect(await screen.findByText("2 images ready")).toBeVisible();
    expect(api.importImage).toHaveBeenNthCalledWith(
      1,
      "project-1",
      "native-capability-1",
      expect.stringMatching(/^agent-native-image-import-/),
      undefined,
      "front.png",
    );
    expect(api.importImage).toHaveBeenNthCalledWith(
      2,
      "project-1",
      "native-capability-2",
      expect.stringMatching(/^agent-native-image-import-/),
      undefined,
      "side.jpg",
    );

    unmount();
    expect(unlisten).toHaveBeenCalledTimes(1);
    expect(host.setAgentDropProject).toHaveBeenLastCalledWith(null);
  });

  it("restores a durable queued Job and resumes terminal monitoring after remount", async () => {
    const api = {
      agentConversations: vi.fn().mockResolvedValue({
        items: [{ id: "restored", state: "idle", message_count: 3 }],
      }),
      createAgentConversation: vi.fn(),
      agentMessages: vi.fn().mockResolvedValue({
        items: [
          { id: "request", role: "user", content: "Generate the image" },
          {
            id: "queued",
            role: "tool_result",
            tool_name: "generate_images",
            content: [{ type: "text", text: "Job queued." }],
            details: {
              status: "queued",
              job: { job_id: "job-restored", status: "queued" },
            },
          },
        ],
      }),
      job: vi.fn().mockResolvedValue({
        id: "job-restored",
        status: "running",
        output_asset_ids: [],
      }),
      sendAgentMessage: vi.fn(),
    };

    render(<AgentPanel projectId="project-1" api={api as never} />);

    expect(await screen.findByText(/Background task is running/)).toBeVisible();
    await waitFor(() => expect(api.job).toHaveBeenCalledWith("project-1", "job-restored"));
    expect(screen.queryByText(/The user approved the external operation/)).toBeNull();
    expect(screen.getByLabelText("Message the Agent")).not.toBeDisabled();
  });

  it("reports an interrupted durable Job instead of waiting forever", async () => {
    const api = {
      agentConversations: vi.fn().mockResolvedValue({
        items: [{ id: "restored", state: "idle", message_count: 2 }],
      }),
      createAgentConversation: vi.fn(),
      agentMessages: vi.fn()
        .mockResolvedValueOnce({
          items: [{
            id: "queued",
            role: "tool_result",
            tool_name: "prepare_multiview",
            content: [{ type: "text", text: "Job queued." }],
            details: {
              status: "queued",
              job: { job_id: "job-interrupted", status: "queued" },
            },
          }],
        })
        .mockResolvedValue({ items: [] }),
      job: vi.fn().mockResolvedValue({
        id: "job-interrupted",
        status: "interrupted",
        output_asset_ids: [],
        error: { user_message: "The Provider rate limit was reached." },
      }),
      sendAgentMessage: vi.fn()
        .mockResolvedValueOnce({ state: "idle" })
        .mockRejectedValueOnce(new Error("AGENT_BUSY"))
        .mockResolvedValue({ state: "idle" }),
    };

    render(<AgentPanel projectId="project-1" api={api as never} />);

    await waitFor(() => expect(api.job).toHaveBeenCalledWith("project-1", "job-interrupted"));
    expect(api.sendAgentMessage).not.toHaveBeenCalled();
  });

  it("keeps waiting through a resumable interrupted Job without an error", async () => {
    const api = {
      agentConversations: vi.fn().mockResolvedValue({
        items: [{ id: "restored", state: "idle", message_count: 1 }],
      }),
      createAgentConversation: vi.fn(),
      agentMessages: vi.fn().mockResolvedValue({
        items: [{
          id: "queued",
          role: "tool_result",
          tool_name: "generate_model3d",
          content: [{ type: "text", text: "Job queued." }],
          details: {
            status: "queued",
            job: { job_id: "job-resumable", status: "queued" },
          },
        }],
      }),
      job: vi.fn().mockResolvedValue({
        id: "job-resumable",
        status: "interrupted",
        output_asset_ids: [],
        error: null,
      }),
      sendAgentMessage: vi.fn(),
    };

    render(<AgentPanel projectId="project-1" api={api as never} />);

    await waitFor(() => expect(api.job).toHaveBeenCalledWith("project-1", "job-resumable"));
    expect(api.sendAgentMessage).not.toHaveBeenCalled();
    expect(screen.getByRole("status")).toHaveTextContent("Background task is running");
  });

  it("does not emit a duplicate terminal message after control_job cancels the pending Job", async () => {
    const api = {
      agentConversations: vi.fn().mockResolvedValue({
        items: [{ id: "restored", state: "idle", message_count: 1 }],
      }),
      createAgentConversation: vi.fn(),
      agentMessages: vi.fn().mockResolvedValue({
        items: [{
          id: "queued",
          role: "tool_result",
          tool_name: "generate_images",
          content: [{ type: "text", text: "Job queued." }],
          details: {
            status: "queued",
            job: { job_id: "job-cancelled", status: "queued" },
          },
        }],
      }),
      job: vi.fn().mockResolvedValue({
        id: "job-cancelled",
        status: "running",
        output_asset_ids: [],
      }),
      agentEvents: vi.fn()
        .mockResolvedValueOnce({
          items: [{
            sequence_no: 1,
            event_type: "tool.call",
            payload: {
              conversation_id: "restored",
              tool_call: {
                id: "cancel-call",
                name: "control_job",
                arguments: { action: "cancel", job_ref: "job-cancelled" },
              },
            },
            created_at: new Date().toISOString(),
          }, {
            sequence_no: 2,
            event_type: "tool.completed",
            payload: {
              conversation_id: "restored",
              tool_call_id: "cancel-call",
              tool_name: "control_job",
              is_error: false,
              result: {
                content: [{ type: "text", text: "Cancellation requested." }],
                details: { status: "succeeded" },
                is_error: false,
              },
            },
            created_at: new Date().toISOString(),
          }],
          next_cursor: 2,
        })
        .mockResolvedValue({ items: [], next_cursor: 2 }),
      sendAgentMessage: vi.fn(),
    };

    render(<AgentPanel projectId="project-1" api={api as never} />);

    await waitFor(() => expect(api.agentEvents).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByText(/Background task is running/)).toBeNull());
    expect(api.sendAgentMessage).not.toHaveBeenCalled();
  });

  it("labels multiview detection outputs as selections and opens the multiview workspace", async () => {
    const onWorkspaceAction = vi.fn();
    const api = {
      agentConversations: vi.fn().mockResolvedValue({
        items: [{ id: "restored", state: "idle", message_count: 1 }],
      }),
      createAgentConversation: vi.fn(),
      agentMessages: vi.fn()
        .mockResolvedValueOnce({
          items: [{
            id: "queued",
            role: "tool_result",
            tool_name: "prepare_multiview",
            content: [{ type: "text", text: "Job queued." }],
            details: {
              status: "queued",
              job: { job_id: "job-multiview", status: "queued" },
            },
          }],
        })
        .mockResolvedValue({ items: [] }),
      job: vi.fn().mockResolvedValue({
        id: "job-multiview",
        job_type: "multiview.detect_regions",
        status: "succeeded",
        output_asset_ids: ["selection-front", "selection-side", "selection-back"],
      }),
      sendAgentMessage: vi.fn().mockResolvedValue({ state: "idle" }),
    };

    render(<AgentPanel
      projectId="project-1"
      api={api as never}
      onWorkspaceAction={onWorkspaceAction}
    />);

    await waitFor(() => expect(api.job).toHaveBeenCalledWith("project-1", "job-multiview"));
    expect(api.sendAgentMessage).not.toHaveBeenCalled();
    expect(onWorkspaceAction).toHaveBeenCalledWith(expect.objectContaining({ mode: "multiview" }));
  });

  it("continues the original task after image analysis finishes", async () => {
    const onWorkspaceAction = vi.fn();
    const api = {
      agentConversations: vi.fn().mockResolvedValue({
        items: [{ id: "restored", state: "idle", message_count: 1 }],
      }),
      createAgentConversation: vi.fn(),
      agentMessages: vi.fn()
        .mockResolvedValueOnce({
          items: [{
            id: "queued",
            role: "tool_result",
            tool_name: "analyze_image",
            content: [{ type: "text", text: "Job queued." }],
            details: {
              status: "queued",
              job: { job_id: "job-analysis", status: "queued" },
            },
          }],
        })
        .mockResolvedValue({ items: [] }),
      job: vi.fn().mockResolvedValue({
        id: "job-analysis",
        job_type: "image.analyze_style",
        status: "succeeded",
        input_asset_ids: ["style-source"],
        output_asset_ids: ["analysis-result"],
      }),
      sendAgentMessage: vi.fn().mockResolvedValue({ state: "idle" }),
    };

    render(<AgentPanel
      projectId="project-1"
      api={api as never}
      onWorkspaceAction={onWorkspaceAction}
    />);

    await waitFor(() => expect(api.job).toHaveBeenCalledWith("project-1", "job-analysis"));
    expect(api.sendAgentMessage).not.toHaveBeenCalled();
    expect(onWorkspaceAction).toHaveBeenCalledWith(expect.objectContaining({
      mode: "compare",
      jobType: "image.analyze_style",
      assetId: "style-source",
      analysisKind: "style",
      resultAssetIds: ["analysis-result"],
    }));
  });

  it("continues the original task after generated images finish", async () => {
    const onWorkspaceAction = vi.fn();
    const api = {
      agentConversations: vi.fn().mockResolvedValue({
        items: [{ id: "restored", state: "idle", message_count: 1 }],
      }),
      createAgentConversation: vi.fn(),
      agentMessages: vi.fn()
        .mockResolvedValueOnce({
          items: [{
            id: "queued",
            role: "tool_result",
            tool_name: "generate_images",
            content: [{ type: "text", text: "Job queued." }],
            details: {
              status: "queued",
              job: { job_id: "job-image", status: "queued" },
            },
          }],
        })
        .mockResolvedValue({ items: [] }),
      job: vi.fn().mockResolvedValue({
        id: "job-image",
        job_type: "image.generate_variants",
        status: "succeeded",
        output_asset_ids: ["image-a", "image-b"],
      }),
      sendAgentMessage: vi.fn().mockResolvedValue({ state: "idle" }),
    };

    render(
      <AgentPanel
        projectId="project-1"
        api={api as never}
        onWorkspaceAction={onWorkspaceAction}
      />,
    );

    await waitFor(() => expect(api.job).toHaveBeenCalledWith("project-1", "job-image"));
    expect(api.sendAgentMessage).not.toHaveBeenCalled();
    expect(onWorkspaceAction).toHaveBeenCalledWith(expect.objectContaining({
      mode: "prompt_image",
      jobId: "job-image",
      jobType: "image.generate_variants",
      resultAssetIds: ["image-a", "image-b"],
    }));
  });

  it("opens transparent export results in the candidate workspace", async () => {
    const onWorkspaceAction = vi.fn();
    const api = {
      agentConversations: vi.fn().mockResolvedValue({
        items: [{ id: "restored", state: "idle", message_count: 1 }],
      }),
      createAgentConversation: vi.fn(),
      agentMessages: vi.fn()
        .mockResolvedValueOnce({
          items: [{
            id: "queued",
            role: "tool_result",
            tool_name: "edit_image",
            content: [{ type: "text", text: "Job queued." }],
            details: {
              status: "queued",
              job: { job_id: "job-transparent", status: "queued" },
            },
          }],
        })
        .mockResolvedValue({ items: [] }),
      job: vi.fn().mockResolvedValue({
        id: "job-transparent",
        job_type: "element.export_transparent",
        status: "succeeded",
        output_asset_ids: ["transparent-image"],
      }),
      sendAgentMessage: vi.fn().mockResolvedValue({ state: "idle" }),
    };

    render(
      <AgentPanel
        projectId="project-1"
        api={api as never}
        onWorkspaceAction={onWorkspaceAction}
      />,
    );

    await waitFor(() => expect(api.job).toHaveBeenCalledWith("project-1", "job-transparent"));
    expect(api.sendAgentMessage).not.toHaveBeenCalled();
    expect(onWorkspaceAction).toHaveBeenCalledWith(expect.objectContaining({ mode: "candidate" }));
  });

  it("lists the project's Pi-style saved conversations and restores the selected transcript", async () => {
    const api = {
      agentConversations: vi.fn().mockResolvedValue({ items: [
        { id: "recent", state: "idle", message_count: 4, preview: "Continue the current model", updated_at: new Date().toISOString() },
        { id: "older", state: "idle", message_count: 2, preview: "Make a stylized fox", updated_at: "2026-07-27T00:00:00Z" },
      ] }),
      createAgentConversation: vi.fn(),
      agentMessages: vi.fn().mockImplementation((_projectId, conversationId) => Promise.resolve({ items: conversationId === "recent"
        ? [{ id: "recent-answer", role: "assistant", content: [{ type: "text", text: "Latest answer" }] }]
        : [{ id: "older-answer", role: "assistant", content: [{ type: "text", text: "Recovered fox answer" }] }],
      })),
    };
    render(<AgentPanel projectId="project-1" api={api as never} />);
    expect(await screen.findByText("Latest answer")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Conversation history" }));
    expect(screen.getByText("Project conversations")).toBeVisible();
    expect(screen.getByText("Make a stylized fox")).toBeVisible();
    fireEvent.click(screen.getByText("Make a stylized fox"));
    expect(await screen.findByText("Recovered fox answer")).toBeVisible();
    expect(api.agentMessages).toHaveBeenCalledWith("project-1", "older", 20);
    expect(api.createAgentConversation).not.toHaveBeenCalled();
  });

  it("creates a separate empty conversation without discarding saved history", async () => {
    const api = {
      agentConversations: vi.fn().mockResolvedValue({ items: [{ id: "saved", state: "idle", message_count: 2, preview: "Saved request" }] }),
      createAgentConversation: vi.fn().mockResolvedValue({ id: "new", state: "idle", message_count: 0, preview: "" }),
      agentMessages: vi.fn().mockResolvedValue({ items: [{ id: "saved-answer", role: "assistant", content: [{ type: "text", text: "Saved answer" }] }] }),
    };
    render(<AgentPanel projectId="project-1" api={api as never} />);
    expect(await screen.findByText("Saved answer")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "New conversation" }));
    await waitFor(() => expect(api.createAgentConversation).toHaveBeenCalledWith("project-1", expect.any(String), expect.any(String)));
    fireEvent.click(screen.getByRole("button", { name: "Conversation history" }));
    expect(screen.getByText("Saved request")).toBeVisible();
    expect(screen.getAllByText("New conversation").length).toBeGreaterThan(0);
  });

  it("creates a live conversation and exposes durable paid approvals", async () => {
    const api = {
      createAgentConversation: vi.fn().mockResolvedValue({ id: "conversation-1" }),
      sendAgentMessage: vi.fn().mockResolvedValue({ state: "idle" }),
      agentMessages: vi.fn().mockResolvedValue({
        items: [{
          id: "assistant-tool-call", role: "assistant", content: [{ type: "tool_call", id: "call-1", name: "model3d.generate", arguments: {} }],
        }, {
          id: "tool-result", role: "tool_result", tool_call_id: "call-1", tool_name: "model3d.generate", content: [{ type: "text", text: "Approval required." }],
          details: { ui_action: { action_id: "approval-1", type: "confirm_external_paid" } },
        }],
      }),
      decideApproval: vi.fn().mockResolvedValue({ status: "queued", summary: "Job queued.", job: { job_id: "job-1" } }),
      job: vi.fn().mockResolvedValue({ id: "job-1", status: "running", output_asset_ids: [] }),
    };
    const onJobQueued = vi.fn();
    render(<AgentPanel projectId="project-1" api={api as never} onJobQueued={onJobQueued} />);
    await waitFor(() => expect(api.createAgentConversation).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText("Message the Agent"), { target: { value: "Create a model" } });
    fireEvent.click(screen.getByLabelText("Send to Agent"));
    expect(await screen.findByRole("button", { name: "Approve and run" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Approve and run" }));
    await waitFor(() => expect(api.decideApproval).toHaveBeenCalledWith("project-1", "approval-1", true, expect.any(String)));
    await waitFor(() => expect(api.job).toHaveBeenCalledWith("project-1", "job-1"));
    expect(api.sendAgentMessage).toHaveBeenCalledTimes(1);
    expect(onJobQueued).toHaveBeenCalledWith("job-1", null, undefined);
    expect(screen.queryByText("The approval could not be submitted. Try again.")).toBeNull();
    expect(screen.getByRole("status")).not.toHaveTextContent("Waiting for your approval");
    expect(screen.getByRole("status")).toHaveTextContent("Background task is running");
    expect(screen.getByRole("status")).not.toHaveTextContent("Agent is ready");
  });

  it("clears a restored approval projection when the durable pending action disappears", async () => {
    const pendingPage = {
      items: [{
        id: "tool-call", role: "assistant", content: [{
          type: "tool_call", id: "image-call", name: "image.transform_from_reference", arguments: {},
        }],
      }],
      pending_ui_actions: [{
        tool_call_id: "image-call",
        tool_name: "image.transform_from_reference",
        result: {
          content: [{ type: "text", text: "Approval is required." }],
          details: {
            status: "awaiting_ui_action",
            ui_action: { action_id: "approval-image", type: "approval_required" },
          },
          is_error: false,
        },
      }],
    };
    const api = {
      agentConversations: vi.fn().mockResolvedValue({ items: [{
        id: "suspended", state: "idle", message_count: 1,
      }] }),
      createAgentConversation: vi.fn(),
      agentMessages: vi.fn()
        .mockResolvedValueOnce(pendingPage)
        .mockResolvedValue({ items: [], pending_ui_actions: [] }),
      agentEvents: vi.fn()
        .mockResolvedValueOnce({
          items: [{
            sequence_no: 1,
            event_type: "agent.idle",
            payload: { conversation_id: "suspended" },
            created_at: new Date().toISOString(),
          }],
          next_cursor: 1,
        })
        .mockResolvedValue({ items: [], next_cursor: 1 }),
    };

    render(<AgentPanel projectId="project-1" api={api as never} />);

    await waitFor(() => expect(api.agentMessages).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("button", { name: "Approve and run" })).toBeNull();
    expect(screen.queryByLabelText("image.transform_from_reference tool Completed")).toBeNull();
    expect(screen.getByRole("status")).not.toHaveTextContent("Waiting for your approval");
  });

  it("restores a suspended paid Tool approval after the Agent idle event", async () => {
    const api = {
      agentConversations: vi.fn().mockResolvedValue({ items: [{
        id: "suspended", state: "idle", message_count: 2, preview: "Create a model",
      }] }),
      createAgentConversation: vi.fn(),
      agentMessages: vi.fn().mockResolvedValue({
        items: [{
          id: "tool-call", role: "assistant", content: [{
            type: "tool_call", id: "model-call", name: "generate_model3d", arguments: {},
          }],
        }],
        pending_ui_actions: [{
          tool_call_id: "model-call",
          tool_name: "generate_model3d",
          result: {
            content: [{ type: "text", text: "Approval is required." }],
            details: {
              status: "awaiting_ui_action",
              ui_action: { action_id: "approval-restore", type: "approval_required" },
            },
            is_error: false,
          },
        }],
        event_cursor: 3,
      }),
      agentEvents: vi.fn()
        .mockResolvedValueOnce({
          items: [
            { sequence_no: 4, event_type: "agent.idle", payload: { conversation_id: "suspended" }, created_at: new Date().toISOString() },
            { sequence_no: 5, event_type: "conversation.suspended", payload: { conversation_id: "suspended" }, created_at: new Date().toISOString() },
          ],
          next_cursor: 5,
        })
        .mockResolvedValue({ items: [], next_cursor: 5 }),
      decideApproval: vi.fn().mockResolvedValue({ status: "queued", summary: "Job queued.", job: { job_id: "job-restore" } }),
      job: vi.fn().mockResolvedValue({ id: "job-restore", status: "running", output_asset_ids: [] }),
    };

    render(<AgentPanel projectId="project-1" api={api as never} />);

    expect(await screen.findByRole("button", { name: "Approve and run" })).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("Waiting for your approval");
    expect(screen.getByLabelText("generate_model3d tool Completed")).toBeVisible();
  });

  it("declines a suspended external action before sending a replacement instruction", async () => {
    const busyError = Object.assign(new Error("busy"), { code: "AGENT_BUSY" });
    const api = {
      agentConversations: vi.fn().mockResolvedValue({ items: [{
        id: "suspended", state: "idle", message_count: 1, preview: "Regenerate the image",
      }] }),
      createAgentConversation: vi.fn(),
      agentMessages: vi.fn()
        .mockResolvedValueOnce({
          items: [{
            id: "tool-call", role: "assistant", content: [{
              type: "tool_call", id: "image-call", name: "image.transform_from_reference", arguments: {},
            }],
          }],
          pending_ui_actions: [{
            tool_call_id: "image-call",
            tool_name: "image.transform_from_reference",
            result: {
              content: [{ type: "text", text: "Approval is required." }],
              details: {
                status: "awaiting_ui_action",
                ui_action: { action_id: "approval-image", type: "approval_required" },
              },
              is_error: false,
            },
          }],
        })
        .mockResolvedValue({
          items: [
            { id: "declined", role: "tool_result", tool_call_id: "image-call", tool_name: "image.transform_from_reference", content: "The external operation was declined.", details: { status: "declined" } },
            { id: "replacement", role: "user", content: "不要再重新生成了" },
            { id: "reply", role: "assistant", content: "明白，将保留当前图片。" },
          ],
          pending_ui_actions: [],
        }),
      decideApproval: vi.fn().mockResolvedValue({ status: "failed", summary: "Declined." }),
      sendAgentMessage: vi.fn()
        .mockRejectedValueOnce(busyError)
        .mockResolvedValue({ state: "idle" }),
    };

    render(<AgentPanel projectId="project-1" api={api as never} />);

    expect(await screen.findByRole("status")).toHaveTextContent("Waiting for your approval");
    fireEvent.change(screen.getByRole("textbox", { name: "Message the Agent" }), {
      target: { value: "不要再重新生成了" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send to Agent" }));

    await waitFor(() => expect(api.sendAgentMessage).toHaveBeenCalledTimes(2));
    expect(api.decideApproval).toHaveBeenCalledWith(
      "project-1", "approval-image", false, expect.any(String),
    );
    expect(api.decideApproval.mock.invocationCallOrder[0]).toBeLessThan(
      api.sendAgentMessage.mock.invocationCallOrder[0],
    );
    expect(api.sendAgentMessage.mock.calls[0][3]).toBe(api.sendAgentMessage.mock.calls[1][3]);
    expect(await screen.findByText("明白，将保留当前图片。")).toBeVisible();
    expect(screen.queryByText(/could not complete this request/)).toBeNull();
    expect(screen.getByRole("textbox", { name: "Message the Agent" })).toHaveValue("");
  });

  it("pairs tool results with their assistant tool call and keeps raw output collapsed", async () => {
    const output = JSON.stringify(Array.from({ length: 21 }, (_, index) => ({ name: `asset-${index}`, asset_type: "source_image", is_current: index === 0 })), null, 2);
    const api = {
      createAgentConversation: vi.fn().mockResolvedValue({ id: "conversation-1" }),
      sendAgentMessage: vi.fn().mockResolvedValue({ state: "idle" }),
      agentMessages: vi.fn().mockResolvedValue({ items: [
        { id: "assistant", role: "assistant", content: [{ type: "tool_call", id: "assets-call", name: "asset.list", arguments: {} }] },
        { id: "assets", role: "tool_result", tool_call_id: "assets-call", tool_name: "asset.list", content: [{ type: "text", text: output }] },
        { id: "reply", role: "assistant", content: [{ type: "text", text: "PI_UI_TOOL_OK" }] },
      ] }),
    };
    const { container } = render(<AgentPanel projectId="project-1" api={api as never} />);
    await waitFor(() => expect(api.createAgentConversation).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText("Message the Agent"), { target: { value: "List the assets" } });
    fireEvent.click(screen.getByLabelText("Send to Agent"));

    await screen.findByText("PI_UI_TOOL_OK");
    expect(container.querySelector(".agent-tool-execution.success")).toBeNull();
    expect(screen.getByText(/21 assets/)).toBeVisible();
    expect(screen.queryByText("asset-1")).toBeNull();
    expect(screen.queryByText(output)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show tool output" }));
    expect(container.querySelector(".agent-tool-output")?.textContent).toBe(output);
    expect(screen.getByLabelText("Message the Agent")).toBeVisible();
  });

  it("uses Pi-style compact renderers for successful read and bash output", async () => {
    const bashOutput = Array.from({ length: 8 }, (_, index) => `line-${index + 1}`).join("\n");
    const api = {
      createAgentConversation: vi.fn().mockResolvedValue({ id: "conversation-1" }),
      sendAgentMessage: vi.fn().mockResolvedValue({ state: "idle" }),
      agentMessages: vi.fn().mockResolvedValue({ items: [
        { id: "tools", role: "assistant", content: [
          { type: "tool_call", id: "read-call", name: "read", arguments: { path: "notes.txt" } },
          { type: "tool_call", id: "bash-call", name: "bash", arguments: { command: "example" } },
        ] },
        { id: "read-result", role: "tool_result", tool_call_id: "read-call", tool_name: "read", content: [{ type: "text", text: "hidden file contents" }] },
        { id: "bash-result", role: "tool_result", tool_call_id: "bash-call", tool_name: "bash", content: [{ type: "text", text: bashOutput }] },
      ] }),
    };
    render(<AgentPanel projectId="project-1" api={api as never} />);
    await waitFor(() => expect(api.createAgentConversation).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText("Message the Agent"), { target: { value: "Inspect" } });
    fireEvent.click(screen.getByLabelText("Send to Agent"));

    await screen.findByText(/line-8/);
    expect(screen.queryByText("hidden file contents")).toBeNull();
    expect(screen.queryByText("line-1")).toBeNull();
    expect(screen.getByText(/3 earlier lines/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Show tool output" }));
    expect(screen.getByText(/hidden file contents/)).toBeVisible();
    expect(screen.getByText(/line-1/)).toBeVisible();
  });

  it("attaches a selected existing image to the final assistant message, not its tool card", async () => {
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:agent-generated-cat"),
      revokeObjectURL: vi.fn(),
    });
    const api = {
      createAgentConversation: vi.fn().mockResolvedValue({ id: "conversation-1" }),
      sendAgentMessage: vi.fn().mockResolvedValue({ state: "idle" }),
      agentMessages: vi.fn().mockResolvedValue({ items: [
        { id: "assistant", role: "assistant", content: [{ type: "tool_call", id: "show-cat", name: "asset.get_metadata", arguments: {} }] },
        {
          id: "generated-cat-result",
          role: "tool_result",
          tool_call_id: "show-cat",
          tool_name: "asset.get_metadata",
          content: [{ type: "text", text: "Found the existing cat image." }],
          details: { status: "succeeded", output_asset_ids: ["cat-asset"] },
        },
        { id: "reply", role: "assistant", content: [{ type: "text", text: "Your cat image is ready." }] },
      ] }),
      assets: vi.fn().mockResolvedValue([{
        id: "cat-asset", asset_type: "generated_image", name: "orange-cat.png", is_current: true, metadata: {},
      }]),
      assetContent: vi.fn().mockResolvedValue(new Blob(["image"], { type: "image/png" })),
    };
    const { container } = render(<AgentPanel projectId="project-1" api={api as never} />);
    await waitFor(() => expect(api.createAgentConversation).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText("Message the Agent"), { target: { value: "Generate a cat" } });
    fireEvent.click(screen.getByLabelText("Send to Agent"));

    const image = await screen.findByRole("img", { name: "Project image: orange-cat.png" });
    expect(image).toHaveAttribute("src", "blob:agent-generated-cat");
    const answer = screen.getByText("Your cat image is ready.").closest<HTMLElement>("article.agent-message.assistant");
    expect(answer).not.toBeNull();
    expect(within(answer!).getByRole("img", { name: "Project image: orange-cat.png" })).toBe(image);
    expect(container.querySelector(".agent-tool-execution .agent-chat-image")).toBeNull();
    expect(screen.queryByText("Found the existing cat image.")).toBeNull();
  });

  it("converts managed Markdown image references into inline Blob thumbnails", async () => {
    const objectUrls = ["blob:mother-preview", "blob:component-preview"];
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => objectUrls.shift() ?? "blob:extra-preview"),
      revokeObjectURL: vi.fn(),
    });
    const api = {
      createAgentConversation: vi.fn().mockResolvedValue({ id: "conversation-1" }),
      sendAgentMessage: vi.fn().mockResolvedValue({ state: "idle" }),
      agentMessages: vi.fn().mockResolvedValue({ items: [
        {
          id: "mother-result",
          role: "tool_result",
          tool_call_id: "mother-call",
          tool_name: "generate_images",
          content: [{ type: "text", text: "Mother image ready." }],
          details: { status: "succeeded", output_asset_ids: ["mother-asset"] },
        },
        {
          id: "split-result",
          role: "tool_result",
          tool_call_id: "split-call",
          tool_name: "image.split_alpha_components",
          content: [{ type: "text", text: "Component ready." }],
          details: { status: "succeeded", output_asset_ids: ["component-asset"] },
        },
        {
          id: "reply",
          role: "assistant",
          content: [{
            type: "text",
            text: "母图：![Q版母图](asset:mother-asset)\n\n组件：![头盔](components/headgear.png)",
          }],
        },
      ] }),
      assets: vi.fn().mockResolvedValue([
        { id: "mother-asset", asset_type: "generated_image", name: "mother.png", is_current: false, metadata: {} },
        { id: "component-asset", asset_type: "crop", name: "headgear.png", is_current: false, metadata: {} },
      ]),
      assetThumbnail: vi.fn()
        .mockResolvedValueOnce(new Blob(["mother"], { type: "image/png" }))
        .mockResolvedValueOnce(new Blob(["component"], { type: "image/png" })),
      assetContent: vi.fn(),
    };

    const { container } = render(<AgentPanel projectId="project-1" api={api as never} />);
    await waitFor(() => expect(api.createAgentConversation).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText("Message the Agent"), { target: { value: "Show the results inline" } });
    fireEvent.click(screen.getByLabelText("Send to Agent"));

    const mother = await screen.findByRole("img", { name: "Q版母图" });
    const component = await screen.findByRole("img", { name: "头盔" });
    const answer = mother.closest<HTMLElement>("article.agent-message.assistant");
    expect(answer).not.toBeNull();
    expect(within(answer!).getByRole("img", { name: "头盔" })).toBe(component);
    expect(mother).toHaveAttribute("src", "blob:mother-preview");
    expect(component).toHaveAttribute("src", "blob:component-preview");
    expect(api.assetThumbnail).toHaveBeenCalledTimes(2);
    expect(api.assetContent).not.toHaveBeenCalled();
    expect(container.querySelector(".agent-chat-images")).toBeNull();
    expect(container.querySelector('img[src^="asset:"]')).toBeNull();
  });

  it("does not attach every image returned by asset.list", async () => {
    const api = {
      createAgentConversation: vi.fn().mockResolvedValue({ id: "conversation-1" }),
      sendAgentMessage: vi.fn().mockResolvedValue({ state: "idle" }),
      agentMessages: vi.fn().mockResolvedValue({ items: [
        { id: "assistant", role: "assistant", content: [{ type: "tool_call", id: "list-call", name: "asset.list", arguments: {} }] },
        {
          id: "list-result",
          role: "tool_result",
          tool_call_id: "list-call",
          tool_name: "asset.list",
          content: [{ type: "text", text: "Found matching robot images." }],
          details: { status: "succeeded", output_asset_ids: ["robot-1", "robot-2"] },
        },
        { id: "reply", role: "assistant", content: [{ type: "text", text: "I found two robot images. Choose one and I can present it." }] },
      ] }),
      assets: vi.fn(),
    };
    const { container } = render(<AgentPanel projectId="project-1" api={api as never} />);
    await waitFor(() => expect(api.createAgentConversation).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText("Message the Agent"), { target: { value: "Find robot images" } });
    fireEvent.click(screen.getByLabelText("Send to Agent"));

    await screen.findByText("I found two robot images. Choose one and I can present it.");
    expect(api.assets).not.toHaveBeenCalled();
    expect(container.querySelector(".agent-chat-image")).toBeNull();
  });

  it("attaches a completed generated image to the final assistant message", async () => {
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:agent-cthulhu-cover"),
      revokeObjectURL: vi.fn(),
    });
    const api = {
      createAgentConversation: vi.fn().mockResolvedValue({ id: "conversation-1" }),
      sendAgentMessage: vi.fn().mockResolvedValue({ state: "idle" }),
      agentMessages: vi.fn().mockResolvedValue({ items: [
        {
          id: "job-complete",
          role: "tool_result",
          tool_call_id: "job-call",
          tool_name: "generate_images",
          content: [{ type: "text", text: "The Job completed successfully." }],
          details: { status: "succeeded", output_asset_ids: ["cthulhu-cover"] },
        },
        { id: "reply", role: "assistant", content: [{ type: "text", text: "The Cthulhu-style cover is ready." }] },
      ] }),
      assets: vi.fn().mockResolvedValue([{
        id: "cthulhu-cover", asset_type: "generated_image", name: "cthulhu-cover.png", is_current: true, metadata: {},
      }]),
      assetContent: vi.fn().mockResolvedValue(new Blob(["image"], { type: "image/png" })),
    };
    const { container } = render(<AgentPanel projectId="project-1" api={api as never} />);
    await waitFor(() => expect(api.createAgentConversation).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText("Message the Agent"), { target: { value: "Generate a Cthulhu cover" } });
    fireEvent.click(screen.getByLabelText("Send to Agent"));

    const image = await screen.findByRole("img", { name: "Project image: cthulhu-cover.png" });
    const answer = screen.getByText("The Cthulhu-style cover is ready.").closest<HTMLElement>("article.agent-message.assistant");
    expect(answer).not.toBeNull();
    expect(within(answer!).getByRole("img", { name: "Project image: cthulhu-cover.png" })).toBe(image);
    expect(container.querySelector(".agent-tool-execution .agent-chat-image")).toBeNull();
  });

  it("does not render persisted model reasoning", async () => {
    const api = {
      createAgentConversation: vi.fn().mockResolvedValue({ id: "conversation-1" }),
      sendAgentMessage: vi.fn().mockResolvedValue({ state: "idle" }),
      agentMessages: vi.fn().mockResolvedValue({ items: [{
        id: "assistant", role: "assistant", content: [
          { type: "thinking", thinking: "I should inspect the current asset." },
          { type: "text", text: "The current asset is ready." },
        ],
      }] }),
    };
    render(<AgentPanel projectId="project-1" api={api as never} />);
    await waitFor(() => expect(api.createAgentConversation).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText("Message the Agent"), { target: { value: "Status" } });
    fireEvent.click(screen.getByLabelText("Send to Agent"));
    expect(screen.queryByText("I should inspect the current asset.")).toBeNull();
    expect(await screen.findByText("The current asset is ready.")).toBeVisible();
    expect(screen.queryByRole("button", { name: /thinking/i })).toBeNull();
  });

  it("does not render streamed Qwen reasoning", async () => {
    const api = {
      agentConversations: vi.fn().mockResolvedValue({ items: [] }),
      createAgentConversation: vi.fn().mockResolvedValue({
        id: "conversation-1", state: "running", message_count: 0,
      }),
      agentMessages: vi.fn().mockResolvedValue({ items: [] }),
      agentEvents: vi.fn()
        .mockResolvedValueOnce({
          items: [{
            sequence_no: 1,
            event_type: "message.started",
            payload: { conversation_id: "conversation-1" },
            created_at: new Date().toISOString(),
          }, {
            sequence_no: 2,
            event_type: "reasoning.started",
            payload: { conversation_id: "conversation-1" },
            created_at: new Date().toISOString(),
          }, {
            sequence_no: 3,
            event_type: "reasoning.delta",
            payload: {
              conversation_id: "conversation-1",
              text: "I am examining the image before selecting a tool.",
            },
            created_at: new Date().toISOString(),
          }],
          next_cursor: 3,
        })
        .mockResolvedValue({ items: [], next_cursor: 3 }),
    };

    render(<AgentPanel projectId="project-1" api={api as never} />);

    expect(screen.queryByText("I am examining the image before selecting a tool.")).toBeNull();
    await waitFor(() => expect(screen.queryByText("I am examining the image before selecting a tool.")).toBeNull());
  });

  it("shows a durable failed status when the Agent stops after tool execution", async () => {
    const api = {
      agentConversations: vi.fn().mockResolvedValue({ items: [] }),
      createAgentConversation: vi.fn().mockResolvedValue({ id: "conversation-1", state: "idle", message_count: 0 }),
      agentMessages: vi.fn().mockResolvedValue({ items: [{
        id: "terminal", role: "assistant", stop_reason: "error", content: [{ type: "text", text: "I could not finish the final response." }],
      }] }),
      agentEvents: vi.fn()
        .mockResolvedValueOnce({
          items: [{
            sequence_no: 1,
            event_type: "conversation.failed",
            payload: { conversation_id: "conversation-1", code: "provider_error", reason: "resource_exhausted" },
            created_at: new Date().toISOString(),
          }],
          next_cursor: 1,
        })
        .mockResolvedValue({ items: [], next_cursor: 1 }),
    };
    render(<AgentPanel projectId="project-1" api={api as never} />);
    expect(await screen.findByText("I could not finish the final response.")).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("Agent stopped before completing this response. You can try again.");
    expect(screen.getByText(/Local Qwen stopped because GPU or system memory was exhausted/)).toBeVisible();
  });

  it("shows a completed status only after the runtime completes the conversation", async () => {
    const api = {
      agentConversations: vi.fn().mockResolvedValue({ items: [] }),
      createAgentConversation: vi.fn().mockResolvedValue({ id: "conversation-1", state: "idle", message_count: 0 }),
      agentMessages: vi.fn().mockResolvedValue({ items: [{
        id: "reply", role: "assistant", content: [{ type: "text", text: "The task is complete." }],
      }] }),
      agentEvents: vi.fn()
        .mockResolvedValueOnce({
          items: [{
            sequence_no: 1,
            event_type: "conversation.completed",
            payload: { conversation_id: "conversation-1" },
            created_at: new Date().toISOString(),
          }],
          next_cursor: 1,
        })
        .mockResolvedValue({ items: [], next_cursor: 1 }),
    };
    render(<AgentPanel projectId="project-1" api={api as never} />);
    expect(await screen.findByText("The task is complete.")).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("Agent completed this response.");
  });

  it("opens the matching workspace when a live tool requests user interaction", async () => {
    const onWorkspaceAction = vi.fn();
    const api = {
      agentConversations: vi.fn().mockResolvedValue({ items: [] }),
      createAgentConversation: vi.fn().mockResolvedValue({ id: "conversation-1", state: "idle", message_count: 0 }),
      agentMessages: vi.fn().mockResolvedValue({ items: [] }),
      agentEvents: vi.fn()
        .mockResolvedValueOnce({
          items: [{
            sequence_no: 1,
            event_type: "tool.completed",
            payload: {
              conversation_id: "conversation-1",
              tool_call_id: "selection-call",
              tool_name: "selection.request_user",
              is_error: false,
              result: {
                content: [{ type: "text", text: "Waiting for the user to confirm a selection." }],
                details: {
                  status: "awaiting_ui_action",
                  ui_action: {
                    action_id: "selection-action",
                    type: "select_rectangle",
                    workspace_mode: "rectangle_selection",
                  },
                },
                is_error: false,
              },
            },
            created_at: new Date().toISOString(),
          }],
          next_cursor: 1,
        })
        .mockResolvedValue({ items: [], next_cursor: 1 }),
    };

    render(<AgentPanel projectId="project-1" api={api as never} onWorkspaceAction={onWorkspaceAction} />);

    await waitFor(() => expect(onWorkspaceAction).toHaveBeenCalledWith(expect.objectContaining({ mode: "selection" })));
    expect(onWorkspaceAction).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Open selection" })).toBeVisible();
  });

  it("writes completed local split outputs back to target extraction", async () => {
    const onWorkspaceAction = vi.fn();
    const api = {
      agentConversations: vi.fn().mockResolvedValue({ items: [] }),
      createAgentConversation: vi.fn().mockResolvedValue({ id: "conversation-1", state: "idle", message_count: 0 }),
      agentMessages: vi.fn().mockResolvedValue({ items: [] }),
      agentEvents: vi.fn()
        .mockResolvedValueOnce({
          items: [
            {
              sequence_no: 1,
              event_type: "tool.call",
              payload: {
                conversation_id: "conversation-1",
                tool_call: {
                  id: "split-call",
                  name: "split_image",
                  arguments: { source_asset_ref: "breakdown-sheet", split_mode: "grid", columns: 2, rows: 1 },
                },
              },
              created_at: new Date().toISOString(),
            },
            {
              sequence_no: 2,
              event_type: "tool.completed",
              payload: {
                conversation_id: "conversation-1",
                tool_call_id: "split-call",
                tool_name: "split_image",
                is_error: false,
                result: {
                  content: [{ type: "text", text: "Image split locally." }],
                  details: {
                    status: "succeeded",
                    output_asset_ids: ["part-a", "part-b"],
                  },
                  is_error: false,
                },
              },
              created_at: new Date().toISOString(),
            },
          ],
          next_cursor: 2,
        })
        .mockResolvedValue({ items: [], next_cursor: 2 }),
    };

    render(<AgentPanel projectId="project-1" api={api as never} onWorkspaceAction={onWorkspaceAction} />);

    await waitFor(() => expect(onWorkspaceAction).toHaveBeenCalledWith(expect.objectContaining({
      mode: "target_extract",
      assetId: "breakdown-sheet",
      jobType: "image.split_local",
      resultAssetIds: ["part-a", "part-b"],
    })));
    expect(onWorkspaceAction).toHaveBeenCalledTimes(1);
  });

  it("keeps the source and extraction method when an Agent target-extraction Job completes", async () => {
    const onWorkspaceAction = vi.fn();
    const api = {
      agentConversations: vi.fn().mockResolvedValue({ items: [] }),
      createAgentConversation: vi.fn().mockResolvedValue({ id: "conversation-1", state: "idle", message_count: 0 }),
      agentMessages: vi.fn().mockResolvedValue({ items: [] }),
      agentEvents: vi.fn()
        .mockResolvedValueOnce({
          items: [
            {
              sequence_no: 1,
              event_type: "tool.call",
              payload: {
                conversation_id: "conversation-1",
                tool_call: {
                  id: "extract-call",
                  name: "split_image",
                  arguments: {
                    source_asset_ref: "source-for-extraction",
                    prompt_asset_ref: "subject-prompt",
                    split_mode: "element",
                  },
                },
              },
              created_at: new Date().toISOString(),
            },
            {
              sequence_no: 2,
              event_type: "tool.completed",
              payload: {
                conversation_id: "conversation-1",
                tool_call_id: "extract-call",
                tool_name: "split_image",
                is_error: false,
                result: {
                  content: [{ type: "text", text: "Target extraction job queued." }],
                  details: {
                    status: "queued",
                    job: { job_id: "target-job", job_type: "element.split" },
                  },
                  is_error: false,
                },
              },
              created_at: new Date().toISOString(),
            },
          ],
          next_cursor: 2,
        })
        .mockResolvedValue({ items: [], next_cursor: 2 }),
      job: vi.fn().mockResolvedValue({
        id: "target-job",
        status: "succeeded",
        job_type: "element.split",
        output_asset_ids: ["extracted-subject"],
      }),
      sendAgentMessage: vi.fn().mockResolvedValue({}),
    };

    render(<AgentPanel projectId="project-1" api={api as never} onWorkspaceAction={onWorkspaceAction} />);

    await waitFor(() => expect(onWorkspaceAction).toHaveBeenCalledWith(expect.objectContaining({
      mode: "target_extract",
      method: "breakdown",
      assetId: "source-for-extraction",
      jobId: "target-job",
      jobType: "element.split",
      resultAssetIds: ["extracted-subject"],
    })));
  });

  it("opens a completed facade workspace only once when the parent callback changes", async () => {
    const firstWorkspaceAction = vi.fn();
    const secondWorkspaceAction = vi.fn();
    const api = {
      agentConversations: vi.fn().mockResolvedValue({ items: [] }),
      createAgentConversation: vi.fn().mockResolvedValue({
        id: "conversation-1",
        state: "idle",
        message_count: 0,
      }),
      agentMessages: vi.fn().mockResolvedValue({ items: [] }),
      agentEvents: vi.fn()
        .mockResolvedValueOnce({
          items: [{
            sequence_no: 1,
            event_type: "tool.completed",
            payload: {
              conversation_id: "conversation-1",
              tool_call_id: "split-call",
              tool_name: "split_image",
              is_error: false,
              result: {
                content: [{ type: "text", text: "Image split." }],
                details: { status: "succeeded" },
                is_error: false,
              },
            },
            created_at: new Date().toISOString(),
          }],
          next_cursor: 1,
        })
        .mockResolvedValue({ items: [], next_cursor: 1 }),
    };

    const { rerender } = render(
      <AgentPanel
        projectId="project-1"
        api={api as never}
        onWorkspaceAction={firstWorkspaceAction}
      />,
    );

    await waitFor(() => expect(firstWorkspaceAction).toHaveBeenCalledWith(expect.objectContaining({ mode: "target_extract" })));
    expect(firstWorkspaceAction).toHaveBeenCalledTimes(1);

    rerender(
      <AgentPanel
        projectId="project-1"
        api={api as never}
        onWorkspaceAction={secondWorkspaceAction}
      />,
    );

    await waitFor(() => expect(api.agentEvents).toHaveBeenCalled());
    expect(firstWorkspaceAction).toHaveBeenCalledTimes(1);
    expect(secondWorkspaceAction).not.toHaveBeenCalled();
  });

  it("opens direct target extraction when split_image needs the user to draw a box", async () => {
    const onWorkspaceAction = vi.fn();
    const api = {
      agentConversations: vi.fn().mockResolvedValue({ items: [] }),
      createAgentConversation: vi.fn().mockResolvedValue({ id: "conversation-1", state: "idle", message_count: 0 }),
      agentMessages: vi.fn().mockResolvedValue({ items: [] }),
      agentEvents: vi.fn()
        .mockResolvedValueOnce({
          items: [{
            sequence_no: 1,
            event_type: "tool.call",
            payload: {
              conversation_id: "conversation-1",
              tool_call: {
                id: "split-selection",
                name: "split_image",
                arguments: {
                  source_asset_ref: "source-1",
                  prompt_asset_ref: "prompt-1",
                  split_mode: "boxsplit",
                },
              },
            },
            created_at: new Date().toISOString(),
          }, {
            sequence_no: 2,
            event_type: "tool.completed",
            payload: {
              conversation_id: "conversation-1",
              tool_call_id: "split-selection",
              tool_name: "split_image",
              is_error: false,
              result: {
                content: [{ type: "text", text: "Waiting for the user to confirm a selection." }],
                details: {
                  status: "awaiting_ui_action",
                  ui_action: {
                    action_id: "target-selection-action",
                    type: "select_rectangle",
                    workspace_mode: "rectangle_selection",
                    asset_id: "source-1",
                  },
                },
                is_error: false,
              },
            },
            created_at: new Date().toISOString(),
          }],
          next_cursor: 2,
        })
        .mockResolvedValue({ items: [], next_cursor: 2 }),
    };

    render(<AgentPanel projectId="project-1" api={api as never} onWorkspaceAction={onWorkspaceAction} />);

    await waitFor(() => expect(onWorkspaceAction).toHaveBeenCalledWith(expect.objectContaining({
      mode: "target_extract",
      method: "direct",
      assetId: "source-1",
      actionId: "target-selection-action",
      instruction: expect.stringContaining("框选"),
    })));
    expect(screen.getByRole("button", { name: "Open target extraction" })).toBeVisible();
  });

  it("opens the matching facade workspace when a paid Tool reaches approval", async () => {
    const onWorkspaceAction = vi.fn();
    const api = {
      agentConversations: vi.fn().mockResolvedValue({ items: [] }),
      createAgentConversation: vi.fn().mockResolvedValue({ id: "conversation-1", state: "idle", message_count: 0 }),
      agentMessages: vi.fn().mockResolvedValue({ items: [] }),
      agentEvents: vi.fn()
        .mockResolvedValueOnce({
          items: [{
            sequence_no: 1,
            event_type: "tool.call",
            payload: {
              conversation_id: "conversation-1",
              tool_call: {
                id: "split-call",
                name: "split_image",
                arguments: { split_mode: "element" },
              },
            },
            created_at: new Date().toISOString(),
          }, {
            sequence_no: 2,
            event_type: "tool.completed",
            payload: {
              conversation_id: "conversation-1",
              tool_call_id: "split-call",
              tool_name: "split_image",
              is_error: false,
              result: {
                content: [{ type: "text", text: "Approval is required." }],
                details: {
                  status: "awaiting_ui_action",
                  ui_action: {
                    action_id: "split-approval",
                    type: "approval_required",
                    workspace_mode: "working",
                  },
                },
                is_error: false,
              },
            },
            created_at: new Date().toISOString(),
          }],
          next_cursor: 2,
        })
        .mockResolvedValue({ items: [], next_cursor: 2 }),
    };

    render(<AgentPanel projectId="project-1" api={api as never} onWorkspaceAction={onWorkspaceAction} />);

    await waitFor(() => expect(onWorkspaceAction).toHaveBeenCalledWith(expect.objectContaining({
      mode: "target_extract",
      method: "breakdown",
    })));
    expect(onWorkspaceAction).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Open target extraction" })).toBeVisible();
  });

  it("passes Agent image-generation parameters into the creative image workspace", async () => {
    const onWorkspaceAction = vi.fn();
    const api = {
      agentConversations: vi.fn().mockResolvedValue({ items: [] }),
      createAgentConversation: vi.fn().mockResolvedValue({ id: "conversation-1", state: "idle", message_count: 0 }),
      agentMessages: vi.fn().mockResolvedValue({ items: [] }),
      agentEvents: vi.fn()
        .mockResolvedValueOnce({
          items: [{
            sequence_no: 1,
            event_type: "tool.call",
            payload: {
              conversation_id: "conversation-1",
              tool_call: {
                id: "generate-call",
                name: "generate_images",
                arguments: {
                  mode: "from_prompt",
                  prompt: "A polished ceramic fox figurine on a studio plinth",
                  candidate_count: 4,
                  aspect_ratio: "16:9",
                },
              },
            },
            created_at: new Date().toISOString(),
          }, {
            sequence_no: 2,
            event_type: "tool.completed",
            payload: {
              conversation_id: "conversation-1",
              tool_call_id: "generate-call",
              tool_name: "generate_images",
              is_error: false,
              result: {
                content: [{ type: "text", text: "Approval is required." }],
                details: {
                  status: "awaiting_ui_action",
                  data: { prompt_asset_id: "prompt-agent" },
                  ui_action: {
                    action_id: "generate-approval",
                    type: "approval_required",
                    workspace_mode: "working",
                  },
                },
                is_error: false,
              },
            },
            created_at: new Date().toISOString(),
          }],
          next_cursor: 2,
        })
        .mockResolvedValue({ items: [], next_cursor: 2 }),
    };

    render(<AgentPanel projectId="project-1" api={api as never} onWorkspaceAction={onWorkspaceAction} />);

    await waitFor(() => expect(onWorkspaceAction).toHaveBeenCalledWith(expect.objectContaining({
      mode: "prompt_image",
      prompt: "A polished ceramic fox figurine on a studio plinth",
      promptAssetId: "prompt-agent",
      candidateCount: 4,
      aspectRatio: "16:9",
      actionId: "generate-approval",
    })));
  });
});
