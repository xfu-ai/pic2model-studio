BEGIN;
CREATE TABLE production_approvals(
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
  tool_call_id TEXT NOT NULL UNIQUE REFERENCES tool_calls(id) ON DELETE RESTRICT,
  tool_name TEXT NOT NULL,
  provider_profile TEXT NOT NULL,
  arguments_hash TEXT NOT NULL,
  scope_hash TEXT NOT NULL,
  input_asset_summary_json TEXT NOT NULL,
  cost_summary_json TEXT NOT NULL,
  arguments_summary_json TEXT NOT NULL,
  decision TEXT NOT NULL CHECK(decision IN('requires_user','approved','denied','consumed')),
  requested_at TEXT NOT NULL,
  decided_at TEXT,
  consumed_at TEXT
);
CREATE INDEX ix_production_approvals_project_decision
  ON production_approvals(project_id,decision,requested_at);
COMMIT;
