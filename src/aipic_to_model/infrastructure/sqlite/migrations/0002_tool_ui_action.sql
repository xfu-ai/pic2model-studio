BEGIN;

-- Rebuild the parent and both referencing tables as one foreign-key-safe unit.
-- Foreign-key enforcement remains enabled throughout this migration.
CREATE TABLE tool_calls_new(
  id TEXT PRIMARY KEY,
  run_id TEXT,
  round_index INTEGER NOT NULL DEFAULT 0,
  tool_name TEXT NOT NULL,
  tool_version TEXT NOT NULL,
  arguments_json TEXT NOT NULL,
  arguments_hash TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  provider_profile TEXT,
  risk_level TEXT NOT NULL CHECK(risk_level IN('read_only','local_reversible','external','external_paid','destructive')),
  status TEXT NOT NULL CHECK(status IN('proposed','approved','running','queued','awaiting_ui_action','unknown_submission','succeeded','failed','cancelled')),
  result_json TEXT,
  error_json TEXT,
  duration_ms INTEGER CHECK(duration_ms IS NULL OR duration_ms>=0),
  started_at TEXT,
  finished_at TEXT
);
CREATE TABLE tool_call_assets_new(
  tool_call_id TEXT NOT NULL REFERENCES tool_calls_new(id) ON DELETE RESTRICT,
  asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE RESTRICT,
  direction TEXT NOT NULL CHECK(direction IN('input','output')),
  role TEXT NOT NULL,
  PRIMARY KEY(tool_call_id,asset_id,direction,role)
);
CREATE TABLE tool_idempotency_new(
  idempotency_key TEXT PRIMARY KEY,
  tool_name TEXT NOT NULL,
  tool_version TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN('reserved','running','queued','unknown_submission','succeeded','failed_retryable','failed_terminal')),
  owner_tool_call_id TEXT NOT NULL REFERENCES tool_calls_new(id) ON DELETE RESTRICT,
  job_id TEXT,
  result_json TEXT,
  updated_at TEXT NOT NULL
);
INSERT INTO tool_calls_new SELECT * FROM tool_calls;
INSERT INTO tool_call_assets_new SELECT * FROM tool_call_assets;
INSERT INTO tool_idempotency_new SELECT * FROM tool_idempotency;
DROP TABLE tool_call_assets;
DROP TABLE tool_idempotency;
DROP TABLE tool_calls;
ALTER TABLE tool_calls_new RENAME TO tool_calls;
ALTER TABLE tool_call_assets_new RENAME TO tool_call_assets;
ALTER TABLE tool_idempotency_new RENAME TO tool_idempotency;
CREATE INDEX ix_tool_calls_run ON tool_calls(run_id,round_index);
COMMIT;
