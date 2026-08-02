CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,checksum TEXT NOT NULL,applied_at TEXT NOT NULL);
CREATE TABLE app_settings(key TEXT PRIMARY KEY,value_json TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE recent_projects(project_id TEXT PRIMARY KEY,root_path TEXT NOT NULL,last_opened_at TEXT NOT NULL,availability TEXT NOT NULL);
CREATE TABLE legacy_migrations(source_fingerprint TEXT PRIMARY KEY,status TEXT NOT NULL,report_json TEXT NOT NULL,completed_at TEXT);
