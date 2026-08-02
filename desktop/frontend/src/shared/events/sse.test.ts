import { describe, expect, it } from "vitest";
import { EventAccumulator, type CanonicalEvent } from "./sse";

const event = (id: string, sequence: number): CanonicalEvent => ({
  event_id: id, event_type: "asset.created", project_id: "project-1", conversation_id: null, run_id: null,
  entity_id: "asset-1", sequence_no: sequence, payload: {}, created_at: "2026-07-26T00:00:00Z",
});

describe("EventAccumulator", () => {
  it("rejects duplicate and stale canonical events", () => {
    const accumulator = new EventAccumulator();
    expect(accumulator.accept(event("event-1", 1))).toBe(true);
    expect(accumulator.accept(event("event-1", 2))).toBe(false);
    expect(accumulator.accept(event("event-2", 1))).toBe(false);
    expect(accumulator.accept(event("event-3", 2))).toBe(true);
  });
});
