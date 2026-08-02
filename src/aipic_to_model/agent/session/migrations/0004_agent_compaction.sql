CREATE TABLE IF NOT EXISTS agent_compactions(
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
  state TEXT NOT NULL CHECK(state IN('started','committed','interrupted')),
  reason TEXT NOT NULL CHECK(reason IN('manual','threshold','overflow')),
  summary TEXT,
  first_kept_sequence INTEGER,
  retained_tail_json TEXT,
  tokens_before INTEGER NOT NULL,
  tokens_after INTEGER,
  usage_json TEXT,
  provider_id TEXT,
  model TEXT,
  previous_compaction_id TEXT REFERENCES agent_compactions(id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL,
  committed_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_agent_compactions_session_created
  ON agent_compactions(session_id, created_at);
