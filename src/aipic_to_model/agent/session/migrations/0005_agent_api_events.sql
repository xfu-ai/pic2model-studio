CREATE TABLE IF NOT EXISTS agent_api_events(
  session_id TEXT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
  sequence_no INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(session_id, sequence_no)
);
CREATE INDEX IF NOT EXISTS ix_agent_api_events_session_sequence
  ON agent_api_events(session_id, sequence_no);
