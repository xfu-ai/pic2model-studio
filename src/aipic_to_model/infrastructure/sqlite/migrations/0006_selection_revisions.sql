BEGIN;
CREATE TABLE selection_revisions(
  selection_id TEXT NOT NULL REFERENCES selections(id) ON DELETE RESTRICT,
  revision INTEGER NOT NULL CHECK(revision>0),
  command_type TEXT NOT NULL CHECK(command_type IN('create','move','resize_n','resize_ne','resize_e','resize_se','resize_s','resize_sw','resize_w','resize_nw','numeric','clear','undo','redo','confirm')),
  target_revision INTEGER,
  before_json TEXT,
  after_json TEXT,
  event_id TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  PRIMARY KEY(selection_id,revision)
);
COMMIT;
