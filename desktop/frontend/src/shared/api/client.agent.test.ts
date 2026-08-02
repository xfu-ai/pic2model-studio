import { describe, expect, it, vi } from "vitest";
import { ApiClient } from "./client";

describe("ApiClient Agent commands", () => {
  it("sends the mandatory request id when creating a conversation", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ id: "conversation", project_id: "project", state: "idle", message_count: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const api = new ApiClient({ base_url: "http://127.0.0.1:1234", bearer_token: "token", origin: "http://tauri.localhost" });
    await api.createAgentConversation("project", "system", "conversation-create");
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(new Headers(init.headers).get("X-Request-Id")).toBe("conversation-create");
    fetchMock.mockRestore();
  });

  it("can limit an Agent transcript request", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ items: [] }), { status: 200 }),
    );
    const api = new ApiClient({ base_url: "http://127.0.0.1:1234", bearer_token: "token", origin: "http://tauri.localhost" });
    await api.agentMessages("project", "conversation", 20, 42);
    expect(String(fetchMock.mock.calls[0][0])).toContain("project_id=project&limit=20&before=42");
    fetchMock.mockRestore();
  });
});
