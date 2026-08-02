CREATE TABLE app_operations(
 request_id TEXT PRIMARY KEY,
 action TEXT NOT NULL,
 payload_hash TEXT NOT NULL,
 result_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);
