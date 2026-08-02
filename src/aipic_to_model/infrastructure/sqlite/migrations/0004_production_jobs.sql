BEGIN;
CREATE TABLE jobs(
  id TEXT PRIMARY KEY,
  run_id TEXT,
  tool_call_id TEXT NOT NULL REFERENCES tool_calls(id) ON DELETE RESTRICT,
  job_type TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN('queued','running','waiting','succeeded','failed','cancelled','interrupted')),
  progress INTEGER CHECK(progress IS NULL OR progress BETWEEN 0 AND 100),
  stage TEXT NOT NULL,
  provider TEXT,
  external_task_id TEXT,
  resume_class TEXT NOT NULL CHECK(resume_class IN('fresh','local_restartable','remote_poll','download_retry','unknown_submission','manual_review','stop_waiting')),
  resume_json TEXT NOT NULL DEFAULT '{}',
  result_asset_ids_json TEXT NOT NULL DEFAULT '[]',
  error_json TEXT,
  cancel_requested_at TEXT,
  cancel_mode TEXT CHECK(cancel_mode IS NULL OR cancel_mode IN('local','remote','stop_waiting')),
  lease_owner TEXT,
  lease_until TEXT,
  heartbeat_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_jobs_provider_external ON jobs(provider,external_task_id) WHERE external_task_id IS NOT NULL;
CREATE INDEX ix_jobs_claim ON jobs(status,resume_class,lease_until);
CREATE TABLE outbox_events(
  sequence_no INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT NOT NULL UNIQUE,
  aggregate_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  published_at TEXT,
  delivery_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE event_consumptions(
  consumer_name TEXT NOT NULL,
  event_id TEXT NOT NULL REFERENCES outbox_events(id) ON DELETE RESTRICT,
  processed_at TEXT NOT NULL,
  PRIMARY KEY(consumer_name,event_id)
);
CREATE TABLE event_consumer_cursors(
  consumer_name TEXT PRIMARY KEY,
  last_sequence_no INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
COMMIT;
