CREATE TABLE IF NOT EXISTS agent_schema_migrations(
  version INTEGER PRIMARY KEY,
  checksum TEXT NOT NULL,
  applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_sessions(
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  system_prompt TEXT NOT NULL,
  profile_json TEXT NOT NULL,
  thinking_level TEXT NOT NULL,
  active_tools_json TEXT NOT NULL,
  active_skills_json TEXT NOT NULL DEFAULT '[]',
  compaction_json TEXT
);
CREATE TABLE IF NOT EXISTS agent_messages(
  session_id TEXT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
  sequence_no INTEGER NOT NULL,
  message_id TEXT NOT NULL,
  role TEXT NOT NULL,
  message_json TEXT NOT NULL,
  tool_call_id TEXT,
  PRIMARY KEY(session_id, sequence_no),
  UNIQUE(session_id, message_id)
);
CREATE INDEX IF NOT EXISTS ix_agent_messages_tool_call ON agent_messages(session_id, tool_call_id);
CREATE TABLE IF NOT EXISTS agent_operations(
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
  state TEXT NOT NULL CHECK(state IN('running','completed','interrupted')),
  started_at TEXT NOT NULL,
  ended_at TEXT
);
CREATE TABLE IF NOT EXISTS agent_tool_operations(
  session_id TEXT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
  tool_call_id TEXT NOT NULL,
  operation_id TEXT NOT NULL REFERENCES agent_operations(id) ON DELETE RESTRICT,
  tool_name TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN('running','completed','interrupted')),
  result_json TEXT,
  PRIMARY KEY(session_id, tool_call_id)
);
