export type CanonicalEvent = {
  event_id: string;
  event_type: string;
  project_id: string;
  conversation_id: string | null;
  run_id: string | null;
  entity_id: string | null;
  sequence_no: number;
  payload: Record<string, unknown>;
  created_at: string;
};

export class EventAccumulator {
  private readonly ids = new Set<string>();
  private latestSequence = 0;

  accept(event: CanonicalEvent): boolean {
    if (!event.event_id || !event.event_type || !Number.isInteger(event.sequence_no) || event.sequence_no <= this.latestSequence || this.ids.has(event.event_id)) return false;
    this.ids.add(event.event_id);
    this.latestSequence = event.sequence_no;
    return true;
  }
}

export async function replayCanonicalEvents(
  session: { base_url: string; bearer_token: string; origin: string },
  projectId: string,
  after: string | undefined,
  accept: (event: CanonicalEvent) => void,
): Promise<string | undefined> {
  const query = new URLSearchParams({ project_id: projectId });
  if (after) query.set("after", after);
  const response = await fetch(`${session.base_url}/v1/events?${query}`, {
    headers: { Authorization: `Bearer ${session.bearer_token}`, Origin: session.origin, Accept: "text/event-stream" },
  });
  if (!response.ok || !response.body) throw new Error("event replay unavailable");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let cursor = after;
  let buffer = "";
  while (true) {
    const next = await reader.read();
    if (next.done) break;
    buffer += decoder.decode(next.value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() ?? "";
    for (const chunk of chunks) {
      const id = chunk.match(/^id:\s*(.+)$/m)?.[1];
      const data = chunk.match(/^data:\s*(.+)$/m)?.[1];
      if (!id || !data) continue;
      const event = JSON.parse(data) as CanonicalEvent;
      accept(event);
      cursor = id;
    }
  }
  return cursor;
}
