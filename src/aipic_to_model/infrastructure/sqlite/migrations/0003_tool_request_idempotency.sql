BEGIN;
CREATE TABLE tool_requests(
  request_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
  payload_hash TEXT NOT NULL,
  tool_call_id TEXT NOT NULL REFERENCES tool_calls(id) ON DELETE RESTRICT,
  result_json TEXT,
  error_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX ix_tool_requests_call ON tool_requests(tool_call_id);
COMMIT;
