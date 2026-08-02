BEGIN;
CREATE TABLE prompt_versions(
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
  asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE RESTRICT,
  kind TEXT NOT NULL CHECK(kind IN('content','style','merged','image','multiview','element','boxsplit')),
  language TEXT NOT NULL CHECK(language IN('zh','en')),
  body TEXT NOT NULL,
  parser_version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  UNIQUE(project_id,asset_id,kind,language)
);
CREATE INDEX ix_prompt_versions_project_asset ON prompt_versions(project_id,asset_id,created_at);
COMMIT;
