CREATE TABLE IF NOT EXISTS agent_job_waits(
  session_id TEXT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
  project_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  tool_call_id TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  job_id TEXT,
  state TEXT NOT NULL CHECK(state IN('awaiting_ui_action','waiting','terminal_returned','waiting_external','declined')),
  created_at TEXT NOT NULL,
  resumed_at TEXT,
  PRIMARY KEY(session_id, tool_call_id)
);
CREATE INDEX IF NOT EXISTS ix_agent_job_waits_active
  ON agent_job_waits(project_id, state, job_id);
